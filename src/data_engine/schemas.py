"""Parquet schema definitions -- single source of truth for all inter-stage tabular data.

Every Parquet file written by this project has a corresponding pydantic model here.
All readers and writers must round-trip through these models.

See: TRD sec.1.2 -- sec.1.7
"""

from __future__ import annotations

import math

from pydantic import BaseModel, field_validator


class Aligned100Hz(BaseModel):
    """FR-1.4: Primary 100 Hz aligned sensor data. One row per 10 ms tick."""

    # Time
    t_s: float
    time_ns: int

    # Position (local ENU, meters from anchor)
    px_m: float
    py_m: float

    # Raw GPS (WGS-84 preserved for audit)
    lat_wgs84: float
    lon_wgs84: float

    # GPS quality metadata
    horizontal_accuracy_m: float
    speed_accuracy_mps: float
    bearing_accuracy_deg: float
    gps_speed_mps: float
    gps_bearing_deg: float  # 0..360, CW from north

    # IMU (body frame: +x forward, +y left, +z up)
    ax_mps2: float
    ay_mps2: float
    az_mps2: float
    gx_rps: float
    gy_rps: float
    gz_rps: float

    # Gravity vector (body frame) -- used for gravity removal in fusion
    grav_x: float
    grav_y: float
    grav_z: float

    # Phone orientation quaternion (sanity reference only)
    quat_w: float
    quat_x: float
    quat_y: float
    quat_z: float

    # Magnetometer (body frame, micro-Tesla) -- uT suffix is a unit abbreviation, not camelCase
    mag_x_uT: float  # noqa: N815
    mag_y_uT: float  # noqa: N815
    mag_z_uT: float  # noqa: N815

    # Provenance flag
    gps_interpolated: bool  # True iff no real GPS fix within +/-50 ms of this tick


class GroundTruth(BaseModel):
    """FR-6.1: RTS-smoothed reference trajectory."""

    t_s: float
    px_m: float
    py_m: float
    v_mps: float
    psi_rad: float  # heading, normalized to [-pi, pi]
    psi_dot_rps: float

    @field_validator("psi_rad")
    @classmethod
    def _check_psi(cls, v: float) -> float:
        if not (-math.pi <= v <= math.pi):
            raise ValueError(f"psi_rad {v:.4f} not in [-pi, pi]")
        return v


class RouteMatched(BaseModel):
    """FR-9.1: Per-tick Valhalla Meili map-matching result."""

    t_s: float
    osm_way_id: int | None = None  # None when unmatched
    snapped_px_m: float | None = None
    snapped_py_m: float | None = None
    distance_from_road_m: float | None = None
    match_confidence: float  # 0..1 from Valhalla


class ReferencePath(BaseModel):
    """FR-9.3: Road centerline sampled every 1 m of arc length."""

    s_m: float  # arc length from trip start
    px_m: float
    py_m: float
    heading_rad: float  # normalized to [-pi, pi]
    curvature_1pm: float  # signed (left turn positive in ENU)
    speed_limit_mps: float
    osm_way_id: int

    @field_validator("heading_rad")
    @classmethod
    def _check_heading(cls, v: float) -> float:
        if not (-math.pi <= v <= math.pi):
            raise ValueError(f"heading_rad {v:.4f} not in [-pi, pi]")
        return v


class IdealSpeed(BaseModel):
    """FR-9.4: Comfort- and limit-constrained speed profile along reference path."""

    s_m: float
    v_ideal_mps: float
    a_ideal_mps2: float
    j_ideal_mps3: float


class IdealTrajectory(BaseModel):
    """FR-9.5: Quintic-polynomial trajectory in the time domain."""

    t_s: float
    px_m: float
    py_m: float
    v_mps: float
    a_lon_mps2: float  # body-frame longitudinal acceleration
    a_lat_mps2: float  # body-frame lateral acceleration (signed)
    j_lon_mps3: float
    psi_rad: float  # normalized to [-pi, pi]
    psi_dot_rps: float

    @field_validator("psi_rad")
    @classmethod
    def _check_psi(cls, v: float) -> float:
        if not (-math.pi <= v <= math.pi):
            raise ValueError(f"psi_rad {v:.4f} not in [-pi, pi]")
        return v
