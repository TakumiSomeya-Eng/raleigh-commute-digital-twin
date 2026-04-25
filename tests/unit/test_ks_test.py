"""Unit tests for FR-2.3 KS-test gate (src/data_engine/ks_test.py).

Test groups:
  1. Identical distributions → all channels pass (p = 1.0)
  2. Very different distributions → gate fails
  3. Report schema matches TRD §1.9 required keys
  4. write_ks_report JSON round-trip
  5. _find_parquets discovers nested files
  6. Configurable pass_rate threshold is respected
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from data_engine.ks_test import _find_parquets, _ks_per_channel, run_ks_test, write_ks_report
from data_engine.parquet_io import write_parquet
from data_engine.schemas import Aligned100Hz

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

_LAT0 = 35.773
_LON0 = -78.610
_CHANNELS = [
    "ax_mps2",
    "ay_mps2",
    "az_mps2",
    "gx_rps",
    "gy_rps",
    "gz_rps",
    "grav_x",
    "grav_y",
    "grav_z",
    "mag_x_uT",
    "mag_y_uT",
    "mag_z_uT",
    "speed_accuracy_mps",
    "horizontal_accuracy_m",
    "gps_bearing_deg",
]


def _make_aligned_df(n: int, seed: int, loc: float = 0.0, scale: float = 0.1) -> pd.DataFrame:
    """Create a minimal valid Aligned100Hz DataFrame with Gaussian noise."""
    rng = np.random.default_rng(seed)
    t0_ns = 1_745_000_000_000_000_000
    dt_ns = 10_000_000  # 10 ms = 100 Hz
    return pd.DataFrame(
        {
            "t_s": np.round(np.arange(n) * 0.01, 2),
            "time_ns": np.array([t0_ns + i * dt_ns for i in range(n)], dtype=np.int64),
            "px_m": rng.normal(loc, scale, n),
            "py_m": rng.normal(loc, scale, n),
            "lat_wgs84": _LAT0 + rng.normal(0, 1e-5, n),
            "lon_wgs84": _LON0 + rng.normal(0, 1e-5, n),
            "horizontal_accuracy_m": np.abs(rng.normal(3.0 + loc, scale, n)) + 0.01,
            "speed_accuracy_mps": np.abs(rng.normal(0.1 + loc, scale, n)) + 0.001,
            "bearing_accuracy_deg": np.abs(rng.normal(5.0, scale, n)) + 0.1,
            "gps_speed_mps": np.abs(rng.normal(10.0 + loc, scale, n)),
            "gps_bearing_deg": (rng.normal(90.0 + loc, scale * 10, n)) % 360.0,
            "ax_mps2": rng.normal(loc, scale, n),
            "ay_mps2": rng.normal(loc, scale, n),
            "az_mps2": rng.normal(loc, scale, n),
            "gx_rps": rng.normal(loc, scale, n),
            "gy_rps": rng.normal(loc, scale, n),
            "gz_rps": rng.normal(loc, scale, n),
            "grav_x": rng.normal(loc, scale, n),
            "grav_y": rng.normal(loc, scale, n),
            "grav_z": 9.81 + rng.normal(loc, scale, n),
            "quat_w": np.ones(n),
            "quat_x": np.zeros(n),
            "quat_y": np.zeros(n),
            "quat_z": np.zeros(n),
            "mag_x_uT": rng.normal(20.0 + loc, scale, n),
            "mag_y_uT": rng.normal(5.0 + loc, scale, n),
            "mag_z_uT": rng.normal(-40.0 + loc, scale, n),
            "gps_interpolated": np.zeros(n, dtype=bool),
        }
    )


def _write_parquet_to(df: pd.DataFrame, directory: Path, name: str = "aligned_100hz") -> Path:
    path = directory / f"{name}.parquet"
    write_parquet(df, path, Aligned100Hz, trip_id=name)
    return path


# ---------------------------------------------------------------------------
# 1. Identical distributions → all pass
# ---------------------------------------------------------------------------


def test_identical_data_gate_passes(tmp_path: Path) -> None:
    df = _make_aligned_df(n=2000, seed=0)
    real_dir = tmp_path / "real"
    synth_dir = tmp_path / "synth" / "s0001"

    _write_parquet_to(df, real_dir)
    _write_parquet_to(df, synth_dir)

    report = run_ks_test(real_dir, tmp_path / "synth")

    assert report["gate_passed"] is True
    assert report["overall_pass_rate"] == pytest.approx(1.0)
    for ch_data in report["channels"].values():
        assert ch_data["pass"] is True


# ---------------------------------------------------------------------------
# 2. Very different distributions → gate fails
# ---------------------------------------------------------------------------


def test_very_different_data_gate_fails(tmp_path: Path) -> None:
    real_df = _make_aligned_df(n=2000, seed=1, loc=0.0, scale=0.1)
    synth_df = _make_aligned_df(n=2000, seed=2, loc=100.0, scale=0.1)

    real_dir = tmp_path / "real"
    synth_dir = tmp_path / "synth" / "s0001"
    _write_parquet_to(real_df, real_dir)
    _write_parquet_to(synth_df, synth_dir)

    report = run_ks_test(real_dir, tmp_path / "synth", pass_rate_threshold=0.80)
    assert report["gate_passed"] is False


# ---------------------------------------------------------------------------
# 3. Report schema matches TRD §1.9
# ---------------------------------------------------------------------------


def test_report_has_required_keys(tmp_path: Path) -> None:
    df = _make_aligned_df(n=500, seed=0)
    real_dir = tmp_path / "real"
    synth_dir = tmp_path / "synth" / "s0001"
    _write_parquet_to(df, real_dir)
    _write_parquet_to(df, synth_dir)

    report = run_ks_test(real_dir, tmp_path / "synth")

    for key in ("channels", "overall_pass_rate", "gate_threshold", "gate_passed"):
        assert key in report, f"Missing required TRD §1.9 key: {key!r}"

    for ch, data in report["channels"].items():
        assert "p_value" in data, f"channel {ch}: missing p_value"
        assert "pass" in data, f"channel {ch}: missing pass"
        assert isinstance(data["p_value"], float)
        assert isinstance(data["pass"], bool)


# ---------------------------------------------------------------------------
# 4. write_ks_report JSON round-trip
# ---------------------------------------------------------------------------


def test_write_read_ks_report(tmp_path: Path) -> None:
    report = {
        "channels": {
            "ax_mps2": {"p_value": 0.234, "pass": True},
            "gz_rps": {"p_value": 0.412, "pass": True},
            "horizontal_accuracy_m": {"p_value": 0.017, "pass": False},
        },
        "overall_pass_rate": 0.667,
        "gate_threshold": 0.80,
        "gate_passed": False,
    }
    path = tmp_path / "ks_report.json"
    write_ks_report(report, path)

    with open(path, encoding="utf-8") as fh:
        loaded = json.load(fh)

    assert loaded["gate_passed"] is False
    assert math.isclose(loaded["overall_pass_rate"], 0.667, rel_tol=1e-6)
    assert loaded["channels"]["ax_mps2"]["pass"] is True
    assert loaded["channels"]["horizontal_accuracy_m"]["pass"] is False


# ---------------------------------------------------------------------------
# 5. _find_parquets discovers nested files
# ---------------------------------------------------------------------------


def test_find_parquets_nested(tmp_path: Path) -> None:
    df = _make_aligned_df(n=10, seed=0)
    for sub in ("s0001", "s0002", "s0003"):
        _write_parquet_to(df, tmp_path / sub)

    found = _find_parquets(tmp_path)
    assert len(found) == 3
    assert all(p.name == "aligned_100hz.parquet" for p in found)


# ---------------------------------------------------------------------------
# 6. Configurable pass_rate threshold respected
# ---------------------------------------------------------------------------


def test_pass_rate_threshold_respected() -> None:
    rng = np.random.default_rng(99)
    n = 5000
    channels = list(_CHANNELS)  # 15 channels

    real_data = {ch: rng.normal(0, 1, n) for ch in channels}
    synth_data = dict(real_data)
    for ch in channels[9:]:  # last 6 channels far from real
        synth_data[ch] = rng.normal(100, 1, n)

    real_df = pd.DataFrame(real_data)
    synth_df = pd.DataFrame(synth_data)

    ch_results = _ks_per_channel(real_df, synth_df, channels, p_threshold=0.05)
    n_pass = sum(1 for v in ch_results.values() if v["pass"])
    pass_rate = n_pass / len(ch_results)

    # 9/15 = 0.60: above 0.50 threshold, below 0.70 threshold
    assert pass_rate > 0.50, f"Expected pass_rate > 0.50, got {pass_rate:.2f}"
    assert pass_rate < 0.70, f"Expected pass_rate < 0.70, got {pass_rate:.2f}"
