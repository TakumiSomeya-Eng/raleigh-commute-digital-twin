"""Unit tests for FR-10.1 – FR-10.3 scoring components (T4.5).

Covers:
- jerk_penalty: calm drive < 0.05; harsh drive > 0.5; monotonic in harshness
- harsh_brake_penalty: calm drive = 0; harsh drive > 0.5; no double-counting
- lat_accel_penalty: straight drive ≈ 0; high-lat drive > 0.5
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parents[2] / "src"))

from scoring.components import harsh_brake_penalty, jerk_penalty, lat_accel_penalty

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_SCORING_YAML = Path(__file__).parents[2] / "config" / "scoring.yaml"


# ---------------------------------------------------------------------------
# Synthetic DataFrame builders
# ---------------------------------------------------------------------------

_DT = 0.01  # 100 Hz, matching fused parquet cadence
_TRIP_S = 60.0  # 60-second synthetic trip


def _make_fused(
    v: np.ndarray,
    psi_dot: np.ndarray | None = None,
    t0: float = 0.0,
    dt: float = _DT,
) -> pd.DataFrame:
    """Build a minimal fused DataFrame from v_mps (and optional psi_dot_rps)."""
    n = len(v)
    t = t0 + np.arange(n, dtype=float) * dt
    if psi_dot is None:
        psi_dot = np.zeros(n)
    return pd.DataFrame(
        {
            "t_s": t,
            "px_m": np.zeros(n),
            "py_m": np.zeros(n),
            "v_mps": v,
            "psi_rad": np.zeros(n),
            "psi_dot_rps": psi_dot,
            "cov_xx": np.ones(n) * 0.01,
            "cov_yy": np.ones(n) * 0.01,
            "cov_yaw": np.ones(n) * 0.001,
        }
    )


def _make_ideal(
    t: np.ndarray,
    j_lon: np.ndarray | None = None,
    a_lat: np.ndarray | None = None,
    v: np.ndarray | None = None,
) -> pd.DataFrame:
    """Build a minimal ideal trajectory DataFrame."""
    n = len(t)
    return pd.DataFrame(
        {
            "t_s": t,
            "px_m": np.zeros(n),
            "py_m": np.zeros(n),
            "v_mps": v if v is not None else np.full(n, 10.0),
            "a_lon_mps2": np.zeros(n),
            "a_lat_mps2": a_lat if a_lat is not None else np.zeros(n),
            "j_lon_mps3": j_lon if j_lon is not None else np.zeros(n),
            "psi_rad": np.zeros(n),
            "psi_dot_rps": np.zeros(n),
        }
    )


def _constant_speed_fused(v_mps: float = 10.0, trip_s: float = _TRIP_S) -> pd.DataFrame:
    n = int(trip_s / _DT)
    return _make_fused(np.full(n, v_mps))


def _constant_speed_ideal(v_mps: float = 10.0, trip_s: float = _TRIP_S) -> pd.DataFrame:
    n = int(trip_s / _DT)
    t = np.arange(n, dtype=float) * _DT
    return _make_ideal(t)


# ---------------------------------------------------------------------------
# jerk_penalty — T4.5 / FR-10.1
# ---------------------------------------------------------------------------


class TestJerkPenalty:
    def test_calm_drive_scores_low(self):
        """Constant speed -> zero jerk -> penalty near 0."""
        fused = _constant_speed_fused()
        ideal = _constant_speed_ideal()
        p = jerk_penalty(fused, ideal, config_path=_SCORING_YAML)
        assert p < 0.05, f"calm drive jerk penalty too high: {p:.4f}"

    def test_harsh_drive_scores_high(self):
        """Large sinusoidal jerk throughout trip -> penalty > 0.5."""
        n = int(_TRIP_S / _DT)
        t = np.arange(n, dtype=float) * _DT
        # Velocity with many large oscillations -> high j_lon
        freq = 2.0  # Hz
        amplitude = 5.0  # m/s oscillation
        v = 10.0 + amplitude * np.sin(2.0 * np.pi * freq * t)
        v = np.clip(v, 0.1, None)
        fused = _make_fused(v)
        ideal = _constant_speed_ideal()
        p = jerk_penalty(fused, ideal, config_path=_SCORING_YAML)
        assert p > 0.5, f"harsh drive jerk penalty too low: {p:.4f}"

    def test_penalty_in_unit_interval(self):
        """Penalty must always be in [0, 1]."""
        n = int(_TRIP_S / _DT)
        t = np.arange(n, dtype=float) * _DT
        # Extremely harsh: square-wave velocity
        v = 10.0 + 8.0 * np.sign(np.sin(2.0 * np.pi * 3.0 * t))
        v = np.clip(v, 0.1, None)
        fused = _make_fused(v)
        ideal = _constant_speed_ideal()
        p = jerk_penalty(fused, ideal, config_path=_SCORING_YAML)
        assert 0.0 <= p <= 1.0

    def test_monotonic_in_harshness(self):
        """Doubling oscillation amplitude should increase penalty (sub-saturation range)."""
        n = int(_TRIP_S / _DT)
        t = np.arange(n, dtype=float) * _DT
        ideal = _constant_speed_ideal()
        # Use low frequency (0.1 Hz) so jerk stays well below saturation for both amps.
        # j_max = amp*(2*pi*0.1)^2 ~ amp*0.39.  mean_excess ~ amp*0.25.
        # For amp=1: penalty ~ 0.08;  amp=2: penalty ~ 0.17  (both < 1.0)
        freq = 0.1

        def make_fused(amp: float) -> pd.DataFrame:
            v = np.clip(10.0 + amp * np.sin(2.0 * np.pi * freq * t), 0.1, None)
            return _make_fused(v)

        p1 = jerk_penalty(make_fused(1.0), ideal, config_path=_SCORING_YAML)
        p2 = jerk_penalty(make_fused(2.0), ideal, config_path=_SCORING_YAML)
        assert p2 > p1, f"penalty not monotonic: p(amp=2)={p2:.4f} <= p(amp=1)={p1:.4f}"

    def test_ideal_jerk_subtracts_penalty(self):
        """If actual jerk matches ideal jerk, excess is zero -> penalty = 0."""
        n = int(_TRIP_S / _DT)
        t = np.arange(n, dtype=float) * _DT
        freq = 1.0
        amp = 3.0
        v = 10.0 + amp * np.sin(2.0 * np.pi * freq * t)
        v = np.clip(v, 0.1, None)

        # Compute actual jerk from v so ideal matches exactly
        a_lon = np.gradient(v, t)
        j_lon = np.gradient(a_lon, t)

        fused = _make_fused(v)
        ideal = _make_ideal(t, j_lon=j_lon)
        p = jerk_penalty(fused, ideal, config_path=_SCORING_YAML)
        assert p < 0.05, f"matching ideal jerk still penalised: {p:.4f}"

    def test_degenerate_trip_returns_zero(self):
        """Very short trip (< 1 s) should return 0."""
        fused = _make_fused(np.full(5, 10.0), dt=0.1)
        ideal = _constant_speed_ideal()
        p = jerk_penalty(fused, ideal, config_path=_SCORING_YAML)
        assert p == 0.0


# ---------------------------------------------------------------------------
# harsh_brake_penalty — T4.5 / FR-10.2
# ---------------------------------------------------------------------------


class TestHarshBrakePenalty:
    def test_calm_drive_zero_events(self):
        """Constant speed -> no braking events -> penalty = 0."""
        fused = _constant_speed_fused()
        p, _ = harsh_brake_penalty(fused, config_path=_SCORING_YAML)
        assert p == 0.0, f"calm drive harsh brake penalty nonzero: {p}"

    def test_harsh_braking_detected(self):
        """Multiple sharp decelerations -> penalty > 0.5."""
        n = int(_TRIP_S / _DT)
        t = np.arange(n, dtype=float) * _DT
        v = np.full(n, 15.0, dtype=float)
        # Insert 3 braking events of 0.5 s each
        for event_start_s in [10.0, 25.0, 45.0]:
            i0 = int(event_start_s / _DT)
            i1 = int((event_start_s + 0.5) / _DT)
            decel_rate = 5.0  # m/s^2 > 3.5 threshold
            for i in range(i0, min(i1, n)):
                v[i] = max(0.0, v[i0] - decel_rate * (t[i] - t[i0]))
            v[i1:] = v[min(i1, n) - 1]

        fused = _make_fused(v)
        p, _ = harsh_brake_penalty(fused, config_path=_SCORING_YAML)
        assert p > 0.5, f"harsh braking penalty too low: {p:.4f}"

    def test_no_double_counting(self):
        """A single sustained braking event counts as exactly one event."""
        n = int(_TRIP_S / _DT)
        v = np.full(n, 15.0, dtype=float)
        # Single long braking event from t=10 to t=12
        i0 = int(10.0 / _DT)
        i1 = int(12.0 / _DT)
        for i in range(i0, min(i1, n)):
            v[i] = max(0.0, 15.0 - 5.0 * (i - i0) * _DT)
        v[i1:] = 0.0
        fused = _make_fused(v)
        # Should be exactly 1 event in 1 minute = 1 epm -> penalty = 1.0 / sat_epm
        import yaml

        with open(_SCORING_YAML, encoding="utf-8") as fh:
            cfg = yaml.safe_load(fh)
        sat_epm = float(cfg.get("saturation", {}).get("harsh_brake_epm", 2.0))
        p, _ = harsh_brake_penalty(fused, config_path=_SCORING_YAML)
        expected = min(1.0, 1.0 / ((_TRIP_S / 60.0) * sat_epm))
        assert abs(p - expected) < 0.05, f"event count off: p={p:.4f}, expected~{expected:.4f}"

    def test_short_decel_below_threshold_not_counted(self):
        """Deceleration below duration threshold is not counted."""
        n = int(_TRIP_S / _DT)
        v = np.full(n, 15.0, dtype=float)
        # Very brief decel (0.1 s < 0.3 s min_duration_s)
        i0 = int(20.0 / _DT)
        i1 = int(20.1 / _DT)
        for i in range(i0, min(i1, n)):
            v[i] = max(0.0, 15.0 - 5.0 * (i - i0) * _DT)
        fused = _make_fused(v)
        p, _ = harsh_brake_penalty(fused, config_path=_SCORING_YAML)
        assert p == 0.0, f"sub-threshold event counted: {p:.4f}"

    def test_penalty_clipped_at_one(self):
        """Extreme braking (many events) must not exceed 1.0."""
        n = int(_TRIP_S / _DT)
        t = np.arange(n, dtype=float) * _DT
        # Brake hard every 5 seconds
        v = np.full(n, 15.0, dtype=float)
        for es in range(0, int(_TRIP_S), 5):
            i0 = int(es / _DT)
            i1 = int((es + 1.0) / _DT)
            for i in range(i0, min(i1, n)):
                v[i] = max(0.0, 15.0 - 6.0 * (t[i] - t[i0]))
            if i1 < n:
                v[i1:] = 15.0
        fused = _make_fused(v)
        p, _ = harsh_brake_penalty(fused, config_path=_SCORING_YAML)
        assert p <= 1.0

    def test_degenerate_trip_returns_zero(self):
        fused = _make_fused(np.full(5, 10.0), dt=0.1)
        p, _ = harsh_brake_penalty(fused, config_path=_SCORING_YAML)
        assert p == 0.0


# ---------------------------------------------------------------------------
# lat_accel_penalty — T4.5 / FR-10.3
# ---------------------------------------------------------------------------


class TestLatAccelPenalty:
    def test_straight_road_zero_lat_accel(self):
        """Zero psi_dot -> zero lateral accel -> penalty near 0."""
        fused = _constant_speed_fused()
        ideal = _constant_speed_ideal()
        p = lat_accel_penalty(fused, ideal, config_path=_SCORING_YAML)
        assert p < 0.01, f"straight road lat accel penalty nonzero: {p:.6f}"

    def test_high_lateral_accel_scores_high(self):
        """Large v * psi_dot sustained throughout trip -> penalty > 0.5."""
        n = int(_TRIP_S / _DT)
        v = np.full(n, 10.0)
        # psi_dot = 0.5 rad/s -> a_lat = 10 * 0.5 = 5 m/s^2
        psi_dot = np.full(n, 0.5)
        fused = _make_fused(v, psi_dot=psi_dot)
        ideal = _constant_speed_ideal()  # a_lat_ideal = 0
        p = lat_accel_penalty(fused, ideal, config_path=_SCORING_YAML)
        assert p > 0.5, f"high lat accel penalty too low: {p:.4f}"

    def test_penalty_in_unit_interval(self):
        """Penalty must always be in [0, 1]."""
        n = int(_TRIP_S / _DT)
        v = np.full(n, 20.0)
        psi_dot = np.full(n, 1.0)  # 20 m/s^2 -> extreme
        fused = _make_fused(v, psi_dot=psi_dot)
        ideal = _constant_speed_ideal()
        p = lat_accel_penalty(fused, ideal, config_path=_SCORING_YAML)
        assert 0.0 <= p <= 1.0

    def test_ideal_alat_subtracts_penalty(self):
        """If actual a_lat matches ideal a_lat, excess is zero -> penalty = 0."""
        n = int(_TRIP_S / _DT)
        v = np.full(n, 10.0)
        psi_dot = np.full(n, 0.3)  # a_lat = 3 m/s^2
        fused = _make_fused(v, psi_dot=psi_dot)

        t = np.arange(n, dtype=float) * _DT
        a_lat_arr = v * psi_dot
        ideal = _make_ideal(t, a_lat=a_lat_arr)
        p = lat_accel_penalty(fused, ideal, config_path=_SCORING_YAML)
        assert p < 0.01, f"matching ideal a_lat still penalised: {p:.6f}"

    def test_monotonic_in_lat_accel(self):
        """Higher psi_dot (with fixed ideal=0) should give higher penalty."""
        n = int(_TRIP_S / _DT)
        v = np.full(n, 10.0)
        ideal = _constant_speed_ideal()

        p1 = lat_accel_penalty(_make_fused(v, psi_dot=np.full(n, 0.1)), ideal, _SCORING_YAML)
        p2 = lat_accel_penalty(_make_fused(v, psi_dot=np.full(n, 0.3)), ideal, _SCORING_YAML)
        assert p2 > p1, f"penalty not monotonic: p(0.3)={p2:.4f} <= p(0.1)={p1:.4f}"

    def test_degenerate_trip_returns_zero(self):
        fused = _make_fused(np.full(5, 10.0), dt=0.1)
        ideal = _constant_speed_ideal()
        p = lat_accel_penalty(fused, ideal, config_path=_SCORING_YAML)
        assert p == 0.0
