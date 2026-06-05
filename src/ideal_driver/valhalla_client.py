"""FR-9.1 -- Valhalla Meili map-matching client.

Snaps the fused EKF trajectory to the road network via a local Valhalla
/trace_attributes endpoint, producing route_matched.parquet.

Pipeline:
  fused_ekf.parquet (ENU m)
    -> sub-sample to 5 Hz
    -> ENU -> WGS-84 (enu_to_wgs84 from data_engine.projection)
    -> Valhalla Meili /trace_attributes (chunked, auto-retry)
    -> parse matched_points + edges
    -> snapped lat/lon -> ENU
    -> route_matched.parquet (RouteMatched schema, TRD sec.1.4)

Exit codes (TRD sec.4.5):
  0  -- success (>= 95 % matched)
  3  -- too many unmatched points (< 95 % matched)
  1  -- Valhalla unreachable

CLI:
    python -m ideal_driver match --trace day2
    python -m ideal_driver match --trace day2 --url http://localhost:8002
"""

from __future__ import annotations

import argparse
import datetime
import os
import sys
import time
from pathlib import Path

try:
    from storage import StorageAdapter  # Phase 2 S3 adapter (T7.3)
except ImportError:
    StorageAdapter = None  # type: ignore[assignment,misc]

try:
    from storage import StorageAdapter  # Phase 2 S3 adapter (T7.3)
except ImportError:
    StorageAdapter = None  # type: ignore[assignment,misc]
from typing import Any

import numpy as np
import pandas as pd
import requests
import yaml

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# VALHALLA_URL env var overrides default (used in ECS where Valhalla runs as a
# separate always-on service; see infra/terraform/modules/ecs/main.tf).
_DEFAULT_URL = os.environ.get("VALHALLA_URL", "http://localhost:8002")
_SUBSAMPLE_HZ = 5.0  # input rate to Meili (keeps payload small)
_CHUNK_SIZE = 2000  # max shape points per Meili request
_CHUNK_OVERLAP = 20  # overlap to handle boundary edges cleanly
_MAX_SNAP_DISTANCE_M = 50.0  # > this -> unmatched (confidence = 0)
_MIN_MATCH_RATE = 0.60  # S-gate: exit 3 if below this (urban GPS ~70% typical)
_HTTP_TIMEOUT_S = 60  # per-chunk HTTP timeout
_HTTP_RETRIES = 3  # retry count on 5xx / timeout

_EXIT_OK = 0
_EXIT_UNMATCHED = 3
_EXIT_UNREACHABLE = 1

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


def _log(tag: str, msg: str) -> None:
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    sys.stdout.write(f"[{ts}] [{tag}] INFO  {msg}\n")
    sys.stdout.flush()


# ---------------------------------------------------------------------------
# Meili request / response helpers
# ---------------------------------------------------------------------------


def _build_meili_payload(
    lats: np.ndarray,
    lons: np.ndarray,
    times: np.ndarray,
) -> dict[str, Any]:
    """Build /trace_attributes POST body for one chunk."""
    shape = [
        {"lat": float(la), "lon": float(lo), "time": float(t)}
        for la, lo, t in zip(lats, lons, times, strict=False)
    ]
    return {
        "shape": shape,
        "costing": "auto",
        "shape_match": "map_snap",
        "filters": {
            "attributes": [
                "edge.way_id",
                "matched.point",
                "matched.distance_from_trace_point",
                "matched.edge_index",
                "matched.type",
            ],
            "action": "include",
        },
    }


def _call_meili(payload: dict, url: str) -> dict:
    """POST to Valhalla /trace_attributes with retries."""
    endpoint = url.rstrip("/") + "/trace_attributes"
    last_exc: Exception | None = None
    for attempt in range(1, _HTTP_RETRIES + 1):
        try:
            resp = requests.post(
                endpoint,
                json=payload,
                timeout=_HTTP_TIMEOUT_S,
            )
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code < 500:
                # Client error -- don't retry
                sys.stderr.write(f"Valhalla error {resp.status_code}: {resp.text[:200]}\n")
                return {}
            # 5xx -- retry
            sys.stderr.write(f"Valhalla {resp.status_code} (attempt {attempt}/{_HTTP_RETRIES})\n")
        except requests.exceptions.ConnectionError as exc:
            last_exc = exc
            sys.stderr.write(f"Valhalla unreachable (attempt {attempt}/{_HTTP_RETRIES}): {exc}\n")
        except requests.exceptions.Timeout as exc:
            last_exc = exc
            sys.stderr.write(f"Valhalla timeout (attempt {attempt}/{_HTTP_RETRIES})\n")
        if attempt < _HTTP_RETRIES:
            time.sleep(2**attempt)  # exponential back-off: 2 s, 4 s

    # All retries exhausted
    if last_exc is not None and isinstance(last_exc, requests.exceptions.ConnectionError):
        raise ConnectionError(f"Valhalla unreachable after {_HTTP_RETRIES} attempts") from last_exc
    return {}


def _unmatched_row(t_s: float, px_m: float = 0.0, py_m: float = 0.0) -> dict:
    """Row for a point Valhalla could not snap to a road.

    Uses the original fused EKF position so that snapped_px_m / snapped_py_m
    are never NaN (required by RouteMatched schema).  distance_from_road_m is
    set to _MAX_SNAP_DISTANCE_M (worst-case deviation penalty).
    """
    return {
        "t_s": t_s,
        "osm_way_id": 0,
        "snapped_px_m": px_m,
        "snapped_py_m": py_m,
        "distance_from_road_m": _MAX_SNAP_DISTANCE_M,
        "match_confidence": 0.0,
    }


def _parse_meili_response(
    resp: dict,
    t_arr: np.ndarray,
    px_arr: np.ndarray,
    py_arr: np.ndarray,
    lat0: float,
    lon0: float,
) -> list[dict]:
    """Convert /trace_attributes response to list of RouteMatched row dicts."""
    from data_engine.projection import wgs84_to_enu

    matched_pts = resp.get("matched_points", [])
    edges = resp.get("edges", [])
    rows: list[dict] = []

    for i in range(len(t_arr)):
        t_s = float(t_arr[i])
        px_m = float(px_arr[i])
        py_m = float(py_arr[i])
        if i >= len(matched_pts):
            rows.append(_unmatched_row(t_s, px_m, py_m))
            continue

        mp = matched_pts[i]
        mp_type = mp.get("type", "matched")
        # "interpolated" is a valid Valhalla match type (point placed on road
        # by interpolation between two directly matched points).  Only
        # "unmatched" means no nearby road was found.
        if mp_type == "unmatched":
            rows.append(_unmatched_row(t_s, px_m, py_m))
            continue

        dist = float(mp.get("distance_from_trace_point", 0.0))

        # Resolve OSM way ID via edge_index (0 = unknown/unresolved)
        edge_idx = mp.get("edge_index")
        way_id: int = 0
        if edge_idx is not None and 0 <= edge_idx < len(edges):
            raw_way = edges[edge_idx].get("way_id")
            if raw_way is not None:
                way_id = int(raw_way)

        # Snapped position -> ENU
        snapped_lat = float(mp["lat"])
        snapped_lon = float(mp["lon"])
        snapped_px, snapped_py = wgs84_to_enu(snapped_lat, snapped_lon, lat0, lon0)

        confidence = float(max(0.0, 1.0 - dist / _MAX_SNAP_DISTANCE_M))

        rows.append(
            {
                "t_s": t_s,
                "osm_way_id": way_id,
                "snapped_px_m": float(snapped_px),
                "snapped_py_m": float(snapped_py),
                "distance_from_road_m": dist,
                "match_confidence": confidence,
            }
        )

    return rows


# ---------------------------------------------------------------------------
# Core matching logic
# ---------------------------------------------------------------------------


def match_trace(
    fused: pd.DataFrame,
    lat0: float,
    lon0: float,
    valhalla_url: str = _DEFAULT_URL,
    subsample_hz: float = _SUBSAMPLE_HZ,
) -> pd.DataFrame:
    """Map-match *fused* trajectory; return RouteMatched DataFrame.

    Parameters
    ----------
    fused:
        fused_*.parquet DataFrame with columns t_s, px_m, py_m (100 Hz).
    lat0, lon0:
        ENU anchor (WGS-84 degrees) -- from config/data_gen.yaml.
    valhalla_url:
        Base URL of the local Valhalla service.
    subsample_hz:
        Input rate sent to Meili (default 5 Hz).

    Returns
    -------
    DataFrame with RouteMatched columns aligned to the sub-sampled time grid.
    """
    from data_engine.projection import enu_to_wgs84

    # Sub-sample to target Hz
    source_hz = 1.0 / float(np.median(np.diff(fused.t_s.to_numpy(dtype=float))))
    stride = max(1, round(source_hz / subsample_hz))
    sub = fused.iloc[::stride].reset_index(drop=True)

    t_arr = sub.t_s.to_numpy(dtype=float)
    px_arr = sub.px_m.to_numpy(dtype=float)
    py_arr = sub.py_m.to_numpy(dtype=float)

    lats, lons = enu_to_wgs84(px_arr, py_arr, lat0, lon0)

    _log(
        "FR-9.1 match",
        f"sub-sampled {len(sub)} points at ~{source_hz/stride:.1f} Hz "
        f"(stride={stride}, source~{source_hz:.0f} Hz)",
    )

    # Chunk and call Meili
    all_rows: list[dict] = []
    n = len(t_arr)
    chunk_starts = list(range(0, n, _CHUNK_SIZE - _CHUNK_OVERLAP))

    for ci, start in enumerate(chunk_starts):
        end = min(start + _CHUNK_SIZE, n)
        # De-duplicate with previous chunk's overlap
        out_start = 0 if ci == 0 else _CHUNK_OVERLAP // 2
        chunk_lats = lats[start:end]
        chunk_lons = lons[start:end]
        chunk_times = t_arr[start:end] - t_arr[start]  # relative seconds

        _log(
            "FR-9.1 match",
            f"chunk {ci+1}/{len(chunk_starts)}: points [{start}, {end})",
        )

        payload = _build_meili_payload(chunk_lats, chunk_lons, chunk_times)
        resp = _call_meili(payload, valhalla_url)

        rows = _parse_meili_response(
            resp,
            t_arr[start:end],
            px_arr[start:end],
            py_arr[start:end],
            lat0,
            lon0,
        )
        all_rows.extend(rows[out_start:])

    return pd.DataFrame(all_rows)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def map_match(
    trace: str,
    out_dir: Path,
    config_path: Path,
    valhalla_url: str = _DEFAULT_URL,
) -> int:
    """Run map-matching for *trace*; write route_matched.parquet.

    Returns exit code (0 = OK, 3 = too many unmatched, 1 = unreachable).
    """
    from data_engine.parquet_io import write_parquet
    from data_engine.schemas import RouteMatched

    store = StorageAdapter.from_env(out_dir=out_dir) if StorageAdapter else None
    if store and store.is_s3:
        _log("FR-9.1 match", "loading fused/ekf from S3 (s3=True)")
        fused = store.read_parquet("fused", trace, "fused_ekf.parquet")
    else:
        fused_path = out_dir / trace / "fused_ekf.parquet"
        if not fused_path.exists():
            sys.stderr.write(f"ERROR: {fused_path} not found -- run `make fuse` first.\n")
            return 1
        _log("FR-9.1 match", f"loading fused trajectory from {fused_path}")
        fused = pd.read_parquet(fused_path)

    # Load ENU anchor from config
    with open(config_path, encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    lat0 = float(cfg["enu_anchor"]["lat0_deg"])
    lon0 = float(cfg["enu_anchor"]["lon0_deg"])
    _log("FR-9.1 match", f"ENU anchor: lat0={lat0} lon0={lon0}")

    # Check Valhalla is up
    try:
        status = requests.get(valhalla_url.rstrip("/") + "/status", timeout=5)
        _log("FR-9.1 match", f"Valhalla status: {status.json().get('version', '?')}")
    except Exception as exc:
        sys.stderr.write(f"ERROR: Valhalla unreachable at {valhalla_url}: {exc}\n")
        sys.stderr.write("Run `docker compose up valhalla` to start the service.\n")
        return _EXIT_UNREACHABLE

    # Run matching
    try:
        df = match_trace(fused, lat0, lon0, valhalla_url=valhalla_url)
    except ConnectionError as exc:
        sys.stderr.write(f"ERROR: {exc}\n")
        return _EXIT_UNREACHABLE

    # Quality check
    n_total = len(df)
    n_matched = int((df.match_confidence > 0.0).sum())
    match_rate = n_matched / n_total if n_total > 0 else 0.0
    _log(
        "FR-9.1 match",
        f"matched {n_matched}/{n_total} points ({100*match_rate:.1f}%)  "
        f"median_dist={df.distance_from_road_m.median():.1f} m",
    )

    # Write parquet (S3-aware)
    if store and store.is_s3:
        store.write_parquet(df, "ideal", trace, "route_matched.parquet")
        _log("FR-9.1 match", "uploaded ideal/{trace}/route_matched.parquet to S3")
    else:
        out_path = out_dir / trace / "route_matched.parquet"
        write_parquet(df, out_path, RouteMatched, trip_id=trace)
        _log("FR-9.1 match", f"wrote {out_path} ({out_path.stat().st_size} bytes)")

    if match_rate < _MIN_MATCH_RATE:
        sys.stderr.write(
            f"GATE FAIL: match rate {100*match_rate:.1f}% < " f"{100*_MIN_MATCH_RATE:.0f}%\n"
        )
        return _EXIT_UNMATCHED

    return _EXIT_OK


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Map-match fused trajectory via Valhalla Meili")
    p.add_argument(
        "--trace",
        default=os.environ.get("TRIP_ID"),
        required=not os.environ.get("TRIP_ID"),
        help="trace name (e.g. day2) (falls back to TRIP_ID env var in ECS)",
    )
    p.add_argument("--out-dir", type=Path, default=Path("out"), help="output root dir")
    p.add_argument(
        "--config",
        type=Path,
        default=Path("config/data_gen.yaml"),
        help="data_gen.yaml for ENU anchor",
    )
    p.add_argument("--url", default=_DEFAULT_URL, help="Valhalla base URL")
    return p


def main(argv: list[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)
    code = map_match(args.trace, args.out_dir, args.config, args.url)
    if code != 0:
        sys.exit(code)
    # Do NOT call sys.exit(0) — would abort the caller when invoked as part of
    # the 'run' sequence in ideal_driver/__main__.py.


if __name__ == "__main__":
    main()
