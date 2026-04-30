"""Unit tests for FR-6.4 EKF vs UKF comparator (T3.4).

Known-answer tests with synthetic straight and curved trip fixtures.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parents[2] / "src"))

from evaluation.comparator import (
    _EQUIV_THRESHOLD,
    _TURN_THRESHOLD,
    _horizontal_rmse,
    _rmse_masked,
    _winner,
    compare,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_fused(
    px: np.ndarray,
    py: np.ndarray,
    t: np.ndarray,
) -> pd.DataFrame:
    n = len(t)
    return pd.DataFrame(
        {
            "t_s": t,
            "px_m": px,
            "py_m": py,
            "v_mps": np.ones(n),
            "psi_rad": np.zeros(n),
            "psi_dot_rps": np.zeros(n),
            "cov_xx": np.ones(n),
            "cov_yy": np.ones(n),
            "cov_yaw": np.full(n, 0.01),
        }
    )


def _make_gt(
    px: np.ndarray,
    py: np.ndarray,
    psi_dot: np.ndarray,
    t: np.ndarray,
) -> pd.DataFrame:
    n = len(t)
    return pd.DataFrame(
        {
            "t_s": t,
            "px_m": px,
            "py_m": py,
            "v_mps": np.ones(n),
            "psi_rad": np.zeros(n),
            "psi_dot_rps": psi_dot,
        }
    )


def _write_parquets(tmp: Path, gt, ekf, ukf, trace: str = "test") -> Path:
    trace_dir = tmp / trace
    trace_dir.mkdir(parents=True, exist_ok=True)
    gt.to_parquet(trace_dir / "ground_truth.parquet", index=False)
    ekf.to_parquet(trace_dir / "fused_ekf.parquet", index=False)
    ukf.to_parquet(trace_dir / "fused_ukf.parquet", index=False)
    return trace_dir


# ---------------------------------------------------------------------------
# Unit tests: helper functions
# ---------------------------------------------------------------------------


class TestHorizontalRmse:
    def test_zero_error(self):
        x = np.array([1.0, 2.0, 3.0])
        assert _horizontal_rmse(x, x, x, x) == pytest.approx(0.0)

    def test_constant_offset(self):
        # 3 m error in x, 4 m in y -> 5 m RMSE
        px = np.zeros(100)
        py = np.zeros(100)
        assert _horizontal_rmse(px + 3, py + 4, px, py) == pytest.approx(5.0, rel=1e-9)


class TestRmseMasked:
    def test_returns_none_when_insufficient_samples(self):
        px = np.ones(5)
        py = np.zeros(5)
        mask = np.ones(5, dtype=bool)
        # 5 < _MIN_SEGMENT_SAMPLES=10 -> None
        assert _rmse_masked(px, py, np.zeros(5), py, mask) is None

    def test_mask_selects_subset(self):
        n = 100
        px = np.zeros(n)
        py = np.zeros(n)
        # First 50 rows have 3 m error; last 50 have 0 error
        px_est = np.concatenate([np.full(50, 3.0), np.zeros(50)])
        mask = np.zeros(n, dtype=bool)
        mask[:50] = True  # only rows with error
        result = _rmse_masked(px_est, py, px, py, mask)
        assert result == pytest.approx(3.0, rel=1e-9)


class TestWinner:
    def test_ekf_wins(self):
        assert _winner(1.0, 2.0) == "ekf"

    def test_ukf_wins(self):
        assert _winner(2.0, 1.0) == "ukf"

    def test_equivalent_positive_delta(self):
        # |delta| < _EQUIV_THRESHOLD -> equivalent
        assert "equivalent" in _winner(1.0, 1.0 + _EQUIV_THRESHOLD * 0.5)

    def test_equivalent_zero_delta(self):
        assert "equivalent" in _winner(5.0, 5.0)

    def test_none_rmse_gives_insufficient(self):
        assert _winner(None, 1.0) == "insufficient_data"
        assert _winner(1.0, None) == "insufficient_data"

    def test_threshold_boundary(self):
        # ekf_rmse = ukf + threshold - eps  -> |delta| < threshold -> equivalent
        # ekf_rmse = ukf + threshold + eps  -> ekf worse -> ukf wins
        lo = _winner(1.0 + _EQUIV_THRESHOLD - 1e-9, 1.0)
        hi = _winner(1.0 + _EQUIV_THRESHOLD + 1e-9, 1.0)
        assert "equivalent" in lo
        assert hi == "ukf"


# ---------------------------------------------------------------------------
# Integration tests: compare() end-to-end
# ---------------------------------------------------------------------------


class TestCompare:
    def _make_straight_trip(self, n: int = 500, dt: float = 0.01, t0: float = 1.0e9):
        t = np.arange(n) * dt + t0
        px_true = np.arange(n) * 0.1  # moving east at 10 m/s
        py_true = np.zeros(n)
        psi_dot_true = np.zeros(n)  # straight -> all zeros
        return t, px_true, py_true, psi_dot_true

    def _make_mixed_trip(self, n: int = 600, dt: float = 0.01, t0: float = 1.0e9):
        """First half straight, second half turning."""
        t = np.arange(n) * dt + t0
        px_true = np.arange(n) * 0.1
        py_true = np.zeros(n)
        psi_dot_true = np.zeros(n)
        # Second 300 rows: clearly turning
        psi_dot_true[300:] = _TURN_THRESHOLD * 2.0
        return t, px_true, py_true, psi_dot_true

    def test_report_has_required_keys(self, tmp_path):
        t, px, py, psi_dot = self._make_straight_trip()
        gt = _make_gt(px, py, psi_dot, t)
        ekf = _make_fused(px + 1.0, py, t)
        ukf = _make_fused(px + 1.5, py, t)
        _write_parquets(tmp_path, gt, ekf, ukf)
        out_path = compare("test", tmp_path)
        report = json.loads(out_path.read_text())
        for key in (
            "trip_id",
            "turn_threshold_rps",
            "equiv_threshold_m",
            "overall",
            "by_curvature",
            "per_minute",
        ):
            assert key in report, f"missing key: {key}"
        for seg in ("straight", "turning"):
            assert seg in report["by_curvature"]
        for seg_key in (
            "n_samples",
            "pct_of_trip",
            "ekf_rmse_m",
            "ukf_rmse_m",
            "delta_rmse_m",
            "winner",
        ):
            assert seg_key in report["overall"], f"missing overall key: {seg_key}"

    def test_ekf_wins_on_straight(self, tmp_path):
        """EKF has smaller error on straight segment -> ekf declared winner."""
        t, px, py, psi_dot = self._make_straight_trip(n=500)
        gt = _make_gt(px, py, psi_dot, t)
        ekf = _make_fused(px + 0.5, py, t)  # 0.5 m error
        ukf = _make_fused(px + 2.0, py, t)  # 2.0 m error
        _write_parquets(tmp_path, gt, ekf, ukf)
        report = json.loads(compare("test", tmp_path).read_text())
        assert report["overall"]["winner"] == "ekf"

    def test_ukf_wins_on_straight(self, tmp_path):
        """UKF has smaller error -> ukf declared winner."""
        t, px, py, psi_dot = self._make_straight_trip(n=500)
        gt = _make_gt(px, py, psi_dot, t)
        ekf = _make_fused(px + 2.0, py, t)
        ukf = _make_fused(px + 0.5, py, t)
        _write_parquets(tmp_path, gt, ekf, ukf)
        report = json.loads(compare("test", tmp_path).read_text())
        assert report["overall"]["winner"] == "ukf"

    def test_equivalent_when_errors_close(self, tmp_path):
        """Same error for both -> equivalent."""
        t, px, py, psi_dot = self._make_straight_trip(n=500)
        gt = _make_gt(px, py, psi_dot, t)
        ekf = _make_fused(px + 1.0, py, t)
        ukf = _make_fused(px + 1.0, py, t)
        _write_parquets(tmp_path, gt, ekf, ukf)
        report = json.loads(compare("test", tmp_path).read_text())
        assert "equivalent" in report["overall"]["winner"]

    def test_segment_split_correct(self, tmp_path):
        """Mixed trip: straight + turning segments have correct sample counts."""
        t, px, py, psi_dot = self._make_mixed_trip(n=600)
        gt = _make_gt(px, py, psi_dot, t)
        ekf = _make_fused(px + 1.0, py, t)
        ukf = _make_fused(px + 1.2, py, t)
        _write_parquets(tmp_path, gt, ekf, ukf)
        report = json.loads(compare("test", tmp_path).read_text())
        straight = report["by_curvature"]["straight"]
        turning = report["by_curvature"]["turning"]
        # First 300 rows straight, last 300 turning
        assert straight["n_samples"] == 300
        assert turning["n_samples"] == 300
        assert straight["n_samples"] + turning["n_samples"] == report["overall"]["n_samples"]

    def test_per_minute_has_entries(self, tmp_path):
        """Per-minute list has at least one entry."""
        t, px, py, psi_dot = self._make_straight_trip(n=500)
        gt = _make_gt(px, py, psi_dot, t)
        ekf = _make_fused(px, py, t)
        ukf = _make_fused(px, py, t)
        _write_parquets(tmp_path, gt, ekf, ukf)
        report = json.loads(compare("test", tmp_path).read_text())
        assert len(report["per_minute"]) >= 1
        for row in report["per_minute"]:
            assert "minute" in row and "winner" in row

    def test_delta_rmse_sign_convention(self, tmp_path):
        """delta_rmse_m = ekf_rmse - ukf_rmse; positive means EKF better."""
        t, px, py, psi_dot = self._make_straight_trip(n=500)
        gt = _make_gt(px, py, psi_dot, t)
        ekf = _make_fused(px + 1.0, py, t)  # 1 m error
        ukf = _make_fused(px + 3.0, py, t)  # 3 m error -> EKF better
        _write_parquets(tmp_path, gt, ekf, ukf)
        report = json.loads(compare("test", tmp_path).read_text())
        # delta = ekf_rmse - ukf_rmse = 1 - 3 = -2 < 0 means UKF bigger -> EKF better
        assert report["overall"]["delta_rmse_m"] < 0
        assert report["overall"]["winner"] == "ekf"

    def test_rmse_values_match_manual(self, tmp_path):
        """Check ekf_rmse against manually computed value."""
        n = 100
        t = np.arange(n) * 0.01 + 1.0e9
        px_true = np.zeros(n)
        py_true = np.zeros(n)
        gt = _make_gt(px_true, py_true, np.zeros(n), t)
        ekf = _make_fused(px_true + 3.0, py_true + 4.0, t)  # 5 m error
        ukf = _make_fused(px_true, py_true, t)  # 0 m error
        _write_parquets(tmp_path, gt, ekf, ukf)
        report = json.loads(compare("test", tmp_path).read_text())
        assert report["overall"]["ekf_rmse_m"] == pytest.approx(5.0, rel=1e-3)
        assert report["overall"]["ukf_rmse_m"] == pytest.approx(0.0, abs=1e-9)

    def test_output_file_path(self, tmp_path):
        """compare() returns path to filter_comparison.json."""
        t, px, py, psi_dot = self._make_straight_trip(n=200)
        gt = _make_gt(px, py, psi_dot, t)
        ekf = _make_fused(px, py, t)
        ukf = _make_fused(px, py, t)
        _write_parquets(tmp_path, gt, ekf, ukf)
        out = compare("test", tmp_path)
        assert out.name == "filter_comparison.json"
        assert out.exists()
