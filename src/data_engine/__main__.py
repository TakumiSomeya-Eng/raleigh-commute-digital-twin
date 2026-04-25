"""CLI entry point for data_engine.

Subcommands:
  ingest  FR-1.1–1.4: parse Sensor Logger CSVs → aligned_100hz.parquet
  fit     FR-2.1:      noise fitting             (T1.4, not yet implemented)
  synth   FR-2.2/2.4:  synthetic generation      (T1.5, not yet implemented)
  ks      FR-2.3:      KS-test gate              (T1.6, not yet implemented)

Usage:
    python -m data_engine ingest --trace day2 --data-dir ./Data --out-dir ./out
    python -m data_engine fit    --traces day1,day2
    python -m data_engine synth  --base day2 --n 10
    python -m data_engine ks     --real out/day2 --synth out/synthetic
"""

from __future__ import annotations

import argparse
import datetime
import logging
import sys
from pathlib import Path

import yaml

from data_engine.errors import MissingRequiredChannelError, SchemaValidationError, StageExitCode
from data_engine.ingest import parse_and_align
from data_engine.parquet_io import write_parquet
from data_engine.schemas import Aligned100Hz

logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
logger = logging.getLogger(__name__)

_DEFAULT_CONFIG = Path(__file__).parent.parent.parent / "config" / "data_gen.yaml"


def _load_config(path: Path) -> dict:  # type: ignore[type-arg]
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)  # type: ignore[no-any-return]


def _cmd_ingest(args: argparse.Namespace) -> int:
    """Execute the ingest pipeline (FR-1.1 through FR-1.5)."""
    cfg_path = Path(args.config) if args.config else _DEFAULT_CONFIG
    out_path = Path(args.out_dir) / args.trace / "aligned_100hz.parquet"

    if args.dry_run:
        sys.stdout.write(f"[dry-run] Would write: {out_path}\n")
        return StageExitCode.SUCCESS

    try:
        cfg = _load_config(cfg_path)
        lat0 = float(cfg["enu_anchor"]["lat0_deg"])
        lon0 = float(cfg["enu_anchor"]["lon0_deg"])
        warmup_s = float(cfg.get("warmup_s", 0.5))
        target_hz = float(cfg.get("target_rate_hz", 100.0))
    except (KeyError, FileNotFoundError) as exc:
        sys.stderr.write(f"Config error: {exc}\n")
        return StageExitCode.USER_ERROR

    try:
        df = parse_and_align(
            Path(args.data_dir),
            lat0_deg=lat0,
            lon0_deg=lon0,
            target_hz=target_hz,
            warmup_s=warmup_s,
        )
    except MissingRequiredChannelError as exc:
        sys.stderr.write(f"[FR-1.1 ingest] ERROR  {exc}\n")
        return StageExitCode.USER_ERROR

    try:
        write_parquet(df, out_path, Aligned100Hz, trip_id=args.trace)
    except SchemaValidationError as exc:
        sys.stderr.write(f"[FR-1.4 ingest] ERROR  {exc}\n")
        return StageExitCode.DATA_ERROR

    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    sys.stdout.write(
        f"[{now}] [FR-1.5 ingest] INFO  "
        f"{len(df)} rows, {float(df['t_s'].iloc[-1]):.1f} s, "
        f"mean horiz-acc {float(df['horizontal_accuracy_m'].mean()):.1f} m  "
        f"→ {out_path}\n"
    )
    return StageExitCode.SUCCESS


def _cmd_stub(name: str) -> int:
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    sys.stdout.write(f"[{now}] [data_engine {name}] INFO  Not yet implemented\n")
    return StageExitCode.SUCCESS


def main(argv: list[str] | None = None) -> int:
    """Entry point for ``python -m data_engine``."""
    parser = argparse.ArgumentParser(prog="data_engine", description=__doc__)
    subs = parser.add_subparsers(dest="command")

    p_in = subs.add_parser("ingest", help="FR-1: CSV → aligned_100hz.parquet")
    p_in.add_argument("--trace", required=True, help="Trip name, e.g. day2")
    p_in.add_argument("--data-dir", required=True, help="Sensor Logger CSV directory")
    p_in.add_argument("--out-dir", default="out", help="Output root (default: out)")
    p_in.add_argument("--config", default=None, help="Path to data_gen.yaml")
    p_in.add_argument("--dry-run", action="store_true", help="Print output path, do not write")

    for cmd in ("fit", "synth", "ks"):
        subs.add_parser(cmd, help="Not yet implemented")

    args = parser.parse_args(argv)
    if args.command == "ingest":
        return _cmd_ingest(args)
    if args.command in ("fit", "synth", "ks"):
        return _cmd_stub(args.command)
    parser.print_help()
    return StageExitCode.USER_ERROR


if __name__ == "__main__":
    sys.exit(main())
