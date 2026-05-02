"""Unit tests for FR-9.4 compute_ideal_speed_profile (T4.3).

Covers:
- curvature speed cap
- forward/backward kinematic passes
- curvature-limited regime (v < speed_limit in sharp turns)
- speed-limit regime (straight road)
- acceleration constraint satisfaction
- deceleration constraint satisfaction
- output schema / columns
- corner path (FRD acceptance criterion)
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parents[2] / "src"))

from ideal_driver.speed_profile import (
    _backward_decel_pass,
    _compute_derivatives,
    _curvature_speed_cap,
    _forward_accel_pass,
    compute_ideal_speed_profile,
)

# ---------------------------------------------------------------------------
# Default kinematic limits (matching config/ideal.yaml)
# ---------------------------------------------------------------------------

_A_LAT = 2.0  # m/s^2
_A_LON = 1.5  # m/s^2  (accel)
_A_DEC = 2.5  # m/s^2  (decel)
_J_MAX = 2.0  # m/s^3
_SPEED_LIMIT = 13.4  # m/s  (30 mph)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ref_path(
    n: int = 200,
    kappa: float | np.ndarray = 0.0,
    speed_limit: float = _SPEED_LIMIT,
    ds: float = 1.0,
) -> pd.DataFrame:
    """Synthetic reference path with uniform curvature and speed limit."""
    s = np.arange(n, dtype=float) * ds
    kappa_arr = np.full(n, kappa) if np.isscalar(kappa) else np.asarray(kappa, dtype=float)
    sl_arr = np.full(n, speed_limit, dtype=float)
    return pd.DataFrame(
        {
            "s_m": s,
            "px_m": s,
            "py_m": np.zeros(n),
            "heading_rad": np.zeros(n),
            "curvature_1pm": kappa_arr,
            "speed_limit_mps": sl_arr,
            "osm_way_id": np.ones(n, dtype=int),
        }
    )


# ---------------------------------------------------------------------------
# _curvature_speed_cap
# ---------------------------------------------------------------------------


class TestCurvatureSpeedCap:
    def test_straight_road_gives_speed_limit(self):
        kappa = np.zeros(10)
        sl = np.full(10, 13.4)
        v = _curvature_speed_cap(kappa, sl, a_lat_max=2.0)
        # kappa=0 -> v_curv = sqrt(2/1e-6) >> speed_limit; result = speed_limit
        np.testing.assert_allclose(v, 13.4)

    def test_sharp_turn_gives_curv_limit(self):
        # R=20m circle -> kappa=0.05 -> v_curv = sqrt(2/0.05) = sqrt(40) = 6.32
        kappa = np.full(5, 0.05)
        sl = np.full(5, 13.4)
        v = _curvature_speed_cap(kappa, sl, a_lat_max=2.0)
        expected = np.sqrt(2.0 / 0.05)  # ~6.32 m/s
        np.testing.assert_allclose(v, expected, rtol=1e-9)

    def test_result_never_exceeds_speed_limit(self):
        rng = np.random.default_rng(0)
        kappa = rng.uniform(0.001, 0.1, size=50)
        sl = np.full(50, 8.0)
        v = _curvature_speed_cap(kappa, sl, a_lat_max=2.0)
        assert np.all(v <= sl + 1e-9)

    def test_a_lat_max_scales_v_curv(self):
        kappa = np.array([0.02])
        sl = np.full(1, 50.0)
        v1 = _curvature_speed_cap(kappa, sl, a_lat_max=2.0)
        v2 = _curvature_speed_cap(kappa, sl, a_lat_max=8.0)
        # v_curv scales with sqrt(a_lat_max)
        np.testing.assert_allclose(v2 / v1, np.sqrt(8.0 / 2.0), rtol=1e-9)


# ---------------------------------------------------------------------------
# _forward_accel_pass
# ---------------------------------------------------------------------------


class TestForwardAccelPass:
    def test_monotone_increase_respects_accel(self):
        n = 100
        s = np.arange(n, dtype=float)
        v_raw = np.full(n, 15.0)
        v_raw[0] = 0.1  # start near zero
        a_max = 1.5
        v = _forward_accel_pass(v_raw, s, a_max)
        for i in range(1, n):
            ds = s[i] - s[i - 1]
            v_limit = np.sqrt(v[i - 1] ** 2 + 2.0 * a_max * ds)
            assert v[i] <= v_limit + 1e-9, f"accel violated at i={i}"

    def test_already_feasible_unchanged(self):
        s = np.arange(10, dtype=float)
        v = np.ones(10) * 5.0  # constant speed is always feasible
        v_out = _forward_accel_pass(v, s, a_max=1.5)
        np.testing.assert_allclose(v_out, v)

    def test_never_increases_speed(self):
        s = np.arange(20, dtype=float)
        v_raw = np.ones(20) * 10.0
        v_out = _forward_accel_pass(v_raw, s, a_max=1.5)
        assert np.all(v_out <= v_raw + 1e-9)


# ---------------------------------------------------------------------------
# _backward_decel_pass
# ---------------------------------------------------------------------------


class TestBackwardDecelPass:
    def test_monotone_decrease_respects_decel(self):
        n = 100
        s = np.arange(n, dtype=float)
        v_raw = np.full(n, 15.0)
        v_raw[-1] = 0.1  # end near zero
        a_dec = 2.5
        v = _backward_decel_pass(v_raw, s, a_dec)
        for i in range(n - 1):
            ds = s[i + 1] - s[i]
            v_limit = np.sqrt(v[i + 1] ** 2 + 2.0 * a_dec * ds)
            assert v[i] <= v_limit + 1e-9, f"decel violated at i={i}"

    def test_already_feasible_unchanged(self):
        s = np.arange(10, dtype=float)
        v = np.ones(10) * 5.0
        v_out = _backward_decel_pass(v, s, a_dec=2.5)
        np.testing.assert_allclose(v_out, v)

    def test_never_increases_speed(self):
        s = np.arange(20, dtype=float)
        v_raw = np.ones(20) * 10.0
        v_out = _backward_decel_pass(v_raw, s, a_dec=2.5)
        assert np.all(v_out <= v_raw + 1e-9)


# ---------------------------------------------------------------------------
# _compute_derivatives
# ---------------------------------------------------------------------------


class TestComputeDerivatives:
    def test_constant_speed_gives_zero_accel(self):
        s = np.arange(50, dtype=float)
        v = np.full(50, 10.0)
        a, j = _compute_derivatives(v, s)
        np.testing.assert_allclose(a, 0.0, atol=1e-10)
        np.testing.assert_allclose(j, 0.0, atol=1e-10)

    def test_linear_speed_increase_gives_correct_accel(self):
        """v = v0 + k*s -> dv/ds = k -> a = v*k."""
        s = np.arange(50, dtype=float)
        k = 0.1
        v = 5.0 + k * s
        a, _ = _compute_derivatives(v, s)
        expected_a = v * k
        # Interior points (endpoints use one-sided differences)
        np.testing.assert_allclose(a[2:-2], expected_a[2:-2], rtol=1e-5)


# ---------------------------------------------------------------------------
# compute_ideal_speed_profile — schema
# ---------------------------------------------------------------------------


class TestComputeIdealSpeedProfileSchema:
    def test_output_columns(self):
        ref = _make_ref_path(n=100)
        df = compute_ideal_speed_profile(ref)
        for col in ("s_m", "v_ideal_mps", "a_ideal_mps2", "j_ideal_mps3"):
            assert col in df.columns, f"missing column: {col}"

    def test_row_count_matches_input(self):
        ref = _make_ref_path(n=150)
        df = compute_ideal_speed_profile(ref)
        assert len(df) == 150

    def test_s_m_passed_through_unchanged(self):
        ref = _make_ref_path(n=100)
        df = compute_ideal_speed_profile(ref)
        np.testing.assert_array_equal(df["s_m"].to_numpy(), ref["s_m"].to_numpy())

    def test_no_nan_in_output(self):
        ref = _make_ref_path(n=100, kappa=0.01)
        df = compute_ideal_speed_profile(ref)
        assert not df.isnull().any().any()


# ---------------------------------------------------------------------------
# compute_ideal_speed_profile — physics correctness
# ---------------------------------------------------------------------------


class TestComputeIdealSpeedProfilePhysics:
    def test_straight_road_speed_approaches_limit(self):
        """Long straight road: interior v should approach speed limit."""
        ref = _make_ref_path(n=500, kappa=0.0, speed_limit=10.0)
        df = compute_ideal_speed_profile(ref, a_lon_max=3.0, a_lon_dec=5.0)
        # Well into the path the speed should be near the limit
        interior = df["v_ideal_mps"].to_numpy()[300:400]
        assert np.all(interior >= 9.5), f"speed too low in interior: {interior.min():.2f}"

    def test_v_never_exceeds_speed_limit(self):
        ref = _make_ref_path(n=200, kappa=0.005, speed_limit=_SPEED_LIMIT)
        df = compute_ideal_speed_profile(ref)
        assert np.all(df["v_ideal_mps"].to_numpy() <= _SPEED_LIMIT + 1e-6)

    def test_v_never_exceeds_curvature_limit(self):
        kappa = 0.02  # v_curv = sqrt(2/0.02) = 10.0 m/s
        ref = _make_ref_path(n=200, kappa=kappa, speed_limit=20.0)
        df = compute_ideal_speed_profile(ref, a_lat_max=2.0)
        v_curv_limit = np.sqrt(2.0 / kappa)
        assert np.all(df["v_ideal_mps"].to_numpy() <= v_curv_limit + 1e-6)

    def test_acceleration_constraint(self):
        """v[i]^2 - v[i-1]^2 <= 2*a_lon_max*ds (with smoothing disabled by high j_max)."""
        ref = _make_ref_path(n=300, kappa=0.0, speed_limit=_SPEED_LIMIT)
        a_lon = 1.5
        # High j_max disables smoothing so forward pass result dominates
        df = compute_ideal_speed_profile(ref, a_lon_max=a_lon, a_lon_dec=10.0, j_max=1000.0)
        v = df["v_ideal_mps"].to_numpy()
        s = df["s_m"].to_numpy()
        for i in range(1, len(v)):
            ds = s[i] - s[i - 1]
            assert v[i] ** 2 - v[i - 1] ** 2 <= 2.0 * a_lon * ds + 1e-6

    def test_deceleration_constraint(self):
        """v[i]^2 - v[i+1]^2 <= 2*a_lon_dec*ds (with smoothing disabled)."""
        ref = _make_ref_path(n=300, kappa=0.0, speed_limit=_SPEED_LIMIT)
        a_dec = 2.5
        df = compute_ideal_speed_profile(ref, a_lon_max=10.0, a_lon_dec=a_dec, j_max=1000.0)
        v = df["v_ideal_mps"].to_numpy()
        s = df["s_m"].to_numpy()
        for i in range(len(v) - 1):
            ds = s[i + 1] - s[i]
            assert v[i] ** 2 - v[i + 1] ** 2 <= 2.0 * a_dec * ds + 1e-6

    def test_corner_drops_below_speed_limit(self):
        """FRD acceptance criterion: curvature-limited regime is real.

        Path: straight -> corner (R=20m, kappa=0.05) -> straight.
        In the corner v_ideal must be < speed_limit.
        """
        n = 500
        kappa = np.zeros(n)
        kappa[200:300] = 0.05  # tight corner, v_curv = sqrt(2/0.05) ~6.3 m/s
        ref = _make_ref_path(n=n, kappa=kappa, speed_limit=_SPEED_LIMIT)
        df = compute_ideal_speed_profile(ref, a_lat_max=_A_LAT)
        v = df["v_ideal_mps"].to_numpy()
        # Deep in the corner, v should be well below the 13.4 m/s speed limit
        v_deep_corner = v[230:270]
        assert np.all(
            v_deep_corner < _SPEED_LIMIT - 2.0
        ), f"v in corner: max={v_deep_corner.max():.2f} not < {_SPEED_LIMIT - 2:.1f} m/s"

    def test_v_min_floor_applied(self):
        """v_ideal must never fall below v_min_mps."""
        ref = _make_ref_path(n=100, kappa=10.0, speed_limit=1.0)  # extreme kappa
        v_min = 0.5
        df = compute_ideal_speed_profile(ref, v_min_mps=v_min)
        assert np.all(df["v_ideal_mps"].to_numpy() >= v_min - 1e-9)
