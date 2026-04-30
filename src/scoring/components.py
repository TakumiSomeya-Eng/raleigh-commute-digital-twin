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
