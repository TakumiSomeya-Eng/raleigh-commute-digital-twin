"""FR-1.3 -- WGS-84 <-> local ENU projection (flat-earth / equirectangular).

Anchor (lat0, lon0) is read from config/data_gen.yaml.
Flat-earth approximation is valid for the < 10 km Raleigh corridor span.

See: TRD sec.1.1
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import yaml
from numpy.typing import NDArray

# Metres per degree of latitude (WGS-84 semi-major axis derived).
_M_PER_DEG_LAT: float = 111_132.954

FloatOrArray = float | NDArray[np.float64]


def _m_per_deg_lon(lat0_deg: float) -> float:
    """Longitudinal scale factor at the given latitude (metres per degree)."""
    return _M_PER_DEG_LAT * math.cos(math.radians(lat0_deg))


def wgs84_to_enu(
    lat: FloatOrArray,
    lon: FloatOrArray,
    lat0_deg: float,
    lon0_deg: float,
) -> tuple[FloatOrArray, FloatOrArray]:
    """Convert WGS-84 (lat, lon) to local ENU (east_m, north_m).

    Args:
        lat: Latitude(s) in decimal degrees.
        lon: Longitude(s) in decimal degrees.
        lat0_deg: Anchor latitude in decimal degrees.
        lon0_deg: Anchor longitude in decimal degrees.

    Returns:
        (east_m, north_m) displacement from anchor in metres.
    """
    east_m = (lon - lon0_deg) * _m_per_deg_lon(lat0_deg)
    north_m = (lat - lat0_deg) * _M_PER_DEG_LAT
    return east_m, north_m


def enu_to_wgs84(
    east_m: FloatOrArray,
    north_m: FloatOrArray,
    lat0_deg: float,
    lon0_deg: float,
) -> tuple[FloatOrArray, FloatOrArray]:
    """Convert local ENU (east_m, north_m) back to WGS-84 (lat, lon).

    Args:
        east_m: Easting displacement(s) from anchor in metres.
        north_m: Northing displacement(s) from anchor in metres.
        lat0_deg: Anchor latitude in decimal degrees.
        lon0_deg: Anchor longitude in decimal degrees.

    Returns:
        (lat, lon) in decimal degrees.
    """
    lat = north_m / _M_PER_DEG_LAT + lat0_deg
    lon = east_m / _m_per_deg_lon(lat0_deg) + lon0_deg
    return lat, lon


def load_anchor(config_path: str | Path | None = None) -> tuple[float, float]:
    """Load (lat0_deg, lon0_deg) from config/data_gen.yaml.

    Args:
        config_path: Path to data_gen.yaml. Defaults to
            <repo_root>/config/data_gen.yaml relative to this file.

    Returns:
        (lat0_deg, lon0_deg) anchor coordinates.
    """
    if config_path is None:
        config_path = Path(__file__).parent.parent.parent / "config" / "data_gen.yaml"
    with open(config_path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    anchor = cfg["enu_anchor"]
    return float(anchor["lat0_deg"]), float(anchor["lon0_deg"])
