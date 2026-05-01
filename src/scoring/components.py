"""FR-10.1 – FR-10.6 — Six scoring component penalty functions.

T4.5 implements: jerk_penalty, harsh_brake_penalty, lat_accel_penalty
T4.6 implements: speed_penalty, deviation_penalty, lane_change_penalty

Each returns a scalar in [0, 1].  Weights and normalization constants
are loaded from ``config/scoring.yaml`` so they can be tuned without
code changes.

Input DataFrames
----------------
``fused``:
    Fused-filter output parquet (fused_ekf.parquet / fused_ukf.parquet).
    Required columns: t_s, v_mps, psi_dot_rps.
    Optional for T4.6:  px_m, py_m.

``ideal``:
    Ideal trajectory parquet (ideal_trajectory.parquet, FR-9.5).
    Required columns: t_s, j_lon_mps3, a_lat_mps2.
    Optional for T4.6: v_mps (as ideal speed).

``reference_path``:
    Reference path parquet (reference_path.parquet, FR-9.3).
    Required for T4.6 only: s_m, px_m, py_m, speed_limit_mps.

Kinematic derivations
---------------------
Longitudinal accel:  a_lon = dv/dt  (gradient over fused time grid)
Lateral accel:       a_lat = v * psi_dot  (centripetal, body frame)
Longitudinal jerk:   j_lon = d(a_lon)/dt
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import yaml

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CONFIG_PATH: Path = Path("config/scoring.yaml")
_MIN_TRIP_DURATION_S: float = 1.0  # guard against degenerate inputs


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------


def _load_config(config_path: Path | None) -> dict:
    """Load scoring config YAML.  Falls back to project-root default."""
    path = Path(config_path) if config_path is not None else _CONFIG_PATH
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


# ---------------------------------------------------------------------------
# Kinematic helpers
# ---------------------------------------------------------------------------


def _a_lon(t: np.ndarray, v: np.ndarray) -> np.ndarray:
    """Longitudinal acceleration = dv/dt."""
    return np.gradient(v, t)


def _a_lat(v: np.ndarray, psi_dot: np.ndarray) -> np.ndarray:
    """Body-frame lateral acceleration = v * psi_dot (centripetal)."""
    return v * psi_dot


def _j_lon(t: np.ndarray, a: np.ndarray) -> np.ndarray:
    """Longitudinal jerk = d(a_lon)/dt."""
    return np.gradient(a, t)


def _interp_ideal(ideal: pd.DataFrame, t_query: np.ndarray, col: str) -> np.ndarray:
    """Linearly interpolate an ideal trajectory column onto a query time grid."""
    t_ideal = ideal["t_s"].to_numpy(dtype=float)
    vals = ideal[col].to_numpy(dtype=float)
    return np.interp(t_query, t_ideal, vals, left=vals[0], right=vals[-1])


# ---------------------------------------------------------------------------
# FR-10.1 — Jerk penalty
# ---------------------------------------------------------------------------


def jerk_penalty(
    fused: pd.DataFrame,
    ideal: pd.DataFrame,
    config_path: Path | None = None,
) -> float:
    """Penalise longitudinal jerk exceeding the ideal trajectory.

    Algorithm
    ---------
    1. Derive j_actual = d²v/dt² from fused ``v_mps``.
    2. Interpolate j_ideal from ideal trajectory onto fused time grid.
    3. excess = max(0, |j_actual| - |j_ideal|)
    4. mean_excess = integral(excess, dt) / trip_duration
    5. penalty = clip(mean_excess / jerk_sat_mps3, 0, 1)

    Parameters
    ----------
    fused:
        Fused filter output.  Required: t_s, v_mps.
    ideal:
        Ideal trajectory.  Required: t_s, j_lon_mps3.
    config_path:
        Path to scoring.yaml (default: ``config/scoring.yaml``).

    Returns
    -------
    Scalar in [0, 1].
    """
    cfg = _load_config(config_path)
    jerk_sat = float(cfg.get("saturation", {}).get("jerk_mean_mps3", 3.0))

    t = fused["t_s"].to_numpy(dtype=float)
    v = fused["v_mps"].to_numpy(dtype=float)

    trip_duration = float(t[-1] - t[0])
    if trip_duration < _MIN_TRIP_DURATION_S:
        return 0.0

    j_actual = _j_lon(t, _a_lon(t, v))
    j_ideal = _interp_ideal(ideal, t, "j_lon_mps3")

    excess = np.maximum(0.0, np.abs(j_actual) - np.abs(j_ideal))
    mean_excess = float(np.trapz(excess, t)) / trip_duration
    return float(np.clip(mean_excess / jerk_sat, 0.0, 1.0))


# ---------------------------------------------------------------------------
# FR-10.2 — Harsh braking penalty
# ---------------------------------------------------------------------------


def harsh_brake_penalty(
    fused: pd.DataFrame,
    config_path: Path | None = None,
) -> float:
    """Count harsh braking events; normalise to events per minute.

    An event is a contiguous interval where longitudinal deceleration
    exceeds the threshold.  Hysteresis via edge-detection (rising/falling
    transitions in the boolean indicator) prevents double-counting.

    Algorithm
    ---------
    1. a_lon = dv/dt from fused v_mps.
    2. is_braking = (a_lon < -threshold)
    3. Detect state transitions (0→1 = start, 1→0 = end).
    4. Count events whose duration >= min_duration_s.
    5. rate_epm = events / (trip_duration_s / 60)
    6. penalty = clip(rate_epm / harsh_brake_epm_sat, 0, 1)

    Parameters
    ----------
    fused:
        Fused filter output.  Required: t_s, v_mps.
    config_path:
        Path to scoring.yaml.

    Returns
    -------
    Scalar in [0, 1].
    """
    cfg = _load_config(config_path)
    thresh = float(cfg.get("thresholds", {}).get("harsh_brake_decel_mps2", 3.5))
    min_dur = float(cfg.get("thresholds", {}).get("harsh_brake_min_duration_s", 0.3))
    sat_epm = float(cfg.get("saturation", {}).get("harsh_brake_epm", 2.0))

    t = fused["t_s"].to_numpy(dtype=float)
    v = fused["v_mps"].to_numpy(dtype=float)

    trip_duration = float(t[-1] - t[0])
    if trip_duration < _MIN_TRIP_DURATION_S:
        return 0.0

    a = _a_lon(t, v)
    is_braking = (a < -thresh).astype(np.int8)

    # Pad with zeros so edge detection works at boundaries
    padded = np.concatenate([[0], is_braking, [0]])
    diff = np.diff(padded.astype(np.int16))

    start_indices = np.where(diff == 1)[0]  # 0-indexed into original t
    end_indices = np.where(diff == -1)[0]  # first index AFTER event

    events = 0
    for s, e in zip(start_indices, end_indices, strict=False):
        # Duration from t[s] to the last True sample t[e-1]
        duration = t[min(e, len(t) - 1)] - t[s]
        if duration >= min_dur:
            events += 1

    trip_minutes = trip_duration / 60.0
    rate_epm = float(events) / max(trip_minutes, 1e-9)
    return float(np.clip(rate_epm / sat_epm, 0.0, 1.0))


# ---------------------------------------------------------------------------
# FR-10.3 — Lateral acceleration penalty
# ---------------------------------------------------------------------------


def lat_accel_penalty(
    fused: pd.DataFrame,
    ideal: pd.DataFrame,
    config_path: Path | None = None,
) -> float:
    """Penalise lateral acceleration exceeding the ideal limit.

    Algorithm
    ---------
    1. a_lat_actual = v * psi_dot  (centripetal, body-frame consistent
       with the gravity decomposition done in FR-1.3 ingestion).
    2. Interpolate |a_lat_ideal| from ideal trajectory onto fused grid.
    3. excess = max(0, |a_lat_actual| - |a_lat_ideal|)
    4. mean_sq = integral(excess^2, dt) / trip_duration
    5. penalty = clip(mean_sq / lat_accel_sq_sat, 0, 1)

    Parameters
    ----------
    fused:
        Fused filter output.  Required: t_s, v_mps, psi_dot_rps.
    ideal:
        Ideal trajectory.  Required: t_s, a_lat_mps2.
    config_path:
        Path to scoring.yaml.

    Returns
    -------
    Scalar in [0, 1].
    """
    cfg = _load_config(config_path)
    sat = float(cfg.get("saturation", {}).get("lat_accel_sq_mean_m2ps4", 4.0))

    t = fused["t_s"].to_numpy(dtype=float)
    v = fused["v_mps"].to_numpy(dtype=float)
    psi_dot = fused["psi_dot_rps"].to_numpy(dtype=float)

    trip_duration = float(t[-1] - t[0])
    if trip_duration < _MIN_TRIP_DURATION_S:
        return 0.0

    a_lat_actual = _a_lat(v, psi_dot)
    a_lat_ideal = _interp_ideal(ideal, t, "a_lat_mps2")

    excess = np.maximum(0.0, np.abs(a_lat_actual) - np.abs(a_lat_ideal))
    mean_sq = float(np.trapz(excess**2, t)) / trip_duration
    return float(np.clip(mean_sq / sat, 0.0, 1.0))


# ---------------------------------------------------------------------------
# Spatial helper
# ---------------------------------------------------------------------------


def _cumulative_arc_length(px: np.ndarray, py: np.ndarray) -> np.ndarray:
    """Cumulative arc-length from successive ENU positions."""
    ds = np.sqrt(np.diff(px) ** 2 + np.diff(py) ** 2)
    return np.concatenate([[0.0], np.cumsum(ds)])


# ---------------------------------------------------------------------------
# FR-10.4 — Speed compliance penalty
# ---------------------------------------------------------------------------


def speed_penalty(
    fused: pd.DataFrame,
    reference_path: pd.DataFrame,
    config_path: Path | None = None,
) -> float:
    """Penalise driving above the posted speed limit.

    A tolerance band of ±speed_tolerance_mps (~2 mph) around the posted
    limit is free.  Excess above (limit + tolerance) is squared before
    integrating to make 5 mph over score worse than 1 mph over.

    Algorithm
    ---------
    1. Compute fused cumulative arc-length from px_m, py_m.
    2. Interpolate speed_limit_mps from reference_path onto fused arc-lengths.
    3. excess = max(0, v - (limit + tolerance))
    4. mean_sq = integral(excess^2, dt) / trip_duration
    5. penalty = clip(mean_sq / speed_sq_sat, 0, 1)

    Parameters
    ----------
    fused:
        Fused filter output.  Required: t_s, v_mps, px_m, py_m.
    reference_path:
        Reference path.  Required: s_m, speed_limit_mps.
    config_path:
        Path to scoring.yaml.

    Returns
    -------
    Scalar in [0, 1].
    """
    cfg = _load_config(config_path)
    tol = float(cfg.get("thresholds", {}).get("speed_tolerance_mps", 0.89))
    sat = float(cfg.get("saturation", {}).get("speed_sq_mean_mps2", 4.0))

    t = fused["t_s"].to_numpy(dtype=float)
    v = fused["v_mps"].to_numpy(dtype=float)
    px = fused["px_m"].to_numpy(dtype=float)
    py = fused["py_m"].to_numpy(dtype=float)

    trip_duration = float(t[-1] - t[0])
    if trip_duration < _MIN_TRIP_DURATION_S:
        return 0.0

    # Map fused positions to arc-length, then interpolate speed limits
    s_fused = _cumulative_arc_length(px, py)
    ref_s = reference_path["s_m"].to_numpy(dtype=float)
    ref_vl = reference_path["speed_limit_mps"].to_numpy(dtype=float)
    v_limit = np.interp(s_fused, ref_s, ref_vl, left=ref_vl[0], right=ref_vl[-1])

    excess = np.maximum(0.0, v - (v_limit + tol))
    mean_sq = float(np.trapz(excess**2, t)) / trip_duration
    return float(np.clip(mean_sq / sat, 0.0, 1.0))


# ---------------------------------------------------------------------------
# FR-10.5 — Route deviation penalty
# ---------------------------------------------------------------------------


def deviation_penalty(
    fused: pd.DataFrame,
    reference_path: pd.DataFrame,
    config_path: Path | None = None,
) -> float:
    """Penalise lateral deviation from the reference centerline.

    Within-lane driving (< deviation_inlane_m from centerline) scores ~0.
    Deviations beyond that are integrated and normalised.

    Algorithm
    ---------
    1. Compute fused cumulative arc-length.
    2. Interpolate reference px_m, py_m at fused arc-lengths.
    3. deviation = Euclidean distance between fused and interpolated ref.
    4. excess = max(0, deviation - inlane_m)
    5. mean_dev = integral(excess, dt) / trip_duration
    6. penalty = clip(mean_dev / deviation_mean_sat, 0, 1)

    Parameters
    ----------
    fused:
        Fused filter output.  Required: t_s, px_m, py_m.
    reference_path:
        Reference path.  Required: s_m, px_m, py_m.
    config_path:
        Path to scoring.yaml.

    Returns
    -------
    Scalar in [0, 1].
    """
    cfg = _load_config(config_path)
    inlane_m = float(cfg.get("thresholds", {}).get("deviation_inlane_m", 1.5))
    sat = float(cfg.get("saturation", {}).get("deviation_mean_m", 3.0))

    t = fused["t_s"].to_numpy(dtype=float)
    px_f = fused["px_m"].to_numpy(dtype=float)
    py_f = fused["py_m"].to_numpy(dtype=float)

    trip_duration = float(t[-1] - t[0])
    if trip_duration < _MIN_TRIP_DURATION_S:
        return 0.0

    s_fused = _cumulative_arc_length(px_f, py_f)
    ref_s = reference_path["s_m"].to_numpy(dtype=float)
    ref_px = reference_path["px_m"].to_numpy(dtype=float)
    ref_py = reference_path["py_m"].to_numpy(dtype=float)

    ref_px_at_s = np.interp(s_fused, ref_s, ref_px, left=ref_px[0], right=ref_px[-1])
    ref_py_at_s = np.interp(s_fused, ref_s, ref_py, left=ref_py[0], right=ref_py[-1])

    dev = np.sqrt((px_f - ref_px_at_s) ** 2 + (py_f - ref_py_at_s) ** 2)
    excess = np.maximum(0.0, dev - inlane_m)
    mean_dev = float(np.trapz(excess, t)) / trip_duration
    return float(np.clip(mean_dev / sat, 0.0, 1.0))


# ---------------------------------------------------------------------------
# FR-10.6 — Lane change penalty
# ---------------------------------------------------------------------------

_MIN_SUSTAINED_S: float = 3.0  # seconds of sustained lateral displacement


def lane_change_penalty(
    fused: pd.DataFrame,
    config_path: Path | None = None,
) -> float:
    """Count abrupt yaw excursions that produce sustained lateral displacement.

    Distinguishes true lane changes (yaw excursion + sustained offset > 2 m)
    from swerves (yaw excursion but vehicle returns to original trajectory).

    Algorithm
    ---------
    1. Compute rolling yaw change: yaw_change[i] = |ψ[i+win] − ψ[i]|.
    2. Detect contiguous events where yaw_change > yaw_delta_rad.
    3. For each event (start index s):
       a. Compute lateral displacement at s + win + sus_win relative to s,
          projected onto the direction perpendicular to the pre-event heading.
       b. If |lateral_displacement| >= lat_disp_m: count as lane change.
       c. Apply cooldown to avoid double-counting the same manoeuvre.
    4. rate_epm = lane_changes / (trip_duration / 60)
    5. penalty = clip(rate_epm / lane_change_epm_sat, 0, 1)

    Parameters
    ----------
    fused:
        Fused filter output.  Required: t_s, v_mps, psi_rad, px_m, py_m.
    config_path:
        Path to scoring.yaml.

    Returns
    -------
    Scalar in [0, 1].
    """
    cfg = _load_config(config_path)
    yaw_delta = float(cfg.get("thresholds", {}).get("lane_change_yaw_delta_rad", 0.15))
    yaw_window_s = float(cfg.get("thresholds", {}).get("lane_change_yaw_window_s", 2.0))
    lat_disp_m = float(cfg.get("thresholds", {}).get("lane_change_lat_disp_m", 2.0))
    sat_epm = float(cfg.get("saturation", {}).get("lane_change_epm", 2.0))

    t = fused["t_s"].to_numpy(dtype=float)
    psi = np.unwrap(fused["psi_rad"].to_numpy(dtype=float))
    px = fused["px_m"].to_numpy(dtype=float)
    py = fused["py_m"].to_numpy(dtype=float)

    trip_duration = float(t[-1] - t[0])
    if trip_duration < _MIN_TRIP_DURATION_S:
        return 0.0

    n = len(t)
    dt_mean = trip_duration / max(n - 1, 1)
    win = max(1, int(yaw_window_s / dt_mean))
    sus_win = max(1, int(_MIN_SUSTAINED_S / dt_mean))

    if win >= n:
        return 0.0

    # Rolling yaw change over forward window of length `win`
    yaw_change = np.abs(psi[win:] - psi[:-win])  # length n - win
    is_excursion = yaw_change > yaw_delta

    # Edge-detect contiguous excursion events
    padded = np.concatenate([[False], is_excursion, [False]])
    diffs = np.diff(padded.astype(np.int8))
    event_starts = np.where(diffs == 1)[0]
    event_ends = np.where(diffs == -1)[0]

    lane_changes = 0
    next_allowed = 0  # cooldown: skip events whose start index is < this

    for s, _e in zip(event_starts, event_ends, strict=False):
        if s < next_allowed:
            continue

        # Advance cooldown past the yaw window for THIS event so that
        # correlated events (e.g. the "unwind" of a swerve) are ignored.
        next_allowed = s + win + 1

        # Check lateral displacement from event start to (event_start + win + sus_win)
        check_idx = min(n - 1, s + win + sus_win)

        # Pre-event heading: perpendicular vector = (-sin ψ, cos ψ)
        psi_pre = psi[max(0, s - 1)]
        perp_x = -np.sin(psi_pre)
        perp_y = np.cos(psi_pre)

        dx = px[check_idx] - px[s]
        dy = py[check_idx] - py[s]
        lat = abs(dx * perp_x + dy * perp_y)

        if lat >= lat_disp_m:
            lane_changes += 1
            next_allowed = check_idx  # extend cooldown to full measurement window

    trip_minutes = trip_duration / 60.0
    rate_epm = float(lane_changes) / max(trip_minutes, 1e-9)
    return float(np.clip(rate_epm / sat_epm, 0.0, 1.0))
