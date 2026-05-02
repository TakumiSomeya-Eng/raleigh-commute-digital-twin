"""FR-6.1 — Rauch–Tung–Striebel offline smoother for soft ground-truth generation.

Batch, offline — allowed to look ahead over the full trip.
State: [px_m, py_m, v_mps, psi_rad, psi_dot_rps]  (CTRV, n=5)

CLI:
    python -m evaluation smooth --trace day2
    python -m evaluation smooth --trace day2 --out-dir out
"""

from __future__ import annotations

import argparse
import datetime
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# CTRV model helpers (pure Python / numpy)
# ---------------------------------------------------------------------------

_N = 5  # state dimension

# Process noise parameters (match ekf.yaml for a fair comparison baseline)
_SIGMA_A = 1.0  # m/s^2
_SIGMA_PSI_DOT = 0.1  # rad/s

# Gyro measurement noise std (rad/s) — from noise_fit results
_SIGMA_GYRO = 0.05


def _ctrv_predict(x: np.ndarray, dt: float) -> np.ndarray:
    """Propagate CTRV state by *dt* seconds."""
    px, py, v, psi, psi_dot = x
    if abs(psi_dot) > 1e-6:
        s = v / psi_dot
        dx = s * (math.sin(psi + psi_dot * dt) - math.sin(psi))
        dy = s * (-math.cos(psi + psi_dot * dt) + math.cos(psi))
    else:
        dx = v * math.cos(psi) * dt
        dy = v * math.sin(psi) * dt
    return np.array([px + dx, py + dy, v, psi + psi_dot * dt, psi_dot])


def _ctrv_jacobian(x: np.ndarray, dt: float) -> np.ndarray:
    """Linearized transition Jacobian F = d(f)/d(x)."""
    _, _, v, psi, psi_dot = x
    F = np.eye(_N)
    if abs(psi_dot) > 1e-6:
        s = v / psi_dot
        sp = psi + psi_dot * dt
        F[0, 2] = (math.sin(sp) - math.sin(psi)) / psi_dot
        F[0, 3] = s * (math.cos(sp) - math.cos(psi))
        F[0, 4] = v * math.cos(sp) * dt / psi_dot - v * (math.sin(sp) - math.sin(psi)) / (
            psi_dot**2
        )
        F[1, 2] = (-math.cos(sp) + math.cos(psi)) / psi_dot
        F[1, 3] = s * (math.sin(sp) - math.sin(psi))
        F[1, 4] = v * math.sin(sp) * dt / psi_dot - v * (-math.cos(sp) + math.cos(psi)) / (
            psi_dot**2
        )
    else:
        F[0, 2] = math.cos(psi) * dt
        F[0, 3] = -v * math.sin(psi) * dt
        F[1, 2] = math.sin(psi) * dt
        F[1, 3] = v * math.cos(psi) * dt
    F[3, 4] = dt
    return F


def _process_noise(dt: float) -> np.ndarray:
    """Discrete process noise Q via van Loan method (linear approximation)."""
    G = np.zeros((_N, 2))
    G[2, 0] = 1.0  # accel -> v
    G[4, 1] = 1.0  # psi_ddot -> psi_dot
    Qc = np.diag([_SIGMA_A**2, _SIGMA_PSI_DOT**2])
    return G @ Qc @ G.T * dt


# ---------------------------------------------------------------------------
# Forward EKF pass
# ---------------------------------------------------------------------------


def _forward(
    df: pd.DataFrame,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    T = len(df)
    xs = np.zeros((T, _N))
    Ps = np.zeros((T, _N, _N))
    xp = np.zeros((T, _N))
    Pp = np.zeros((T, _N, _N))
    Fs = np.zeros((T, _N, _N))

    # Initial state from first real GPS fix
    first_gps = int(df[~df.gps_interpolated].index[0])
    row0 = df.loc[first_gps]
    psi0 = math.radians(float(row0.gps_bearing_deg)) if float(row0.gps_speed_mps) > 2.0 else 0.0
    x = np.array([float(row0.px_m), float(row0.py_m), float(row0.gps_speed_mps), psi0, 0.0])
    P = np.diag([50.0**2, 50.0**2, 5.0**2, math.pi**2, 0.5**2])

    for i in range(first_gps + 1):
        xp[i] = x.copy()
        Pp[i] = P.copy()
        xs[i] = x.copy()
        Ps[i] = P.copy()
        Fs[i] = np.eye(_N)

    t_arr = df.t_s.to_numpy(dtype=float)
    px_arr = df.px_m.to_numpy(dtype=float)
    py_arr = df.py_m.to_numpy(dtype=float)
    hacc_arr = df.horizontal_accuracy_m.to_numpy(dtype=float)
    v_gps_arr = df.gps_speed_mps.to_numpy(dtype=float)
    gz_arr = df.gz_rps.to_numpy(dtype=float)
    interp_arr = df.gps_interpolated.to_numpy()

    H_gps = np.zeros((2, _N))
    H_gps[0, 0] = 1.0
    H_gps[1, 1] = 1.0

    H_v = np.zeros((1, _N))
    H_v[0, 2] = 1.0

    H_psi = np.zeros((1, _N))
    H_psi[0, 4] = 1.0
    R_psi = np.array([[_SIGMA_GYRO**2]])

    I5 = np.eye(_N)

    for i in range(first_gps + 1, T):
        dt = float(t_arr[i] - t_arr[i - 1])
        if dt <= 0.0 or dt > 1.0:
            dt = 0.01

        # Predict
        F = _ctrv_jacobian(x, dt)
        x = _ctrv_predict(x, dt)
        P = F @ P @ F.T + _process_noise(dt)
        xp[i] = x.copy()
        Pp[i] = P.copy()
        Fs[i] = F

        # GPS position update (real fixes only)
        if not interp_arr[i]:
            hacc = max(float(hacc_arr[i]), 0.5)
            R_gps = np.diag([hacc**2, hacc**2])
            S = H_gps @ P @ H_gps.T + R_gps
            K = P @ H_gps.T @ np.linalg.inv(S)
            innov = np.array([px_arr[i], py_arr[i]]) - H_gps @ x
            x = x + K @ innov
            P = (I5 - K @ H_gps) @ P

            # GPS speed update when moving
            if v_gps_arr[i] > 2.0:
                R_v = np.array([[2.0**2]])
                S_v = H_v @ P @ H_v.T + R_v
                K_v = P @ H_v.T @ np.linalg.inv(S_v)
                x = x + (K_v * (v_gps_arr[i] - x[2])).flatten()
                P = (I5 - K_v @ H_v) @ P

        # Yaw-rate update (gyroscope, every tick)
        S_psi = H_psi @ P @ H_psi.T + R_psi
        K_psi = P @ H_psi.T @ np.linalg.inv(S_psi)
        x = x + (K_psi * (gz_arr[i] - x[4])).flatten()
        P = (I5 - K_psi @ H_psi) @ P

        x[3] = (x[3] + math.pi) % (2 * math.pi) - math.pi

        xs[i] = x.copy()
        Ps[i] = P.copy()

    return xs, Ps, xp, Pp, Fs


# ---------------------------------------------------------------------------
# Backward RTS pass
# ---------------------------------------------------------------------------


def _backward(
    xs: np.ndarray,
    Ps: np.ndarray,
    xp: np.ndarray,
    Pp: np.ndarray,
    Fs: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    T = len(xs)
    x_s = xs.copy()
    P_s = Ps.copy()

    for i in range(T - 2, -1, -1):
        Pp_next = Pp[i + 1]
        try:
            Pp_next_inv = np.linalg.inv(Pp_next)
        except np.linalg.LinAlgError:
            continue
        G = Ps[i] @ Fs[i + 1].T @ Pp_next_inv
        x_s[i] = xs[i] + G @ (x_s[i + 1] - xp[i + 1])
        P_s[i] = Ps[i] + G @ (P_s[i + 1] - Pp_next) @ G.T
        x_s[i, 3] = (x_s[i, 3] + math.pi) % (2 * math.pi) - math.pi

    return x_s, P_s


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def smooth(trace: str, out_dir: Path) -> Path:
    """Run RTS smoother on *trace*; write ground_truth.parquet; return path."""
    from data_engine.parquet_io import write_parquet
    from data_engine.schemas import GroundTruth

    in_path = out_dir / trace / "aligned_100hz.parquet"
    if not in_path.exists():
        sys.stderr.write(f"ERROR: {in_path} not found — run `make data TRACE={trace}` first.\n")
        sys.exit(1)

    _log("FR-6.1 smooth", f"loading {in_path}")
    df = pd.read_parquet(in_path)

    _log("FR-6.1 smooth", f"forward EKF pass ({len(df)} rows)")
    xs, Ps, xp, Pp, Fs = _forward(df)

    _log("FR-6.1 smooth", "backward RTS pass")
    x_s, _ = _backward(xs, Ps, xp, Pp, Fs)

    # Use absolute epoch timestamps (time_ns / 1e9) so ground truth aligns
    # with fused_*.parquet which carries ROS header stamps (absolute epoch).
    if "time_ns" in df.columns:
        t_out = df.time_ns.to_numpy(dtype=float) / 1e9
    else:
        t_out = df.t_s.to_numpy(dtype=float)

    out = pd.DataFrame(
        {
            "t_s": t_out,
            "px_m": x_s[:, 0],
            "py_m": x_s[:, 1],
            "v_mps": np.clip(x_s[:, 2], 0.0, None),
            "psi_rad": x_s[:, 3],
            "psi_dot_rps": x_s[:, 4],
        }
    )

    out_path = out_dir / trace / "ground_truth.parquet"
    write_parquet(out, out_path, GroundTruth, trip_id=trace)
    _log(
        "FR-6.1 smooth",
        f"wrote {out_path} ({out_path.stat().st_size} bytes, {len(out)} rows)",
    )
    return out_path


def _log(tag: str, msg: str) -> None:
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    sys.stdout.write(f"[{ts}] [{tag}] INFO  {msg}\n")
    sys.stdout.flush()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Run RTS smoother to produce ground_truth.parquet")
    p.add_argument("--trace", required=True, help="trace name (e.g. day2)")
    p.add_argument("--out-dir", type=Path, default=Path("out"), help="output root dir")
    return p


def main(argv: list[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)
    smooth(args.trace, args.out_dir)


if __name__ == "__main__":
    main()
