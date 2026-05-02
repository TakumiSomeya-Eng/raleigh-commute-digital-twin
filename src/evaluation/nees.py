"""FR-6.3 -- NEES and innovation statistics.

Post-hoc NEES on the 2-DOF position sub-state.

The fused_*.parquet schema stores three diagonal covariance entries:
  cov_xx, cov_yy, cov_yaw.
Full 5x5 covariance is not recorded, so NEES is restricted to the
2D position sub-state:

  NEES_i = (dpx_i)^2 / cov_xx_i + (dpy_i)^2 / cov_yy_i  ~  chi2(2)

For N independent samples the sum follows chi2(2*N), giving a 95% CI
for the sample mean NEES.

Consistency interpretation (DOF = 2):
  - Mean NEES << 2  ->  filter over-conservative (P too large)
  - Mean NEES ~  2  ->  filter well-calibrated
  - Mean NEES >> 2  ->  filter over-confident (P too small)

GPS innovation statistics (optional, requires aligned_100hz.parquet):
  For each real GPS fix compute the 2D innovation between GPS measurement
  and the fused estimate at that timestamp.  Fixes with Mahalanobis
  distance > chi2(0.99, 2) = 9.21 would have been (or were) rejected
  by the chi2 gate.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.stats import chi2 as _chi2

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_NEES_DOF = 2  # position-only sub-state (px, py)

# Only use timesteps where the filter is "GPS-anchored" (sigma < 5 m).
# Rows with larger covariance correspond to GPS-outage / diverged segments
# where the NEES is dominated by covariance inflation rather than filter quality.
_MAX_COV_M2 = 25.0  # 5 m standard deviation

# chi2(0.99, 2) threshold for GPS chi2 gate (TRD S2.1)
_CHI2_GATE_99 = float(_chi2.ppf(0.99, 2))


# ---------------------------------------------------------------------------
# NEES computation
# ---------------------------------------------------------------------------


def compute_nees(
    fused: pd.DataFrame,
    gt: pd.DataFrame,
    aligned: pd.DataFrame | None = None,
) -> dict:
    """Compute NEES and (optionally) GPS innovation statistics.

    Parameters
    ----------
    fused:
        Filter output (fused_*.parquet).  Required columns:
        t_s, px_m, py_m, cov_xx, cov_yy.
    gt:
        Ground-truth trajectory (ground_truth.parquet).  Required columns:
        t_s, px_m, py_m.
    aligned:
        Raw 100-Hz aligned data (aligned_100hz.parquet) for GPS innovation
        statistics.  Optional.  Required columns: t_s, px_m, py_m,
        horizontal_accuracy_m, gps_interpolated.

    Returns
    -------
    dict with keys:
        nees_mean, nees_ci_95, nees_consistent, nees_dof,
        nees_n_samples, rejection_count, rejection_rate, notes
    """
    t_f = fused.t_s.to_numpy(dtype=float)
    t_gt = gt.t_s.to_numpy(dtype=float)

    # Clamp GT time range to fused coverage
    mask_gt = (t_gt >= t_f[0]) & (t_gt <= t_f[-1])
    t_eval = t_gt[mask_gt]

    px_f = np.interp(t_eval, t_f, fused.px_m.to_numpy(dtype=float))
    py_f = np.interp(t_eval, t_f, fused.py_m.to_numpy(dtype=float))
    cov_xx = np.interp(t_eval, t_f, fused.cov_xx.to_numpy(dtype=float))
    cov_yy = np.interp(t_eval, t_f, fused.cov_yy.to_numpy(dtype=float))
    px_gt = gt.px_m.to_numpy(dtype=float)[mask_gt]
    py_gt = gt.py_m.to_numpy(dtype=float)[mask_gt]

    # Keep only "confident" timesteps (GPS-anchored, not diverged)
    confident = (cov_xx > 0.0) & (cov_yy > 0.0) & (cov_xx <= _MAX_COV_M2) & (cov_yy <= _MAX_COV_M2)
    n_confident = int(confident.sum())

    if n_confident < 10:
        return {
            "nees_mean": None,
            "nees_ci_95": [None, None],
            "nees_consistent": None,
            "nees_dof": _NEES_DOF,
            "nees_n_samples": n_confident,
            "rejection_count": None,
            "rejection_rate": None,
            "notes": (
                f"Too few GPS-anchored samples for NEES (n={n_confident}); "
                "filter may have diverged."
            ),
        }

    dpx = px_f[confident] - px_gt[confident]
    dpy = py_f[confident] - py_gt[confident]
    eps = dpx**2 / cov_xx[confident] + dpy**2 / cov_yy[confident]

    nees_mean = float(np.mean(eps))

    # 95% CI for the sample mean: sum(eps) ~ chi2(DOF * N)
    nu_n = _NEES_DOF * n_confident
    ci_lo = float(_chi2.ppf(0.025, nu_n) / n_confident)
    ci_hi = float(_chi2.ppf(0.975, nu_n) / n_confident)
    consistent = bool(ci_lo <= nees_mean <= ci_hi)

    notes = None
    if not consistent:
        if nees_mean > ci_hi:
            notes = (
                f"NEES above 95% CI ({nees_mean:.2f} > {ci_hi:.2f}) -- "
                "Q or R possibly under-estimated (over-confident filter)."
            )
        else:
            notes = (
                f"NEES below 95% CI ({nees_mean:.2f} < {ci_lo:.2f}) -- "
                "Q or R possibly over-estimated (conservative filter)."
            )

    # GPS innovation statistics (optional)
    rejection_count: int | None = None
    rejection_rate: float | None = None
    if aligned is not None:
        rejection_count, rejection_rate = _gps_rejection_stats(fused, aligned)

    return {
        "nees_mean": round(nees_mean, 4),
        "nees_ci_95": [round(ci_lo, 4), round(ci_hi, 4)],
        "nees_consistent": consistent,
        "nees_dof": _NEES_DOF,
        "nees_n_samples": n_confident,
        "rejection_count": rejection_count,
        "rejection_rate": (None if rejection_rate is None else round(rejection_rate, 4)),
        "notes": notes,
    }


# ---------------------------------------------------------------------------
# GPS innovation statistics
# ---------------------------------------------------------------------------


def _gps_rejection_stats(
    fused: pd.DataFrame,
    aligned: pd.DataFrame,
) -> tuple[int, float]:
    """Count GPS fixes whose innovation exceeds the chi2(0.99, 2) gate.

    Uses the interpolated fused covariance at each GPS fix time to form
    S_diag = [cov_xx + R_gps, cov_yy + R_gps], then evaluates the
    diagonal-approximation Mahalanobis distance of the GPS innovation.

    Returns (rejection_count, rejection_rate).
    """
    t_f = fused.t_s.to_numpy(dtype=float)

    # Real GPS fixes only
    gps_mask = ~aligned.gps_interpolated.to_numpy()
    if gps_mask.sum() == 0:
        return 0, 0.0

    t_gps = aligned.t_s.to_numpy(dtype=float)[gps_mask]
    # Clamp to fused coverage
    in_range = (t_gps >= t_f[0]) & (t_gps <= t_f[-1])
    t_gps = t_gps[in_range]
    if len(t_gps) == 0:
        return 0, 0.0

    px_gps = aligned.px_m.to_numpy(dtype=float)[gps_mask][in_range]
    py_gps = aligned.py_m.to_numpy(dtype=float)[gps_mask][in_range]
    hacc = np.maximum(
        aligned.horizontal_accuracy_m.to_numpy(dtype=float)[gps_mask][in_range],
        0.5,
    )
    r_gps = hacc**2  # diagonal R element (same for x and y)

    px_f_at_gps = np.interp(t_gps, t_f, fused.px_m.to_numpy(dtype=float))
    py_f_at_gps = np.interp(t_gps, t_f, fused.py_m.to_numpy(dtype=float))
    cov_xx_at = np.interp(t_gps, t_f, fused.cov_xx.to_numpy(dtype=float))
    cov_yy_at = np.interp(t_gps, t_f, fused.cov_yy.to_numpy(dtype=float))

    s_xx = cov_xx_at + r_gps
    s_yy = cov_yy_at + r_gps

    innov_x = px_gps - px_f_at_gps
    innov_y = py_gps - py_f_at_gps
    mahal2 = innov_x**2 / s_xx + innov_y**2 / s_yy

    n_total = len(t_gps)
    n_rejected = int((mahal2 > _CHI2_GATE_99).sum())
    rate = float(n_rejected / n_total) if n_total > 0 else 0.0
    return n_rejected, rate
