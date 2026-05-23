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
    Required for T4.6 only: px_m, py_m, speed_limit_mps.

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
from scipy.signal import butter, filtfilt
from scipy.spatial import cKDTree

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


def _lpf_accel(a: np.ndarray, t: np.ndarray, cutoff_hz: float = 3.0, order: int = 2) -> np.ndarray:
    """Zero-phase Butterworth low-pass filter for acceleration.

    Suppresses road-vibration spikes (> cutoff_hz) without introducing phase
    delay.  Falls back to the raw array when the signal is too short to filter.
    """
    if len(a) < 2:
        return a
    dt_med = float(np.median(np.diff(t))) if len(t) > 1 else 0.01
    fs = 1.0 / max(dt_med, 1e-9)
    nyq = 0.5 * fs
    if cutoff_hz >= nyq:
        return a
    lpf_b, lpf_a = butter(order, cutoff_hz / nyq, btype="low")
    padlen = 3 * max(len(lpf_b), len(lpf_a))
    if len(a) <= padlen:
        return a
    return filtfilt(lpf_b, lpf_a, a)


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

    a_lon = _lpf_accel(_a_lon(t, v), t)
    j_actual = _lpf_accel(_j_lon(t, a_lon), t, cutoff_hz=1.0)
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
) -> tuple[float, list[dict]]:
    """Count harsh braking events; normalise to events per minute.

    An event is a contiguous interval where the LPF-smoothed longitudinal
    deceleration exceeds the threshold.  A 3 Hz Butterworth low-pass filter
    removes road-vibration spikes before detection.  A 1 s cooldown after
    each event end prevents double-counting when the signal briefly
    re-crosses the threshold.

    Algorithm
    ---------
    1. a_raw = dv/dt from fused v_mps.
    2. a     = LPF(a_raw, cutoff=3 Hz, order=2)  -- suppress road vibration.
    3. is_braking = (a < -threshold)
    4. Detect state transitions (0→1 = start, 1→0 = end).
    5. Count events whose duration >= min_duration_s and whose start is
       >= 1 s after the previous event's end (cooldown).
    6. rate_epm = events / (trip_duration_s / 60)
    7. penalty  = clip(rate_epm / harsh_brake_epm_sat, 0, 1)

    Parameters
    ----------
    fused:
        Fused filter output.  Required: t_s, v_mps.
        Optional (for event positions): px_m, py_m.
    config_path:
        Path to scoring.yaml.

    Returns
    -------
    (penalty, events) where penalty ∈ [0, 1] and events is a list of dicts
    with keys t_s, decel_mps2, and (when px_m/py_m are present) px_m, py_m.
    """
    cfg = _load_config(config_path)
    thresh = float(cfg.get("thresholds", {}).get("harsh_brake_decel_mps2", 3.5))
    min_dur = float(cfg.get("thresholds", {}).get("harsh_brake_min_duration_s", 0.3))
    sat_epm = float(cfg.get("saturation", {}).get("harsh_brake_epm", 2.0))
    cooldown_s: float = 1.0

    t = fused["t_s"].to_numpy(dtype=float)
    v = fused["v_mps"].to_numpy(dtype=float)

    trip_duration = float(t[-1] - t[0])
    if trip_duration < _MIN_TRIP_DURATION_S:
        return 0.0, []

    has_pos = "px_m" in fused.columns and "py_m" in fused.columns
    px = fused["px_m"].to_numpy(dtype=float) if has_pos else None
    py = fused["py_m"].to_numpy(dtype=float) if has_pos else None

    a_raw = _a_lon(t, v)
    a = _lpf_accel(a_raw, t)
    is_braking = (a < -thresh).astype(np.int8)

    # Pad with zeros so edge detection works at boundaries
    padded = np.concatenate([[0], is_braking, [0]])
    diff = np.diff(padded.astype(np.int16))

    start_indices = np.where(diff == 1)[0]  # 0-indexed into original t
    end_indices = np.where(diff == -1)[0]  # first index AFTER event

    event_count = 0
    event_list: list[dict] = []
    last_event_end_t = -float("inf")

    for s, e in zip(start_indices, end_indices, strict=False):
        end_idx = min(e, len(t) - 1)
        duration = t[end_idx] - t[s]
        if duration < min_dur:
            continue
        if t[s] - last_event_end_t < cooldown_s:
            continue
        event_count += 1
        last_event_end_t = t[end_idx]
        peak_idx = int(s + np.argmin(a[s : end_idx + 1]))
        ev: dict = {
            "t_s": float(t[s]),
            "decel_mps2": float(-a[peak_idx]),
        }
        if px is not None:
            ev["px_m"] = float(px[s])
            ev["py_m"] = float(py[s])  # type: ignore[index]
        event_list.append(ev)

    trip_minutes = trip_duration / 60.0
    rate_epm = float(event_count) / max(trip_minutes, 1e-9)
    return float(np.clip(rate_epm / sat_epm, 0.0, 1.0)), event_list


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


def _nearest_ref_idx(
    px_fused: np.ndarray,
    py_fused: np.ndarray,
    ref_px: np.ndarray,
    ref_py: np.ndarray,
) -> np.ndarray:
    """Return the index of the nearest reference-path point for each fused position.

    Uses a KD-tree so the complexity is O(n log m) rather than O(n × m).
    This avoids the arc-length coordinate mismatch that occurs when the fused
    trajectory drifts relative to the OSM-matched centerline.
    """
    tree = cKDTree(np.column_stack([ref_px, ref_py]))
    _, idx = tree.query(np.column_stack([px_fused, py_fused]), k=1)
    return idx.astype(int)


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
    1. For each fused position (px_m, py_m), find the nearest point on the
       reference path using a KD-tree (2D nearest-neighbour projection).
    2. Read speed_limit_mps at that nearest reference point.
    3. excess = max(0, v - (limit + tolerance))
    4. mean_sq = integral(excess^2, dt) / trip_duration
    5. penalty = clip(mean_sq / speed_sq_sat, 0, 1)

    Parameters
    ----------
    fused:
        Fused filter output.  Required: t_s, v_mps, px_m, py_m.
    reference_path:
        Reference path.  Required: px_m, py_m, speed_limit_mps.
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

    # Nearest-point projection: avoid arc-length coordinate mismatch
    ref_px = reference_path["px_m"].to_numpy(dtype=float)
    ref_py = reference_path["py_m"].to_numpy(dtype=float)
    ref_vl = reference_path["speed_limit_mps"].to_numpy(dtype=float)
    v_limit = ref_vl[_nearest_ref_idx(px, py, ref_px, ref_py)]

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
    1. For each fused position (px_m, py_m), find the nearest point on the
       reference path using a KD-tree (2D nearest-neighbour projection).
    2. deviation = Euclidean distance from fused position to that nearest point.
    3. excess = max(0, deviation - inlane_m)
    4. mean_dev = integral(excess, dt) / trip_duration
    5. penalty = clip(mean_dev / deviation_mean_sat, 0, 1)

    Parameters
    ----------
    fused:
        Fused filter output.  Required: t_s, px_m, py_m.
    reference_path:
        Reference path.  Required: px_m, py_m.
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

    # Nearest-point projection: avoid arc-length coordinate mismatch
    ref_px = reference_path["px_m"].to_numpy(dtype=float)
    ref_py = reference_path["py_m"].to_numpy(dtype=float)
    idx = _nearest_ref_idx(px_f, py_f, ref_px, ref_py)

    dev = np.sqrt((px_f - ref_px[idx]) ** 2 + (py_f - ref_py[idx]) ** 2)
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
    reference_path: pd.DataFrame | None = None,
) -> float:
    """Count abrupt yaw excursions that produce sustained lateral displacement.

    Distinguishes true lane changes (yaw excursion + sustained offset > 2 m)
    from swerves (yaw excursion but vehicle returns to original trajectory).

    Algorithm
    ---------
    1. Compute rolling yaw change: yaw_change[i] = |ψ[i+win] − ψ[i]|.
    2. Detect contiguous events where yaw_change > yaw_delta_rad.
    3. For each event (start index s):
       a. When reference_path is provided: measure the CHANGE in road-relative
          lateral offset (distance to reference centerline) between s and
          s + win + sus_win.  Road curves preserve this distance; lane changes
          shift it by ~one lane width.
       b. When reference_path is None (legacy): project displacement onto the
          perpendicular of the pre-event heading (vehicle-centric coordinates).
          This triggers falsely on highway interchanges and curves.
       c. If |lateral_change| >= lat_disp_m: count as lane change.
       d. Apply cooldown to avoid double-counting the same manoeuvre.
    4. rate_epm = lane_changes / (trip_duration / 60)
    5. penalty = clip(rate_epm / lane_change_epm_sat, 0, 1)

    Parameters
    ----------
    fused:
        Fused filter output.  Required: t_s, v_mps, psi_rad, px_m, py_m.
    config_path:
        Path to scoring.yaml.
    reference_path:
        Reference path DataFrame with px_m, py_m columns.  When supplied,
        road-relative lateral offset is used instead of heading-based
        displacement, eliminating false positives from curves and interchanges.

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

    # Pre-compute road-relative offset for all positions when reference_path available.
    road_offset: np.ndarray | None = None
    if reference_path is not None:
        ref_px = reference_path["px_m"].to_numpy(dtype=float)
        ref_py = reference_path["py_m"].to_numpy(dtype=float)
        idx = _nearest_ref_idx(px, py, ref_px, ref_py)
        road_offset = np.sqrt((px - ref_px[idx]) ** 2 + (py - ref_py[idx]) ** 2)

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

        if road_offset is not None:
            # Road-relative: change in distance to centerline.
            # Road curves keep this ~constant; lane changes shift it by ~lane width.
            # Skip events where either endpoint is off the reference road (e.g. at
            # trip start / end before joining the reference route, or after a genuine
            # road departure).  Changes are only meaningful while on the reference road.
            _ON_ROAD_M = 10.0
            if road_offset[s] > _ON_ROAD_M or road_offset[check_idx] > _ON_ROAD_M:
                continue
            lat = abs(road_offset[check_idx] - road_offset[s])
        else:
            # Legacy heading-based (triggers falsely on highway curves).
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
