"""FR-2.2, FR-2.4 — Synthetic scenario generator.

Generates perturbed Parquet files from a base real trip by resampling
noise from fitted distributions (config/noise_fit_{base}.yaml) and
optionally injecting stress events.

Stress event types (FRD §FR-2.2):
  gps_dropout    : force gps_interpolated=True + large horizontal_accuracy
  imu_bias_step  : add a constant delta to one accelerometer axis after at_s
  mag_anomaly    : inject large random magnetometer values for a window

See: TRD sec.1.12, FRD FR-2.2, FR-2.4
"""

from __future__ import annotations

import datetime
import json
import logging
import multiprocessing
from pathlib import Path
from typing import Any, TypedDict

import numpy as np
import pandas as pd
import scipy.stats
import yaml

from data_engine.noise_fit import CHANNEL_SPECS
from data_engine.parquet_io import read_parquet, write_parquet
from data_engine.schemas import Aligned100Hz

logger = logging.getLogger(__name__)

# Axis name → accelerometer column (imu_bias_step)
_IMU_BIAS_AXES: dict[str, str] = {
    "x": "ax_mps2",
    "y": "ay_mps2",
    "z": "az_mps2",
}
# 500 µT spike ≈ 10x Earth's field — unambiguously anomalous
_MAG_ANOMALY_SCALE_UT: float = 500.0


class ScenarioResult(TypedDict):
    scenario_id: str
    seed: int
    stress_events: list[dict[str, Any]]
    parquet_path: str
    generated_at_utc: str


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _load_noise_params(yaml_path: Path) -> dict[str, dict[str, Any]]:
    """Return the ``channels`` dict from a noise_fit YAML."""
    with open(yaml_path, encoding="utf-8") as fh:
        data: dict[str, Any] = yaml.safe_load(fh)
    return data["channels"]  # type: ignore[no-any-return]


def _inject_noise(
    base_df: pd.DataFrame,
    noise_params: dict[str, dict[str, Any]],
    rng: np.random.Generator,
) -> pd.DataFrame:
    """Return a copy of *base_df* with noise resampled per fitted distribution.

    Channel treatment:
      window > 0  — preserve rolling-mean trend; replace residuals.
      window = 0  — replace channel entirely with IID samples.
    Non-fitted columns are copied unchanged.
    """
    df = base_df.copy()
    n = len(df)

    for channel, spec in CHANNEL_SPECS.items():
        if channel not in noise_params:
            continue

        p = noise_params[channel]
        dist_name: str = p["dist"]
        window: int = spec["window"]

        if dist_name == "gaussian":
            noise = rng.normal(loc=float(p["loc"]), scale=float(p["scale"]), size=n)
            if window > 0:
                trend = (
                    pd.Series(base_df[channel].to_numpy(), dtype=np.float64)
                    .rolling(window, min_periods=1, center=True)
                    .mean()
                    .to_numpy()
                )
                df[channel] = trend + noise
            else:
                df[channel] = noise

        elif dist_name == "rayleigh":
            df[channel] = rng.rayleigh(scale=float(p["scale"]), size=n)

        elif dist_name == "von_mises":
            base_rad = np.deg2rad(base_df[channel].to_numpy(dtype=np.float64))
            if window > 0:
                cos_m = (
                    pd.Series(np.cos(base_rad))
                    .rolling(window, min_periods=1, center=True)
                    .mean()
                    .to_numpy()
                )
                sin_m = (
                    pd.Series(np.sin(base_rad))
                    .rolling(window, min_periods=1, center=True)
                    .mean()
                    .to_numpy()
                )
                mean_angle = np.arctan2(sin_m, cos_m)
            else:
                mu = float(scipy.stats.circmean(base_rad, high=np.pi, low=-np.pi))
                mean_angle = np.full(n, mu)

            noise_rad = rng.vonmises(mu=float(p["loc"]), kappa=float(p["kappa"]), size=n)
            synth_rad = np.arctan2(
                np.sin(mean_angle + noise_rad),
                np.cos(mean_angle + noise_rad),
            )
            df[channel] = np.degrees(synth_rad) % 360.0

    return df


def _apply_stress(
    df: pd.DataFrame,
    stress_events: list[dict[str, Any]],
    rng: np.random.Generator,
) -> pd.DataFrame:
    """Apply stress events to *df* in declaration order."""
    t_s = df["t_s"].to_numpy()

    for event in stress_events:
        etype: str = event["type"]

        if etype == "gps_dropout":
            mask = (t_s >= float(event["start_s"])) & (t_s <= float(event["end_s"]))
            df.loc[mask, "gps_interpolated"] = True
            df.loc[mask, "horizontal_accuracy_m"] = 50.0

        elif etype == "imu_bias_step":
            col = _IMU_BIAS_AXES.get(str(event["axis"]), "ax_mps2")
            mask = t_s >= float(event["at_s"])
            df.loc[mask, col] = df.loc[mask, col] + float(event["delta"])

        elif etype == "mag_anomaly":
            start = float(event["start_s"])
            mask = (t_s >= start) & (t_s <= start + float(event["duration_s"]))
            n_rows = int(mask.sum())
            for col in ("mag_x_uT", "mag_y_uT", "mag_z_uT"):
                df.loc[mask, col] = rng.normal(0.0, _MAG_ANOMALY_SCALE_UT, n_rows)

    return df


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_scenario(
    base_parquet: Path,
    noise_yaml: Path,
    scenario_id: str,
    seed: int,
    stress_events: list[dict[str, Any]] | None,
    out_dir: Path,
    base_trip_id: str,
) -> ScenarioResult:
    """Generate one synthetic scenario from *base_parquet*.

    All randomness flows through a single ``numpy.random.Generator`` seeded
    with *seed*, so two calls with equal inputs produce identical DataFrames.

    Args:
        base_parquet: Real aligned_100hz.parquet to perturb.
        noise_yaml: noise_fit YAML for the base trip.
        scenario_id: Output subdirectory name, e.g. ``"s0001"``.
        seed: RNG seed — uniquely determines all noise and stress amplitudes.
        stress_events: List of stress-event dicts (may be empty or None).
        out_dir: Output root; parquet is written to
            ``out_dir/synthetic/{scenario_id}/aligned_100hz.parquet``.
        base_trip_id: Logical trip name written to Parquet metadata.

    Returns:
        ScenarioResult dict suitable for inclusion in scenario_manifest.json.
    """
    rng = np.random.default_rng(seed)

    base_df = read_parquet(base_parquet)
    noise_params = _load_noise_params(noise_yaml)

    df = _inject_noise(base_df, noise_params, rng)
    df = _apply_stress(df, stress_events or [], rng)

    out_path = out_dir / "synthetic" / scenario_id / "aligned_100hz.parquet"
    write_parquet(
        df,
        out_path,
        Aligned100Hz,
        trip_id=scenario_id,
        extra_metadata={"base_trip_id": base_trip_id, "seed": str(seed)},
    )

    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    logger.info("[FR-2.2 synth] %s  seed=%d  → %s", scenario_id, seed, out_path)
    return ScenarioResult(
        scenario_id=scenario_id,
        seed=seed,
        stress_events=stress_events or [],
        parquet_path=str(out_path.relative_to(out_dir)),
        generated_at_utc=now,
    )


def _mp_worker(
    args: tuple[str, str, str, int, list[dict[str, Any]], str, str],
) -> ScenarioResult:
    """Top-level worker for multiprocessing.Pool (must be picklable)."""
    base_s, noise_s, scenario_id, seed, stress_events, out_s, base_trip_id = args
    return generate_scenario(
        Path(base_s),
        Path(noise_s),
        scenario_id,
        seed,
        stress_events,
        Path(out_s),
        base_trip_id,
    )


def generate_batch(
    base_parquet: Path,
    noise_yaml: Path,
    out_dir: Path,
    base_trip_id: str,
    n: int,
    seed0: int = 0,
    workers: int = 1,
    stress_events_per_scenario: list[list[dict[str, Any]]] | None = None,
) -> list[ScenarioResult]:
    """Generate *n* synthetic scenarios from *base_parquet*.

    Args:
        base_parquet: Real aligned_100hz.parquet.
        noise_yaml: Fitted noise parameters for the base trip.
        out_dir: Output root.
        base_trip_id: Logical trip name.
        n: Number of scenarios.
        seed0: Scenario ``i`` gets seed ``seed0 + i``.
        workers: Parallel worker processes.  1 = sequential (safe for tests).
        stress_events_per_scenario: Per-scenario stress event lists.
            If None, all scenarios are generated without stress events.
    """
    if stress_events_per_scenario is None:
        stress_events_per_scenario = [[] for _ in range(n)]

    args_list: list[tuple[str, str, str, int, list[dict[str, Any]], str, str]] = [
        (
            str(base_parquet),
            str(noise_yaml),
            f"s{i + 1:04d}",
            seed0 + i,
            stress_events_per_scenario[i],
            str(out_dir),
            base_trip_id,
        )
        for i in range(n)
    ]

    if workers > 1:
        ctx = multiprocessing.get_context("spawn")
        with ctx.Pool(workers) as pool:
            results: list[ScenarioResult] = pool.map(_mp_worker, args_list)
    else:
        results = [_mp_worker(a) for a in args_list]

    return results


def write_manifest(
    results: list[ScenarioResult],
    path: Path,
    base_trip_id: str,
) -> None:
    """Append *results* to the scenario manifest at *path*.

    Creates the file with a fresh manifest if it does not exist.
    """
    if path.exists():
        with open(path, encoding="utf-8") as fh:
            manifest: dict[str, Any] = json.load(fh)
    else:
        manifest = {
            "manifest_version": 1,
            "base_trip_id": base_trip_id,
            "scenarios": [],
        }

    manifest["scenarios"].extend(list(results))

    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)
    logger.info(
        "[FR-2.2 synth] manifest → %s (%d total)",
        path,
        len(manifest["scenarios"]),
    )
