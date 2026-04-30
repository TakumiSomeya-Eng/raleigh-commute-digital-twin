"""Unit tests for FR-9.5 quintic polynomial synthesis (T4.4).

Covers:
- _quintic_coeffs: BC satisfaction at t=0 and t=T
- _eval_quintic / _eval_quintic_derivs: correct values
- C2 continuity at segment boundaries
- synthesize_trajectory: schema, position on centerline,
  monotone time, C2 continuity, trip-time sanity (FRD criterion)
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parents[2] / "src"))

from ideal_driver.quintic import (
    _eval_quintic,
    _eval_quintic_derivs,
    _find_waypoints,
    _quintic_coeffs,
    synthesize_trajectory,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_A_LAT = 2.0
_A_LON = 1.5
_A_DEC = 2.5
_SPEED_LIMIT = 13.4


def _make_straight_inputs(
    n: int = 300,
    speed_mps: float = 10.0,
    ds: float = 1.0,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Straight-road reference path + constant ideal speed."""
    s = np.arange(n, dtype=float) * ds
    ref = pd.DataFrame(
        {
            "s_m": s,
            "px_m": s,
            "py_m": np.zeros(n),
            "heading_rad": np.zeros(n),
            "curvature_1pm": np.zeros(n),
            "speed_limit_mps": np.full(n, _SPEED_LIMIT),
            "osm_way_id": np.ones(n, dtype=int),
        }
    )
    spd = pd.DataFrame(
        {
            "s_m": s,
            "v_ideal_mps": np.full(n, speed_mps),
            "a_ideal_mps2": np.zeros(n),
            "j_ideal_mps3": np.zeros(n),
        }
    )
    return ref, spd


def _make_corner_inputs(
    n: int = 500,
    radius_m: float = 50.0,
    corner_start: int = 150,
    corner_end: int = 350,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Path with a central circular turn; speed profile from speed_profile module."""
    from ideal_driver.speed_profile import compute_ideal_speed_profile

    s = np.arange(n, dtype=float)
    kappa = np.zeros(n)
    kappa[corner_start:corner_end] = 1.0 / radius_m
    v_lim = np.full(n, _SPEED_LIMIT)
    ref = pd.DataFrame(
        {
            "s_m": s,
            "px_m": s.copy(),
            "py_m": np.zeros(n),
            "heading_rad": np.zeros(n),
            "curvature_1pm": kappa,
            "speed_limit_mps": v_lim,
            "osm_way_id": np.ones(n, dtype=int),
        }
    )
    spd = compute_ideal_speed_profile(ref, a_lat_max=_A_LAT, a_lon_max=_A_LON, a_lon_dec=_A_DEC)
    return ref, spd


# ---------------------------------------------------------------------------
# _quintic_coeffs — boundary-condition verification
# ---------------------------------------------------------------------------


class TestQuinticCoeffs:
    def _check_bcs(self, T, v0, a0, L, v1, a1, tol=1e-9):
        c = _quintic_coeffs(T, v0, a0, L, v1, a1)
        t0 = np.array([0.0])
        tT = np.array([T])
        s0 = _eval_quintic(c, t0)
        sT = _eval_quintic(c, tT)
        v0c, a0c, _ = _eval_quintic_derivs(c, t0)
        v1c, a1c, _ = _eval_quintic_derivs(c, tT)
        np.testing.assert_allclose(s0, [0.0], atol=tol)
        np.testing.assert_allclose(sT, [L], atol=tol)
        np.testing.assert_allclose(v0c, [v0], atol=tol)
        np.testing.assert_allclose(v1c, [v1], atol=tol)
        np.testing.assert_allclose(a0c, [a0], atol=tol)
        np.testing.assert_allclose(a1c, [a1], atol=tol)

    def test_rest_to_rest(self):
        self._check_bcs(T=5.0, v0=0.0, a0=0.0, L=10.0, v1=0.0, a1=0.0)

    def test_constant_speed(self):
        # v0=v1=V, a0=a1=0 -> should give s(t)=V*t exactly
        V = 8.0
        T = 10.0
        L = V * T
        self._check_bcs(T=T, v0=V, a0=0.0, L=L, v1=V, a1=0.0)

    def test_acceleration_segment(self):
        self._check_bcs(T=8.0, v0=2.0, a0=0.5, L=60.0, v1=10.0, a1=0.0)

    def test_deceleration_segment(self):
        self._check_bcs(T=6.0, v0=12.0, a0=0.0, L=50.0, v1=5.0, a1=-1.0)

    def test_short_segment(self):
        self._check_bcs(T=1.0, v0=5.0, a0=0.0, L=5.5, v1=6.0, a1=0.0, tol=1e-7)


# ---------------------------------------------------------------------------
# _quintic_coeffs — constant-speed analytically known
# ---------------------------------------------------------------------------


class TestQuinticConstantSpeed:
    def test_coeffs_reduce_to_linear(self):
        """v=const, a=0 everywhere -> s(t) = V*t -> only c1 is nonzero."""
        V = 7.0
        T = 4.0
        c = _quintic_coeffs(T, v0=V, a0=0.0, L=V * T, v1=V, a1=0.0)
        np.testing.assert_allclose(c[0], 0.0, atol=1e-9)
        np.testing.assert_allclose(c[1], V, atol=1e-9)
        np.testing.assert_allclose(c[2:], 0.0, atol=1e-9)

    def test_velocity_constant_everywhere(self):
        V = 7.0
        T = 4.0
        c = _quintic_coeffs(T, v0=V, a0=0.0, L=V * T, v1=V, a1=0.0)
        t = np.linspace(0, T, 50)
        v, a, j = _eval_quintic_derivs(c, t)
        np.testing.assert_allclose(v, V, atol=1e-9)
        np.testing.assert_allclose(a, 0.0, atol=1e-9)
        np.testing.assert_allclose(j, 0.0, atol=1e-9)


# ---------------------------------------------------------------------------
# _find_waypoints
# ---------------------------------------------------------------------------


class TestFindWaypoints:
    def test_straight_gives_only_start_end(self):
        kappa = np.zeros(100)
        wps = _find_waypoints(kappa)
        assert wps[0] == 0
        assert wps[-1] == 99

    def test_single_peak_detected(self):
        kappa = np.zeros(100)
        kappa[50] = 0.1
        wps = _find_waypoints(kappa, min_spacing_m=5.0, kappa_threshold=0.01)
        assert 50 in wps

    def test_below_threshold_not_detected(self):
        kappa = np.zeros(100)
        kappa[50] = 0.001  # below default threshold
        wps = _find_waypoints(kappa, kappa_threshold=0.005)
        # Only start/end
        assert list(wps) == [0, 99]


# ---------------------------------------------------------------------------
# synthesize_trajectory — schema
# ---------------------------------------------------------------------------


class TestSynthesizeTrajectorySchema:
    def test_output_columns(self):
        ref, spd = _make_straight_inputs(n=200)
        df = synthesize_trajectory(ref, spd)
        for col in (
            "t_s",
            "px_m",
            "py_m",
            "v_mps",
            "a_lon_mps2",
            "a_lat_mps2",
            "j_lon_mps3",
            "psi_rad",
            "psi_dot_rps",
        ):
            assert col in df.columns, f"missing column: {col}"

    def test_no_nan_in_output(self):
        ref, spd = _make_straight_inputs(n=200)
        df = synthesize_trajectory(ref, spd)
        assert not df.isnull().any().any()

    def test_time_is_monotone(self):
        ref, spd = _make_straight_inputs(n=200)
        df = synthesize_trajectory(ref, spd)
        diffs = np.diff(df["t_s"].to_numpy())
        assert np.all(diffs > 0), "t_s must be strictly increasing"

    def test_v_mps_nonnegative(self):
        ref, spd = _make_straight_inputs(n=200)
        df = synthesize_trajectory(ref, spd)
        assert np.all(df["v_mps"].to_numpy() >= 0.0)


# ---------------------------------------------------------------------------
# synthesize_trajectory — physics and geometry
# ---------------------------------------------------------------------------


class TestSynthesizeTrajectoryPhysics:
    def test_straight_position_on_centerline(self):
        """For straight road, py should stay near zero (on centerline)."""
        ref, spd = _make_straight_inputs(n=300)
        df = synthesize_trajectory(ref, spd)
        np.testing.assert_allclose(df["py_m"].to_numpy(), 0.0, atol=0.5)

    def test_straight_constant_speed_approx(self):
        """Constant speed input -> output v_mps should be near that speed."""
        V = 10.0
        ref, spd = _make_straight_inputs(n=300, speed_mps=V)
        df = synthesize_trajectory(ref, spd)
        # Allow for edge effects at start/end
        v_interior = df["v_mps"].to_numpy()[5:-5]
        np.testing.assert_allclose(v_interior, V, rtol=0.05)

    def test_straight_zero_curvature_gives_zero_alat(self):
        """Zero curvature -> a_lat = v^2 * kappa = 0."""
        ref, spd = _make_straight_inputs(n=200)
        df = synthesize_trajectory(ref, spd)
        np.testing.assert_allclose(df["a_lat_mps2"].to_numpy(), 0.0, atol=1e-9)

    def test_c2_continuity_at_segment_boundaries(self):
        """At curvature-peak waypoints, v and a_lon must be continuous."""
        ref, spd = _make_corner_inputs(n=400)
        df = synthesize_trajectory(ref, spd, dt_out=0.01)
        v = df["v_mps"].to_numpy()
        a = df["a_lon_mps2"].to_numpy()
        # No large discontinuities anywhere (max jump < 0.5 m/s or 1 m/s^2)
        dv = np.abs(np.diff(v))
        da = np.abs(np.diff(a))
        assert np.all(dv < 0.5), f"velocity jump too large: {dv.max():.3f}"
        assert np.all(da < 2.0), f"accel jump too large: {da.max():.3f}"

    def test_trip_time_within_15_percent_of_fastest_legal(self):
        """FRD: total trip time <= 1.15 * (L / v_mean_limit)."""
        n = 300
        V = _SPEED_LIMIT
        ref, spd = _make_straight_inputs(n=n, speed_mps=V * 0.95)
        df = synthesize_trajectory(ref, spd)
        T_actual = float(df["t_s"].iloc[-1])
        L = float(ref["s_m"].iloc[-1])
        T_fastest_legal = L / V
        assert (
            T_actual <= T_fastest_legal * 1.15
        ), f"T_actual={T_actual:.1f} > 1.15 * T_legal={T_fastest_legal * 1.15:.1f}"

    def test_mismatched_lengths_raises(self):
        ref, spd = _make_straight_inputs(n=100)
        spd_short = spd.iloc[:50].reset_index(drop=True)
        with pytest.raises(ValueError, match="aligned"):
            synthesize_trajectory(ref, spd_short)
