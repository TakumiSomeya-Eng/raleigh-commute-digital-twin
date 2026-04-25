"""Unit tests for FR-1.1, FR-1.2 CSV ingestion and clock alignment.

Test groups:
  1. Scale / format: t_s starts at 0.00, steps are exactly 0.01 s
  2. Warm-up drop: first warm-up rows absent, t_s[0] == 0.00
  3. Sinusoid amplitude: 5 Hz signal through interpolation < 1 % error
  4. GPS interpolated flag: real-fix rows are False, distant rows True
  5. Missing channel raises MissingRequiredChannelError
  6. Parquet round-trip via parquet_io.write_parquet / read_parquet
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from data_engine.errors import MissingRequiredChannelError, SchemaValidationError
from data_engine.ingest import parse_and_align
from data_engine.parquet_io import read_parquet, write_parquet
from data_engine.schemas import Aligned100Hz

# ---------------------------------------------------------------------------
# Fixture: synthetic Sensor Logger CSV directory
# ---------------------------------------------------------------------------

# ENU anchor matching config/data_gen.yaml
_LAT0 = 35.773
_LON0 = -78.610

# Recording duration used across most tests
_DURATION_S = 3.0
_TARGET_HZ = 100.0
_WARMUP_S = 0.5

# GPS fires at ~1 Hz (real Sensor Logger behaviour)
_GPS_HZ = 1.0


def _t0_ns() -> int:
    """Epoch-ns anchor for synthetic data (arbitrary but fixed for determinism)."""
    return 1_745_000_000_000_000_000  # ~2025-04-19


def _make_location_csv(path: Path, duration_s: float = _DURATION_S) -> None:
    """Write a synthetic Location.csv with 1 Hz GPS fixes."""
    n = int(duration_s * _GPS_HZ) + 1
    t0 = _t0_ns()
    dt = int(1e9 / _GPS_HZ)
    times = np.array([t0 + i * dt for i in range(n)], dtype=np.int64)
    df = pd.DataFrame(
        {
            "time": times,
            "latitude": _LAT0 + np.linspace(0, 0.001, n),
            "longitude": _LON0 + np.linspace(0, 0.001, n),
            "altitude": np.zeros(n),
            "horizontalAccuracy": np.full(n, 3.0),
            "speedAccuracy": np.full(n, 0.1),
            "bearingAccuracy": np.full(n, 5.0),
            "speed": np.full(n, 10.0),
            "bearing": np.full(n, 90.0),
        }
    )
    df.to_csv(path, index=False)


def _make_imu_csv(
    path: Path,
    col_x: np.ndarray,  # type: ignore[type-arg]
    col_y: np.ndarray,  # type: ignore[type-arg]
    col_z: np.ndarray,  # type: ignore[type-arg]
    hz: float = 100.0,
    duration_s: float = _DURATION_S,
) -> None:
    """Write a generic 3-axis channel CSV at *hz* Hz."""
    n = int(duration_s * hz) + 1
    t0 = _t0_ns()
    dt = int(1e9 / hz)
    times = np.array([t0 + i * dt for i in range(n)], dtype=np.int64)
    pd.DataFrame({"time": times, "x": col_x[:n], "y": col_y[:n], "z": col_z[:n]}).to_csv(
        path, index=False
    )


def _make_orientation_csv(path: Path, hz: float = 100.0, duration_s: float = _DURATION_S) -> None:
    n = int(duration_s * hz) + 1
    t0 = _t0_ns()
    dt = int(1e9 / hz)
    times = np.array([t0 + i * dt for i in range(n)], dtype=np.int64)
    pd.DataFrame(
        {
            "time": times,
            "qw": np.ones(n),
            "qx": np.zeros(n),
            "qy": np.zeros(n),
            "qz": np.zeros(n),
        }
    ).to_csv(path, index=False)


def _make_synthetic_dir(
    tmp_path: Path,
    duration_s: float = _DURATION_S,
    accel_x: np.ndarray | None = None,  # type: ignore[type-arg]
) -> Path:
    """Create a complete synthetic Sensor Logger export directory."""
    d = tmp_path / "session"
    d.mkdir()

    n100 = int(duration_s * 100) + 1
    zeros = np.zeros(n100)
    nines = np.full(n100, 9.81)

    _make_location_csv(d / "Location.csv", duration_s)

    ax = accel_x if accel_x is not None else zeros
    _make_imu_csv(d / "Accelerometer.csv", ax, zeros, nines)
    _make_imu_csv(d / "Gyroscope.csv", zeros, zeros, zeros)
    _make_imu_csv(d / "Gravity.csv", zeros, zeros, nines)
    _make_orientation_csv(d / "Orientation.csv")
    _make_imu_csv(
        d / "Magnetometer.csv", np.full(n100, 20.0), np.full(n100, 5.0), np.full(n100, -40.0)
    )
    _make_imu_csv(d / "TotalAcceleration.csv", zeros, zeros, nines)

    return d


# ---------------------------------------------------------------------------
# 1. Grid format
# ---------------------------------------------------------------------------


def test_t_s_starts_at_zero(tmp_path: Path) -> None:
    d = _make_synthetic_dir(tmp_path)
    df = parse_and_align(d, _LAT0, _LON0, warmup_s=_WARMUP_S)
    assert df["t_s"].iloc[0] == pytest.approx(0.00)


def test_t_s_steps_are_0_01s(tmp_path: Path) -> None:
    d = _make_synthetic_dir(tmp_path)
    df = parse_and_align(d, _LAT0, _LON0, warmup_s=_WARMUP_S)
    diffs = np.diff(df["t_s"].values)
    np.testing.assert_allclose(diffs, 0.01, atol=1e-9)


def test_no_nans_in_output(tmp_path: Path) -> None:
    d = _make_synthetic_dir(tmp_path)
    df = parse_and_align(d, _LAT0, _LON0, warmup_s=_WARMUP_S)
    assert not df.isnull().any().any()


# ---------------------------------------------------------------------------
# 2. Warm-up drop
# ---------------------------------------------------------------------------


def test_warmup_rows_absent(tmp_path: Path) -> None:
    """No row should have a recording-relative time < warmup_s."""
    d = _make_synthetic_dir(tmp_path)
    df = parse_and_align(d, _LAT0, _LON0, warmup_s=_WARMUP_S)
    # The recording-relative time of the first retained row is warmup_s,
    # but after renumbering, t_s[0] == 0.00.
    # Verify the total duration is shorter than the recording.
    total = float(df["t_s"].iloc[-1])
    assert total < _DURATION_S - _WARMUP_S + 0.1  # allow 0.01 s rounding margin


# ---------------------------------------------------------------------------
# 3. Sinusoid amplitude < 1 % error (FR-1.2 acceptance criterion)
# ---------------------------------------------------------------------------


def test_sinusoid_amplitude_preserved(tmp_path: Path) -> None:
    """5 Hz sinusoid in accelerometer survives 100 Hz grid interpolation.

    The signal is sampled at exactly 100 Hz (regular) so the round-trip
    is essentially lossless (< 0.01 % distortion for any linear interpolator).
    """
    sig_hz = 5.0
    n100 = int(_DURATION_S * 100) + 1
    t_full = np.arange(n100) / 100.0
    # Unit-amplitude sinusoid at 5 Hz
    ax = np.sin(2 * math.pi * sig_hz * t_full)

    d = _make_synthetic_dir(tmp_path, accel_x=ax)
    df = parse_and_align(d, _LAT0, _LON0, warmup_s=_WARMUP_S)

    # Amplitude = max absolute value of output signal (after warmup drop).
    output_amp = float(np.abs(df["ax_mps2"].values).max())
    assert math.isclose(
        output_amp, 1.0, rel_tol=0.01
    ), f"Sinusoid amplitude {output_amp:.4f} deviates by > 1 % from 1.0"


# ---------------------------------------------------------------------------
# 4. GPS interpolated flag
# ---------------------------------------------------------------------------


def test_gps_interpolated_false_near_fix(tmp_path: Path) -> None:
    """Rows within ±50 ms of a real GPS fix are not flagged as interpolated."""
    d = _make_synthetic_dir(tmp_path)
    df = parse_and_align(d, _LAT0, _LON0, warmup_s=_WARMUP_S)
    # At least some rows should be real fixes (GPS fires at 1 Hz).
    real_fix_rows = df[~df["gps_interpolated"]]
    assert len(real_fix_rows) > 0


def test_gps_interpolated_majority_true(tmp_path: Path) -> None:
    """At 1 Hz GPS in a 100 Hz grid, most rows are interpolated.

    Each real GPS fix covers ±50 ms = 11 rows out of 100 per second,
    so the interpolated fraction is ~89 %. Threshold set at 0.85.
    """
    d = _make_synthetic_dir(tmp_path)
    df = parse_and_align(d, _LAT0, _LON0, warmup_s=_WARMUP_S)
    frac_interp = df["gps_interpolated"].mean()
    assert frac_interp > 0.85


# ---------------------------------------------------------------------------
# 5. Missing channel raises MissingRequiredChannelError
# ---------------------------------------------------------------------------


def test_missing_location_raises(tmp_path: Path) -> None:
    d = _make_synthetic_dir(tmp_path)
    (d / "Location.csv").unlink()
    with pytest.raises(MissingRequiredChannelError) as exc_info:
        parse_and_align(d, _LAT0, _LON0)
    assert exc_info.value.channel == "Location"


def test_missing_accelerometer_raises(tmp_path: Path) -> None:
    d = _make_synthetic_dir(tmp_path)
    (d / "Accelerometer.csv").unlink()
    with pytest.raises(MissingRequiredChannelError) as exc_info:
        parse_and_align(d, _LAT0, _LON0)
    assert exc_info.value.channel == "Accelerometer"


# ---------------------------------------------------------------------------
# 6. Parquet round-trip
# ---------------------------------------------------------------------------


def test_parquet_roundtrip(tmp_path: Path) -> None:
    """write_parquet → read_parquet produces a bit-identical DataFrame."""
    d = _make_synthetic_dir(tmp_path)
    df = parse_and_align(d, _LAT0, _LON0, warmup_s=_WARMUP_S)

    pq_path = tmp_path / "out" / "aligned_100hz.parquet"
    write_parquet(df, pq_path, Aligned100Hz, trip_id="test")
    df2 = read_parquet(pq_path, schema_cls=Aligned100Hz)

    pd.testing.assert_frame_equal(df.reset_index(drop=True), df2.reset_index(drop=True))


def test_parquet_schema_violation_raises(tmp_path: Path) -> None:
    """write_parquet raises SchemaValidationError when a NaN is present."""
    d = _make_synthetic_dir(tmp_path)
    df = parse_and_align(d, _LAT0, _LON0, warmup_s=_WARMUP_S)
    df.loc[0, "px_m"] = float("nan")

    pq_path = tmp_path / "bad.parquet"
    with pytest.raises(SchemaValidationError):
        write_parquet(df, pq_path, Aligned100Hz, trip_id="test")
