"""FR-9.5 — Jerk-minimizing quintic polynomial trajectory synthesis.

Algorithm:
1. Detect waypoints at curvature extrema (|kappa| peaks via find_peaks).
2. For each segment between consecutive waypoints:
   a. Segment travel time: T = integral(ds / v) over segment (trapezoidal rule).
   b. Fit quintic s(t) with C2 boundary conditions from ideal_speed.parquet:
        s(0)=0,   ds/dt(0)=v0,   d2s/dt2(0)=a0
        s(T)=L,   ds/dt(T)=v1,   d2s/dt2(T)=a1
3. Sample stitched trajectory at uniform dt (default 0.1 s = 10 Hz).
4. Interpolate spatial path quantities at sampled arc lengths.
5. Derive output columns:
        v_mps = ds/dt  (quintic 1st deriv)
        a_lon_mps2 = d2s/dt2  (quintic 2nd deriv)
        j_lon_mps3 = d3s/dt3  (quintic 3rd deriv)
        a_lat_mps2 = v^2 * kappa(s)
        psi_rad    = heading(s)
        psi_dot_rps = v * kappa(s)

C2 continuity is guaranteed by construction: at every interior waypoint
the BCs for the ending segment and the starting segment share the same
v and a values from ideal_speed, so position, velocity, and acceleration
are all continuous across segment boundaries.

Output schema: IdealTrajectory (TRD sec.1.7).

CLI:
    python -m ideal_driver traj --trace day2
"""

from __future__ import annotations

import argparse
import datetime
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
from scipy.interpolate import interp1d
from scipy.signal import find_peaks

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DT_OUT: float = 0.1  # output sample interval (10 Hz)
_MIN_WP_SPACING_M: float = 20.0  # minimum arc-length between waypoints
_KAPPA_PEAK_HEIGHT: float = 0.005  # |kappa| threshold to register a peak
_V_FLOOR: float = 0.1  # floor for 1/v to avoid division by zero


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


def _log(tag: str, msg: str) -> None:
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    sys.stdout.write(f"[{ts}] [{tag}] INFO  {msg}\n")
    sys.stdout.flush()


# ---------------------------------------------------------------------------
# Quintic polynomial  s(t) for one segment
# ---------------------------------------------------------------------------


def _quintic_coeffs(
    T: float,
    v0: float,
    a0: float,
    L: float,
    v1: float,
    a1: float,
) -> np.ndarray:
    """Compute coefficients of s(t) = c0 + c1*t + ... + c5*t^5.

    Boundary conditions:
        s(0) = 0,  s'(0) = v0,  s''(0) = a0
        s(T) = L,  s'(T) = v1,  s''(T) = a1

    Returns array [c0, c1, c2, c3, c4, c5].

    Derivation (closed form via 3x3 linear system):
        c0 = 0,  c1 = v0,  c2 = a0/2
        Then [1 T T^2; 3 4T 5T^2; 6 12T 20T^2] * [c3, c4*T, c5*T^2]^T = [r0, r1, r2]^T
        where r0 = (L - c1*T - c2*T^2)/T^3,
              r1 = (v1 - c1 - 2*c2*T)/T^2,
              r2 = (a1 - 2*c2)/T
        The 3x3 inverse (det=2): (1/2)*[[20,-8,1],[-30,14,-2],[12,-6,1]].
    """
    c0 = 0.0
    c1 = v0
    c2 = a0 / 2.0

    T2 = T * T
    T3 = T2 * T

    r0 = (L - c1 * T - c2 * T2) / T3
    r1 = (v1 - c1 - 2.0 * c2 * T) / T2
    r2 = (a1 - 2.0 * c2) / T

    c3 = (20.0 * r0 - 8.0 * r1 + r2) / 2.0
    c4 = (-30.0 * r0 + 14.0 * r1 - 2.0 * r2) / (2.0 * T)
    c5 = (12.0 * r0 - 6.0 * r1 + r2) / (2.0 * T2)

    return np.array([c0, c1, c2, c3, c4, c5])


def _eval_quintic(c: np.ndarray, t: np.ndarray) -> np.ndarray:
    """Evaluate s(t) at each element of *t*."""
    return c[0] + c[1] * t + c[2] * t**2 + c[3] * t**3 + c[4] * t**4 + c[5] * t**5


def _eval_quintic_derivs(
    c: np.ndarray,
    t: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return (v=ds/dt, a=d2s/dt2, j=d3s/dt3) at each element of *t*."""
    v = c[1] + 2.0 * c[2] * t + 3.0 * c[3] * t**2 + 4.0 * c[4] * t**3 + 5.0 * c[5] * t**4
    a = 2.0 * c[2] + 6.0 * c[3] * t + 12.0 * c[4] * t**2 + 20.0 * c[5] * t**3
    j = 6.0 * c[3] + 24.0 * c[4] * t + 60.0 * c[5] * t**2
    return v, a, j


# ---------------------------------------------------------------------------
# Waypoint detection
# ---------------------------------------------------------------------------


def _find_waypoints(
    kappa: np.ndarray,
    min_spacing_m: float = _MIN_WP_SPACING_M,
    kappa_threshold: float = _KAPPA_PEAK_HEIGHT,
) -> np.ndarray:
    """Return sorted index array of curvature peaks plus start/end.

    Parameters
    ----------
    kappa:
        ``curvature_1pm`` array (signed).
    min_spacing_m:
        Minimum arc-length distance (in 1 m steps = array indices) between peaks.
    kappa_threshold:
        Minimum |kappa| for a peak to be included.

    Returns
    -------
    1-D sorted integer array of waypoint indices, always including 0 and n-1.
    """
    kappa_abs = np.abs(kappa)
    min_dist = max(1, int(min_spacing_m))
    peaks, _ = find_peaks(kappa_abs, height=kappa_threshold, distance=min_dist)
    return np.unique(np.concatenate([[0], peaks, [len(kappa) - 1]]))


# ---------------------------------------------------------------------------
# Core synthesis function
# ---------------------------------------------------------------------------


def synthesize_trajectory(
    ref_path: pd.DataFrame,
    ideal_speed: pd.DataFrame,
    dt_out: float = _DT_OUT,
    min_wp_spacing_m: float = _MIN_WP_SPACING_M,
    kappa_threshold: float = _KAPPA_PEAK_HEIGHT,
) -> pd.DataFrame:
    """Synthesise ideal trajectory from reference path + speed profile.

    Parameters
    ----------
    ref_path:
        ``reference_path.parquet`` (FR-9.3).  Required: s_m, px_m, py_m,
        heading_rad, curvature_1pm.
    ideal_speed:
        ``ideal_speed.parquet`` (FR-9.4).  Required: s_m, v_ideal_mps,
        a_ideal_mps2.  Must be aligned to ref_path (same number of rows).
    dt_out:
        Output time step in seconds (default 0.1 s = 10 Hz).
    min_wp_spacing_m:
        Minimum distance between waypoints (metres, ~arc-length indices).
    kappa_threshold:
        Minimum |kappa| for a waypoint at a curvature peak.

    Returns
    -------
    DataFrame conforming to IdealTrajectory (TRD sec.1.7).
    """
    s_arr = ref_path["s_m"].to_numpy(dtype=float)
    px_arr = ref_path["px_m"].to_numpy(dtype=float)
    py_arr = ref_path["py_m"].to_numpy(dtype=float)
    kappa_arr = ref_path["curvature_1pm"].to_numpy(dtype=float)
    heading_arr = ref_path["heading_rad"].to_numpy(dtype=float)

    v_arr = ideal_speed["v_ideal_mps"].to_numpy(dtype=float)
    a_arr = ideal_speed["a_ideal_mps2"].to_numpy(dtype=float)

    n = len(s_arr)
    if len(v_arr) != n:
        raise ValueError(
            f"ref_path ({n} rows) and ideal_speed ({len(v_arr)} rows) must be aligned."
        )

    # Detect waypoints
    waypoints = _find_waypoints(kappa_arr, min_wp_spacing_m, kappa_threshold)

    # Pre-compute interpolators for spatial quantities
    heading_unwrapped = np.unwrap(heading_arr)
    interp_px = interp1d(s_arr, px_arr, kind="linear", fill_value="extrapolate")
    interp_py = interp1d(s_arr, py_arr, kind="linear", fill_value="extrapolate")
    interp_kappa = interp1d(s_arr, kappa_arr, kind="linear", fill_value="extrapolate")
    interp_heading = interp1d(s_arr, heading_unwrapped, kind="linear", fill_value="extrapolate")

    # Accumulate trajectory samples across all segments
    t_list: list[float] = []
    s_list: list[float] = []
    v_list: list[float] = []
    a_lon_list: list[float] = []
    j_lon_list: list[float] = []

    t_cumulative = 0.0

    for seg_i in range(len(waypoints) - 1):
        i0 = int(waypoints[seg_i])
        i1 = int(waypoints[seg_i + 1])

        s_seg = s_arr[i0 : i1 + 1]
        v_seg = v_arr[i0 : i1 + 1]

        L = float(s_seg[-1] - s_seg[0])
        if L < 1e-6:
            continue

        v0 = float(v_arr[i0])
        v1 = float(v_arr[i1])
        a0 = float(a_arr[i0])
        a1 = float(a_arr[i1])

        # Segment travel time via trapezoidal integration of 1/v
        inv_v = 1.0 / np.maximum(v_seg, _V_FLOOR)
        T_seg = float(np.trapz(inv_v, s_seg))
        if T_seg < 1e-6:
            continue

        # Quintic coefficients for s(t) on this segment
        coeffs = _quintic_coeffs(T_seg, v0, a0, L, v1, a1)

        # Sample times (skip t=0 after first segment to avoid duplicate)
        t_lo = 0.0 if seg_i == 0 else dt_out
        t_hi = T_seg + dt_out * 0.5
        t_local = np.arange(t_lo, t_hi, dt_out)
        if len(t_local) == 0:
            t_local = np.array([T_seg])

        s_local = np.clip(
            _eval_quintic(coeffs, t_local) + s_seg[0],
            s_arr[0],
            s_arr[-1],
        )
        v_local, a_local, j_local = _eval_quintic_derivs(coeffs, t_local)

        t_list.extend((t_local + t_cumulative).tolist())
        s_list.extend(s_local.tolist())
        v_list.extend(v_local.tolist())
        a_lon_list.extend(a_local.tolist())
        j_lon_list.extend(j_local.tolist())

        t_cumulative += T_seg

    if not t_list:
        raise ValueError("No trajectory segments generated -- check ref_path length.")

    t_out = np.array(t_list)
    s_out = np.array(s_list)
    v_out = np.maximum(np.array(v_list), 0.0)
    a_lon_out = np.array(a_lon_list)
    j_lon_out = np.array(j_lon_list)

    # Spatial interpolation
    px_out = interp_px(s_out)
    py_out = interp_py(s_out)
    kappa_out = interp_kappa(s_out)
    heading_out = (interp_heading(s_out) + np.pi) % (2.0 * np.pi) - np.pi

    a_lat_out = v_out**2 * kappa_out
    psi_dot_out = v_out * kappa_out

    return pd.DataFrame(
        {
            "t_s": t_out,
            "px_m": px_out,
            "py_m": py_out,
            "v_mps": v_out,
            "a_lon_mps2": a_lon_out,
            "a_lat_mps2": a_lat_out,
            "j_lon_mps3": j_lon_out,
            "psi_rad": heading_out,
            "psi_dot_rps": psi_dot_out,
        }
    )


# ---------------------------------------------------------------------------
# make_ideal_trajectory  (CLI pipeline entry for T4.4)
# ---------------------------------------------------------------------------


def make_ideal_trajectory(
    trace: str,
    out_dir: Path,
    config_path: Path,
) -> int:
    """Synthesise ``ideal_trajectory.parquet`` for *trace*.

    Returns exit code: 0 = success, 1 = error.
    """
    from data_engine.parquet_io import write_parquet
    from data_engine.schemas import IdealTrajectory

    ref_path_file = out_dir / trace / "reference_path.parquet"
    ideal_speed_file = out_dir / trace / "ideal_speed.parquet"

    for path in (ref_path_file, ideal_speed_file):
        if not path.exists():
            sys.stderr.write(f"ERROR: {path} not found -- run earlier pipeline stages.\n")
            return 1

    _log("FR-9.5 traj", f"loading ref_path from {ref_path_file}")
    ref_path = pd.read_parquet(ref_path_file)
    _log("FR-9.5 traj", f"loading ideal_speed from {ideal_speed_file}")
    ideal_speed = pd.read_parquet(ideal_speed_file)

    with open(config_path, encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    path_cfg = cfg.get("path", {})
    dt_out = float(path_cfg.get("traj_dt_s", _DT_OUT))

    _log("FR-9.5 traj", f"synthesising trajectory at dt={dt_out} s")

    try:
        df = synthesize_trajectory(ref_path, ideal_speed, dt_out=dt_out)
    except ValueError as exc:
        sys.stderr.write(f"ERROR: {exc}\n")
        return 1

    t_total = float(df["t_s"].iloc[-1])
    n_pts = len(df)
    _log(
        "FR-9.5 traj",
        f"{n_pts} points, T_total={t_total:.1f} s, " f"v_mean={df['v_mps'].mean():.1f} m/s",
    )

    out_path = out_dir / trace / "ideal_trajectory.parquet"
    write_parquet(df, out_path, IdealTrajectory, trip_id=trace)
    _log("FR-9.5 traj", f"wrote {out_path} ({out_path.stat().st_size} bytes)")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Synthesise ideal trajectory (FR-9.5)")
    p.add_argument("--trace", required=True, help="trace name (e.g. day2)")
    p.add_argument("--out-dir", type=Path, default=Path("out"))
    p.add_argument(
        "--config",
        type=Path,
        default=Path("config/ideal.yaml"),
        help="ideal.yaml",
    )
    return p


def main(argv: list[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)
    code = make_ideal_trajectory(args.trace, args.out_dir, args.config)
    if code != 0:
        sys.exit(code)


if __name__ == "__main__":
    main()
