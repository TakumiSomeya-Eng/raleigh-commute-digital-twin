"""FR-10.7 — Weighted aggregate score combining all six components.

Output: score in [0, 100] and score.json per TRD §1.8.

Implemented in task T4.7.

Usage (CLI):
    python -m scoring score --trace day2 --filter ekf --out-dir out
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import sys
from pathlib import Path

import pandas as pd
import yaml

_CONFIG_PATH: Path = Path("config/scoring.yaml")
_IDEAL_CONFIG_PATH: Path = Path("config/ideal.yaml")
_NOTES: str = "SUGGESTED — final tipping decision is manual."

# Canonical component order (matches TRD §1.8)
_COMPONENT_NAMES = ("jerk", "harsh_brake", "lat_accel", "speed", "deviation", "lane_change")


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


def _log(msg: str) -> None:
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    sys.stdout.write(f"[{ts}] [FR-10.7 score] INFO  {msg}\n")
    sys.stdout.flush()


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------


def _load_config(config_path: Path | None) -> dict:
    path = Path(config_path) if config_path is not None else _CONFIG_PATH
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _config_hash(scoring_yaml: Path, ideal_yaml: Path | None = None) -> str:
    """sha256 over scoring.yaml + ideal.yaml concatenated bytes."""
    h = hashlib.sha256()
    for p in filter(None, [scoring_yaml, ideal_yaml]):
        if Path(p).exists():
            h.update(Path(p).read_bytes())
    return f"sha256:{h.hexdigest()}"


# ---------------------------------------------------------------------------
# Core aggregate computation
# ---------------------------------------------------------------------------


def compute_aggregate(
    raw_penalties: dict[str, float],
    weights: dict[str, float],
) -> tuple[float, dict[str, dict]]:
    """Combine six raw penalties into a weighted aggregate.

    Parameters
    ----------
    raw_penalties:
        Mapping of component name -> raw penalty in [0, 1].
    weights:
        Mapping of component name -> weight (must sum to 1.0).

    Returns
    -------
    (aggregate_raw, components_detail)

    aggregate_raw:
        Weighted sum in [0, 1].
    components_detail:
        Dict with per-component {"raw", "weight", "weighted"} sub-dicts,
        matching TRD §1.8 schema.
    """
    aggregate = 0.0
    components_detail: dict[str, dict] = {}

    for name in _COMPONENT_NAMES:
        raw = float(raw_penalties.get(name, 0.0))
        w = float(weights.get(name, 0.0))
        weighted = raw * w
        aggregate += weighted
        components_detail[name] = {
            "raw": round(raw, 6),
            "weight": round(w, 4),
            "weighted": round(weighted, 6),
        }

    return float(aggregate), components_detail


# ---------------------------------------------------------------------------
# score.json builder
# ---------------------------------------------------------------------------


def build_score_json(
    trip_id: str,
    fused_source: str,
    fused: pd.DataFrame,
    ideal: pd.DataFrame,
    reference_path: pd.DataFrame,
    config_path: Path | None = None,
    ideal_config_path: Path | None = None,
) -> dict:
    """Compute all six component penalties and return a score.json dict.

    Parameters
    ----------
    trip_id:
        Trace name (e.g. "day2").
    fused_source:
        Filter name: "ekf" or "ukf".
    fused:
        Fused filter output parquet.
    ideal:
        Ideal trajectory parquet (FR-9.5).
    reference_path:
        Reference path parquet (FR-9.3).
    config_path:
        Path to scoring.yaml.
    ideal_config_path:
        Path to ideal.yaml (used only for config_hash).

    Returns
    -------
    dict conforming to TRD §1.8 ``score.json`` schema.
    """
    from scoring.components import (
        deviation_penalty,
        harsh_brake_penalty,
        jerk_penalty,
        lane_change_penalty,
        lat_accel_penalty,
        speed_penalty,
    )
    from scoring.tip_lookup import lookup_tip

    scoring_yaml = Path(config_path) if config_path is not None else _CONFIG_PATH
    ideal_yaml = Path(ideal_config_path) if ideal_config_path is not None else _IDEAL_CONFIG_PATH

    cfg = _load_config(scoring_yaml)
    weights: dict[str, float] = cfg.get("weights", {})

    # Compute all six raw penalties
    raw: dict[str, float] = {
        "jerk": jerk_penalty(fused, ideal, scoring_yaml),
        "harsh_brake": harsh_brake_penalty(fused, scoring_yaml)[0],
        "lat_accel": lat_accel_penalty(fused, ideal, scoring_yaml),
        "speed": speed_penalty(fused, reference_path, scoring_yaml),
        "deviation": deviation_penalty(fused, reference_path, scoring_yaml),
        "lane_change": lane_change_penalty(fused, scoring_yaml, reference_path=reference_path),
    }

    aggregate_raw, components_detail = compute_aggregate(raw, weights)
    score_0_100 = round(100.0 * (1.0 - aggregate_raw), 4)

    tip = lookup_tip(score_0_100, scoring_yaml)

    timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    return {
        "trip_id": trip_id,
        "config_hash": _config_hash(scoring_yaml, ideal_yaml),
        "fused_source": fused_source,
        "components": components_detail,
        "aggregate_raw": round(aggregate_raw, 6),
        "score_0_100": score_0_100,
        "suggested_tip_band": tip["band"],
        "suggested_tip_pct": tip["tip_pct"],
        "timestamp_utc": timestamp,
        "notes": _NOTES,
    }


# ---------------------------------------------------------------------------
# Pipeline entry point
# ---------------------------------------------------------------------------


def make_score(
    trace: str,
    out_dir: Path,
    filter_name: str,
    config_path: Path,
    ideal_config_path: Path,
) -> int:
    """Compute ``score.json`` for *trace*.  Returns exit code (0 = success)."""
    fused_path = out_dir / trace / f"fused_{filter_name}.parquet"
    ideal_path = out_dir / trace / "ideal_trajectory.parquet"
    ref_path_file = out_dir / trace / "reference_path.parquet"

    for p in (fused_path, ideal_path, ref_path_file):
        if not p.exists():
            sys.stderr.write(f"ERROR: {p} not found -- run earlier pipeline stages.\n")
            return 1

    _log(f"loading fused_{filter_name} from {fused_path}")
    fused = pd.read_parquet(fused_path)
    _log(f"loading ideal_trajectory from {ideal_path}")
    ideal = pd.read_parquet(ideal_path)
    _log(f"loading reference_path from {ref_path_file}")
    ref_path = pd.read_parquet(ref_path_file)

    _log(f"computing score for trace={trace} filter={filter_name}")
    try:
        score_doc = build_score_json(
            trip_id=trace,
            fused_source=filter_name,
            fused=fused,
            ideal=ideal,
            reference_path=ref_path,
            config_path=config_path,
            ideal_config_path=ideal_config_path,
        )
    except Exception as exc:
        sys.stderr.write(f"ERROR computing score: {exc}\n")
        return 1

    out_path = out_dir / trace / "score.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(score_doc, indent=2) + "\n", encoding="utf-8")

    _log(
        f"score_0_100={score_doc['score_0_100']:.1f}  "
        f"tip={score_doc['suggested_tip_pct']}% ({score_doc['suggested_tip_band']})  "
        f"wrote {out_path}"
    )
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Compute driver score.json (FR-10.7)")
    p.add_argument("--trace", required=True, help="trace name (e.g. day2)")
    p.add_argument("--filter", dest="filter_name", default="ekf", help="ekf or ukf")
    p.add_argument("--out-dir", type=Path, default=Path("out"))
    p.add_argument("--config", type=Path, default=_CONFIG_PATH, help="scoring.yaml")
    p.add_argument("--ideal-config", type=Path, default=_IDEAL_CONFIG_PATH, help="ideal.yaml")
    return p


def main(argv: list[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)
    code = make_score(args.trace, args.out_dir, args.filter_name, args.config, args.ideal_config)
    sys.exit(code)
