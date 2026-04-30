"""Unit tests for FR-6.3 NEES statistics (T3.3).

Known-answer tests: verify NEES values, CI bounds, consistency flag,
and GPS innovation rejection counting.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parents[2] / "src"))

from evaluation.nees import _MAX_COV_M2, _NEES_DOF, compute_nees

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_fused(
    px: float | np.ndarray,
    py: float | np.ndarray,
    cov_xx: float | np.ndarray,
    cov_yy: float | np.ndarray,
    n: int = 200,
    dt: float = 0.01,
    t0: float = 1.0e9,
) -> pd.DataFrame:
    t = np.arange(n) * dt + t0
    return pd.DataFrame(
        {
            "t_s": t,
            "px_m": np.full(n, px) if np.isscalar(px) else np.asarray(px),
            "py_m": np.full(n, py) if np.isscalar(py) else np.asarray(py),
            "v_mps": np.ones(n),
            "psi_rad": np.zeros(n),
            "psi_dot_rps": np.zeros(n),
            "cov_xx": np.full(n, cov_xx) if np.isscalar(cov_xx) else np.asarray(cov_xx),
            "cov_yy": np.full(n, cov_yy) if np.isscalar(cov_yy) else np.asarray(cov_yy),
            "cov_yaw": np.full(n, 0.01),
        }
    )


def _make_gt(
    px: float | np.ndarray,
    py: float | np.ndarray,
    n: int = 200,
    dt: float = 0.01,
    t0: float = 1.0e9,
) -> pd.DataFrame:
    t = np.arange(n) * dt + t0
    return pd.DataFrame(
        {
            "t_s": t,
            "px_m": np.full(n, px) if np.isscalar(px) else np.asarray(px),
            "py_m": np.full(n, py) if np.isscalar(py) else np.asarray(py),
            "v_mps": np.ones(n),
            "psi_rad": np.zeros(n),
            "psi_dot_rps": np.zeros(n),
        }
    )


def _make_aligned(
    px_gps: np.ndarray,
    py_gps: np.ndarray,
    hacc: float = 3.0,
    n: int = 200,
    dt: float = 0.01,
    t0: float = 1.0e9,
    gps_stride: int = 10,
) -> pd.DataFrame:
    """Aligned dataframe with GPS fixes every gps_stride rows."""
    t = np.arange(n) * dt + t0
    interp = np.ones(n, dtype=bool)
    interp[::gps_stride] = False  # real fixes at stride
    return pd.DataFrame(
        {
            "t_s": t,
            "px_m": px_gps if len(px_gps) == n else np.interp(t, t[~interp], px_gps),
            "py_m": py_gps if len(py_gps) == n else np.interp(t, t[~interp], py_gps),
            "horizontal_accuracy_m": np.full(n, hacc),
            "gps_interpolated": interp,
        }
    )


# ---------------------------------------------------------------------------
# NEES core
# ---------------------------------------------------------------------------


class TestNEESCore:
    def test_zero_error_gives_zero_nees(self):
        """Perfect estimate -> NEES = 0."""
        fused = _make_fused(5.0, 3.0, 1.0, 1.0)
        gt = _make_gt(5.0, 3.0)
        r = compute_nees(fused, gt)
        assert r["nees_mean"] == pytest.approx(0.0, abs=1e-9)

    def test_unit_error_unit_cov(self):
        """error = [1, 0], cov = [1, 1] -> NEES = 1.0 per step."""
        fused = _make_fused(1.0, 0.0, 1.0, 1.0)
        gt = _make_gt(0.0, 0.0)
        r = compute_nees(fused, gt)
        assert r["nees_mean"] == pytest.approx(1.0, abs=1e-9)

    def test_nees_scales_with_error(self):
        """NEES quadratic in error: doubling error -> 4x NEES."""
        fused1 = _make_fused(1.0, 0.0, 1.0, 1.0)
        fused2 = _make_fused(2.0, 0.0, 1.0, 1.0)
        gt = _make_gt(0.0, 0.0)
        r1 = compute_nees(fused1, gt)
        r2 = compute_nees(fused2, gt)
        assert r2["nees_mean"] == pytest.approx(4 * r1["nees_mean"], rel=1e-9)

    def test_nees_dof_is_two(self):
        fused = _make_fused(0.0, 0.0, 1.0, 1.0)
        gt = _make_gt(0.0, 0.0)
        r = compute_nees(fused, gt)
        assert r["nees_dof"] == _NEES_DOF == 2

    def test_report_keys_present(self):
        fused = _make_fused(0.0, 0.0, 1.0, 1.0)
        gt = _make_gt(0.0, 0.0)
        r = compute_nees(fused, gt)
        for key in (
            "nees_mean",
            "nees_ci_95",
            "nees_consistent",
            "nees_dof",
            "nees_n_samples",
            "rejection_count",
            "rejection_rate",
            "notes",
        ):
            assert key in r, f"missing key: {key}"


# ---------------------------------------------------------------------------
# Consistency flag
# ---------------------------------------------------------------------------


class TestConsistency:
    def test_consistent_filter(self):
        """Errors drawn from N(0, sqrt(cov)) -> mean NEES ~ DOF=2 -> consistent."""
        rng = np.random.default_rng(0)
        n = 1000
        cov = 4.0
        errors = rng.normal(0.0, np.sqrt(cov), (n, 2))
        t = np.arange(n) * 0.01 + 1.0e9
        fused = pd.DataFrame(
            {
                "t_s": t,
                "px_m": errors[:, 0],
                "py_m": errors[:, 1],
                "v_mps": np.ones(n),
                "psi_rad": np.zeros(n),
                "psi_dot_rps": np.zeros(n),
                "cov_xx": np.full(n, cov),
                "cov_yy": np.full(n, cov),
                "cov_yaw": np.full(n, 0.01),
            }
        )
        gt = pd.DataFrame(
            {
                "t_s": t,
                "px_m": np.zeros(n),
                "py_m": np.zeros(n),
                "v_mps": np.ones(n),
                "psi_rad": np.zeros(n),
                "psi_dot_rps": np.zeros(n),
            }
        )
        r = compute_nees(fused, gt)
        assert r["nees_consistent"] is True
        assert abs(r["nees_mean"] - 2.0) < 0.3  # chi2(2) mean = 2

    def test_overconfident_filter_inconsistent(self):
        """Filter cov << true error -> NEES >> DOF -> inconsistent."""
        n = 500
        t = np.arange(n) * 0.01 + 1.0e9
        fused = pd.DataFrame(
            {
                "t_s": t,
                "px_m": np.full(n, 5.0),  # 5 m error
                "py_m": np.zeros(n),
                "v_mps": np.ones(n),
                "psi_rad": np.zeros(n),
                "psi_dot_rps": np.zeros(n),
                "cov_xx": np.full(n, 0.01),  # 0.1 m std claimed
                "cov_yy": np.full(n, 0.01),
                "cov_yaw": np.full(n, 0.01),
            }
        )
        gt = pd.DataFrame(
            {
                "t_s": t,
                "px_m": np.zeros(n),
                "py_m": np.zeros(n),
                "v_mps": np.ones(n),
                "psi_rad": np.zeros(n),
                "psi_dot_rps": np.zeros(n),
            }
        )
        r = compute_nees(fused, gt)
        assert r["nees_consistent"] is False
        assert r["nees_mean"] > r["nees_ci_95"][1]
        assert r["notes"] is not None

    def test_conservative_filter_inconsistent(self):
        """Filter cov >> true error -> NEES << DOF -> inconsistent."""
        n = 500
        t = np.arange(n) * 0.01 + 1.0e9
        fused = pd.DataFrame(
            {
                "t_s": t,
                "px_m": np.full(n, 0.01),  # tiny error
                "py_m": np.zeros(n),
                "v_mps": np.ones(n),
                "psi_rad": np.zeros(n),
                "psi_dot_rps": np.zeros(n),
                "cov_xx": np.full(n, 10.0),  # huge claimed uncertainty
                "cov_yy": np.full(n, 10.0),
                "cov_yaw": np.full(n, 0.01),
            }
        )
        gt = pd.DataFrame(
            {
                "t_s": t,
                "px_m": np.zeros(n),
                "py_m": np.zeros(n),
                "v_mps": np.ones(n),
                "psi_rad": np.zeros(n),
                "psi_dot_rps": np.zeros(n),
            }
        )
        r = compute_nees(fused, gt)
        assert r["nees_consistent"] is False
        assert r["nees_mean"] < r["nees_ci_95"][0]


# ---------------------------------------------------------------------------
# Covariance threshold filtering
# ---------------------------------------------------------------------------


class TestCovarianceFiltering:
    def test_large_cov_rows_excluded(self):
        """Rows with cov > _MAX_COV_M2 are excluded from NEES."""
        n = 200
        t = np.arange(n) * 0.01 + 1.0e9
        # First 100 rows: confident (cov=1), last 100: diverged (cov=1e6)
        cov_vals = np.concatenate([np.ones(100), np.full(100, 1.0e6)])
        fused = pd.DataFrame(
            {
                "t_s": t,
                "px_m": np.full(n, 1.0),
                "py_m": np.zeros(n),
                "v_mps": np.ones(n),
                "psi_rad": np.zeros(n),
                "psi_dot_rps": np.zeros(n),
                "cov_xx": cov_vals,
                "cov_yy": cov_vals,
                "cov_yaw": np.full(n, 0.01),
            }
        )
        gt = _make_gt(0.0, 0.0)
        r = compute_nees(fused, gt)
        assert r["nees_n_samples"] == 100

    def test_all_large_cov_returns_none(self):
        """All covariances above threshold -> graceful None result."""
        n = 50
        t = np.arange(n) * 0.01 + 1.0e9
        fused = pd.DataFrame(
            {
                "t_s": t,
                "px_m": np.zeros(n),
                "py_m": np.zeros(n),
                "v_mps": np.ones(n),
                "psi_rad": np.zeros(n),
                "psi_dot_rps": np.zeros(n),
                "cov_xx": np.full(n, 1.0e9),
                "cov_yy": np.full(n, 1.0e9),
                "cov_yaw": np.full(n, 0.01),
            }
        )
        gt = _make_gt(0.0, 0.0, n=n)
        r = compute_nees(fused, gt)
        assert r["nees_mean"] is None
        assert r["nees_consistent"] is None
        assert r["notes"] is not None

    def test_max_cov_threshold_boundary(self):
        """Row exactly at _MAX_COV_M2 is included; row above is excluded."""
        n = 20
        t = np.arange(n) * 0.01 + 1.0e9
        # Alternate: at-threshold and above-threshold
        cov_vals = np.array([_MAX_COV_M2 if i % 2 == 0 else _MAX_COV_M2 + 1.0 for i in range(n)])
        fused = pd.DataFrame(
            {
                "t_s": t,
                "px_m": np.zeros(n),
                "py_m": np.zeros(n),
                "v_mps": np.ones(n),
                "psi_rad": np.zeros(n),
                "psi_dot_rps": np.zeros(n),
                "cov_xx": cov_vals,
                "cov_yy": cov_vals,
                "cov_yaw": np.full(n, 0.01),
            }
        )
        gt = _make_gt(0.0, 0.0, n=n)
        r = compute_nees(fused, gt)
        assert r["nees_n_samples"] == 10  # only the 10 at-threshold rows


# ---------------------------------------------------------------------------
# GPS innovation / rejection stats
# ---------------------------------------------------------------------------


class TestGPSRejection:
    def test_no_rejection_for_small_innovation(self):
        """GPS matches fused exactly -> zero rejections."""
        n = 200
        fused = _make_fused(0.0, 0.0, 1.0, 1.0, n=n)
        gt = _make_gt(0.0, 0.0, n=n)
        aligned = _make_aligned(
            np.zeros(n),
            np.zeros(n),
            hacc=3.0,
            n=n,
            gps_stride=10,
        )
        r = compute_nees(fused, gt, aligned=aligned)
        assert r["rejection_count"] == 0
        assert r["rejection_rate"] == pytest.approx(0.0)

    def test_large_innovation_causes_rejections(self):
        """GPS far from fused -> many rejections."""
        n = 200
        fused = _make_fused(0.0, 0.0, 1.0, 1.0, n=n)
        gt = _make_gt(0.0, 0.0, n=n)
        # GPS 1000 m away from fused estimate
        aligned = _make_aligned(
            np.full(n, 1000.0),
            np.zeros(n),
            hacc=3.0,
            n=n,
            gps_stride=10,
        )
        r = compute_nees(fused, gt, aligned=aligned)
        assert r["rejection_count"] is not None
        assert r["rejection_count"] > 0
        assert r["rejection_rate"] > 0.0

    def test_rejection_rate_in_unit_interval(self):
        """Rejection rate is always in [0, 1]."""
        rng = np.random.default_rng(7)
        n = 200
        fused = _make_fused(0.0, 0.0, 4.0, 4.0, n=n)
        gt = _make_gt(0.0, 0.0, n=n)
        noise = rng.normal(0, 5.0, n)
        aligned = _make_aligned(noise, np.zeros(n), hacc=3.0, n=n, gps_stride=5)
        r = compute_nees(fused, gt, aligned=aligned)
        assert 0.0 <= r["rejection_rate"] <= 1.0

    def test_without_aligned_rejection_is_none(self):
        """No aligned df -> rejection_count and rejection_rate are None."""
        fused = _make_fused(0.0, 0.0, 1.0, 1.0)
        gt = _make_gt(0.0, 0.0)
        r = compute_nees(fused, gt, aligned=None)
        assert r["rejection_count"] is None
        assert r["rejection_rate"] is None


# ---------------------------------------------------------------------------
# CI bounds
# ---------------------------------------------------------------------------


class TestCIBounds:
    def test_ci_bounds_tighter_with_more_samples(self):
        """More samples -> narrower CI."""
        fused_small = _make_fused(1.0, 0.0, 1.0, 1.0, n=50)
        fused_large = _make_fused(1.0, 0.0, 1.0, 1.0, n=1000)
        gt_small = _make_gt(0.0, 0.0, n=50)
        gt_large = _make_gt(0.0, 0.0, n=1000)
        r_small = compute_nees(fused_small, gt_small)
        r_large = compute_nees(fused_large, gt_large)
        width_small = r_small["nees_ci_95"][1] - r_small["nees_ci_95"][0]
        width_large = r_large["nees_ci_95"][1] - r_large["nees_ci_95"][0]
        assert width_large < width_small

    def test_ci_covers_true_mean_for_consistent_filter(self):
        """95% CI should contain DOF=2 for a well-calibrated filter."""
        rng = np.random.default_rng(99)
        n = 2000
        cov = 9.0
        errors = rng.normal(0.0, np.sqrt(cov), (n, 2))
        t = np.arange(n) * 0.01 + 1.0e9
        fused = pd.DataFrame(
            {
                "t_s": t,
                "px_m": errors[:, 0],
                "py_m": errors[:, 1],
                "v_mps": np.ones(n),
                "psi_rad": np.zeros(n),
                "psi_dot_rps": np.zeros(n),
                "cov_xx": np.full(n, cov),
                "cov_yy": np.full(n, cov),
                "cov_yaw": np.full(n, 0.01),
            }
        )
        gt = pd.DataFrame(
            {
                "t_s": t,
                "px_m": np.zeros(n),
                "py_m": np.zeros(n),
                "v_mps": np.ones(n),
                "psi_rad": np.zeros(n),
                "psi_dot_rps": np.zeros(n),
            }
        )
        r = compute_nees(fused, gt)
        # True mean NEES = 2.0; CI should contain it
        assert r["nees_ci_95"][0] < 2.0 < r["nees_ci_95"][1]
