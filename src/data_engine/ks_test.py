"""FR-2.3 — Two-sample KS-test gate between real and synthetic data.

Runs scipy.stats.ks_2samp per sensor channel between pooled real-trip samples
and pooled synthetic-scenario samples.  Exits 0 iff the fraction of channels
with p > p_threshold meets the configured pass-rate gate.

Report schema: TRD §1.9  (ks_report.json)

See: TRD sec.1.9, FRD FR-2.3
"""

from __future__ import annotations

import datetime
import json
import logging
from pathlib import Path
from typing import Any

import pandas as pd
import scipy.stats

from data_engine.noise_fit import CHANNEL_SPECS
from data_engine.parquet_io import read_parquet

logger = logging.getLogger(__name__)

# Channels tested by default — the 15 fitted channels from CHANNEL_SPECS.
DEFAULT_CHANNELS: list[str] = list(CHANNEL_SPECS.keys())


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _find_parquets(directory: Path) -> list[Path]:
    """Recursively discover all ``aligned_100hz.parquet`` files under *directory*."""
    return sorted(directory.rglob("aligned_100hz.parquet"))


def _load_pool(paths: list[Path]) -> pd.DataFrame:
    """Concatenate multiple Parquet files into a single DataFrame."""
    if not paths:
        raise ValueError("No aligned_100hz.parquet files found")
    return pd.concat([read_parquet(p) for p in paths], ignore_index=True)


def _ks_per_channel(
    real_df: pd.DataFrame,
    synth_df: pd.DataFrame,
    channels: list[str],
    p_threshold: float,
) -> dict[str, dict[str, Any]]:
    """Run two-sample KS test for each channel.

    Returns a dict mapping channel name to ``{"p_value": float, "pass": bool}``.
    """
    results: dict[str, dict[str, Any]] = {}
    for ch in channels:
        if ch not in real_df.columns or ch not in synth_df.columns:
            logger.warning("[FR-2.3 ks] channel %r missing from data — skipped", ch)
            continue
        _, pval = scipy.stats.ks_2samp(
            real_df[ch].to_numpy(),
            synth_df[ch].to_numpy(),
        )
        results[ch] = {"p_value": round(float(pval), 6), "pass": bool(pval > p_threshold)}
    return results


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_ks_test(
    real_dir: Path,
    synth_dir: Path,
    channels: list[str] | None = None,
    p_threshold: float = 0.05,
    pass_rate_threshold: float = 0.80,
) -> dict[str, Any]:
    """Run the KS-test gate and return a report dict matching TRD §1.9.

    Args:
        real_dir: Root directory containing real aligned_100hz.parquet files.
        synth_dir: Root directory containing synthetic parquet files.
        channels: Channels to test.  Defaults to all 15 CHANNEL_SPECS keys.
        p_threshold: Per-channel p-value threshold (default 0.05).
        pass_rate_threshold: Fraction of channels that must pass (default 0.80).

    Returns:
        Report dict with keys ``channels``, ``overall_pass_rate``,
        ``gate_threshold``, ``gate_passed``, plus diagnostic metadata.
    """
    channels = channels or DEFAULT_CHANNELS

    real_paths = _find_parquets(real_dir)
    synth_paths = _find_parquets(synth_dir)

    logger.info(
        "[FR-2.3 ks] real=%d files, synth=%d files",
        len(real_paths),
        len(synth_paths),
    )

    real_df = _load_pool(real_paths)
    synth_df = _load_pool(synth_paths)

    channel_results = _ks_per_channel(real_df, synth_df, channels, p_threshold)

    n_tested = len(channel_results)
    n_pass = sum(1 for v in channel_results.values() if v["pass"])
    pass_rate = n_pass / n_tested if n_tested > 0 else 0.0
    gate_passed = pass_rate >= pass_rate_threshold

    report: dict[str, Any] = {
        "channels": channel_results,
        "overall_pass_rate": round(pass_rate, 4),
        "gate_threshold": pass_rate_threshold,
        "gate_passed": gate_passed,
        "p_threshold": p_threshold,
        "n_real_samples": len(real_df),
        "n_synth_samples": len(synth_df),
        "generated_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }

    status = "PASS" if gate_passed else "FAIL"
    logger.info(
        "[FR-2.3 ks] %s  pass_rate=%.2f  (%d/%d channels)",
        status,
        pass_rate,
        n_pass,
        n_tested,
    )
    return report


def write_ks_report(report: dict[str, Any], path: Path) -> None:
    """Write *report* to *path* as indented JSON (overwrites if present)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)
    logger.info("[FR-2.3 ks] report → %s", path)
