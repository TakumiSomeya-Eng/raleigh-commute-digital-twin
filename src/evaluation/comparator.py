"""FR-6.4 -- EKF vs UKF per-segment RMSE comparison.

Segments the trip by curvature (|psi_dot_rps| > TURN_THRESHOLD) using the
RTS-smoothed ground truth.  Reports RMSE per segment per filter and declares
a winner or "equivalent" when |delta_RMSE| < EQUIV_THRESHOLD.

CLI:
    python -m evaluation compare --trace day2
    python -m evaluation compare --trace day2 --out-dir out

Output: out/{trace}/filter_comparison.json
"""

from __future__ import annotations

import argparse
import datetime
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# |psi_dot| > this -> "turning" segment (matches TRD §2.5 suggestion)
_TURN_THRESHOLD: float = 0.05  # rad/s

# Declare filters "equivalent" when |RMSE_ekf - RMSE_ukf| < this
_EQUIV_THRESHOLD: float = 0.3  # metres

# Minimum GT samples for a segment to be reported (not "insufficient_data")
_MIN_SEGMENT_SAMPLES: int = 10


# ---------------------------------------------------------------------------
# Logging helper
# ---------------------------------------------------------------------------


def _log(tag: str, msg: str) -> None:
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    sys.stdout.write(f"[{ts}] [{tag}] INFO  {msg}\n")
    sys.stdout.flush()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _horizontal_rmse(
    px_est: np.ndarray,
    py_est: np.ndarray,
    px_ref: np.ndarray,
    py_ref: np.ndarray,
) -> float:
    err2 = (px_est - px_ref) ** 2 + (py_est - py_ref) ** 2
    return float(np.sqrt(np.mean(err2)))


def _rmse_masked(
    px_est: np.ndarray,
    py_est: np.ndarray,
    px_ref: np.ndarray,
    py_ref: np.ndarray,
    mask: np.ndarray,
) -> float | None:
    """RMSE for rows where *mask* is True.  Returns None if too few samples."""
    if int(mask.sum()) < _MIN_SEGMENT_SAMPLES:
        return None
    return _horizontal_rmse(px_est[mask], py_est[mask], px_ref[mask], py_ref[mask])


def _winner(rmse_ekf: float | None, rmse_ukf: float | None) -> str:
    """Declare winner for a segment."""
    if rmse_ekf is None or rmse_ukf is None:
        return "insufficient_data"
    delta = rmse_ekf - rmse_ukf  # positive -> UKF is better
    if abs(delta) < _EQUIV_THRESHOLD:
        return f"equivalent (|delta| < {_EQUIV_THRESHOLD} m)"
    return "ekf" if delta < 0 else "ukf"


def _seg_dict(
    n: int,
    n_total: int,
    rmse_ekf: float | None,
    rmse_ukf: float | None,
) -> dict:
    delta = (
        None
        if (rmse_ekf is None or rmse_ukf is None)
        else round(rmse_ekf - rmse_ukf, 4)  # positive -> EKF better
    )
    return {
        "n_samples": n,
        "pct_of_trip": round(100.0 * n / n_total, 1) if n_total > 0 else 0.0,
        "ekf_rmse_m": None if rmse_ekf is None else round(rmse_ekf, 4),
        "ukf_rmse_m": None if rmse_ukf is None else round(rmse_ukf, 4),
        "delta_rmse_m": delta,
        "winner": _winner(rmse_ekf, rmse_ukf),
    }


def _per_minute_comparison(
    t_s: np.ndarray,
    px_ekf: np.ndarray,
    py_ekf: np.ndarray,
    px_ukf: np.ndarray,
    py_ukf: np.ndarray,
    px_gt: np.ndarray,
    py_gt: np.ndarray,
) -> list[dict]:
    t_rel = t_s - t_s[0]
    n_min = int(t_rel[-1] // 60) + 1
    rows = []
    for m in range(n_min):
        mask = (t_rel >= m * 60) & (t_rel < (m + 1) * 60)
        rmse_e = _rmse_masked(px_ekf, py_ekf, px_gt, py_gt, mask)
        rmse_u = _rmse_masked(px_ukf, py_ukf, px_gt, py_gt, mask)
        rows.append(
            {
                "minute": m,
                "n_samples": int(mask.sum()),
                "ekf_rmse_m": None if rmse_e is None else round(rmse_e, 4),
                "ukf_rmse_m": None if rmse_u is None else round(rmse_u, 4),
                "winner": _winner(rmse_e, rmse_u),
            }
        )
    return rows


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def compare(trace: str, out_dir: Path) -> Path:
    """Compare EKF and UKF RMSE; write filter_comparison.json; return path."""
    gt_path = out_dir / trace / "ground_truth.parquet"
    ekf_path = out_dir / trace / "fused_ekf.parquet"
    ukf_path = out_dir / trace / "fused_ukf.parquet"

    for p in (gt_path, ekf_path, ukf_path):
        if not p.exists():
            sys.stderr.write(f"ERROR: {p} not found.\n")
            sys.exit(1)

    _log("FR-6.4 compare", f"loading ground truth from {gt_path}")
    gt = pd.read_parquet(gt_path)
    _log("FR-6.4 compare", f"loading EKF from {ekf_path}")
    ekf = pd.read_parquet(ekf_path)
    _log("FR-6.4 compare", f"loading UKF from {ukf_path}")
    ukf = pd.read_parquet(ukf_path)

    # Common time window (intersection of all three)
    t_gt = gt.t_s.to_numpy(dtype=float)
    t_ekf = ekf.t_s.to_numpy(dtype=float)
    t_ukf = ukf.t_s.to_numpy(dtype=float)
    t_min = float(max(t_gt[0], t_ekf[0], t_ukf[0]))
    t_max = float(min(t_gt[-1], t_ekf[-1], t_ukf[-1]))

    mask_gt = (t_gt >= t_min) & (t_gt <= t_max)
    t_eval = t_gt[mask_gt]

    px_ekf_i = np.interp(t_eval, t_ekf, ekf.px_m.to_numpy(dtype=float))
    py_ekf_i = np.interp(t_eval, t_ekf, ekf.py_m.to_numpy(dtype=float))
    px_ukf_i = np.interp(t_eval, t_ukf, ukf.px_m.to_numpy(dtype=float))
    py_ukf_i = np.interp(t_eval, t_ukf, ukf.py_m.to_numpy(dtype=float))
    px_gt_i = gt.px_m.to_numpy(dtype=float)[mask_gt]
    py_gt_i = gt.py_m.to_numpy(dtype=float)[mask_gt]

    # Curvature labels from ground truth psi_dot (smoother, less noisy)
    psi_dot_gt = gt.psi_dot_rps.to_numpy(dtype=float)[mask_gt]
    is_turning = np.abs(psi_dot_gt) > _TURN_THRESHOLD
    is_straight = ~is_turning

    n_total = len(t_eval)
    n_straight = int(is_straight.sum())
    n_turning = int(is_turning.sum())

    _log(
        "FR-6.4 compare",
        f"n={n_total}  straight={n_straight} ({100*n_straight/n_total:.1f}%)  "
        f"turning={n_turning} ({100*n_turning/n_total:.1f}%)",
    )

    # Overall RMSE
    rmse_ekf_all = _horizontal_rmse(px_ekf_i, py_ekf_i, px_gt_i, py_gt_i)
    rmse_ukf_all = _horizontal_rmse(px_ukf_i, py_ukf_i, px_gt_i, py_gt_i)

    # Segment RMSE
    rmse_ekf_str = _rmse_masked(px_ekf_i, py_ekf_i, px_gt_i, py_gt_i, is_straight)
    rmse_ukf_str = _rmse_masked(px_ukf_i, py_ukf_i, px_gt_i, py_gt_i, is_straight)
    rmse_ekf_trn = _rmse_masked(px_ekf_i, py_ekf_i, px_gt_i, py_gt_i, is_turning)
    rmse_ukf_trn = _rmse_masked(px_ukf_i, py_ukf_i, px_gt_i, py_gt_i, is_turning)

    per_min = _per_minute_comparison(
        t_eval, px_ekf_i, py_ekf_i, px_ukf_i, py_ukf_i, px_gt_i, py_gt_i
    )

    report = {
        "trip_id": trace,
        "turn_threshold_rps": _TURN_THRESHOLD,
        "equiv_threshold_m": _EQUIV_THRESHOLD,
        "overall": _seg_dict(n_total, n_total, rmse_ekf_all, rmse_ukf_all),
        "by_curvature": {
            "straight": _seg_dict(n_straight, n_total, rmse_ekf_str, rmse_ukf_str),
            "turning": _seg_dict(n_turning, n_total, rmse_ekf_trn, rmse_ukf_trn),
        },
        "per_minute": per_min,
    }

    out_path = out_dir / trace / "filter_comparison.json"
    out_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    _log(
        "FR-6.4 compare",
        f"overall  EKF={rmse_ekf_all:.1f} m  UKF={rmse_ukf_all:.1f} m  "
        f"winner={report['overall']['winner']}",
    )
    _log(
        "FR-6.4 compare",
        f"straight EKF={rmse_ekf_str:.1f} m  UKF={rmse_ukf_str:.1f} m  "
        f"winner={report['by_curvature']['straight']['winner']}",
    )
    trn_e_str = "N/A" if rmse_ekf_trn is None else f"{rmse_ekf_trn:.1f} m"
    trn_u_str = "N/A" if rmse_ukf_trn is None else f"{rmse_ukf_trn:.1f} m"
    _log(
        "FR-6.4 compare",
        f"turning  EKF={trn_e_str}  UKF={trn_u_str}  "
        f"winner={report['by_curvature']['turning']['winner']}",
    )
    _log("FR-6.4 compare", f"wrote {out_path}")
    return out_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Compare EKF vs UKF RMSE per segment")
    p.add_argument("--trace", required=True, help="trace name (e.g. day2)")
    p.add_argument("--out-dir", type=Path, default=Path("out"), help="output root dir")
    return p


def main(argv: list[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)
    compare(args.trace, args.out_dir)


if __name__ == "__main__":
    main()
