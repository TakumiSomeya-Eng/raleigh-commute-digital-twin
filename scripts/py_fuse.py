"""FR-4.2 — Python EKF/UKF runner (fallback when Docker/ROS2 unavailable).

Reads aligned_100hz.parquet directly; produces fused_{filter}.parquet with
the same 9-column schema as the C++ node + mcap_to_parquet pipeline.

Algorithm mirrors ekf_node.cpp + ctrv_model.hpp + chi2_gate.hpp exactly.
Only EKF is implemented here; UKF requires the C++ node.

Usage:
    python scripts/py_fuse.py \\
        --trace day2 --filter ekf --out-dir out
"""

from __future__ import annotations

import argparse
import datetime
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

# ---- ENU projection (mirrors C++ ekf_node.cpp §latlon_to_enu) -------------
_R_EARTH_M = 6_371_000.0
_DEG2RAD = math.pi / 180.0
_LAT0_RAD = 35.773 * _DEG2RAD
_LON0_RAD = -78.610 * _DEG2RAD

# ---- EKF parameters --------------------------------------------------------
_SIGMA_A = 1.0
_SIGMA_PSI_DOT = 0.1
_SIGMA_BEARING = 0.35
_CHI2_1DOF_99 = 6.635
_CHI2_2DOF_99 = 9.210
_WAIT_GPS_COUNT = 3
_BEARING_MIN_SPEED = 2.0
_BEARING_MAX_ACCURACY_DEG = 45.0
_R_V = 1.0  # GPS speed measurement noise variance (m²/s²)

# State indices — match C++ kPx, kPy, kV, kPsi, kPsiDot
kPx, kPy, kV, kPsi, kPsiDot = 0, 1, 2, 3, 4  # noqa: N816


def _log(tag: str, msg: str) -> None:
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    sys.stdout.write(f"[{ts}] [{tag}] INFO  {msg}\n")
    sys.stdout.flush()


def _load_gz_var(noise_fit_path: Path) -> float:
    with open(noise_fit_path, encoding="utf-8") as fh:
        nf = yaml.safe_load(fh)
    sz = float(nf["channels"]["gz_rps"]["scale"])
    return sz * sz


def _latlon_to_enu(lat_deg: float, lon_deg: float) -> tuple[float, float]:
    px = (lon_deg * _DEG2RAD - _LON0_RAD) * math.cos(_LAT0_RAD) * _R_EARTH_M
    py = (lat_deg * _DEG2RAD - _LAT0_RAD) * _R_EARTH_M
    return px, py


def _ctrv_predict(x: np.ndarray, dt: float, a_lon: float) -> np.ndarray:
    xn = x.copy()
    px, py, v, psi, psi_dot = x
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
        inv_pd = 1.0 / psi_dot
        inv_pd2 = inv_pd * inv_pd
        J[kPx, kV] = inv_pd * (math.sin(psi_new) - math.sin(psi))
        J[kPx, kPsi] = (v * inv_pd) * (math.cos(psi_new) - math.cos(psi))
        J[kPx, kPsiDot] = (
            -v * inv_pd2 * (math.sin(psi_new) - math.sin(psi))
            + (v * inv_pd) * math.cos(psi_new) * dt
        )
        J[kPy, kV] = inv_pd * (-math.cos(psi_new) + math.cos(psi))
        J[kPy, kPsi] = (v * inv_pd) * (math.sin(psi_new) - math.sin(psi))
        J[kPy, kPsiDot] = (
            -v * inv_pd2 * (-math.cos(psi_new) + math.cos(psi))
            + (v * inv_pd) * math.sin(psi_new) * dt
        )
        J[kPsi, kPsiDot] = dt
    return J


def _gate_1d(innov: float, S: float) -> bool:
    return (innov * innov) / S <= _CHI2_1DOF_99


def _gate_2d(innov: np.ndarray, S: np.ndarray) -> bool:
    return float(innov @ np.linalg.inv(S) @ innov) <= _CHI2_2DOF_99


def run_ekf(parquet_path: Path, gz_var: float, out_path: Path) -> None:
    _log("FR-4.2 py-ekf", f"reading {parquet_path}")
    df = pd.read_parquet(parquet_path)

    x = np.zeros(5)
    P = np.eye(5)
    initialized = False
    init_positions: list[tuple[float, float]] = []
    prev_t_s: float = 0.0
    records: list[dict] = []

    I5 = np.eye(5)

    for row in df.itertuples(index=False):
        t_s = float(row.t_s)
        is_gps = not bool(row.gps_interpolated)

        # ---- Initialization (mirrors on_gps() before initialized_) ----------
        if not initialized:
            if is_gps:
                px, py = _latlon_to_enu(float(row.lat_wgs84), float(row.lon_wgs84))
                init_positions.append((px, py))
                if len(init_positions) >= _WAIT_GPS_COUNT:
                    x = np.zeros(5)
                    x[kPx] = px
                    x[kPy] = py
                    if len(init_positions) >= 2:
                        p0 = init_positions[0]
                        x[kPsi] = math.atan2(py - p0[1], px - p0[0])
                    var_h = float(row.horizontal_accuracy_m) ** 2
                    P = np.zeros((5, 5))
                    P[kPx, kPx] = var_h
                    P[kPy, kPy] = var_h
                    P[kV, kV] = 16.0  # ±4 m/s
                    P[kPsi, kPsi] = math.pi**2  # ±π rad
                    P[kPsiDot, kPsiDot] = 0.25  # ±0.5 rad/s
                    initialized = True
                    prev_t_s = t_s
                    _log(
                        "FR-4.2 py-ekf",
                        f"initialized  px={x[kPx]:.1f}  py={x[kPy]:.1f}  psi={x[kPsi]:.3f}",
                    )
            continue  # skip pre-init rows and the init row itself (C++ also skips)

        # ---- Predict (mirrors on_imu() predict step) -----------------------
        dt = t_s - prev_t_s
        prev_t_s = t_s

        if 0.0 < dt <= 0.5:
            F = _jacobian(x, dt)
            x = _ctrv_predict(x, dt, float(row.ax_mps2))
            Q = np.zeros((5, 5))
            Q[kV, kV] = (_SIGMA_A * dt) ** 2
            Q[kPsiDot, kPsiDot] = (_SIGMA_PSI_DOT * dt) ** 2
            P = F @ P @ F.T + Q

        # ---- IMU yaw-rate pseudo-measurement --------------------------------
        if gz_var > 0.0:
            z_yaw = float(row.gz_rps)
            innov_yaw = z_yaw - x[kPsiDot]
            S_yaw = P[kPsiDot, kPsiDot] + gz_var
            if _gate_1d(innov_yaw, S_yaw):
                H_yaw = np.zeros(5)
                H_yaw[kPsiDot] = 1.0
                K_yaw = P @ H_yaw / S_yaw
                x += K_yaw * innov_yaw
                P = (I5 - np.outer(K_yaw, H_yaw)) @ P
                x[kPsi] = math.remainder(x[kPsi], 2.0 * math.pi)

        x[kV] = max(0.0, x[kV])

        # ---- GPS updates (real rows only, mirrors on_gps/on_speed/on_bearing)
        if is_gps:
            px_gps, py_gps = _latlon_to_enu(float(row.lat_wgs84), float(row.lon_wgs84))
            var_h = float(row.horizontal_accuracy_m) ** 2

            # 2D position update
            H_gps = np.zeros((2, 5))
            H_gps[0, kPx] = 1.0
            H_gps[1, kPy] = 1.0
            z_gps = np.array([px_gps, py_gps])
            innov_gps = z_gps - H_gps @ x
            S_gps = H_gps @ P @ H_gps.T + np.eye(2) * var_h
            if _gate_2d(innov_gps, S_gps):
                K_gps = P @ H_gps.T @ np.linalg.inv(S_gps)
                x += K_gps @ innov_gps
                P = (I5 - K_gps @ H_gps) @ P
                x[kPsi] = math.remainder(x[kPsi], 2.0 * math.pi)

            # Speed update
            v_gps = float(row.gps_speed_mps)
            if 0.0 <= v_gps <= 40.0:
                innov_v = v_gps - x[kV]
                s_v = P[kV, kV] + _R_V
                K_v = P[:, kV] / s_v
                x += K_v * innov_v
                x[kV] = max(0.0, x[kV])
                I_KH = I5.copy()
                I_KH[:, kV] -= K_v
                P = I_KH @ P
                x[kPsi] = math.remainder(x[kPsi], 2.0 * math.pi)

            # Bearing update
            if (
                v_gps >= _BEARING_MIN_SPEED
                and float(row.bearing_accuracy_deg) < _BEARING_MAX_ACCURACY_DEG
            ):
                brg_rad = math.radians(float(row.gps_bearing_deg))
                z_brg = math.atan2(math.cos(brg_rad), math.sin(brg_rad))
                innov_brg = math.remainder(z_brg - x[kPsi], 2.0 * math.pi)
                S_brg = P[kPsi, kPsi] + _SIGMA_BEARING**2
                if _gate_1d(innov_brg, S_brg):
                    H_brg = np.zeros(5)
                    H_brg[kPsi] = 1.0
                    K_brg = P @ H_brg / S_brg
                    x += K_brg * innov_brg
                    P = (I5 - np.outer(K_brg, H_brg)) @ P
                    x[kPsi] = math.remainder(x[kPsi], 2.0 * math.pi)

        records.append(
            {
                "t_s": t_s,
                "px_m": float(x[kPx]),
                "py_m": float(x[kPy]),
                "v_mps": float(x[kV]),
                "psi_rad": float(x[kPsi]),
                "psi_dot_rps": float(x[kPsiDot]),
                "cov_xx": float(P[kPx, kPx]),
                "cov_yy": float(P[kPy, kPy]),
                "cov_yaw": float(P[kPsi, kPsi]),
            }
        )

    out_df = pd.DataFrame(records)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_df.to_parquet(out_path, index=False)
    _log(
        "FR-4.2 py-ekf",
        f"wrote {out_path}  {out_path.stat().st_size} bytes  {len(records)} rows",
    )


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Python EKF fusion — reads aligned Parquet, writes fused Parquet."
    )
    p.add_argument("--trace", required=True, help="trace name, e.g. day2")
    p.add_argument("--filter", dest="filter_name", default="ekf", help="only ekf supported")
    p.add_argument("--out-dir", type=Path, default=Path("out"), dest="out_dir")
    return p


def main(argv: list[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)
    if args.filter_name != "ekf":
        sys.stderr.write(f"ERROR: py_fuse only supports --filter ekf (got {args.filter_name})\n")
        sys.exit(1)

    trace = args.trace
    parquet_path = args.out_dir / trace / "aligned_100hz.parquet"
    noise_fit_path = Path("config") / f"noise_fit_{trace}.yaml"
    out_path = args.out_dir / trace / f"fused_{args.filter_name}.parquet"

    if not parquet_path.exists():
        sys.stderr.write(f"ERROR: {parquet_path} not found — run make data TRACE={trace} first\n")
        sys.exit(1)
    if not noise_fit_path.exists():
        sys.stderr.write(f"ERROR: {noise_fit_path} not found\n")
        sys.exit(1)

    gz_var = _load_gz_var(noise_fit_path)
    _log("FR-4.2 py-ekf", f"TRACE={trace}  gz_var={gz_var:.6f}")
    run_ekf(parquet_path, gz_var, out_path)


if __name__ == "__main__":
    main()
