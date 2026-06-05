"""FR-9.4 — Comfort- and curvature-constrained ideal speed profile.

Algorithm (three-pass + jerk smoothing):

1. Curvature cap:  v_curv(s) = sqrt(a_lat_max / |kappa(s)|)
2. Speed-limit cap: v_raw(s) = min(speed_limit(s), v_curv(s))
3. Forward pass:  v[i] limited by max longitudinal acceleration
4. Backward pass: v[i] limited by max longitudinal deceleration
5. Jerk smoothing: Gaussian filter + re-enforce feasibility (2 cycles)
6. Derivatives:   a = v * dv/ds,  j = v * da/ds  (d/dt = v * d/ds)

Parameters from ``config/ideal.yaml`` (TRD sec.9):
    limits.max_accel_lat_mps2   -> a_lat_max (curvature cap)
    limits.max_accel_lon_mps2   -> a_lon_max (forward pass cap)
    limits.max_decel_lon_mps2   -> a_lon_dec (backward pass cap)
    limits.max_jerk_mps3        -> j_max     (smoothing target)

Output schema: ``IdealSpeed`` (TRD sec.1.6).

CLI:
    python -m ideal_driver speed --trace day2
"""

from __future__ import annotations

import argparse
import datetime
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
from scipy.ndimage import gaussian_filter1d

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_KAPPA_MIN: float = 1e-6  # avoid division by zero for straight roads
_V_MIN_MPS: float = 0.5  # minimum allowed speed (prevents dt -> inf)


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


def _log(tag: str, msg: str) -> None:
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    sys.stdout.write(f"[{ts}] [{tag}] INFO  {msg}\n")
    sys.stdout.flush()


# ---------------------------------------------------------------------------
# Kinematic passes
# ---------------------------------------------------------------------------


def _curvature_speed_cap(
    kappa: np.ndarray,
    speed_limit: np.ndarray,
    a_lat_max: float,
) -> np.ndarray:
    """Element-wise min(speed_limit, sqrt(a_lat_max / |kappa|))."""
    kappa_abs = np.maximum(np.abs(kappa), _KAPPA_MIN)
    v_curv = np.sqrt(a_lat_max / kappa_abs)
    return np.minimum(speed_limit, v_curv)


def _forward_accel_pass(v_in: np.ndarray, s: np.ndarray, a_max: float) -> np.ndarray:
    """Enforce v[i]^2 <= v[i-1]^2 + 2*a_max*ds (forward sweep).

    Equivalent to |dv/dt| <= a_max when accelerating.
    """
    n = len(v_in)
    v = v_in.copy()
    for i in range(1, n):
        ds = s[i] - s[i - 1]
        v_limit = np.sqrt(v[i - 1] ** 2 + 2.0 * a_max * ds)
        if v[i] > v_limit:
            v[i] = v_limit
    return v


def _backward_decel_pass(v_in: np.ndarray, s: np.ndarray, a_dec: float) -> np.ndarray:
    """Enforce v[i]^2 <= v[i+1]^2 + 2*a_dec*ds (backward sweep).

    Equivalent to |dv/dt| <= a_dec when decelerating.
    """
    n = len(v_in)
    v = v_in.copy()
    for i in range(n - 2, -1, -1):
        ds = s[i + 1] - s[i]
        v_limit = np.sqrt(v[i + 1] ** 2 + 2.0 * a_dec * ds)
        if v[i] > v_limit:
            v[i] = v_limit
    return v


def _smooth_jerk(
    v_in: np.ndarray,
    s: np.ndarray,
    a_lon_max: float,
    a_lon_dec: float,
    sigma_s: float,
) -> np.ndarray:
    """Gaussian smoothing + re-feasibility to reduce peak jerk.

    Velocity can only stay the same or decrease (never increase beyond v_in).
    """
    ds = float(s[1] - s[0]) if len(s) > 1 else 1.0
    sigma_pts = max(1.0, sigma_s / ds)

    v_smooth = gaussian_filter1d(v_in.astype(float), sigma=sigma_pts, mode="nearest")
    v_smooth = np.minimum(v_smooth, v_in)  # never raise speed

    # Restore accel/decel feasibility
    v_smooth = _forward_accel_pass(v_smooth, s, a_lon_max)
    v_smooth = _backward_decel_pass(v_smooth, s, a_lon_dec)
    return v_smooth


# ---------------------------------------------------------------------------
# Derivatives
# ---------------------------------------------------------------------------


def _compute_derivatives(
    v: np.ndarray,
    s: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Return (a_ideal_mps2, j_ideal_mps3) from v(s).

    Arc-length parameterisation identities:
        a = dv/dt = v * dv/ds
        j = da/dt = v * d(a)/ds
    """
    dv_ds = np.gradient(v, s)
    a = v * dv_ds
    da_ds = np.gradient(a, s)
    j = v * da_ds
    return a, j


# ---------------------------------------------------------------------------
# Core function
# ---------------------------------------------------------------------------


def compute_ideal_speed_profile(
    ref_path: pd.DataFrame,
    a_lat_max: float = 2.0,
    a_lon_max: float = 1.5,
    a_lon_dec: float = 2.5,
    j_max: float = 2.0,
    v_min_mps: float = _V_MIN_MPS,
) -> pd.DataFrame:
    """Compute the ideal speed profile along a reference path.

    Parameters
    ----------
    ref_path:
        ``reference_path.parquet`` DataFrame (FR-9.3 schema).
        Required columns: ``s_m``, ``curvature_1pm``, ``speed_limit_mps``.
    a_lat_max:
        Max lateral acceleration (m/s^2); controls curvature-limited speed.
    a_lon_max:
        Max longitudinal acceleration (m/s^2); controls forward sweep.
    a_lon_dec:
        Max longitudinal deceleration (m/s^2); controls backward sweep.
    j_max:
        Jerk target (m/s^3); controls Gaussian smoothing sigma.
    v_min_mps:
        Floor velocity (prevents numerical singularities at rest).

    Returns
    -------
    DataFrame conforming to the ``IdealSpeed`` schema (TRD sec.1.6).
    """
    s = ref_path["s_m"].to_numpy(dtype=float)
    kappa = ref_path["curvature_1pm"].to_numpy(dtype=float)
    speed_limit = ref_path["speed_limit_mps"].to_numpy(dtype=float)

    # Step 1: curvature + speed-limit cap
    v_raw = _curvature_speed_cap(kappa, speed_limit, a_lat_max)
    v_raw = np.maximum(v_raw, v_min_mps)

    # Step 2: forward accel pass
    v_fwd = _forward_accel_pass(v_raw, s, a_lon_max)

    # Step 3: backward decel pass
    v_bwd = _backward_decel_pass(v_fwd, s, a_lon_dec)

    # Step 4: jerk smoothing (two cycles)
    # Sigma: spread velocity transitions over a distance s.t. jerk ~ j_max.
    # At mean speed v, a changes over sigma metres -> j ~ v*a_max/sigma.
    v_mean = max(float(np.mean(v_bwd)), v_min_mps)
    sigma_s = max(3.0, v_mean * a_lon_max / max(j_max, 0.1))
    v_s1 = _smooth_jerk(v_bwd, s, a_lon_max, a_lon_dec, sigma_s)
    v_final = _smooth_jerk(v_s1, s, a_lon_max, a_lon_dec, sigma_s)

    v_final = np.maximum(v_final, v_min_mps)

    # Step 5: time-domain derivatives
    a_ideal, j_ideal = _compute_derivatives(v_final, s)

    return pd.DataFrame(
        {
            "s_m": s,
            "v_ideal_mps": v_final,
            "a_ideal_mps2": a_ideal,
            "j_ideal_mps3": j_ideal,
        }
    )


# ---------------------------------------------------------------------------
# make_ideal_speed  (CLI pipeline entry for T4.3)
# ---------------------------------------------------------------------------


def make_ideal_speed(
    trace: str,
    out_dir: Path,
    config_path: Path,
) -> int:
    """Compute ``ideal_speed.parquet`` for *trace*.

    Returns exit code: 0 = success, 1 = error.
    """
    from data_engine.parquet_io import write_parquet
    from data_engine.schemas import IdealSpeed

    ref_path_file = out_dir / trace / "reference_path.parquet"
    if not ref_path_file.exists():
        sys.stderr.write(
            f"ERROR: {ref_path_file} not found -- run `make ref TRACE={trace}` first.\n"
        )
        return 1

    _log("FR-9.4 speed", f"loading reference path from {ref_path_file}")
    ref_path = pd.read_parquet(ref_path_file)

    with open(config_path, encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    lim = cfg.get("limits", {})
    a_lat_max = float(lim.get("max_accel_lat_mps2", 2.0))
    a_lon_max = float(lim.get("max_accel_lon_mps2", 1.5))
    a_lon_dec = float(lim.get("max_decel_lon_mps2", 2.5))
    j_max = float(lim.get("max_jerk_mps3", 2.0))

    _log(
        "FR-9.4 speed",
        f"limits: a_lat={a_lat_max} a_lon={a_lon_max} a_dec={a_lon_dec} j_max={j_max}",
    )

    df = compute_ideal_speed_profile(
        ref_path,
        a_lat_max=a_lat_max,
        a_lon_max=a_lon_max,
        a_lon_dec=a_lon_dec,
        j_max=j_max,
    )

    n_pts = len(df)
    v_mean = float(df["v_ideal_mps"].mean())
    v_min_out = float(df["v_ideal_mps"].min())
    v_max_out = float(df["v_ideal_mps"].max())
    _log(
        "FR-9.4 speed",
        f"{n_pts} points, v_mean={v_mean:.1f} v_min={v_min_out:.1f} " f"v_max={v_max_out:.1f} m/s",
    )

    out_path = out_dir / trace / "ideal_speed.parquet"
    write_parquet(df, out_path, IdealSpeed, trip_id=trace)
    _log("FR-9.4 speed", f"wrote {out_path} ({out_path.stat().st_size} bytes)")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Compute ideal speed profile from reference path (FR-9.4)"
    )
    p.add_argument("--trace", required=True, help="trace name (e.g. day2)")
    p.add_argument("--out-dir", type=Path, default=Path("out"))
    p.add_argument(
        "--config",
        type=Path,
        default=Path("config/ideal.yaml"),
        help="ideal.yaml with kinematic limits",
    )
    return p


def main(argv: list[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)
    code = make_ideal_speed(args.trace, args.out_dir, args.config)
    if code != 0:
        sys.exit(code)


if __name__ == "__main__":
    main()
