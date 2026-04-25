"""Unit tests for FR-1.4 Parquet schemas (src/data_engine/schemas.py).

Three test groups:
  1. Valid construction for every schema
  2. Validator rejection (heading_rad / psi_rad outside [-pi, pi])
  3. Round-trip: pydantic -> pyarrow Table -> dict -> pydantic
"""

from __future__ import annotations

import math
from typing import Any

import pandas as pd
import pyarrow as pa
import pytest
from data_engine.schemas import (
    Aligned100Hz,
    GroundTruth,
    IdealSpeed,
    IdealTrajectory,
    ReferencePath,
    RouteMatched,
)
from pydantic import ValidationError

# ---------------------------------------------------------------------------
# Row fixtures
# ---------------------------------------------------------------------------


def _aligned_row() -> dict[str, Any]:
    return {
        "t_s": 0.0,
        "time_ns": 1_000_000_000,
        "px_m": 1.0,
        "py_m": 2.0,
        "lat_wgs84": 35.773,
        "lon_wgs84": -78.610,
        "horizontal_accuracy_m": 3.0,
        "speed_accuracy_mps": 0.1,
        "bearing_accuracy_deg": 5.0,
        "gps_speed_mps": 10.0,
        "gps_bearing_deg": 90.0,
        "ax_mps2": 0.1,
        "ay_mps2": 0.0,
        "az_mps2": 9.8,
        "gx_rps": 0.0,
        "gy_rps": 0.0,
        "gz_rps": 0.01,
        "grav_x": 0.0,
        "grav_y": 0.0,
        "grav_z": 9.81,
        "quat_w": 1.0,
        "quat_x": 0.0,
        "quat_y": 0.0,
        "quat_z": 0.0,
        "mag_x_uT": 20.0,
        "mag_y_uT": 5.0,
        "mag_z_uT": -40.0,
        "gps_interpolated": False,
    }


def _ground_truth_row() -> dict[str, Any]:
    return {
        "t_s": 0.0,
        "px_m": 1.0,
        "py_m": 2.0,
        "v_mps": 10.0,
        "psi_rad": 0.5,
        "psi_dot_rps": 0.01,
    }


def _route_matched_row() -> dict[str, Any]:
    return {
        "t_s": 0.0,
        "osm_way_id": 123,
        "snapped_px_m": 1.0,
        "snapped_py_m": 2.0,
        "distance_from_road_m": 0.5,
        "match_confidence": 0.95,
    }


def _reference_path_row() -> dict[str, Any]:
    return {
        "s_m": 0.0,
        "px_m": 1.0,
        "py_m": 2.0,
        "heading_rad": 0.5,
        "curvature_1pm": 0.01,
        "speed_limit_mps": 13.4,
        "osm_way_id": 456,
    }


def _ideal_speed_row() -> dict[str, Any]:
    return {"s_m": 0.0, "v_ideal_mps": 10.0, "a_ideal_mps2": 0.5, "j_ideal_mps3": 0.1}


def _ideal_trajectory_row() -> dict[str, Any]:
    return {
        "t_s": 0.0,
        "px_m": 1.0,
        "py_m": 2.0,
        "v_mps": 10.0,
        "a_lon_mps2": 0.5,
        "a_lat_mps2": 0.1,
        "j_lon_mps3": 0.05,
        "psi_rad": 0.5,
        "psi_dot_rps": 0.01,
    }


# ---------------------------------------------------------------------------
# 1. Valid construction
# ---------------------------------------------------------------------------


def test_aligned_100hz_valid() -> None:
    m = Aligned100Hz(**_aligned_row())
    assert m.t_s == 0.0
    assert m.gps_interpolated is False


def test_ground_truth_valid() -> None:
    m = GroundTruth(**_ground_truth_row())
    assert m.psi_rad == pytest.approx(0.5)


def test_route_matched_valid() -> None:
    m = RouteMatched(**_route_matched_row())
    assert m.match_confidence == pytest.approx(0.95)


def test_route_matched_optional_none() -> None:
    m = RouteMatched(t_s=0.0, match_confidence=0.0)
    assert m.osm_way_id is None
    assert m.snapped_px_m is None
    assert m.distance_from_road_m is None


def test_reference_path_valid() -> None:
    ReferencePath(**_reference_path_row())


def test_ideal_speed_valid() -> None:
    IdealSpeed(**_ideal_speed_row())


def test_ideal_trajectory_valid() -> None:
    IdealTrajectory(**_ideal_trajectory_row())


# ---------------------------------------------------------------------------
# 2. Validator rejection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_angle", [math.pi + 0.001, -math.pi - 0.001, 10.0])
def test_ground_truth_rejects_bad_psi(bad_angle: float) -> None:
    with pytest.raises(ValidationError):
        GroundTruth(**{**_ground_truth_row(), "psi_rad": bad_angle})


@pytest.mark.parametrize("bad_angle", [math.pi + 0.001, -math.pi - 0.001])
def test_reference_path_rejects_bad_heading(bad_angle: float) -> None:
    with pytest.raises(ValidationError):
        ReferencePath(**{**_reference_path_row(), "heading_rad": bad_angle})


@pytest.mark.parametrize("bad_angle", [math.pi + 0.001, -math.pi - 0.001])
def test_ideal_trajectory_rejects_bad_psi(bad_angle: float) -> None:
    with pytest.raises(ValidationError):
        IdealTrajectory(**{**_ideal_trajectory_row(), "psi_rad": bad_angle})


# ---------------------------------------------------------------------------
# 3. Round-trip: pydantic -> pyarrow Table -> pydantic
# ---------------------------------------------------------------------------


def _roundtrip(model_cls: type, row: dict[str, Any]) -> None:
    original = model_cls(**row)
    df = pd.DataFrame([original.model_dump()])
    table = pa.Table.from_pandas(df, preserve_index=False)
    recovered_row = {k: v[0] for k, v in table.to_pydict().items()}
    recovered = model_cls(**recovered_row)
    assert recovered.model_dump() == original.model_dump()


def test_aligned_100hz_roundtrip() -> None:
    _roundtrip(Aligned100Hz, _aligned_row())


def test_ground_truth_roundtrip() -> None:
    _roundtrip(GroundTruth, _ground_truth_row())


def test_route_matched_roundtrip() -> None:
    _roundtrip(RouteMatched, _route_matched_row())


def test_reference_path_roundtrip() -> None:
    _roundtrip(ReferencePath, _reference_path_row())


def test_ideal_speed_roundtrip() -> None:
    _roundtrip(IdealSpeed, _ideal_speed_row())


def test_ideal_trajectory_roundtrip() -> None:
    _roundtrip(IdealTrajectory, _ideal_trajectory_row())
