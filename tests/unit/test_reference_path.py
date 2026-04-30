"""Unit tests for FR-9.3 build_reference_path (T4.2).

All tests use synthetic DataFrames; no live Valhalla or Overpass required.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parents[2] / "src"))

from ideal_driver.reference_path import (
    _fill_way_ids,
    _remove_consecutive_duplicates,
    build_reference_path,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DEFAULT_SPD = 13.4


def _make_speed_lookup(
    default_mps: float = _DEFAULT_SPD, corridors: dict | None = None
) -> MagicMock:
    """Return a mock SpeedLimitLookup that returns *default_mps* for everything."""
    sl = MagicMock()
    sl._default_mps = default_mps
    overrides = corridors or {}
    sl.lookup.side_effect = lambda way_ids: {w: overrides.get(w, default_mps) for w in way_ids}
    return sl


def _straight_route(n: int = 100, dx: float = 5.0, way_id: int = 1) -> pd.DataFrame:
    """n matched points along x-axis, spaced dx metres apart."""
    px = np.arange(n, dtype=float) * dx
    py = np.zeros(n)
    return pd.DataFrame(
        {
            "t_s": np.arange(n, dtype=float) * 0.2 + 1.0e9,
            "snapped_px_m": px,
            "snapped_py_m": py,
            "osm_way_id": np.full(n, way_id, dtype=float),
            "match_confidence": np.ones(n),
            "distance_from_road_m": np.ones(n) * 1.0,
        }
    )


def _circle_route(
    radius: float = 50.0,
    n: int = 200,
    way_id: int = 42,
) -> pd.DataFrame:
    """Points on a circle of *radius* metres; curvature should be 1/radius."""
    theta = np.linspace(0, 2 * np.pi, n, endpoint=False)
    px = radius * np.cos(theta)
    py = radius * np.sin(theta)
    return pd.DataFrame(
        {
            "t_s": np.arange(n, dtype=float) + 1.0e9,
            "snapped_px_m": px,
            "snapped_py_m": py,
            "osm_way_id": np.full(n, way_id, dtype=float),
            "match_confidence": np.ones(n),
            "distance_from_road_m": np.ones(n),
        }
    )


# ---------------------------------------------------------------------------
# _remove_consecutive_duplicates
# ---------------------------------------------------------------------------


class TestRemoveConsecutiveDuplicates:
    def test_no_duplicates_unchanged(self):
        px = np.array([0.0, 1.0, 2.0])
        py = np.zeros(3)
        wids = np.array([1, 1, 1])
        rpx, rpy, rw = _remove_consecutive_duplicates(px, py, wids)
        assert len(rpx) == 3

    def test_duplicate_removed(self):
        px = np.array([0.0, 0.0, 1.0])
        py = np.zeros(3)
        wids = np.array([1, 1, 1])
        rpx, rpy, rw = _remove_consecutive_duplicates(px, py, wids)
        assert len(rpx) == 2

    def test_all_same_gives_one_point(self):
        px = np.array([5.0, 5.0, 5.0])
        py = np.zeros(3)
        wids = np.array([7, 7, 7])
        rpx, rpy, rw = _remove_consecutive_duplicates(px, py, wids)
        assert len(rpx) == 1


# ---------------------------------------------------------------------------
# _fill_way_ids
# ---------------------------------------------------------------------------


class TestFillWayIds:
    def test_no_nones(self):
        arr = np.array([1, 2, 3])
        result = _fill_way_ids(arr)
        np.testing.assert_array_equal(result, [1, 2, 3])

    def test_forward_fill(self):
        arr = np.array([1, None, None])
        result = _fill_way_ids(arr)
        np.testing.assert_array_equal(result, [1, 1, 1])

    def test_backward_fill_leading_none(self):
        arr = np.array([None, None, 5])
        result = _fill_way_ids(arr)
        np.testing.assert_array_equal(result, [5, 5, 5])

    def test_all_none_gives_zeros(self):
        arr = np.array([None, None])
        result = _fill_way_ids(arr)
        np.testing.assert_array_equal(result, [0, 0])


# ---------------------------------------------------------------------------
# build_reference_path — schema / output shape
# ---------------------------------------------------------------------------


class TestBuildReferencePathSchema:
    def test_output_columns(self):
        route = _straight_route(n=50)
        sl = _make_speed_lookup()
        df = build_reference_path(route, sl)
        for col in (
            "s_m",
            "px_m",
            "py_m",
            "heading_rad",
            "curvature_1pm",
            "speed_limit_mps",
            "osm_way_id",
        ):
            assert col in df.columns, f"missing column: {col}"

    def test_s_m_starts_at_zero(self):
        route = _straight_route(n=50)
        sl = _make_speed_lookup()
        df = build_reference_path(route, sl)
        assert df["s_m"].iloc[0] == pytest.approx(0.0)

    def test_s_m_is_monotone(self):
        route = _straight_route(n=50)
        sl = _make_speed_lookup()
        df = build_reference_path(route, sl)
        diffs = np.diff(df["s_m"].to_numpy())
        assert (diffs > 0).all()

    def test_resampling_step_1m(self):
        """Default step = 1 m; consecutive s values differ by 1."""
        route = _straight_route(n=100, dx=5.0)
        sl = _make_speed_lookup()
        df = build_reference_path(route, sl, resample_step_m=1.0)
        diffs = np.diff(df["s_m"].to_numpy())
        np.testing.assert_allclose(diffs, 1.0, atol=1e-10)

    def test_custom_resample_step(self):
        route = _straight_route(n=100, dx=5.0)
        sl = _make_speed_lookup()
        df2 = build_reference_path(route, sl, resample_step_m=2.0)
        diffs = np.diff(df2["s_m"].to_numpy())
        np.testing.assert_allclose(diffs, 2.0, atol=1e-10)

    def test_heading_in_minus_pi_to_pi(self):
        route = _straight_route(n=50)
        sl = _make_speed_lookup()
        df = build_reference_path(route, sl)
        assert (df["heading_rad"] >= -np.pi).all()
        assert (df["heading_rad"] <= np.pi).all()

    def test_speed_limits_from_lookup(self):
        route = _straight_route(n=50, way_id=7)
        sl = _make_speed_lookup(default_mps=_DEFAULT_SPD, corridors={7: 22.352})
        df = build_reference_path(route, sl)
        np.testing.assert_allclose(df["speed_limit_mps"].to_numpy(), 22.352, rtol=1e-6)

    def test_way_id_preserved(self):
        route = _straight_route(n=50, way_id=99)
        sl = _make_speed_lookup()
        df = build_reference_path(route, sl)
        assert (df["osm_way_id"] == 99).all()


# ---------------------------------------------------------------------------
# build_reference_path — geometry correctness
# ---------------------------------------------------------------------------


class TestBuildReferencePathGeometry:
    def test_straight_path_heading_is_east(self):
        """East-going straight road -> heading ≈ 0."""
        route = _straight_route(n=200, dx=5.0)
        sl = _make_speed_lookup()
        df = build_reference_path(route, sl)
        # Interior points should have heading close to 0 (east)
        interior = df["heading_rad"].to_numpy()[5:-5]
        np.testing.assert_allclose(interior, 0.0, atol=0.05)

    def test_straight_path_curvature_near_zero(self):
        route = _straight_route(n=200, dx=5.0)
        sl = _make_speed_lookup()
        df = build_reference_path(route, sl)
        interior = df["curvature_1pm"].to_numpy()[5:-5]
        np.testing.assert_allclose(interior, 0.0, atol=1e-4)

    def test_circle_curvature_approx_1_over_r(self):
        """Points on circle of radius r -> |kappa| ≈ 1/r after smoothing."""
        radius = 50.0
        route = _circle_route(radius=radius, n=400)
        sl = _make_speed_lookup()
        df = build_reference_path(route, sl, curvature_smooth_window_m=3.0)
        # Use interior 50% of points to avoid endpoint artefacts
        n = len(df)
        mid = df["curvature_1pm"].to_numpy()[n // 4 : 3 * n // 4]
        expected = 1.0 / radius
        np.testing.assert_allclose(np.abs(mid), expected, rtol=0.15)

    def test_positions_close_to_input(self):
        """Resampled positions should stay near the original snapped points."""
        route = _straight_route(n=50, dx=5.0)
        sl = _make_speed_lookup()
        df = build_reference_path(route, sl)
        # All resampled x should lie in [0, 49*5]
        assert df["px_m"].min() >= -0.1
        assert df["px_m"].max() <= 50 * 5.0 + 0.1
        # All y should be near zero
        np.testing.assert_allclose(df["py_m"].to_numpy(), 0.0, atol=0.1)


# ---------------------------------------------------------------------------
# build_reference_path — error handling
# ---------------------------------------------------------------------------


class TestBuildReferencePathErrors:
    def test_too_few_points_raises(self):
        route = _straight_route(n=1)
        sl = _make_speed_lookup()
        with pytest.raises(ValueError, match="Insufficient"):
            build_reference_path(route, sl)

    def test_too_short_raises(self):
        """Path of 0.5 m can't be resampled at 1 m step."""
        route = pd.DataFrame(
            {
                "t_s": [1.0e9, 1.0e9 + 0.1],
                "snapped_px_m": [0.0, 0.5],
                "snapped_py_m": [0.0, 0.0],
                "osm_way_id": [1, 1],
                "match_confidence": [1.0, 1.0],
                "distance_from_road_m": [1.0, 1.0],
            }
        )
        sl = _make_speed_lookup()
        with pytest.raises(ValueError):
            build_reference_path(route, sl, resample_step_m=1.0)

    def test_zero_confidence_points_filtered(self):
        """Rows with match_confidence == 0 must be excluded from the path."""
        route = _straight_route(n=50)
        route.loc[5:10, "match_confidence"] = 0.0
        sl = _make_speed_lookup()
        # Should not raise; unconfident rows simply excluded
        df = build_reference_path(route, sl)
        assert len(df) > 0

    def test_none_way_ids_filled(self):
        """None osm_way_id rows get forward-filled; output has no None."""
        route = _straight_route(n=50)
        route.loc[10:15, "osm_way_id"] = None
        sl = _make_speed_lookup()
        df = build_reference_path(route, sl)
        assert df["osm_way_id"].notna().all()
