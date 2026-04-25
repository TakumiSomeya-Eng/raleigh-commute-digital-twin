"""FR-2.1 — Empirical noise model fitting from calibration traces.

Fits parametric distributions per sensor channel and writes config/noise_fit.yaml.

Channel → distribution mapping (TRD §2.1):
  Gaussian  : ax/ay/az, gx/gy/gz, grav_x/y/z, mag_x/y/z_uT, speed_accuracy_mps
  Rayleigh  : horizontal_accuracy_m
  von Mises : gps_bearing_deg

See: TRD sec.2.1, FRD FR-2.1
"""

from __future__ import annotations

import datetime
import logging
from pathlib import Path
from typing import TypedDict

import numpy as np
import pandas as pd
import scipy.stats
import yaml

from data_engine.parquet_io import read_parquet

logger = logging.getLogger(__name__)

_SCHEMA_VERSION = "1.0"


class _ChannelSpec(TypedDict):
    dist: str  # "gaussian" | "rayleigh" | "von_mises"
    window: int  # moving-average detrend window (0 = no detrend)


CHANNEL_SPECS: dict[str, _ChannelSpec] = {
    "ax_mps2": {"dist": "gaussian", "window": 50},
    "ay_mps2": {"dist": "gaussian", "window": 50},
    "az_mps2": {"dist": "gaussian", "window": 50},
    "gx_rps": {"dist": "gaussian", "window": 50},
    "gy_rps": {"dist": "gaussian", "window": 50},
    "gz_rps": {"dist": "gaussian", "window": 50},
    "grav_x": {"dist": "gaussian", "window": 200},
    "grav_y": {"dist": "gaussian", "window": 200},
    "grav_z": {"dist": "gaussian", "window": 200},
    "mag_x_uT": {"dist": "gaussian", "window": 100},
    "mag_y_uT": {"dist": "gaussian", "window": 100},
    "mag_z_uT": {"dist": "gaussian", "window": 100},
    "speed_accuracy_mps": {"dist": "gaussian", "window": 0},
    "horizontal_accuracy_m": {"dist": "rayleigh", "window": 0},
    "gps_bearing_deg": {"dist": "von_mises", "window": 50},
}


class FitResult(TypedDict):
    channel: str
    dist: str
    params: dict[str, float]  # loc+scale (gaussian), scale (rayleigh), kappa+loc (von_mises)
    sigma_2: dict[str, float]  # 2-sigma half-width (2*SE) for each param
    n_samples: int


# ---------------------------------------------------------------------------
# Residual computation
# ---------------------------------------------------------------------------


def compute_residuals(series: np.ndarray, window: int) -> np.ndarray:
    """Subtract rolling-mean trend from *series*.

    Args:
        series: 1-D float array.
        window: Rolling window size.  0 = no detrending (return raw array).

    Returns:
        Residuals as a float64 1-D array with no NaNs.
    """
    if window == 0:
        return series.astype(np.float64)
    s = pd.Series(series, dtype=np.float64)
    trend = s.rolling(window, min_periods=1, center=True).mean()
    return (s - trend).to_numpy(dtype=np.float64)


def _bearing_residuals(series_deg: np.ndarray, window: int) -> np.ndarray:
    """Circular residuals (radians) from rolling circular mean.

    Converts degrees to radians, subtracts a rolling circular mean computed
    via unit-vector averaging, and returns the angular difference wrapped to
    [-π, π].
    """
    rad = np.deg2rad(series_deg.astype(np.float64))
    if window == 0:
        mu = float(scipy.stats.circmean(rad, high=np.pi, low=-np.pi))
        return np.arctan2(np.sin(rad - mu), np.cos(rad - mu))

    cos_s = pd.Series(np.cos(rad))
    sin_s = pd.Series(np.sin(rad))
    cos_m = cos_s.rolling(window, min_periods=1, center=True).mean().to_numpy()
    sin_m = sin_s.rolling(window, min_periods=1, center=True).mean().to_numpy()
    mean_angle = np.arctan2(sin_m, cos_m)
    return np.arctan2(np.sin(rad - mean_angle), np.cos(rad - mean_angle))


# ---------------------------------------------------------------------------
# Distribution fitting
# ---------------------------------------------------------------------------


def fit_channel(series: np.ndarray, channel: str) -> FitResult:
    """Fit the noise distribution for one channel.

    Args:
        series: Raw 1-D sensor values (float).
        channel: Key into CHANNEL_SPECS.

    Returns:
        FitResult with MLE parameters and 2-sigma half-widths.
    """
    spec = CHANNEL_SPECS[channel]
    dist_name = spec["dist"]
    window = spec["window"]

    if dist_name == "von_mises":
        residuals = _bearing_residuals(series, window)
    else:
        residuals = compute_residuals(series, window)

    n = len(residuals)

    params: dict[str, float]
    sigma_2: dict[str, float]

    if dist_name == "gaussian":
        loc, scale = scipy.stats.norm.fit(residuals)
        params = {"loc": float(loc), "scale": float(scale)}
        # Asymptotic SE from Fisher information: SE(mu)=s/sqrt(n), SE(s)=s/sqrt(2n)
        sigma_2 = {
            "loc": float(2.0 * scale / np.sqrt(n)),
            "scale": float(2.0 * scale / np.sqrt(2.0 * n)),
        }

    elif dist_name == "rayleigh":
        # Fix loc=0: GPS accuracy is always non-negative
        _, scale = scipy.stats.rayleigh.fit(residuals, floc=0)
        params = {"scale": float(scale)}
        # Fisher information for Rayleigh: I(s)=4n/s^2 → SE=s/(2*sqrt(n))
        sigma_2 = {"scale": float(scale / np.sqrt(n))}

    else:  # von_mises
        kappa, loc, _ = scipy.stats.vonmises.fit(residuals, fscale=1)
        params = {"kappa": float(kappa), "loc": float(loc)}
        se_kappa = kappa / np.sqrt(n) if n > 0 else 0.0
        se_loc = 1.0 / (kappa * np.sqrt(n)) if (kappa > 0 and n > 0) else 0.0
        sigma_2 = {"kappa": float(2.0 * se_kappa), "loc": float(2.0 * se_loc)}

    return FitResult(
        channel=channel,
        dist=dist_name,
        params=params,
        sigma_2=sigma_2,
        n_samples=n,
    )


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------


def compare_fits(fit_a: FitResult, fit_b: FitResult) -> bool:
    """Return True iff the key parameter's 2σ intervals do NOT overlap.

    Key parameter is ``scale`` for gaussian/rayleigh, ``kappa`` for von_mises.
    Non-overlapping intervals indicate a statistically significant difference.
    """
    key = "kappa" if fit_a["dist"] == "von_mises" else "scale"

    val_a = fit_a["params"][key]
    val_b = fit_b["params"][key]
    half_a = fit_a["sigma_2"][key]
    half_b = fit_b["sigma_2"][key]

    overlap = (val_a - half_a <= val_b + half_b) and (val_b - half_b <= val_a + half_a)
    return not overlap


# ---------------------------------------------------------------------------
# QQ plot
# ---------------------------------------------------------------------------


def plot_qq(residuals: np.ndarray, fit_result: FitResult, out_path: Path) -> None:
    """Save a QQ plot of *residuals* against the fitted distribution.

    Uses matplotlib Agg backend (headless-safe).
    """
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    dist_name = fit_result["dist"]
    params = fit_result["params"]

    if dist_name == "gaussian":
        frozen: scipy.stats.rv_continuous = scipy.stats.norm(
            loc=params["loc"], scale=params["scale"]
        )
    elif dist_name == "rayleigh":
        frozen = scipy.stats.rayleigh(scale=params["scale"])
    else:
        frozen = scipy.stats.vonmises(kappa=params["kappa"], loc=params["loc"])

    n = len(residuals)
    probs = np.linspace(0.5 / n, 1.0 - 0.5 / n, n)
    theoretical = frozen.ppf(probs)
    sample = np.sort(residuals)

    fig, ax = plt.subplots(figsize=(5, 5))
    ax.scatter(theoretical, sample, s=1, alpha=0.3)
    lims = [
        min(float(theoretical.min()), float(sample.min())),
        max(float(theoretical.max()), float(sample.max())),
    ]
    ax.plot(lims, lims, "r--", linewidth=1)
    ax.set_xlabel("Theoretical quantiles")
    ax.set_ylabel("Sample quantiles")
    ax.set_title(f"QQ: {fit_result['channel']} ({dist_name})")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=100, bbox_inches="tight")
    plt.close(fig)


# ---------------------------------------------------------------------------
# Trip-level API
# ---------------------------------------------------------------------------


def fit_trip(parquet_path: Path) -> list[FitResult]:
    """Fit all CHANNEL_SPECS channels from one aligned_100hz.parquet.

    Args:
        parquet_path: Path to an aligned_100hz.parquet file.

    Returns:
        List of FitResult, one per channel in CHANNEL_SPECS.
    """
    df = read_parquet(parquet_path)
    results: list[FitResult] = []
    for channel in CHANNEL_SPECS:
        result = fit_channel(df[channel].to_numpy(), channel)
        results.append(result)
        logger.info(
            "[FR-2.1 fit] %-25s  %s  n=%d",
            channel,
            result["dist"],
            result["n_samples"],
        )
    return results


def compare_trips(
    fits_a: list[FitResult],
    fits_b: list[FitResult],
) -> dict[str, bool]:
    """Compare two sets of FitResults channel-by-channel.

    Returns a dict mapping channel name to True if the two fits are
    significantly different (non-overlapping 2σ intervals on key parameter).
    """
    by_ch_a = {f["channel"]: f for f in fits_a}
    by_ch_b = {f["channel"]: f for f in fits_b}
    return {ch: compare_fits(by_ch_a[ch], by_ch_b[ch]) for ch in by_ch_a if ch in by_ch_b}


# ---------------------------------------------------------------------------
# YAML I/O
# ---------------------------------------------------------------------------


def write_noise_fit_yaml(
    fits: list[FitResult],
    path: Path,
    trip_id: str = "",
) -> None:
    """Write fitted parameters to *path* (overwrites any existing content).

    Each channel entry contains ``dist`` and its MLE parameters as flat
    top-level keys for easy YAML readability.
    """
    channels: dict[str, dict[str, object]] = {}
    for f in fits:
        entry: dict[str, object] = {"dist": f["dist"]}
        entry.update({k: round(v, 8) for k, v in f["params"].items()})
        channels[f["channel"]] = entry

    n = fits[0]["n_samples"] if fits else 0
    data: dict[str, object] = {
        "schema_version": _SCHEMA_VERSION,
        "generated_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "trip_id": trip_id,
        "n_samples": n,
        "channels": channels,
    }

    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        yaml.dump(data, fh, default_flow_style=False, sort_keys=False, allow_unicode=True)
    logger.info("[FR-2.1 fit] wrote %s", path)
