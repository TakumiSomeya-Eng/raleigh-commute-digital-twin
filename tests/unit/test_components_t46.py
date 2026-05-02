"""Unit tests for FR-10.4 – FR-10.6 scoring components (T4.6).

Covers:
- speed_penalty:      at-limit = 0; within tolerance = 0; over = positive;
                      quadratic: 5 mph/60 s > 1 mph/300 s; clamped at 1
- deviation_penalty:  on-centerline ≈ 0; within lane ≈ 0; off = positive;
                      monotonic in deviation magnitude
- lane_change_penalty: straight = 0; lane change detected; swerve not counted;
                      multiple changes counted; clamped at 1
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parents[2] / "src"))

from scoring.components import deviation_penalty, lane_change_penalty, speed_penalty

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_SCORING_YAML = Path(__file__).parents[2] / "config" / "scoring.yaml"

# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

_DT = 0.01  # 100 Hz
_SPEED_LIMIT = 13.4  # m/s  (30 mph)

# ---------------------------------------------------------------------------
# Synthetic builders
# ---------------------------------------------------------------------------


def _make_fused(
    t: np.ndarray,
    v: np.ndarray,
    px: np.ndarray,
    py: np.ndarray,
    psi: np.ndarray | None = None,
    psi_dot: np.ndarray | None = None,
) -> pd.DataFrame:
    n = len(t)
    return pd.DataFrame(
        {
            "t_s": t,
            "v_mps": v,
            "px_m": px,
            "py_m": py,
            "psi_rad": psi if psi is not None else np.zeros(n),
            "psi_dot_rps": psi_dot if psi_dot is not None else np.zeros(n),
            "cov_xx": np.ones(n) * 0.01,
            "cov_yy": np.ones(n) * 0.01,
            "cov_yaw": np.ones(n) * 0.001,
        }
    )


def _straight_fused(
    trip_s: float = 60.0,
    v_mps: float = _SPEED_LIMIT,
    lateral_offset_m: float = 0.0,
) -> pd.DataFrame:
    """Straight fused drive along the x-axis with a constant lateral offset."""
    n = int(trip_s / _DT)
    t = np.arange(n, dtype=float) * _DT
    v = np.full(n, v_mps)
    px = v_mps * t
    py = np.full(n, lateral_offset_m)
    return _make_fused(t, v, px, py)


def _straight_reference_path(
    total_m: float = 1000.0,
    speed_limit: float = _SPEED_LIMIT,
) -> pd.DataFrame:
    """Reference path along the x-axis."""
    s = np.arange(0, total_m + 1, 1.0)
    return pd.DataFrame(
        {
            "s_m": s,
            "px_m": s,
            "py_m": np.zeros(len(s)),
            "heading_rad": np.zeros(len(s)),
            "curvature_1pm": np.zeros(len(s)),
            "speed_limit_mps": np.full(len(s), speed_limit),
            "osm_way_id": np.ones(len(s), dtype=int),
        }
    )


def _make_lane_change_fused(
    trip_s: float = 60.0,
    v_mps: float = 15.0,
    event_t: float = 10.0,
    event_dur_s: float = 0.5,
    yaw_delta: float = 0.3,
) -> pd.DataFrame:
    """Synthetic fused trajectory with one lane-change manoeuvre.

    Phase 1 [0, event_t]:          straight, psi=0
    Phase 2 [event_t, event_t+dur]: linear yaw from 0 to yaw_delta
    Phase 3 [event_t+dur, trip_s]:  straight at new heading yaw_delta
    """
    n = int(trip_s / _DT)
    t = np.arange(n, dtype=float) * _DT
    psi = np.zeros(n)
    i0 = int(event_t / _DT)
    i1 = int((event_t + event_dur_s) / _DT)
    i1 = min(i1, n)
    psi[i0:i1] = yaw_delta * np.linspace(0.0, 1.0, i1 - i0)
    psi[i1:] = yaw_delta

    vx = v_mps * np.cos(psi)
    vy = v_mps * np.sin(psi)
    px = np.cumsum(vx) * _DT
    py = np.cumsum(vy) * _DT
    psi_dot = np.gradient(psi, t)

    return _make_fused(t, np.full(n, v_mps), px, py, psi, psi_dot)


def _make_swerve_fused(
    trip_s: float = 60.0,
    v_mps: float = 15.0,
    event_t: float = 10.0,
    swerve_amp: float = 0.25,
    swerve_half_dur_s: float = 0.5,
) -> pd.DataFrame:
    """Synthetic fused trajectory with one swerve (yaw then returns).

    The vehicle turns left by swerve_amp rad, then right by the same amount,
    ending with the original heading.  Net lateral displacement is near zero.
    """
    n = int(trip_s / _DT)
    t = np.arange(n, dtype=float) * _DT
    psi = np.zeros(n)
    i0 = int(event_t / _DT)
    i1 = int((event_t + swerve_half_dur_s) / _DT)
    i2 = int((event_t + 2.0 * swerve_half_dur_s) / _DT)
    i1 = min(i1, n)
    i2 = min(i2, n)

    # Left turn
    psi[i0:i1] = swerve_amp * np.linspace(0.0, 1.0, i1 - i0)
    # Right turn (returns to 0)
    psi[i1:i2] = swerve_amp * np.linspace(1.0, 0.0, i2 - i1)
    # Straight after (psi = 0, already zero)

    vx = v_mps * np.cos(psi)
    vy = v_mps * np.sin(psi)
    px = np.cumsum(vx) * _DT
    py = np.cumsum(vy) * _DT
    psi_dot = np.gradient(psi, t)

    return _make_fused(t, np.full(n, v_mps), px, py, psi, psi_dot)


def _degenerate_fused() -> pd.DataFrame:
    """Fused with < 1 s trip duration (degenerate guard)."""
    n = 5
    t = np.arange(n, dtype=float) * 0.1
    v = np.full(n, 10.0)
    px = v * t
    return _make_fused(t, v, px, np.zeros(n))


# ===========================================================================
# speed_penalty — FR-10.4
# ===========================================================================


class TestSpeedPenalty:
    def _ref(self, vl: float = _SPEED_LIMIT) -> pd.DataFrame:
        return _straight_reference_path(speed_limit=vl)

    def test_at_speed_limit_zero_penalty(self):
        """Driving exactly at speed limit -> penalty = 0."""
        fused = _straight_fused(v_mps=_SPEED_LIMIT)
        p = speed_penalty(fused, self._ref(), config_path=_SCORING_YAML)
        assert p == 0.0, f"at-limit penalty nonzero: {p}"

    def test_within_tolerance_zero_penalty(self):
        """v = limit + 0.5 m/s < tolerance (0.89 m/s) -> penalty = 0."""
        fused = _straight_fused(v_mps=_SPEED_LIMIT + 0.5)
        p = speed_penalty(fused, self._ref(), config_path=_SCORING_YAML)
        assert p == 0.0, f"within-tolerance penalty nonzero: {p}"

    def test_over_limit_positive_penalty(self):
        """v = limit + 2 m/s (well over tolerance) -> penalty > 0."""
        fused = _straight_fused(v_mps=_SPEED_LIMIT + 2.0)
        p = speed_penalty(fused, self._ref(), config_path=_SCORING_YAML)
        assert p > 0.0, f"over-limit penalty is zero: {p}"

    def test_five_mph_over_60s_worse_than_one_mph_over_300s(self):
        """FRD acceptance: 5 mph over 60 s > 1 mph over 300 s.

        1 mph (~0.45 m/s) is within the ±2 mph tolerance band -> penalty = 0.
        5 mph (~2.24 m/s) is above it -> penalty > 0.
        """
        ref = self._ref()
        # 5 mph over for 60 s
        p_harsh = speed_penalty(_straight_fused(60.0, _SPEED_LIMIT + 2.24), ref, _SCORING_YAML)
        # 1 mph over for 300 s (within tolerance band)
        p_mild = speed_penalty(_straight_fused(300.0, _SPEED_LIMIT + 0.45), ref, _SCORING_YAML)
        assert (
            p_harsh > p_mild
        ), f"5-mph-over ({p_harsh:.4f}) not worse than 1-mph-over ({p_mild:.4f})"

    def test_quadratic_scaling(self):
        """2× excess speed should give ~4× penalty (sub-saturation)."""
        ref = self._ref()
        # Two excess speeds above tolerance: 0.5 m/s and 1.0 m/s above tolerance
        tol = 0.89
        p1 = speed_penalty(_straight_fused(60.0, _SPEED_LIMIT + tol + 0.5), ref, _SCORING_YAML)
        p2 = speed_penalty(_straight_fused(60.0, _SPEED_LIMIT + tol + 1.0), ref, _SCORING_YAML)
        # Ratio should be close to 4 (quadratic); allow 20% deviation
        ratio = p2 / max(p1, 1e-9)
        assert 2.5 <= ratio <= 6.0, f"quadratic ratio off: {ratio:.2f} (p1={p1:.4f}, p2={p2:.4f})"

    def test_penalty_clipped_at_one(self):
        """Extreme over-speed -> penalty never exceeds 1."""
        fused = _straight_fused(v_mps=_SPEED_LIMIT + 20.0, trip_s=300.0)
        p = speed_penalty(fused, self._ref(), config_path=_SCORING_YAML)
        assert p <= 1.0

    def test_degenerate_trip_returns_zero(self):
        fused = _degenerate_fused()
        p = speed_penalty(fused, self._ref(), config_path=_SCORING_YAML)
        assert p == 0.0


# ===========================================================================
# deviation_penalty — FR-10.5
# ===========================================================================


class TestDeviationPenalty:
    def _ref(self) -> pd.DataFrame:
        return _straight_reference_path()

    def test_on_centerline_zero_penalty(self):
        """Fused exactly on reference centerline -> penalty = 0."""
        fused = _straight_fused(lateral_offset_m=0.0)
        p = deviation_penalty(fused, self._ref(), config_path=_SCORING_YAML)
        assert p < 1e-6, f"on-centerline deviation penalty nonzero: {p}"

    def test_within_inlane_threshold_zero_penalty(self):
        """1 m deviation < 1.5 m threshold -> excess = 0 -> penalty = 0."""
        fused = _straight_fused(lateral_offset_m=1.0)
        p = deviation_penalty(fused, self._ref(), config_path=_SCORING_YAML)
        assert p < 1e-6, f"within-threshold deviation penalty nonzero: {p}"

    def test_large_deviation_positive_penalty(self):
        """3 m deviation > 1.5 m threshold -> penalty > 0."""
        fused = _straight_fused(lateral_offset_m=3.0)
        p = deviation_penalty(fused, self._ref(), config_path=_SCORING_YAML)
        assert p > 0.0, f"3-m deviation penalty is zero: {p}"

    def test_monotonic_in_deviation(self):
        """Larger lateral offset -> larger penalty."""
        ref = self._ref()
        p1 = deviation_penalty(_straight_fused(lateral_offset_m=2.0), ref, _SCORING_YAML)
        p2 = deviation_penalty(_straight_fused(lateral_offset_m=4.0), ref, _SCORING_YAML)
        assert p2 > p1, f"penalty not monotonic: p(4m)={p2:.4f} <= p(2m)={p1:.4f}"

    def test_half_lane_drift_noticeable(self):
        """FRD: 3 m drift (half a lane) should be noticeable (penalty > 0.01)."""
        fused = _straight_fused(lateral_offset_m=3.0)
        p = deviation_penalty(fused, self._ref(), config_path=_SCORING_YAML)
        assert p > 0.01, f"3-m drift barely noticeable: {p:.4f}"

    def test_penalty_clipped_at_one(self):
        """Extreme deviation -> penalty never exceeds 1."""
        fused = _straight_fused(lateral_offset_m=20.0, trip_s=300.0)
        p = deviation_penalty(fused, self._ref(), config_path=_SCORING_YAML)
        assert p <= 1.0

    def test_degenerate_trip_returns_zero(self):
        fused = _degenerate_fused()
        p = deviation_penalty(fused, self._ref(), config_path=_SCORING_YAML)
        assert p == 0.0


# ===========================================================================
# lane_change_penalty — FR-10.6
# ===========================================================================


class TestLaneChangePenalty:
    def test_straight_drive_zero_penalty(self):
        """No yaw events on straight road -> penalty = 0."""
        n = int(60.0 / _DT)
        t = np.arange(n, dtype=float) * _DT
        px = 15.0 * t
        fused = _make_fused(t, np.full(n, 15.0), px, np.zeros(n))
        p = lane_change_penalty(fused, config_path=_SCORING_YAML)
        assert p == 0.0, f"straight drive lane-change penalty nonzero: {p}"

    def test_lane_change_detected(self):
        """Single highway lane change -> penalty > 0."""
        fused = _make_lane_change_fused(trip_s=60.0, yaw_delta=0.30, event_t=10.0, event_dur_s=0.5)
        p = lane_change_penalty(fused, config_path=_SCORING_YAML)
        assert p > 0.0, f"lane change not detected: {p}"

    def test_swerve_not_counted(self):
        """Yaw excursion that returns to original heading/position -> penalty = 0."""
        fused = _make_swerve_fused(
            trip_s=60.0, swerve_amp=0.25, swerve_half_dur_s=0.5, event_t=10.0
        )
        p = lane_change_penalty(fused, config_path=_SCORING_YAML)
        assert p == 0.0, f"swerve incorrectly counted: {p:.4f}"

    def test_two_lane_changes_counted(self):
        """Two lane changes in one trip -> higher penalty than one."""
        fused_one = _make_lane_change_fused(trip_s=120.0, event_t=15.0)

        # Build two-change trajectory manually
        n = int(120.0 / _DT)
        t = np.arange(n, dtype=float) * _DT
        psi = np.zeros(n)
        # Event 1: t=15s
        i0 = int(15.0 / _DT)
        i1 = int(15.5 / _DT)
        psi[i0:i1] = 0.3 * np.linspace(0.0, 1.0, i1 - i0)
        psi[i1:] = 0.3
        # Event 2: t=70s (additional turn)
        j0 = int(70.0 / _DT)
        j1 = int(70.5 / _DT)
        psi[j0:j1] = 0.3 + 0.3 * np.linspace(0.0, 1.0, j1 - j0)
        psi[j1:] = 0.6
        vx = 15.0 * np.cos(psi)
        vy = 15.0 * np.sin(psi)
        px = np.cumsum(vx) * _DT
        py = np.cumsum(vy) * _DT
        fused_two_lc = _make_fused(t, np.full(n, 15.0), px, py, psi, np.gradient(psi, t))

        p_one = lane_change_penalty(fused_one, config_path=_SCORING_YAML)
        p_two = lane_change_penalty(fused_two_lc, config_path=_SCORING_YAML)
        assert p_two > p_one, f"two changes ({p_two:.4f}) not worse than one ({p_one:.4f})"

    def test_small_wiggle_not_counted(self):
        """Small yaw oscillation (< threshold) does not trigger penalty."""
        n = int(60.0 / _DT)
        t = np.arange(n, dtype=float) * _DT
        # Psi oscillates ±0.05 rad (< 0.15 threshold)
        psi = 0.05 * np.sin(2.0 * np.pi * 0.5 * t)
        vx = 15.0 * np.cos(psi)
        vy = 15.0 * np.sin(psi)
        px = np.cumsum(vx) * _DT
        py = np.cumsum(vy) * _DT
        fused = _make_fused(t, np.full(n, 15.0), px, py, psi, np.gradient(psi, t))
        p = lane_change_penalty(fused, config_path=_SCORING_YAML)
        assert p == 0.0, f"small wiggle incorrectly counted: {p:.4f}"

    def test_penalty_clipped_at_one(self):
        """Many rapid lane changes -> penalty never exceeds 1."""
        n = int(120.0 / _DT)
        t = np.arange(n, dtype=float) * _DT
        psi = np.zeros(n)
        # Every 10 s: add 0.3 rad
        cumulative = 0.0
        for es in range(10, 110, 10):
            i0 = int(es / _DT)
            i1 = int((es + 0.5) / _DT)
            i1 = min(i1, n)
            psi[i0:i1] = cumulative + 0.3 * np.linspace(0.0, 1.0, i1 - i0)
            cumulative += 0.3
            psi[i1:] = cumulative
        vx = 15.0 * np.cos(psi)
        vy = 15.0 * np.sin(psi)
        px = np.cumsum(vx) * _DT
        py = np.cumsum(vy) * _DT
        fused = _make_fused(t, np.full(n, 15.0), px, py, psi, np.gradient(psi, t))
        p = lane_change_penalty(fused, config_path=_SCORING_YAML)
        assert p <= 1.0

    def test_degenerate_trip_returns_zero(self):
        fused = _degenerate_fused()
        p = lane_change_penalty(fused, config_path=_SCORING_YAML)
        assert p == 0.0
