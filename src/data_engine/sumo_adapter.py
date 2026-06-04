"""T8.3 / T8.4 — SUMO FCD XML → Sensor Logger CSV converter.

Public API (matches sumo_adapter_spec.py exactly):
    parse_fcd             : FCD XML file → tidy DataFrame
    add_noise             : add Gaussian sensor noise per driving style
    to_sensor_logger_csvs : DataFrame → 7 Sensor Logger CSV files
    convert               : end-to-end wrapper (CLI entry point)
"""

from __future__ import annotations

import datetime
import math
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Literal

import numpy as np
import pandas as pd

# ── Types ──────────────────────────────────────────────────────────────────────

DrivingStyle = Literal["calm", "normal", "aggressive"]

# ── Constants (mirrors sumo_adapter_spec.py) ───────────────────────────────────

SENSOR_LOGGER_FILES: tuple[str, ...] = (
    "Location.csv",
    "Accelerometer.csv",
    "Gyroscope.csv",
    "Gravity.csv",
    "Orientation.csv",
    "Magnetometer.csv",
    "TotalAcceleration.csv",
)

NOISE_SIGMAS: dict[str, dict[str, float]] = {
    "calm": {"gps_m": 3.0, "accel": 0.10, "gyro": 0.005, "mag_uT": 1.5},
    "normal": {"gps_m": 5.0, "accel": 0.15, "gyro": 0.008, "mag_uT": 2.5},
    "aggressive": {"gps_m": 8.0, "accel": 0.25, "gyro": 0.015, "mag_uT": 4.0},
}

HARSH_BRAKE_THRESHOLD_MPS2: float = 3.0

# horizontalAccuracy written to Location.csv per driving style (sumo-osm.md)
_HORIZ_ACC: dict[str, float] = {"calm": 3.0, "normal": 5.0, "aggressive": 8.0}

# 1 degree of latitude/longitude ≈ 111 320 m (equirectangular, < 10 km span)
_M_PER_DEG: float = 111_320.0

# Gravity constant (2-D simulation: vehicle moves on horizontal plane)
_GRAVITY_Z: float = -9.81

# Epoch anchor for time column: 2026-01-01T00:00:00Z in nanoseconds
_EPOCH_ANCHOR_NS: int = int(
    datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc).timestamp() * 1_000_000_000
)


# ── Private helpers ────────────────────────────────────────────────────────────


def _mod360(
    angle: np.ndarray[float, np.dtype[np.float64]],
) -> np.ndarray[float, np.dtype[np.float64]]:
    """Wrap angles into [0, 360)."""
    return np.mod(angle, 360.0)


def _t_s_to_epoch_ns(t_s: pd.Series) -> pd.Series:
    """Convert elapsed-second timestamps to int64 epoch nanoseconds.

    Uses a fixed anchor (2026-01-01T00:00:00Z) so every output satisfies the
    plausibility check in the tests (between year 2020 and year 2100).
    Rounds to the nearest nanosecond; strictly-increasing property is preserved
    as long as Δt >= 1 ns (guaranteed for step-length >= 0.01 s).
    """
    ns = (t_s.to_numpy(dtype=float) * 1_000_000_000).round().astype(np.int64)
    return pd.Series(ns + _EPOCH_ANCHOR_NS, dtype=np.int64)


def _bearing_to_quaternion(
    bearing_deg: np.ndarray[float, np.dtype[np.float64]],
) -> tuple[np.ndarray[float, np.dtype[np.float64]], ...]:
    """Convert compass bearing (N=0, CW) to a yaw-only unit quaternion.

    Maps bearing to a rotation about the vertical axis in ENU:
        yaw_rad = (90 - bearing_deg) * π / 180   (east = 0, CCW positive)
        qw = cos(yaw/2),  qx = 0,  qy = 0,  qz = sin(yaw/2)

    Norm is exactly 1.0 for every row.
    """
    yaw = (90.0 - bearing_deg) * (math.pi / 180.0)
    half = yaw / 2.0
    return np.cos(half), np.zeros(len(bearing_deg)), np.zeros(len(bearing_deg)), np.sin(half)


def _log(tag: str, msg: str) -> None:
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    sys.stdout.write(f"[{ts}] [{tag}] INFO  {msg}\n")


# ── Public API ─────────────────────────────────────────────────────────────────


def parse_fcd(fcd_path: Path) -> pd.DataFrame:
    """Parse a SUMO FCD (Floating Car Data) XML file into a tidy DataFrame.

    Parameters
    ----------
    fcd_path:
        Path to a ``*.xml`` file produced by SUMO with ``--fcd-output.geo true``.

    Returns
    -------
    pd.DataFrame with columns:
        t_s       : float  — elapsed seconds from start (first row = 0.0)
        lon       : float  — WGS-84 longitude (degrees)
        lat       : float  — WGS-84 latitude  (degrees)
        speed_mps : float  — vehicle speed in m/s, always >= 0
        bearing   : float  — heading in [0, 360), north=0, clockwise

    Raises
    ------
    FileNotFoundError  if fcd_path does not exist.
    ValueError         if no <vehicle> elements are found.
    ValueError         if a <vehicle> element is missing a required attribute.
    """
    fcd_path = Path(fcd_path)
    if not fcd_path.exists():
        raise FileNotFoundError(fcd_path)

    try:
        tree = ET.parse(fcd_path)
    except ET.ParseError as exc:
        raise ValueError(f"XML parse error: {exc}") from exc

    root = tree.getroot()
    rows: list[dict[str, float]] = []

    for ts in root.findall("timestep"):
        t = float(ts.get("time", "0"))
        for veh in ts.findall("vehicle"):
            x = veh.get("x")  # lon (--fcd-output.geo true)
            y = veh.get("y")  # lat
            speed = veh.get("speed")
            angle = veh.get("angle")

            if x is None or y is None or speed is None or angle is None:
                attrs = {"x": x, "y": y, "speed": speed, "angle": angle}
                missing = [k for k, v in attrs.items() if v is None]
                raise ValueError(f"<vehicle> at t={t} missing required attribute(s): {missing}")

            rows.append(
                {
                    "t_s": t,
                    "lon": float(x),
                    "lat": float(y),
                    "speed_mps": float(speed),
                    "bearing": float(angle),
                }
            )

    if not rows:
        raise ValueError(f"No <vehicle> elements found in FCD file: {fcd_path}")

    df = pd.DataFrame(rows)

    # Normalise elapsed time so that the first row is t_s = 0.0
    df["t_s"] = df["t_s"] - df["t_s"].iloc[0]

    # Enforce value-range contracts
    df["speed_mps"] = df["speed_mps"].clip(lower=0.0)
    df["bearing"] = _mod360(df["bearing"].to_numpy())

    return df.reset_index(drop=True)


def add_noise(
    fcd_df: pd.DataFrame,
    style: DrivingStyle,
    *,
    seed: int | None = None,
) -> pd.DataFrame:
    """Add Gaussian sensor noise to a parsed FCD DataFrame.

    Parameters
    ----------
    fcd_df : pd.DataFrame
        Output of ``parse_fcd()``.
    style : DrivingStyle
        Controls noise sigma magnitudes via ``NOISE_SIGMAS``.
    seed : int | None
        Optional random seed for reproducibility.

    Returns
    -------
    pd.DataFrame — same schema as input, with noise on lat, lon, speed_mps.
        bearing and t_s are carried through unchanged.

    Contract
    --------
    - Output shape equals input shape.
    - speed_mps >= 0 (zero-clipped after noise).
    - bearing remains in [0, 360).
    - With fixed seed, output is deterministic.
    - 99.7 % of GPS position errors are within 3 × gps_sigma metres (3σ).
    """
    sigmas = NOISE_SIGMAS[style]
    rng = np.random.default_rng(seed)
    n = len(fcd_df)

    out = fcd_df.copy()

    # GPS position noise: sigma in metres -> degrees
    gps_sigma_deg = sigmas["gps_m"] / _M_PER_DEG
    out["lat"] = out["lat"].to_numpy() + rng.normal(0.0, gps_sigma_deg, n)
    out["lon"] = out["lon"].to_numpy() + rng.normal(0.0, gps_sigma_deg, n)

    # Speed noise: sigma = accel_sigma * median dt (spec: +/- accel * dt)
    dt_vals = fcd_df["t_s"].diff().dropna()
    dt = float(dt_vals.median()) if len(dt_vals) > 0 else 0.01
    if dt <= 0 or math.isnan(dt):
        dt = 0.01
    speed_sigma = sigmas["accel"] * dt
    out["speed_mps"] = (out["speed_mps"].to_numpy() + rng.normal(0.0, speed_sigma, n)).clip(min=0.0)

    # bearing is not perturbed by add_noise (heading comes from angle delta)
    out["bearing"] = _mod360(out["bearing"].to_numpy())

    return out.reset_index(drop=True)


def to_sensor_logger_csvs(
    fcd_df: pd.DataFrame,
    style: DrivingStyle,
    out_dir: Path,
    *,
    trip_id: str | None = None,
) -> dict[str, Path]:
    """Convert a (possibly noise-added) FCD DataFrame to 7 Sensor Logger CSVs.

    Parameters
    ----------
    fcd_df : pd.DataFrame
        Output of ``parse_fcd()`` or ``add_noise()``.
    style : DrivingStyle
        Sets ``horizontalAccuracy`` in Location.csv.
    out_dir : Path
        Destination directory; created recursively if absent.
    trip_id : str | None
        Unused by the existing pipeline; stored only for traceability.

    Returns
    -------
    dict[str, Path] mapping each filename in SENSOR_LOGGER_FILES to its Path.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    n = len(fcd_df)
    sigmas = NOISE_SIGMAS[style]
    rng = np.random.default_rng(None)  # IMU noise is non-deterministic by design

    # ── Shared arrays ──────────────────────────────────────────────────────────
    time_ns: pd.Series = _t_s_to_epoch_ns(fcd_df["t_s"])
    bearing_deg = fcd_df["bearing"].to_numpy(dtype=float)
    bearing_rad = bearing_deg * (math.pi / 180.0)

    # Δt per row (seconds); fall back to 0.01 for the first row
    dt_arr = fcd_df["t_s"].diff().to_numpy(dtype=float)
    dt_arr[0] = dt_arr[1] if n > 1 else 0.01
    dt_arr = np.where(dt_arr > 0, dt_arr, 0.01)

    # ── Accelerometer: Δv/Δt projected onto heading, plus sensor noise ─────────
    # ENU: north = +Y (bearing 0°), east = +X (bearing 90°)
    # heading from +X axis (CCW) = 90° - bearing
    heading_rad = (math.pi / 2.0) - bearing_rad
    dv = fcd_df["speed_mps"].diff().to_numpy(dtype=float)
    dv[0] = 0.0
    a_tangential = dv / dt_arr  # along-track acceleration (m/s²)
    ax = a_tangential * np.cos(heading_rad) + rng.normal(0.0, sigmas["accel"], n)
    ay = a_tangential * np.sin(heading_rad) + rng.normal(0.0, sigmas["accel"], n)
    az = rng.normal(0.0, sigmas["accel"], n)

    # ── Gyroscope: Δbearing/Δt (rad/s), unwrapped to avoid 360→0 jumps ────────
    unwrapped = np.unwrap(bearing_rad)
    dbearing = np.diff(unwrapped, prepend=unwrapped[0])
    gz = dbearing / dt_arr + rng.normal(0.0, sigmas["gyro"], n)
    gx = rng.normal(0.0, sigmas["gyro"], n)
    gy = rng.normal(0.0, sigmas["gyro"], n)

    # ── Orientation: yaw-only unit quaternion from bearing ─────────────────────
    qw, qx, qy, qz = _bearing_to_quaternion(bearing_deg)

    # ── Magnetometer: Earth's horizontal field projected onto heading ──────────
    # Approximate Earth field magnitude ≈ 50 µT
    B = 50.0
    mx = B * np.sin(bearing_rad) + rng.normal(0.0, sigmas["mag_uT"], n)
    my = B * np.cos(bearing_rad) + rng.normal(0.0, sigmas["mag_uT"], n)
    mz = rng.normal(0.0, sigmas["mag_uT"], n)

    # ── Gravity: constant for 2-D simulation ───────────────────────────────────
    grav_x = np.zeros(n)
    grav_y = np.zeros(n)
    grav_z = np.full(n, _GRAVITY_Z)

    # ── Write all seven files ──────────────────────────────────────────────────
    paths: dict[str, Path] = {}

    # Location.csv — downsample to ~1 Hz to match real Sensor Logger GPS rate.
    # ingest marks a 100 Hz tick gps_interpolated=False only within ±50 ms of a
    # real GPS row; writing at 100 Hz would make every tick a GPS tick and break
    # the EKF initialisation logic (gps_start never transitions).
    _gps_step = max(1, round(1.0 / fcd_df["t_s"].diff().median())) if n > 1 else 1
    gps_idx = np.arange(0, n, _gps_step)
    p = out_dir / "Location.csv"
    pd.DataFrame(
        {
            "time": time_ns[gps_idx],
            "latitude": fcd_df["lat"].to_numpy()[gps_idx],
            "longitude": fcd_df["lon"].to_numpy()[gps_idx],
            "altitude": np.zeros(len(gps_idx)),
            "speed": fcd_df["speed_mps"].to_numpy()[gps_idx],
            "bearing": bearing_deg[gps_idx],
            "horizontalAccuracy": np.full(len(gps_idx), _HORIZ_ACC[style]),
            "verticalAccuracy": np.full(len(gps_idx), 10.0),
            "speedAccuracy": np.full(len(gps_idx), 0.5),
            "bearingAccuracy": np.full(len(gps_idx), 5.0),
            "floor": np.zeros(len(gps_idx), dtype=np.int64),
        }
    ).to_csv(p, index=False)
    paths["Location.csv"] = p

    # Accelerometer.csv
    p = out_dir / "Accelerometer.csv"
    pd.DataFrame({"time": time_ns, "x": ax, "y": ay, "z": az}).to_csv(p, index=False)
    paths["Accelerometer.csv"] = p

    # Gyroscope.csv
    p = out_dir / "Gyroscope.csv"
    pd.DataFrame({"time": time_ns, "x": gx, "y": gy, "z": gz}).to_csv(p, index=False)
    paths["Gyroscope.csv"] = p

    # Gravity.csv
    p = out_dir / "Gravity.csv"
    pd.DataFrame({"time": time_ns, "x": grav_x, "y": grav_y, "z": grav_z}).to_csv(p, index=False)
    paths["Gravity.csv"] = p

    # Orientation.csv
    p = out_dir / "Orientation.csv"
    pd.DataFrame({"time": time_ns, "qw": qw, "qx": qx, "qy": qy, "qz": qz}).to_csv(p, index=False)
    paths["Orientation.csv"] = p

    # Magnetometer.csv
    p = out_dir / "Magnetometer.csv"
    pd.DataFrame({"time": time_ns, "x": mx, "y": my, "z": mz}).to_csv(p, index=False)
    paths["Magnetometer.csv"] = p

    # TotalAcceleration.csv = Accelerometer + Gravity (element-wise)
    p = out_dir / "TotalAcceleration.csv"
    pd.DataFrame(
        {
            "time": time_ns,
            "x": ax + grav_x,
            "y": ay + grav_y,
            "z": az + grav_z,
        }
    ).to_csv(p, index=False)
    paths["TotalAcceleration.csv"] = p

    _log("T8.3 sumo_adapter", f"wrote 7 CSVs to {out_dir} (style={style}, n={n})")
    return paths


def convert(
    fcd_path: Path,
    style: DrivingStyle,
    out_dir: Path,
    *,
    seed: int | None = None,
    trip_id: str | None = None,
) -> dict[str, Path]:
    """End-to-end: FCD XML → noise → 7 Sensor Logger CSVs.

    Equivalent to::

        df = parse_fcd(fcd_path)
        df = add_noise(df, style, seed=seed)
        return to_sensor_logger_csvs(df, style, out_dir, trip_id=trip_id)
    """
    df = parse_fcd(fcd_path)
    df = add_noise(df, style, seed=seed)
    return to_sensor_logger_csvs(df, style, out_dir, trip_id=trip_id)


# ── CLI entry point ────────────────────────────────────────────────────────────


def _cli() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Convert SUMO FCD XML to Sensor Logger CSV files (T8.3)"
    )
    parser.add_argument("--fcd", required=True, type=Path, help="Input FCD XML")
    parser.add_argument("--style", required=True, choices=list(NOISE_SIGMAS))
    parser.add_argument("--out", required=True, type=Path, help="Output directory")
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--trip-id", default=None)
    args = parser.parse_args()

    paths = convert(args.fcd, args.style, args.out, seed=args.seed, trip_id=args.trip_id)
    for fname, p in paths.items():
        sys.stdout.write(f"  {fname} -> {p}\n")


if __name__ == "__main__":
    _cli()
