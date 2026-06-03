# sumo_adapter.py — Interface Specification (T8.3)
#
# This file defines ONLY the public function signatures and their contracts.
# Test Writer Agent reads this to write tests BEFORE implementation exists.
# Impl Agent reads this to ensure the implementation matches the contract.
#
# DO NOT add implementation logic to this file.

from __future__ import annotations

from pathlib import Path
from typing import Literal

import pandas as pd

# ── Types ─────────────────────────────────────────────────────────────────────

DrivingStyle = Literal["calm", "normal", "aggressive"]

# ── Constants (shared between adapter and tests) ───────────────────────────────

#: Seven filenames that Sensor Logger produces. sumo_adapter must emit all seven.
SENSOR_LOGGER_FILES: tuple[str, ...] = (
    "Location.csv",
    "Accelerometer.csv",
    "Gyroscope.csv",
    "Gravity.csv",
    "Orientation.csv",
    "Magnetometer.csv",
    "TotalAcceleration.csv",
)

#: Gaussian noise sigmas applied per style (matches noise_fit.py real measurements)
NOISE_SIGMAS: dict[DrivingStyle, dict[str, float]] = {
    "calm":       {"gps_m": 3.0, "accel": 0.10, "gyro": 0.005, "mag_uT": 1.5},
    "normal":     {"gps_m": 5.0, "accel": 0.15, "gyro": 0.008, "mag_uT": 2.5},
    "aggressive": {"gps_m": 8.0, "accel": 0.25, "gyro": 0.015, "mag_uT": 4.0},
}

#: Harsh-brake threshold (must match config/scoring.yaml)
HARSH_BRAKE_THRESHOLD_MPS2: float = 3.0


# ── Public API ────────────────────────────────────────────────────────────────


def parse_fcd(fcd_path: Path) -> pd.DataFrame:
    """Parse a SUMO FCD (Floating Car Data) XML file into a tidy DataFrame.

    Parameters
    ----------
    fcd_path:
        Path to a ``*.xml`` file produced by SUMO with ``--fcd-output.geo true``.

    Returns
    -------
    pd.DataFrame with columns:
        t_s       : float   — elapsed seconds from start of simulation
        lon       : float   — WGS-84 longitude (degrees)
        lat       : float   — WGS-84 latitude  (degrees)
        speed_mps : float   — vehicle speed (m/s, non-negative)
        bearing   : float   — heading in [0, 360), north=0, clockwise

    Contract
    --------
    - ``t_s`` starts at 0.0 and increases monotonically.
    - ``speed_mps`` is always ≥ 0.
    - ``bearing`` is always in [0, 360).
    - Empty FCD (no <vehicle> elements) raises ``ValueError``.
    - Missing required XML attributes raise ``ValueError``.
    """
    ...


def add_noise(
    fcd_df: pd.DataFrame,
    style: DrivingStyle,
    *,
    seed: int | None = None,
) -> pd.DataFrame:
    """Add Gaussian sensor noise to a parsed FCD DataFrame.

    Parameters
    ----------
    fcd_df:
        Output of ``parse_fcd()``.
    style:
        Driving style key — controls noise sigma magnitudes (see NOISE_SIGMAS).
    seed:
        Optional random seed for reproducibility in tests.

    Returns
    -------
    pd.DataFrame — same schema as input, with noise applied to:
        lat, lon : ± gps_m  (converted to degrees via 1 deg ≈ 111_320 m)
        speed_mps: ± accel × dt  (zero-clipped)

    Contract
    --------
    - Output shape equals input shape.
    - ``speed_mps`` remains ≥ 0 after noise (zero-clipped).
    - ``bearing`` remains in [0, 360).
    - With ``seed`` fixed, output is deterministic.
    - 99.7 % of GPS position errors are within 3 × gps_sigma metres (3σ rule).
    """
    ...


def to_sensor_logger_csvs(
    fcd_df: pd.DataFrame,
    style: DrivingStyle,
    out_dir: Path,
    *,
    trip_id: str | None = None,
) -> dict[str, Path]:
    """Convert a (possibly noise-added) FCD DataFrame to Sensor Logger CSV files.

    Parameters
    ----------
    fcd_df:
        Output of ``parse_fcd()`` or ``add_noise()``.
    style:
        Used to set ``horizontalAccuracy`` in Location.csv.
    out_dir:
        Directory where the seven CSV files will be written.
        Created if it does not exist.
    trip_id:
        Optional label stored in a ``trip_id`` metadata row (ignored by
        existing pipeline, useful for tracing).

    Returns
    -------
    dict mapping filename → Path for each of the seven files.
    All seven keys in ``SENSOR_LOGGER_FILES`` are always present.

    Contract
    --------
    - Exactly seven files are written (``SENSOR_LOGGER_FILES``).
    - ``Location.csv`` column ``time`` is int64 epoch nanoseconds.
    - ``Location.csv`` column ``speed`` is in m/s.
    - ``Location.csv`` column ``course`` equals ``bearing`` from fcd_df.
    - ``Accelerometer.csv`` columns are ``time, x, y, z`` (m/s²).
    - ``Gyroscope.csv`` columns are ``time, x, y, z`` (rad/s).
    - ``Gravity.csv``: x=0.0, y=0.0, z=-9.81 (constant, 2-D sim).
    - ``Orientation.csv``: quaternion derived from bearing (qw, qx, qy, qz).
    - ``Magnetometer.csv``: magnetic field from bearing (µT).
    - ``TotalAcceleration.csv``: vector sum of Accelerometer + Gravity.
    - All ``time`` columns are int64 epoch nanoseconds, strictly increasing.
    - Files can be read directly by ``data_engine.ingest.parse_and_align()``.
    """
    ...


def convert(
    fcd_path: Path,
    style: DrivingStyle,
    out_dir: Path,
    *,
    seed: int | None = None,
    trip_id: str | None = None,
) -> dict[str, Path]:
    """End-to-end convenience wrapper: FCD XML → 7 Sensor Logger CSVs.

    Equivalent to::

        df = parse_fcd(fcd_path)
        df = add_noise(df, style, seed=seed)
        return to_sensor_logger_csvs(df, style, out_dir, trip_id=trip_id)

    This is the function called by the CLI entry point and by integration tests.
    """
    ...
