"""FR-9.3 — Road centerline extraction and curvature computation.

Takes ``route_matched.parquet`` (FR-9.1 output), stitches snapped positions
into a continuous polyline, resamples to a 1 m arc-length grid, and computes
signed curvature kappa(s).  Speed limits from FR-9.2 are attached at each
resampled point.

Output schema: ``ReferencePath`` (TRD §1.5).

CLI:
    python -m ideal_driver ref --trace day2
"""

from __future__ import annotations

import argparse
import datetime
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd
import yaml
from scipy.ndimage import uniform_filter1d

if TYPE_CHECKING:
    from ideal_driver.speed_limits import SpeedLimitLookup


# ---------------------------------------------------------------------------
# Logging helper (matches project log format -- TRD §4.4)
# ---------------------------------------------------------------------------


def _log(tag: str, msg: str) -> None:
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    sys.stdout.write(f"[{ts}] [{tag}] INFO  {msg}\n")
    sys.stdout.flush()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _remove_consecutive_duplicates(
    px: np.ndarray,
    py: np.ndarray,
    way_ids: np.ndarray,
    tol_m: float = 0.01,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Drop consecutive points closer than *tol_m* apart."""
    dists = np.hypot(np.diff(px), np.diff(py))
    keep = np.concatenate([[True], dists > tol_m])
    return px[keep], py[keep], way_ids[keep]


def _fill_way_ids(way_ids: np.ndarray) -> np.ndarray:
    """Forward-then-backward-fill None/NaN way IDs.

    Needed when some matched points had no OSM edge index.
    """
    result = list(way_ids)
    # forward pass
    last: int | None = None
    for i, w in enumerate(result):
        if w is not None and not (isinstance(w, float) and np.isnan(w)):
            last = int(w)
        elif last is not None:
            result[i] = last
    # backward pass
    last = None
    for i in range(len(result) - 1, -1, -1):
        w = result[i]
        if w is not None and not (isinstance(w, float) and np.isnan(w)):
            last = int(w)
        elif last is not None:
            result[i] = last
    # final safety: replace any remaining None with 0
    return np.array([int(w) if w is not None else 0 for w in result])


# ---------------------------------------------------------------------------
# Core function
# ---------------------------------------------------------------------------


def build_reference_path(
    route_matched: pd.DataFrame,
    speed_lookup: SpeedLimitLookup,
    resample_step_m: float = 1.0,
    curvature_smooth_window_m: float = 5.0,
) -> pd.DataFrame:
    """Build a uniformly-sampled reference path from a matched route.

    Parameters
    ----------
    route_matched:
        ``route_matched.parquet`` DataFrame (FR-9.1 schema).
    speed_lookup:
        :class:`SpeedLimitLookup` instance (FR-9.2).
    resample_step_m:
        Arc-length spacing between output rows (default 1 m).
    curvature_smooth_window_m:
        Gaussian smoothing window for kink removal (default 5 m).

    Returns
    -------
    DataFrame conforming to the ``ReferencePath`` schema (TRD §1.5).

    Raises
    ------
    ValueError
        When the matched route has fewer than 2 usable points or is too
        short to resample.
    """
    # ----------------------------------------------------------------
    # 1. Filter to well-matched rows, sort by time
    # ----------------------------------------------------------------
    valid = route_matched.dropna(subset=["snapped_px_m", "snapped_py_m"]).copy()
    valid = valid[valid["match_confidence"] > 0.0]
    if "t_s" in valid.columns:
        valid = valid.sort_values("t_s")
    valid = valid.reset_index(drop=True)

    if len(valid) < 2:
        raise ValueError(f"Insufficient matched points ({len(valid)}) to build reference path.")

    px_raw = valid["snapped_px_m"].to_numpy(dtype=float)
    py_raw = valid["snapped_py_m"].to_numpy(dtype=float)
    way_ids_raw = valid["osm_way_id"].to_numpy()

    # ----------------------------------------------------------------
    # 2. Remove consecutive duplicates; fill missing way IDs
    # ----------------------------------------------------------------
    px_raw, py_raw, way_ids_raw = _remove_consecutive_duplicates(px_raw, py_raw, way_ids_raw)
    way_ids_raw = _fill_way_ids(way_ids_raw)

    if len(px_raw) < 2:
        raise ValueError("Reference path collapsed to < 2 unique points after dedup.")

    # ----------------------------------------------------------------
    # 3. Compute cumulative arc length of original points
    # ----------------------------------------------------------------
    seg_lens = np.hypot(np.diff(px_raw), np.diff(py_raw))
    s_orig = np.concatenate([[0.0], np.cumsum(seg_lens)])
    total_len = float(s_orig[-1])

    if total_len < resample_step_m:
        raise ValueError(
            f"Path is too short ({total_len:.2f} m) to resample at {resample_step_m} m steps."
        )

    # ----------------------------------------------------------------
    # 4. Resample to uniform grid
    # ----------------------------------------------------------------
    s_new = np.arange(0.0, total_len, resample_step_m)
    px_r = np.interp(s_new, s_orig, px_raw)
    py_r = np.interp(s_new, s_orig, py_raw)

    # Nearest-neighbour way ID assignment at each resampled point
    idx_nearest = np.searchsorted(s_orig, s_new, side="left")
    idx_nearest = np.clip(idx_nearest, 0, len(s_orig) - 1)
    way_ids_r = way_ids_raw[idx_nearest].astype(int)

    # ----------------------------------------------------------------
    # 5. Heading (forward-difference, gradient for endpoints)
    # ----------------------------------------------------------------
    dpx = np.gradient(px_r, s_new)
    dpy = np.gradient(py_r, s_new)
    heading_unwrapped = np.unwrap(np.arctan2(dpy, dpx))

    # ----------------------------------------------------------------
    # 6. Curvature kappa = d(psi)/d(s), smoothed to remove kinks
    # ----------------------------------------------------------------
    curvature_raw = np.gradient(heading_unwrapped, s_new)
    window_pts = max(1, round(curvature_smooth_window_m / resample_step_m))
    curvature = uniform_filter1d(curvature_raw, size=window_pts, mode="nearest")

    # Normalise heading to [-pi, pi]
    heading_norm = (heading_unwrapped + np.pi) % (2.0 * np.pi) - np.pi

    # ----------------------------------------------------------------
    # 7. Speed limits
    # ----------------------------------------------------------------
    unique_ways = list({int(w) for w in way_ids_r})
    spd_map = speed_lookup.lookup(unique_ways)
    default_spd = speed_lookup._default_mps
    speed_limits = np.array(
        [spd_map.get(int(w), default_spd) for w in way_ids_r],
        dtype=float,
    )

    # ----------------------------------------------------------------
    # 8. Assemble output DataFrame
    # ----------------------------------------------------------------
    return pd.DataFrame(
        {
            "s_m": s_new,
            "px_m": px_r,
            "py_m": py_r,
            "heading_rad": heading_norm,
            "curvature_1pm": curvature,
            "speed_limit_mps": speed_limits,
            "osm_way_id": way_ids_r,
        }
    )


# ---------------------------------------------------------------------------
# make_reference_path (CLI entry point for T4.2)
# ---------------------------------------------------------------------------


def make_reference_path(
    trace: str,
    out_dir: Path,
    config_path: Path,
    speed_limits_path: Path,
    skip_overpass: bool = False,
) -> int:
    """Build ``reference_path.parquet`` for *trace*.

    Returns exit code: 0 = success, 1 = error.
    """
    from data_engine.parquet_io import write_parquet
    from data_engine.schemas import ReferencePath

    from ideal_driver.speed_limits import SpeedLimitLookup

    matched_path = out_dir / trace / "route_matched.parquet"
    if not matched_path.exists():
        sys.stderr.write(
            f"ERROR: {matched_path} not found -- run `make ideal TRACE={trace}` first.\n"
        )
        return 1

    _log("FR-9.3 ref", f"loading matched route from {matched_path}")
    route_matched = pd.read_parquet(matched_path)

    # Load path config
    with open(config_path, encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    path_cfg = cfg.get("path", {})
    resample_step_m = float(path_cfg.get("resample_step_m", 1.0))
    smooth_window_m = float(path_cfg.get("curvature_smooth_window_m", 5.0))

    speed_lookup = SpeedLimitLookup(
        speed_limits_path,
        skip_overpass=skip_overpass,
    )

    _log(
        "FR-9.3 ref",
        f"building reference path (resample={resample_step_m} m, "
        f"smooth={smooth_window_m} m, skip_overpass={skip_overpass})",
    )

    try:
        df = build_reference_path(
            route_matched,
            speed_lookup,
            resample_step_m=resample_step_m,
            curvature_smooth_window_m=smooth_window_m,
        )
    except ValueError as exc:
        sys.stderr.write(f"ERROR building reference path: {exc}\n")
        return 1

    n_pts = len(df)
    total_len = float(df["s_m"].iloc[-1])
    _log("FR-9.3 ref", f"reference path: {n_pts} points, {total_len:.1f} m total")

    out_path = out_dir / trace / "reference_path.parquet"
    write_parquet(df, out_path, ReferencePath, trip_id=trace)
    _log("FR-9.3 ref", f"wrote {out_path} ({out_path.stat().st_size} bytes)")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Extract road centerline reference path (FR-9.3)")
    p.add_argument(
        "--trace",
        default=os.environ.get("TRIP_ID"),
        required=not os.environ.get("TRIP_ID"),
        help="trace name (e.g. day2) (falls back to TRIP_ID env var in ECS)",
    )
    p.add_argument("--out-dir", type=Path, default=Path("out"))
    p.add_argument(
        "--config",
        type=Path,
        default=Path("config/ideal.yaml"),
        help="ideal.yaml for path resampling parameters",
    )
    p.add_argument(
        "--speed-limits",
        type=Path,
        default=Path("config/speed_limits.yaml"),
        help="speed_limits.yaml for hand-coded corridor overrides",
    )
    p.add_argument(
        "--skip-overpass",
        action="store_true",
        default=False,
        help="skip Overpass API query (use YAML/default only)",
    )
    return p


def main(argv: list[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)
    code = make_reference_path(
        args.trace,
        args.out_dir,
        args.config,
        args.speed_limits,
        skip_overpass=args.skip_overpass,
    )
    if code != 0:
        sys.exit(code)


if __name__ == "__main__":
    main()
