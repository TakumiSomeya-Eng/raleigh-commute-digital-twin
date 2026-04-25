"""Unit tests for FR-2.1 noise fitting (src/data_engine/noise_fit.py).

Test groups:
  1. compute_residuals: window=0 returns raw; window>0 mean-centers the output
  2. fit_channel (gaussian): MLE scale recovered within 5 % on synthetic data
  3. fit_channel (rayleigh): MLE scale recovered within 5 %
  4. fit_channel (von_mises): kappa recovered within 10 % on synthetic data
  5. compare_fits: non-overlapping intervals → True; overlapping → False
  6. write_noise_fit_yaml: round-trip through YAML preserves channel/dist/params
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import scipy.stats
import yaml
from data_engine.noise_fit import (
    FitResult,
    compare_fits,
    compute_residuals,
    fit_channel,
    write_noise_fit_yaml,
)

# ---------------------------------------------------------------------------
# 1. compute_residuals
# ---------------------------------------------------------------------------


def test_residuals_window_zero_returns_raw() -> None:
    data = np.array([1.0, 2.0, 3.0, 4.0])
    out = compute_residuals(data, window=0)
    np.testing.assert_array_equal(out, data)


def test_residuals_window_nonzero_near_zero_mean() -> None:
    rng = np.random.default_rng(0)
    # Slow linear trend + small noise
    t = np.arange(500, dtype=np.float64)
    data = 0.01 * t + rng.normal(0, 0.1, 500)
    residuals = compute_residuals(data, window=50)
    # After trend removal the mean should be close to 0
    assert abs(float(residuals.mean())) < 0.05


# ---------------------------------------------------------------------------
# 2. fit_channel — gaussian
# ---------------------------------------------------------------------------


def test_gaussian_fit_recovers_scale() -> None:
    rng = np.random.default_rng(42)
    data = rng.normal(loc=0.0, scale=0.5, size=5000)
    result = fit_channel(data, "ax_mps2")
    assert result["dist"] == "gaussian"
    assert math.isclose(
        result["params"]["scale"], 0.5, rel_tol=0.05
    ), f"scale {result['params']['scale']:.4f} not within 5 % of 0.5"
    assert result["n_samples"] == 5000


def test_gaussian_fit_sigma2_decreases_with_n() -> None:
    rng = np.random.default_rng(7)
    small = fit_channel(rng.normal(0, 1, 500), "ax_mps2")
    large = fit_channel(rng.normal(0, 1, 50000), "ax_mps2")
    # More samples → narrower confidence intervals
    assert small["sigma_2"]["scale"] > large["sigma_2"]["scale"]


# ---------------------------------------------------------------------------
# 3. fit_channel — rayleigh
# ---------------------------------------------------------------------------


def test_rayleigh_fit_recovers_scale() -> None:
    data = scipy.stats.rayleigh.rvs(scale=2.0, size=5000, random_state=42)
    result = fit_channel(data, "horizontal_accuracy_m")
    assert result["dist"] == "rayleigh"
    assert math.isclose(
        result["params"]["scale"], 2.0, rel_tol=0.05
    ), f"scale {result['params']['scale']:.4f} not within 5 % of 2.0"


# ---------------------------------------------------------------------------
# 4. fit_channel — von Mises
# ---------------------------------------------------------------------------


def test_von_mises_fit_recovers_kappa() -> None:
    # Sample from von Mises (radians), convert to degrees for fit_channel
    data_rad = scipy.stats.vonmises.rvs(kappa=5.0, size=10000, random_state=42)
    data_deg = np.rad2deg(data_rad)
    result = fit_channel(data_deg, "gps_bearing_deg")
    assert result["dist"] == "von_mises"
    assert math.isclose(
        result["params"]["kappa"], 5.0, rel_tol=0.10
    ), f"kappa {result['params']['kappa']:.4f} not within 10 % of 5.0"


# ---------------------------------------------------------------------------
# 5. compare_fits
# ---------------------------------------------------------------------------


def _make_gaussian_fit(channel: str, scale: float, n: int) -> FitResult:
    return FitResult(
        channel=channel,
        dist="gaussian",
        params={"loc": 0.0, "scale": scale},
        sigma_2={
            "loc": 2.0 * scale / math.sqrt(n),
            "scale": 2.0 * scale / math.sqrt(2.0 * n),
        },
        n_samples=n,
    )


def test_compare_fits_different_scales_returns_true() -> None:
    # scale 0.05 vs 1.00 with n=100_000 → intervals far apart
    fit_a = _make_gaussian_fit("ax_mps2", scale=0.05, n=100_000)
    fit_b = _make_gaussian_fit("ax_mps2", scale=1.00, n=100_000)
    assert compare_fits(fit_a, fit_b) is True


def test_compare_fits_same_scale_returns_false() -> None:
    # scale 0.50 vs 0.51 with n=100 → wide intervals, clearly overlapping
    fit_a = _make_gaussian_fit("ax_mps2", scale=0.50, n=100)
    fit_b = _make_gaussian_fit("ax_mps2", scale=0.51, n=100)
    assert compare_fits(fit_a, fit_b) is False


# ---------------------------------------------------------------------------
# 6. write_noise_fit_yaml round-trip
# ---------------------------------------------------------------------------


def test_write_noise_fit_yaml_roundtrip(tmp_path: Path) -> None:
    fit = FitResult(
        channel="ax_mps2",
        dist="gaussian",
        params={"loc": 0.001, "scale": 0.05},
        sigma_2={"loc": 0.0001, "scale": 0.00005},
        n_samples=1000,
    )
    yaml_path = tmp_path / "noise_fit.yaml"
    write_noise_fit_yaml([fit], yaml_path, trip_id="test_trip")

    with open(yaml_path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh)

    assert data["trip_id"] == "test_trip"
    assert "channels" in data
    assert "ax_mps2" in data["channels"]
    ch = data["channels"]["ax_mps2"]
    assert ch["dist"] == "gaussian"
    assert math.isclose(ch["scale"], 0.05, rel_tol=1e-6)
    assert math.isclose(ch["loc"], 0.001, rel_tol=1e-6)


def test_write_noise_fit_yaml_overwrites(tmp_path: Path) -> None:
    fit = FitResult(
        channel="gx_rps",
        dist="gaussian",
        params={"loc": 0.0, "scale": 0.01},
        sigma_2={"loc": 0.001, "scale": 0.0005},
        n_samples=500,
    )
    yaml_path = tmp_path / "noise_fit.yaml"
    # Write twice — second write must not raise and must reflect new content
    write_noise_fit_yaml([fit], yaml_path, trip_id="v1")
    fit2 = FitResult(
        channel="gx_rps",
        dist="gaussian",
        params={"loc": 0.0, "scale": 0.02},
        sigma_2={"loc": 0.001, "scale": 0.001},
        n_samples=500,
    )
    write_noise_fit_yaml([fit2], yaml_path, trip_id="v2")

    with open(yaml_path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    assert data["trip_id"] == "v2"
    assert math.isclose(data["channels"]["gx_rps"]["scale"], 0.02, rel_tol=1e-6)
