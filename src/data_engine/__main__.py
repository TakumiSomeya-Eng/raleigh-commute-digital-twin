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


def _cmd_fit(args: argparse.Namespace) -> int:
    """Execute the noise-fitting pipeline (FR-2.1)."""
    from data_engine.noise_fit import fit_trip, write_noise_fit_yaml

    trace_names = [t.strip() for t in args.traces.split(",")]
    yaml_out = Path(args.yaml_out)

    for trace in trace_names:
        pq_path = Path(args.out_dir) / trace / "aligned_100hz.parquet"
        if not pq_path.exists():
            sys.stderr.write(f"[FR-2.1 fit] ERROR  not found: {pq_path}\n")
            return StageExitCode.USER_ERROR

        fits = fit_trip(pq_path)

        out = yaml_out.parent / f"{yaml_out.stem}_{trace}{yaml_out.suffix}"
        write_noise_fit_yaml(fits, out, trip_id=trace)

        now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        sys.stdout.write(
            f"[{now}] [FR-2.1 fit] INFO  " f"{len(fits)} channels fitted for {trace!r}  → {out}\n"
        )

    return StageExitCode.SUCCESS


def _cmd_synth(args: argparse.Namespace) -> int:
    """Execute the synthetic generation pipeline (FR-2.2)."""
    from data_engine.synth import generate_batch, write_manifest

    base_pq = Path(args.out_dir) / args.base / "aligned_100hz.parquet"
    if not base_pq.exists():
        sys.stderr.write(f"[FR-2.2 synth] ERROR  not found: {base_pq}\n")
        return StageExitCode.USER_ERROR

    noise_yaml = (
        Path(args.noise_yaml) if args.noise_yaml else Path("config") / f"noise_fit_{args.base}.yaml"
    )
    if not noise_yaml.exists():
        sys.stderr.write(f"[FR-2.2 synth] ERROR  not found: {noise_yaml}\n")
        return StageExitCode.USER_ERROR

    out_dir = Path(args.out_dir)
    results = generate_batch(
        base_pq,
        noise_yaml,
        out_dir,
        args.base,
        n=args.n,
        seed0=args.seed,
        workers=args.workers,
    )

    manifest_path = out_dir / "synthetic" / "scenario_manifest.json"
    write_manifest(results, manifest_path, args.base)

    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    sys.stdout.write(
        f"[{now}] [FR-2.2 synth] INFO  "
        f"{len(results)} scenarios from {args.base!r}  → {manifest_path}\n"
    )
    return StageExitCode.SUCCESS


def _cmd_ks(args: argparse.Namespace) -> int:
    """Execute the KS-test gate (FR-2.3)."""
    from data_engine.ks_test import run_ks_test, write_ks_report

    cfg_path = Path(args.config) if args.config else _DEFAULT_CONFIG
    p_threshold = 0.05
    pass_rate = 0.80
    max_comparison_n = 200
    try:
        cfg = _load_config(cfg_path)
        p_threshold = float(cfg.get("ks_gate_p_threshold", 0.05))
        pass_rate = float(cfg.get("ks_gate_pass_rate", 0.80))
        max_comparison_n = int(cfg.get("ks_max_comparison_n", 200))
    except (KeyError, FileNotFoundError):
        pass

    real_dir = Path(args.real)
    synth_dir = Path(args.synth)
    if not real_dir.exists():
        sys.stderr.write(f"[FR-2.3 ks] ERROR  not found: {real_dir}\n")
        return StageExitCode.USER_ERROR
    if not synth_dir.exists():
        sys.stderr.write(f"[FR-2.3 ks] ERROR  not found: {synth_dir}\n")
        return StageExitCode.USER_ERROR

    try:
        report = run_ks_test(
            real_dir,
            synth_dir,
            p_threshold=p_threshold,
            pass_rate_threshold=pass_rate,
            max_comparison_n=max_comparison_n,
        )
    except ValueError as exc:
        sys.stderr.write(f"[FR-2.3 ks] ERROR  {exc}\n")
        return StageExitCode.USER_ERROR

    out_path = Path(args.out) if args.out else synth_dir / "ks_report.json"
    write_ks_report(report, out_path)

    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    gate = "PASS" if report["gate_passed"] else "FAIL"
    sys.stdout.write(
        f"[{now}] [FR-2.3 ks] INFO  "
        f"pass_rate={report['overall_pass_rate']:.2f}  gate={gate}  → {out_path}\n"
    )
    return StageExitCode.SUCCESS if report["gate_passed"] else StageExitCode.GATE_FAILURE


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

    p_fit = subs.add_parser("fit", help="FR-2.1: fit noise distributions per channel")
    p_fit.add_argument(
        "--traces", required=True, help="Comma-separated trace names, e.g. day1,day2"
    )
    p_fit.add_argument("--out-dir", default="out", help="Root where aligned parquets live")
    p_fit.add_argument(
        "--yaml-out",
        default="config/noise_fit.yaml",
        help="Output YAML path template (trace name is appended before extension)",
    )

    p_synth = subs.add_parser("synth", help="FR-2.2: generate synthetic scenarios")
    p_synth.add_argument("--base", required=True, help="Base trace name, e.g. day2")
    p_synth.add_argument("--n", type=int, default=10, help="Number of scenarios")
    p_synth.add_argument("--seed", type=int, default=0, help="Seed for scenario 0")
    p_synth.add_argument("--out-dir", default="out", help="Output root")
    p_synth.add_argument(
        "--noise-yaml",
        default=None,
        help="Noise YAML (default: config/noise_fit_{base}.yaml)",
    )
    p_synth.add_argument("--workers", type=int, default=1, help="Parallel worker processes")

    p_ks = subs.add_parser("ks", help="FR-2.3: KS-test gate real vs. synthetic")
    p_ks.add_argument("--real", required=True, help="Real data directory (e.g. out/day2)")
    p_ks.add_argument("--synth", required=True, help="Synthetic data root (e.g. out/synthetic)")
    p_ks.add_argument("--out", default=None, help="Report path (default: <synth>/ks_report.json)")
    p_ks.add_argument("--config", default=None, help="Path to data_gen.yaml")

    args = parser.parse_args(argv)
    if args.command == "ingest":
        return _cmd_ingest(args)
    if args.command == "fit":
        return _cmd_fit(args)
    if args.command == "synth":
        return _cmd_synth(args)
    if args.command == "ks":
        return _cmd_ks(args)
    parser.print_help()
    return StageExitCode.USER_ERROR


if __name__ == "__main__":
    sys.exit(main())
