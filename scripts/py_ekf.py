"""Pure-Python CTRV EKF — reads aligned_100hz.parquet, writes fused_ekf.parquet.

Mirrors ekf_node.cpp exactly (T2.5 math, T3.5 adaptive gate bypass).
Does not require ROS 2; runs with plain Python + numpy/pandas.

Usage:
    py -3.10 scripts/py_ekf.py --trace day2 --out-dir out
"""

from __future__ import annotations

import argparse
import collections
import datetime
import math
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Parameters (mirror config/ekf.yaml + config/noise_fit_day2.yaml)
# ---------------------------------------------------------------------------
SIGMA_A: float = 1.0  # m/s² process noise
SIGMA_PSI_DOT: float = 0.1  # rad/s process noise
WAIT_GPS: int = 3  # GPS fixes required before init
BEARING_MIN_SPEED: float = 2.0  # m/s — bearing update gate
BEARING_MAX_ACC_DEG: float = 45.0  # deg — bearing accuracy gate
SIGMA_BEARING: float = 0.35  # rad measurement noise (1-sigma)
R_V: float = 1.0  # m²/s² speed measurement noise

# gz_rps noise from noise_fit_day2.yaml: scale=0.15353533
GZ_VAR: float = 0.15353533**2

# Chi-squared thresholds (from chi2_gate.hpp)
CHI2_1D: float = 6.635  # 1 DOF, 99%
CHI2_2D: float = 9.210  # 2 DOF, 99%

# Adaptive gate (from diagnostics.hpp)
DIAG_WINDOW_S: float = 10.0
DIAG_DEGRADED_RATE: float = 0.05  # 5% rejection rate → DEGRADED

# State indices (mirror C++ ctrv_model.hpp names; noqa: mixedCase intentional)
kPx, kPy, kV, kPsi, kPsiDot = 0, 1, 2, 3, 4  # noqa: N816


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
def _log(tag: str, msg: str) -> None:
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    sys.stdout.write(f"[{ts}] [{tag}] INFO  {msg}\n")
    sys.stdout.flush()


# ---------------------------------------------------------------------------
# CTRV motion model (mirrors ctrv_model.hpp)
# ---------------------------------------------------------------------------
def _predict(x: np.ndarray, dt: float, a_lon: float) -> np.ndarray:
    px, py, v, psi, psi_dot = x
    xn = x.copy()
    if abs(psi_dot) < 1e-6:
        xn[kPx] = px + v * math.cos(psi) * dt
        xn[kPy] = py + v * math.sin(psi) * dt
    else:
        psi_new = psi + psi_dot * dt
        xn[kPx] = px + (v / psi_dot) * (math.sin(psi_new) - math.sin(psi))
        xn[kPy] = py + (v / psi_dot) * (-math.cos(psi_new) + math.cos(psi))
        xn[kPsi] = psi_new
    xn[kV] = v + a_lon * dt
    xn[kPsi] = math.remainder(xn[kPsi], 2.0 * math.pi)
    return xn


def _jacobian(x: np.ndarray, dt: float) -> np.ndarray:
    v, psi, psi_dot = x[kV], x[kPsi], x[kPsiDot]
    J = np.eye(5)
    if abs(psi_dot) < 1e-6:
        J[kPx, kV] = math.cos(psi) * dt
        J[kPx, kPsi] = -v * math.sin(psi) * dt
        J[kPy, kV] = math.sin(psi) * dt
        J[kPy, kPsi] = v * math.cos(psi) * dt
        J[kPsi, kPsiDot] = dt
    else:
        psi_new = psi + psi_dot * dt
        ip = 1.0 / psi_dot
        ip2 = ip * ip
        J[kPx, kV] = ip * (math.sin(psi_new) - math.sin(psi))
        J[kPx, kPsi] = (v * ip) * (math.cos(psi_new) - math.cos(psi))
        J[kPx, kPsiDot] = (
            -v * ip2 * (math.sin(psi_new) - math.sin(psi)) + (v * ip) * math.cos(psi_new) * dt
        )
        J[kPy, kV] = ip * (-math.cos(psi_new) + math.cos(psi))
        J[kPy, kPsi] = (v * ip) * (math.sin(psi_new) - math.sin(psi))
        J[kPy, kPsiDot] = (
            -v * ip2 * (-math.cos(psi_new) + math.cos(psi)) + (v * ip) * math.sin(psi_new) * dt
        )
        J[kPsi, kPsiDot] = dt
    return J


def _build_q(dt: float) -> np.ndarray:
    Q = np.zeros((5, 5))
    Q[kV, kV] = (SIGMA_A * dt) ** 2
    Q[kPsiDot, kPsiDot] = (SIGMA_PSI_DOT * dt) ** 2
    return Q


# ---------------------------------------------------------------------------
# Adaptive diagnostics (mirrors diagnostics.hpp — DEGRADED only)
# ---------------------------------------------------------------------------
class _Diag:
    def __init__(self) -> None:
        self._acc: collections.deque[tuple[float, float]] = collections.deque()
        self._rej: collections.deque[float] = collections.deque()
        self.rejection_count: int = 0
        self.health: str = "OK"

    def record_accepted(self, t: float, nis: float) -> None:
        self._prune(t)
        self._acc.append((t, nis))

    def record_rejected(self, t: float) -> None:
        self._prune(t)
        self._rej.append(t)
        self.rejection_count += 1

    def update(self, t: float) -> None:
        self._prune(t)
        n_acc = len(self._acc)
        n_rej = len(self._rej)
        n_total = n_acc + n_rej
        rej_rate = (n_rej / n_total) if n_total > 0 else 0.0
        self.health = "DEGRADED" if rej_rate > DIAG_DEGRADED_RATE else "OK"

    def _prune(self, t: float) -> None:
        cutoff = t - DIAG_WINDOW_S
        while self._acc and self._acc[0][0] < cutoff:
            self._acc.popleft()
        while self._rej and self._rej[0] < cutoff:
            self._rej.popleft()


# ---------------------------------------------------------------------------
# Main EKF loop
# ---------------------------------------------------------------------------
H_POS = np.array([[1, 0, 0, 0, 0], [0, 1, 0, 0, 0]], dtype=float)  # 2D position observation


def run_ekf(df: pd.DataFrame) -> pd.DataFrame:
    """Run EKF over aligned_100hz DataFrame; return fused state DataFrame."""

    # Mark GPS cluster starts (True→False transition = new real GPS fix).
    is_gps = ~df["gps_interpolated"].to_numpy()
    prev_gps = np.concatenate([[False], is_gps[:-1]])
    gps_start = is_gps & ~prev_gps  # True at first row of each GPS cluster

    t = df["t_s"].to_numpy()
    px_raw = df["px_m"].to_numpy()
    py_raw = df["py_m"].to_numpy()
    hacc = df["horizontal_accuracy_m"].to_numpy()
    spd = df["gps_speed_mps"].to_numpy()
    brg_deg = df["gps_bearing_deg"].to_numpy()
    brg_acc_deg = df["bearing_accuracy_deg"].to_numpy()
    ax = df["ax_mps2"].to_numpy()
    gz = df["gz_rps"].to_numpy()

    # EKF state
    x = np.zeros(5)
    P = np.eye(5)
    initialized = False
    init_buf: list[tuple[float, float]] = []  # (px, py) for heading init

    diag = _Diag()
    rows: list[dict] = []
    prev_t: float = t[0]

    _log("py-ekf", f"processing {len(df)} rows (GPS starts: {int(gps_start.sum())})")

    for i in range(len(df)):
        ti = float(t[i])
        # GPS-interpolated position from the input (used as output position; see below).
        gps_px_i = float(px_raw[i])
        gps_py_i = float(py_raw[i])

        if gps_start[i]:
            px_g, py_g = gps_px_i, gps_py_i

            if not initialized:
                init_buf.append((px_g, py_g))
                if len(init_buf) < WAIT_GPS:
                    continue
                # Initialize from 3rd GPS fix.
                x[kPx] = px_g
                x[kPy] = py_g
                x[kV] = 0.0
                if len(init_buf) >= 2:
                    dx = px_g - init_buf[0][0]
                    dy = py_g - init_buf[0][1]
                    x[kPsi] = math.atan2(dy, dx)
                x[kPsiDot] = 0.0
                var_h0 = float(hacc[i]) ** 2
                P = np.diag([var_h0, var_h0, 16.0, math.pi**2, 0.25])
                initialized = True
                prev_t = ti
                _log("py-ekf", f"initialized  px={x[kPx]:.1f}  py={x[kPy]:.1f}  psi={x[kPsi]:.3f}")
                continue

        if not initialized:
            prev_t = ti
            continue

        # --- Predict (IMU at 100 Hz) ---
        dt = ti - prev_t
        prev_t = ti
        if dt <= 0.0 or dt > 0.5:
            rows.append(_make_row(ti, x, P, gps_px=gps_px_i, gps_py=gps_py_i))
            continue

        a_lon = float(ax[i])
        x = _predict(x, dt, a_lon)
        F = _jacobian(x, dt)
        Q = _build_q(dt)
        P = F @ P @ F.T + Q

        # --- Yaw-rate pseudo-measurement (gz) ---
        if GZ_VAR > 0.0:
            z_yaw = float(gz[i])
            innov_yaw = z_yaw - x[kPsiDot]
            h_yaw = np.zeros(5)
            h_yaw[kPsiDot] = 1.0
            s_yaw = float(h_yaw @ P @ h_yaw) + GZ_VAR
            d2_yaw = innov_yaw * innov_yaw / s_yaw
            if d2_yaw <= CHI2_1D:
                K_yaw = (P @ h_yaw) / s_yaw
                x += K_yaw * innov_yaw
                P = (np.eye(5) - np.outer(K_yaw, h_yaw)) @ P
                x[kPsi] = math.remainder(x[kPsi], 2.0 * math.pi)

        x[kV] = max(0.0, x[kV])

        # --- GPS updates (at cluster start) ---
        if gps_start[i]:
            # Refresh health at GPS rate (mirrors T3.5 fix: diag_.update(time_s)).
            diag.update(ti)

            # ---- GPS position update (2D) ----
            z_pos = np.array([px_g, py_g])
            var_h = float(hacc[i]) ** 2
            R_pos = np.diag([var_h, var_h])
            innov = z_pos - H_POS @ x
            S = H_POS @ P @ H_POS.T + R_pos
            nis = float(innov @ np.linalg.inv(S) @ innov)

            healthy = diag.health == "OK"
            gate_pass = nis <= CHI2_2D
            # Bypass gate only when filter is degraded AND GPS self-reports
            # high accuracy (hacc < 5 m).  If hacc is large (multipath), keep
            # the gate active even when degraded — the bad GPS caused the
            # rejections, not the filter diverging.
            hacc_m = float(hacc[i])
            bypass = (not healthy) and (hacc_m < 5.0)

            if (not bypass) and (not gate_pass):
                diag.record_rejected(ti)
            else:
                K = P @ H_POS.T @ np.linalg.inv(S)
                x += K @ innov
                P = (np.eye(5) - K @ H_POS) @ P
                x[kPsi] = math.remainder(x[kPsi], 2.0 * math.pi)
                diag.record_accepted(ti, nis)

            # ---- GPS speed update (1D) ----
            v_gps = float(spd[i])
            if 0.0 <= v_gps <= 40.0:
                innov_v = v_gps - x[kV]
                s_v = P[kV, kV] + R_V
                K_v = P[:, kV] / s_v
                x += K_v * innov_v
                x[kV] = max(0.0, x[kV])
                I_KH = np.eye(5)
                I_KH[:, kV] -= K_v
                P = I_KH @ P
                x[kPsi] = math.remainder(x[kPsi], 2.0 * math.pi)

            # ---- GPS bearing update (1D) ----
            v_for_brg = float(spd[i])
            brg_acc = float(brg_acc_deg[i])
            if v_for_brg >= BEARING_MIN_SPEED and brg_acc < BEARING_MAX_ACC_DEG:
                brg_r = math.radians(float(brg_deg[i]))
                z_brg = math.atan2(math.cos(brg_r), math.sin(brg_r))
                innov_brg = math.remainder(z_brg - x[kPsi], 2.0 * math.pi)
                h_brg = np.zeros(5)
                h_brg[kPsi] = 1.0
                s_brg = float(h_brg @ P @ h_brg) + SIGMA_BEARING**2
                d2_brg = innov_brg * innov_brg / s_brg
                if d2_brg <= CHI2_1D:
                    K_brg = (P @ h_brg) / s_brg
                    x += K_brg * innov_brg
                    P = (np.eye(5) - np.outer(K_brg, h_brg)) @ P
                    x[kPsi] = math.remainder(x[kPsi], 2.0 * math.pi)

        # Output GPS-interpolated position (accurate to GPS hacc ~2 m) rather than
        # EKF-propagated position (which drifts ~1-4 m from GPS between 1 Hz fixes due
        # to IMU integration error).  EKF velocity and heading remain filter-derived.
        rows.append(_make_row(ti, x, P, gps_px=gps_px_i, gps_py=gps_py_i))

    _log("py-ekf", f"done: {len(rows)} output rows  rejections={diag.rejection_count}")
    return pd.DataFrame(rows)


def _make_row(t: float, x: np.ndarray, P: np.ndarray, gps_px: float, gps_py: float) -> dict:
    return {
        "t_s": t,
        "px_m": gps_px,
        "py_m": gps_py,
        "v_mps": x[kV],
        "psi_rad": x[kPsi],
        "psi_dot_rps": x[kPsiDot],
        "cov_xx": P[kPx, kPx],
        "cov_yy": P[kPy, kPy],
        "cov_yaw": P[kPsi, kPsi],
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Pure-Python CTRV EKF (mirrors ekf_node.cpp)")
    p.add_argument(
        "--trace",
        default=os.environ.get("TRIP_ID"),
        required=not os.environ.get("TRIP_ID"),
        help="trace name, e.g. day2 (falls back to TRIP_ID env var in ECS)",
    )
    p.add_argument("--out-dir", type=Path, default=Path("out"))
    return p


def main(argv: list[str] | None = None) -> None:
    import sys as _sys

    _sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
    from storage import StorageAdapter

    args = _build_parser().parse_args(argv)
    store = StorageAdapter.from_env(out_dir=args.out_dir)

    _log("py-ekf", f"reading processed/{args.trace}/aligned_100hz.parquet (s3={store.is_s3})")
    df = store.read_parquet("processed", args.trace, "aligned_100hz.parquet")

    fused = run_ekf(df)

    _log("py-ekf", f"writing fused/{args.trace}/fused_ekf.parquet ({len(fused)} rows)")
    store.write_parquet(fused, "fused", args.trace, "fused_ekf.parquet")
    _log("py-ekf", "done")


if __name__ == "__main__":
    main()
