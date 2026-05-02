"""Unit tests for FR-6.2 RMSE harness (T3.2).

Tests cover: metric computation, per-minute breakdown,
GPS-only baseline, and S1 gate logic.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parents[2] / "src"))

from evaluation.rmse import _horizontal_rmse, _per_minute_rmse, compute_rmse

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_fake_gt(tmp: Path, n: int = 600, v: float = 10.0, dt: float = 0.01) -> Path:
    """Write a synthetic straight-line ground_truth.parquet."""
    t = np.arange(n) * dt
    px = v * t
    py = np.zeros(n)
    df = pd.DataFrame(
        {
            "t_s": t,
            "px_m": px,
            "py_m": py,
            "v_mps": np.full(n, v),
            "psi_rad": np.zeros(n),
            "psi_dot_rps": np.zeros(n),
        }
    )
    p = tmp / "ground_truth.parquet"
    df.to_parquet(p, index=False)
    return p


def _write_fake_fused(
    tmp: Path, gt_df: pd.DataFrame, offset_m: float = 0.5, name: str = "ekf"
) -> Path:
    """Write fused output with a constant offset from ground truth."""
    df = gt_df.copy()
    df["px_m"] = df["px_m"] + offset_m
    df["py_m"] = df["py_m"]
    # Add extra columns present in real fused output
    df["psi_dot_rps"] = 0.0
    df["cov_xx"] = 1.0
    df["cov_yy"] = 1.0
    df["cov_yaw"] = 0.01
    p = tmp / f"fused_{name}.parquet"
    df.to_parquet(p, index=False)
    return p


def _write_fake_aligned(tmp: Path, gt_df: pd.DataFrame, offset_m: float = 2.0) -> Path:
    """Write aligned_100hz.parquet with a larger GPS offset (GPS-only baseline)."""
    n = len(gt_df)
    rng = np.random.default_rng(42)
    df = pd.DataFrame(
        {
            "t_s": gt_df.t_s.to_numpy(),
            "time_ns": (gt_df.t_s.to_numpy() * 1e9).astype(np.int64),
            "px_m": gt_df.px_m.to_numpy() + rng.normal(0, offset_m, n),
            "py_m": gt_df.py_m.to_numpy() + rng.normal(0, offset_m, n),
            "lat_wgs84": np.zeros(n),
            "lon_wgs84": np.zeros(n),
            "horizontal_accuracy_m": np.full(n, 3.0),
            "speed_accuracy_mps": np.full(n, 0.5),
            "bearing_accuracy_deg": np.full(n, 5.0),
            "gps_speed_mps": np.full(n, 10.0),
            "gps_bearing_deg": np.zeros(n),
            "ax_mps2": np.zeros(n),
            "ay_mps2": np.zeros(n),
            "az_mps2": np.zeros(n),
            "gx_rps": np.zeros(n),
            "gy_rps": np.zeros(n),
            "gz_rps": np.zeros(n),
            "grav_x": np.zeros(n),
            "grav_y": np.zeros(n),
            "grav_z": np.full(n, -9.81),
            "quat_w": np.ones(n),
            "quat_x": np.zeros(n),
            "quat_y": np.zeros(n),
            "quat_z": np.zeros(n),
            "mag_x_uT": np.zeros(n),
            "mag_y_uT": np.zeros(n),
            "mag_z_uT": np.zeros(n),
            "gps_interpolated": np.zeros(n, dtype=bool),
        }
    )
    p = tmp / "aligned_100hz.parquet"
    df.to_parquet(p, index=False)
    return p


# ---------------------------------------------------------------------------
# Tests: metric functions
# ---------------------------------------------------------------------------


class TestHorizontalRmse:
    def test_zero_error(self):
        x = np.array([1.0, 2.0, 3.0])
        assert _horizontal_rmse(x, x, x, x) == pytest.approx(0.0, abs=1e-12)

    def test_constant_offset(self):
        px = np.array([0.0, 1.0, 2.0])
        py = np.zeros(3)
        # 3 m offset in x
        assert _horizontal_rmse(px + 3.0, py, px, py) == pytest.approx(3.0, abs=1e-9)

    def test_diagonal_offset(self):
        px = np.array([0.0])
        py = np.array([0.0])
        # 3-4-5 triangle
        rmse = _horizontal_rmse(px + 3.0, py + 4.0, px, py)
        assert rmse == pytest.approx(5.0, abs=1e-9)


class TestPerMinuteRmse:
    def test_two_minutes(self):
        # 120 s trip at 1 Hz, first minute 1m error, second minute 2m error
        n1, n2 = 60, 60
        t = np.concatenate([np.arange(n1), np.arange(n2) + 60]).astype(float)
        px_ref = np.zeros(n1 + n2)
        py_ref = np.zeros(n1 + n2)
        px_est = np.concatenate([np.ones(n1), 2.0 * np.ones(n2)])
        py_est = np.zeros(n1 + n2)
        per = _per_minute_rmse(t, px_est, py_est, px_ref, py_ref)
        assert len(per) == 2
        assert per[0] == pytest.approx(1.0, abs=1e-9)
        assert per[1] == pytest.approx(2.0, abs=1e-9)


# ---------------------------------------------------------------------------
# Tests: compute_rmse integration
# ---------------------------------------------------------------------------


class TestComputeRmse:
    @pytest.fixture
    def trace_dir(self, tmp_path):
        trace = tmp_path / "day2"
        trace.mkdir()
        n, v, dt = 600, 10.0, 0.01
        _write_fake_gt(trace, n=n, v=v, dt=dt)
        gt_df = pd.read_parquet(trace / "ground_truth.parquet")
        _write_fake_fused(trace, gt_df, offset_m=0.5, name="ekf")
        _write_fake_aligned(trace, gt_df, offset_m=2.0)
        return tmp_path

    def test_returns_dict_with_required_keys(self, trace_dir):
        report = compute_rmse("day2", "ekf", trace_dir)
        for key in (
            "trip_id",
            "filter",
            "overall_rmse_m",
            "gps_only_rmse_m",
            "improvement_pct",
            "per_minute_rmse_m",
            "s1_pass",
        ):
            assert key in report

    def test_overall_rmse_close_to_offset(self, trace_dir):
        report = compute_rmse("day2", "ekf", trace_dir)
        # Filter has 0.5m constant px offset -> RMSE ~= 0.5m
        assert abs(report["overall_rmse_m"] - 0.5) < 0.05

    def test_gps_rmse_larger_than_filter(self, trace_dir):
        report = compute_rmse("day2", "ekf", trace_dir)
        assert report["gps_only_rmse_m"] > report["overall_rmse_m"]

    def test_s1_pass_when_filter_better(self, trace_dir):
        """Filter (0.5 m offset) should beat GPS (2.0 m noise) -> S1 pass."""
        report = compute_rmse("day2", "ekf", trace_dir)
        assert report["s1_pass"] is True

    def test_s1_fail_when_filter_worse(self, tmp_path):
        """If filter has larger error than GPS, S1 must fail."""
        trace = tmp_path / "day2"
        trace.mkdir()
        n, v, dt = 600, 10.0, 0.01
        _write_fake_gt(trace, n=n, v=v, dt=dt)
        gt_df = pd.read_parquet(trace / "ground_truth.parquet")
        # Filter: 5 m offset (worse than GPS 2 m)
        _write_fake_fused(trace, gt_df, offset_m=5.0, name="ekf")
        _write_fake_aligned(trace, gt_df, offset_m=2.0)
        report = compute_rmse("day2", "ekf", tmp_path)
        assert report["s1_pass"] is False

    def test_per_minute_list_nonempty(self, trace_dir):
        report = compute_rmse("day2", "ekf", trace_dir)
        assert len(report["per_minute_rmse_m"]) >= 1
