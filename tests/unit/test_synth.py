"""Unit tests for FR-2.2 / FR-2.4 synthetic scenario generator.

Test groups:
  1. Determinism: same seed → identical DataFrame
  2. Different seeds produce different values
  3. gps_dropout: gps_interpolated=True and large horizontal_accuracy in window
  4. imu_bias_step: accelerometer axis shifted after at_s
  5. mag_anomaly: magnetometer values large in window
  6. Batch: 10 scenarios complete in < 10 s (timing smoke test)
  7. Manifest: append-safe write
"""

from __future__ import annotations

import math
import time
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from data_engine.ingest import parse_and_align
from data_engine.noise_fit import fit_trip, write_noise_fit_yaml
from data_engine.parquet_io import read_parquet, write_parquet
from data_engine.schemas import Aligned100Hz
from data_engine.synth import generate_batch, generate_scenario, write_manifest

# ---------------------------------------------------------------------------
# Shared constants (match test_ingest.py fixture parameters)
# ---------------------------------------------------------------------------

_LAT0 = 35.773
_LON0 = -78.610
_DURATION_S = 3.0


def _t0_ns() -> int:
    return 1_745_000_000_000_000_000


def _make_location_csv(path: Path, duration_s: float = _DURATION_S) -> None:
    n = int(duration_s * 1.0) + 1
    t0 = _t0_ns()
    dt = int(1e9)
    times = np.array([t0 + i * dt for i in range(n)], dtype=np.int64)
    pd.DataFrame(
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
    ).to_csv(path, index=False)


def _make_imu_csv(
    path: Path,
    col_x: np.ndarray,  # type: ignore[type-arg]
    col_y: np.ndarray,  # type: ignore[type-arg]
    col_z: np.ndarray,  # type: ignore[type-arg]
    hz: float = 100.0,
    duration_s: float = _DURATION_S,
) -> None:
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


def _make_synthetic_dir(tmp_path: Path, duration_s: float = _DURATION_S) -> Path:
    d = tmp_path / "session"
    d.mkdir(exist_ok=True)
    n100 = int(duration_s * 100) + 1
    zeros = np.zeros(n100)
    nines = np.full(n100, 9.81)
    _make_location_csv(d / "Location.csv", duration_s)
    _make_imu_csv(d / "Accelerometer.csv", zeros, zeros, nines)
    _make_imu_csv(d / "Gyroscope.csv", zeros, zeros, zeros)
    _make_imu_csv(d / "Gravity.csv", zeros, zeros, nines)
    _make_orientation_csv(d / "Orientation.csv")
    _make_imu_csv(
        d / "Magnetometer.csv", np.full(n100, 20.0), np.full(n100, 5.0), np.full(n100, -40.0)
    )
    _make_imu_csv(d / "TotalAcceleration.csv", zeros, zeros, nines)
    return d


# ---------------------------------------------------------------------------
# Module-scoped fixtures (expensive setup runs only once)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def base_parquet(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Parse synthetic CSVs → write aligned_100hz.parquet."""
    root = tmp_path_factory.mktemp("synth_base")
    csv_dir = _make_synthetic_dir(root)
    df = parse_and_align(csv_dir, _LAT0, _LON0, warmup_s=0.5)
    pq = root / "base" / "aligned_100hz.parquet"
    write_parquet(df, pq, Aligned100Hz, trip_id="test_base")
    return pq


@pytest.fixture(scope="module")
def noise_yaml(tmp_path_factory: pytest.TempPathFactory, base_parquet: Path) -> Path:
    """Fit noise params from base_parquet and write YAML."""
    root = tmp_path_factory.mktemp("synth_noise")
    fits = fit_trip(base_parquet)
    yaml_path = root / "noise_fit_test_base.yaml"
    write_noise_fit_yaml(fits, yaml_path, trip_id="test_base")
    return yaml_path


# ---------------------------------------------------------------------------
# 1. Determinism
# ---------------------------------------------------------------------------


def test_same_seed_produces_identical_dataframe(
    base_parquet: Path, noise_yaml: Path, tmp_path: Path
) -> None:
    r1 = generate_scenario(base_parquet, noise_yaml, "s0001", 42, None, tmp_path / "run1", "tb")
    r2 = generate_scenario(base_parquet, noise_yaml, "s0001", 42, None, tmp_path / "run2", "tb")

    df1 = read_parquet(Path(tmp_path / "run1") / r1["parquet_path"])
    df2 = read_parquet(Path(tmp_path / "run2") / r2["parquet_path"])

    float_cols = df1.select_dtypes(include="float").columns
    pd.testing.assert_frame_equal(df1[float_cols], df2[float_cols])


# ---------------------------------------------------------------------------
# 2. Different seeds differ
# ---------------------------------------------------------------------------


def test_different_seeds_differ(base_parquet: Path, noise_yaml: Path, tmp_path: Path) -> None:
    r0 = generate_scenario(base_parquet, noise_yaml, "s0001", 0, None, tmp_path / "r0", "tb")
    r1 = generate_scenario(base_parquet, noise_yaml, "s0001", 1, None, tmp_path / "r1", "tb")

    df0 = read_parquet(Path(tmp_path / "r0") / r0["parquet_path"])
    df1 = read_parquet(Path(tmp_path / "r1") / r1["parquet_path"])

    # horizontal_accuracy_m is fully resampled (Rayleigh, window=0), so seeds must differ
    assert not df0["horizontal_accuracy_m"].equals(df1["horizontal_accuracy_m"])


# ---------------------------------------------------------------------------
# 3. GPS dropout stress event
# ---------------------------------------------------------------------------


def test_gps_dropout_sets_interpolated_and_accuracy(
    base_parquet: Path, noise_yaml: Path, tmp_path: Path
) -> None:
    event = {"type": "gps_dropout", "start_s": 0.5, "end_s": 1.0}
    r = generate_scenario(base_parquet, noise_yaml, "s0001", 0, [event], tmp_path, "tb")
    df = read_parquet(Path(tmp_path) / r["parquet_path"])

    dropout_rows = df[(df["t_s"] >= 0.5) & (df["t_s"] <= 1.0)]
    assert dropout_rows["gps_interpolated"].all(), "All dropout rows must be interpolated"
    assert (dropout_rows["horizontal_accuracy_m"] == 50.0).all()


def test_gps_dropout_outside_window_unchanged(
    base_parquet: Path, noise_yaml: Path, tmp_path: Path
) -> None:
    event = {"type": "gps_dropout", "start_s": 0.5, "end_s": 1.0}
    r = generate_scenario(base_parquet, noise_yaml, "s0001", 0, [event], tmp_path, "tb")
    df = read_parquet(Path(tmp_path) / r["parquet_path"])

    outside = df[df["t_s"] > 1.05]
    # Rows after the dropout window should not all be flagged as interpolated
    assert not outside["gps_interpolated"].all()


# ---------------------------------------------------------------------------
# 4. IMU bias step
# ---------------------------------------------------------------------------


def test_imu_bias_step_shifts_axis_after_at_s(
    base_parquet: Path, noise_yaml: Path, tmp_path: Path
) -> None:
    event = {"type": "imu_bias_step", "axis": "x", "delta": 5.0, "at_s": 1.0}
    r = generate_scenario(base_parquet, noise_yaml, "s0001", 0, [event], tmp_path, "tb")
    df = read_parquet(Path(tmp_path) / r["parquet_path"])

    before = df[df["t_s"] < 1.0]["ax_mps2"].mean()
    after = df[df["t_s"] >= 1.0]["ax_mps2"].mean()
    # Post-step mean should be ~5 higher than pre-step mean
    assert math.isclose(
        after - before, 5.0, abs_tol=0.5
    ), f"Expected bias shift ~5.0, got {after - before:.3f}"


# ---------------------------------------------------------------------------
# 5. Magnetic anomaly
# ---------------------------------------------------------------------------


def test_mag_anomaly_injects_large_values(
    base_parquet: Path, noise_yaml: Path, tmp_path: Path
) -> None:
    event = {"type": "mag_anomaly", "start_s": 0.5, "duration_s": 0.5}
    r = generate_scenario(base_parquet, noise_yaml, "s0001", 0, [event], tmp_path, "tb")
    df = read_parquet(Path(tmp_path) / r["parquet_path"])

    anomaly = df[(df["t_s"] >= 0.5) & (df["t_s"] < 1.0)]
    # 500 µT anomaly >> normal Earth field (~50 µT) — abs mean should be large
    for col in ("mag_x_uT", "mag_y_uT", "mag_z_uT"):
        assert float(anomaly[col].abs().mean()) > 50.0, f"{col} anomaly not large enough"


# ---------------------------------------------------------------------------
# 6. Batch timing
# ---------------------------------------------------------------------------


def test_batch_10_scenarios_under_10s(base_parquet: Path, noise_yaml: Path, tmp_path: Path) -> None:
    t0 = time.monotonic()
    results = generate_batch(
        base_parquet, noise_yaml, tmp_path, "test_base", n=10, seed0=100, workers=1
    )
    elapsed = time.monotonic() - t0

    assert len(results) == 10
    assert elapsed < 10.0, f"10 scenarios took {elapsed:.1f} s (limit: 10 s)"


# ---------------------------------------------------------------------------
# 7. Manifest
# ---------------------------------------------------------------------------


def test_write_manifest_creates_and_appends(
    base_parquet: Path, noise_yaml: Path, tmp_path: Path
) -> None:
    import json

    results1 = generate_batch(base_parquet, noise_yaml, tmp_path / "a", "tb", n=2, seed0=0)
    manifest_path = tmp_path / "manifest.json"
    write_manifest(results1, manifest_path, "tb")

    results2 = generate_batch(base_parquet, noise_yaml, tmp_path / "b", "tb", n=3, seed0=10)
    write_manifest(results2, manifest_path, "tb")

    with open(manifest_path, encoding="utf-8") as fh:
        data = json.load(fh)

    assert data["manifest_version"] == 1
    assert len(data["scenarios"]) == 5  # 2 + 3
    assert {s["seed"] for s in data["scenarios"]} == {0, 1, 10, 11, 12}
