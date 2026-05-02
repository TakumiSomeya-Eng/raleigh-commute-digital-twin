"""FR-6.2 — Horizontal RMSE harness comparing fused odom to soft ground truth.

Implements PRD S1 gate: exit code 4 if EKF RMSE >= 0.75 * GPS-only RMSE.

CLI:
    python -m evaluation rmse --trace day2 --filter ekf
    python -m evaluation rmse --trace day2 --filter ukf
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from evaluation.nees import compute_nees

# Exit codes (TRD §4.5)
_EXIT_OK = 0
_EXIT_S1_FAIL = 4

# S1 gate threshold
_S1_THRESHOLD = 0.75


def _log(tag: str, msg: str) -> None:
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    sys.stdout.write(f"[{ts}] [{tag}] INFO  {msg}\n")
    sys.stdout.flush()


# ---------------------------------------------------------------------------
# Interpolation helpers
# ---------------------------------------------------------------------------


def _interp_to_gt(fused: pd.DataFrame, gt: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """Interpolate fused filter output to the ground-truth time grid.

    Returns (px_interp, py_interp) arrays aligned with gt.t_s.
    """
    t_gt = gt.t_s.to_numpy(dtype=float)
    t_f = fused.t_s.to_numpy(dtype=float)

    # Clamp gt time range to fused coverage
    mask = (t_gt >= t_f[0]) & (t_gt <= t_f[-1])
    t_eval = t_gt[mask]

    px_f = np.interp(t_eval, t_f, fused.px_m.to_numpy(dtype=float))
    py_f = np.interp(t_eval, t_f, fused.py_m.to_numpy(dtype=float))
    return px_f, py_f, mask


# ---------------------------------------------------------------------------
# Core RMSE computation
# ---------------------------------------------------------------------------


def _horizontal_rmse(
    px_est: np.ndarray, py_est: np.ndarray, px_ref: np.ndarray, py_ref: np.ndarray
) -> float:
    err2 = (px_est - px_ref) ** 2 + (py_est - py_ref) ** 2
    return float(np.sqrt(np.mean(err2)))


def _per_minute_rmse(
    t_s: np.ndarray,
    px_est: np.ndarray,
    py_est: np.ndarray,
    px_ref: np.ndarray,
    py_ref: np.ndarray,
) -> list[float]:
    t_rel = t_s - t_s[0]
    n_min = int(t_rel[-1] // 60) + 1
    per_min: list[float] = []
    for m in range(n_min):
        mask = (t_rel >= m * 60) & (t_rel < (m + 1) * 60)
        if mask.sum() == 0:
            per_min.append(0.0)
            continue
        per_min.append(_horizontal_rmse(px_est[mask], py_est[mask], px_ref[mask], py_ref[mask]))
    return per_min


# ---------------------------------------------------------------------------
# Config hash (TRD §3.x)
# ---------------------------------------------------------------------------


def _config_hash(config_dir: Path) -> str:
    """SHA-256 of sorted config file contents."""
    parts: list[str] = []
    for p in sorted(config_dir.glob("*.yaml")):
        parts.append(p.read_text(encoding="utf-8"))
    combined = "\n".join(parts).encode("utf-8")
    return "sha256:" + hashlib.sha256(combined).hexdigest()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def compute_rmse(
    trace: str,
    filter_name: str,
    out_dir: Path,
    config_dir: Path | None = None,
) -> dict:
    """Compute RMSE metrics; return report dict (does NOT write to disk)."""
    gt_path = out_dir / trace / "ground_truth.parquet"
    fused_path = out_dir / trace / f"fused_{filter_name}.parquet"
    aligned_path = out_dir / trace / "aligned_100hz.parquet"

    for p in (gt_path, fused_path, aligned_path):
        if not p.exists():
            sys.stderr.write(f"ERROR: {p} not found.\n")
            sys.exit(1)

    _log("FR-6.2 rmse", f"loading ground truth from {gt_path}")
    gt = pd.read_parquet(gt_path)

    _log("FR-6.2 rmse", f"loading fused filter from {fused_path}")
    fused = pd.read_parquet(fused_path)

    _log("FR-6.2 rmse", f"loading aligned data from {aligned_path}")
    aligned = pd.read_parquet(aligned_path)

    # Interpolate fused to GT time grid
    px_f, py_f, mask = _interp_to_gt(fused, gt)
    t_gt = gt.t_s.to_numpy(dtype=float)[mask]
    px_gt = gt.px_m.to_numpy(dtype=float)[mask]
    py_gt = gt.py_m.to_numpy(dtype=float)[mask]

    overall_rmse = _horizontal_rmse(px_f, py_f, px_gt, py_gt)
    per_min = _per_minute_rmse(t_gt, px_f, py_f, px_gt, py_gt)

    # GPS-only baseline: aligned px/py interpolated to same GT grid
    t_al = aligned.t_s.to_numpy(dtype=float)
    px_gps = np.interp(t_gt, t_al, aligned.px_m.to_numpy(dtype=float))
    py_gps = np.interp(t_gt, t_al, aligned.py_m.to_numpy(dtype=float))
    gps_only_rmse = _horizontal_rmse(px_gps, py_gps, px_gt, py_gt)

    improvement_pct = 100.0 * (1.0 - overall_rmse / gps_only_rmse) if gps_only_rmse > 0 else 0.0
    s1_pass = overall_rmse < _S1_THRESHOLD * gps_only_rmse

    cfg_hash = _config_hash(config_dir) if config_dir else "N/A"

    # FR-6.3: NEES and GPS innovation statistics
    _log("FR-6.2 rmse", "computing NEES (FR-6.3)")
    nees_stats = compute_nees(fused, gt, aligned=aligned)

    return {
        "trip_id": trace,
        "filter": filter_name,
        "overall_rmse_m": round(overall_rmse, 4),
        "gps_only_rmse_m": round(gps_only_rmse, 4),
        "improvement_pct": round(improvement_pct, 2),
        "per_minute_rmse_m": [round(v, 4) for v in per_min],
        "s1_pass": s1_pass,
        "nees_mean": nees_stats["nees_mean"],
        "nees_ci_95": nees_stats["nees_ci_95"],
        "nees_consistent": nees_stats["nees_consistent"],
        "nees_dof": nees_stats["nees_dof"],
        "nees_n_samples": nees_stats["nees_n_samples"],
        "rejection_count": nees_stats["rejection_count"],
        "rejection_rate": nees_stats["rejection_rate"],
        "nees_notes": nees_stats["notes"],
        "config_hash": cfg_hash,
    }


def run_rmse(
    trace: str,
    filter_name: str,
    out_dir: Path,
    config_dir: Path | None = None,
) -> int:
    """Compute RMSE, write rmse_report.json, return exit code."""
    report = compute_rmse(trace, filter_name, out_dir, config_dir)

    out_path = out_dir / trace / f"rmse_report_{filter_name}.json"
    out_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    _log(
        "FR-6.2 rmse",
        f"overall_rmse={report['overall_rmse_m']:.3f} m  "
        f"gps_only={report['gps_only_rmse_m']:.3f} m  "
        f"improvement={report['improvement_pct']:.1f}%  "
        f"s1_pass={report['s1_pass']}",
    )
    nees_str = f"{report['nees_mean']:.3f}" if report["nees_mean"] is not None else "N/A"
    consistent_str = (
        str(report["nees_consistent"]) if report["nees_consistent"] is not None else "N/A"
    )
    _log(
        "FR-6.3 nees",
        f"nees_mean={nees_str}  "
        f"ci95={report['nees_ci_95']}  "
        f"consistent={consistent_str}  "
        f"n={report['nees_n_samples']}  "
        f"rejections={report['rejection_count']} ({report['rejection_rate']})",
    )
    if report.get("nees_notes"):
        _log("FR-6.3 nees", f"NOTE: {report['nees_notes']}")
    _log("FR-6.2 rmse", f"wrote {out_path}")

    if not report["s1_pass"]:
        sys.stderr.write(
            f"GATE FAIL: EKF RMSE {report['overall_rmse_m']:.3f} m >= "
            f"0.75 * GPS-only {report['gps_only_rmse_m']:.3f} m "
            f"({_S1_THRESHOLD * report['gps_only_rmse_m']:.3f} m threshold)\n"
        )
        return _EXIT_S1_FAIL
    return _EXIT_OK


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Compute RMSE and check PRD S1 gate")
    p.add_argument("--trace", required=True, help="trace name (e.g. day2)")
    p.add_argument("--filter", dest="filter_name", default="ekf", help="ekf or ukf")
    p.add_argument("--out-dir", type=Path, default=Path("out"), help="output root dir")
    p.add_argument("--config-dir", type=Path, default=Path("config"), help="config dir for hash")
    return p


def main(argv: list[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)
    code = run_rmse(args.trace, args.filter_name, args.out_dir, args.config_dir)
    sys.exit(code)


if __name__ == "__main__":
    main()
