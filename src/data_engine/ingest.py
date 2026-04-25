"""FR-1.1, FR-1.2 — Sensor Logger CSV parser and 100 Hz clock alignment.

Parses the seven required channels from a Sensor Logger export directory
and resamples them onto a uniform 100 Hz timeline.

Sensor Logger export format (Kelvin Tan, iOS/Android):
  Each channel is a separate CSV file named <Channel>.csv.
  All files share a ``time`` column (epoch nanoseconds, int64).
  Location.csv additionally has ``latitude``, ``longitude``,
  ``horizontalAccuracy``, ``speedAccuracy``, ``bearingAccuracy``,
  ``speed``, ``bearing``.  Other channels have ``x``, ``y``, ``z``.
  Orientation.csv has ``qw``, ``qx``, ``qy``, ``qz``
  (or ``w``, ``x``, ``y``, ``z`` in older exports).

See: TRD sec.1.2, FRD FR-1.1, FR-1.2
"""

from __future__ import annotations

import datetime
import logging
from pathlib import Path

import numpy as np
import pandas as pd
from numpy.typing import NDArray

from data_engine.errors import MissingRequiredChannelError
from data_engine.projection import wgs84_to_enu

logger = logging.getLogger(__name__)

# Required channel file stems (case-sensitive, matches Sensor Logger output).
REQUIRED_CHANNELS: tuple[str, ...] = (
    "Location",
    "Accelerometer",
    "Gyroscope",
    "Gravity",
    "Orientation",
    "Magnetometer",
    "TotalAcceleration",
)

# A grid tick is labelled gps_interpolated=False only when a real GPS row
# falls within this window on either side (TRD §1.2).
_GPS_FIX_TOLERANCE_S: float = 0.050  # ±50 ms


def _read_channel(data_dir: Path, channel: str) -> pd.DataFrame:
    """Read one Sensor Logger CSV channel file.

    Args:
        data_dir: Directory containing the exported CSVs.
        channel: Channel name without extension, e.g. ``"Location"``.

    Returns:
        DataFrame with ``time`` forced to int64 epoch-nanoseconds.

    Raises:
        MissingRequiredChannelError: When the file is absent.
    """
    path = data_dir / f"{channel}.csv"
    if not path.exists():
        raise MissingRequiredChannelError(channel)
    df = pd.read_csv(path, dtype={"time": np.int64})
    df.columns = [c.strip() for c in df.columns]
    return df


def _interp(
    t_raw: NDArray[np.int64],
    values: NDArray[np.float64],
    t_grid: NDArray[np.int64],
) -> NDArray[np.float64]:
    """Linear interpolation of *values* at *t_raw* onto *t_grid* (all ns)."""
    return np.interp(
        t_grid.astype(np.float64),
        t_raw.astype(np.float64),
        values.astype(np.float64),
    )


def _orientation_cols(df: pd.DataFrame) -> tuple[str, str, str, str]:
    """Return quaternion column names, tolerating old (w/x/y/z) exports."""
    if "qw" in df.columns:
        return "qw", "qx", "qy", "qz"
    return "w", "x", "y", "z"


def parse_and_align(
    data_dir: Path,
    lat0_deg: float,
    lon0_deg: float,
    target_hz: float = 100.0,
    warmup_s: float = 0.5,
) -> pd.DataFrame:
    """Parse all Sensor Logger CSVs and align to a uniform 100 Hz grid.

    Steps:
      1. Load all seven required channels; raise on any missing file.
      2. Determine common time range from the GPS channel.
      3. Build a uniform ``target_hz`` grid over that range.
      4. Linearly interpolate every channel onto the grid.
      5. Project GPS lat/lon to local ENU metres.
      6. Mark rows whose nearest real GPS fix is > 50 ms away as interpolated.
      7. Drop the first ``warmup_s`` seconds and renumber ``t_s`` from 0.00.

    Args:
        data_dir: Directory containing Sensor Logger CSV exports.
        lat0_deg: ENU anchor latitude in decimal degrees.
        lon0_deg: ENU anchor longitude in decimal degrees.
        target_hz: Output sample rate in Hz.
        warmup_s: Seconds to discard from the start of the recording.

    Returns:
        DataFrame with all ``Aligned100Hz`` schema columns, NaN-free,
        ``t_s`` starting at 0.00 after the warm-up drop.

    Raises:
        MissingRequiredChannelError: If any required channel CSV is absent.
    """
    channels: dict[str, pd.DataFrame] = {
        ch: _read_channel(data_dir, ch) for ch in REQUIRED_CHANNELS
    }

    loc = channels["Location"]
    acc = channels["Accelerometer"]
    gyr = channels["Gyroscope"]
    grav = channels["Gravity"]
    ori = channels["Orientation"]
    mag = channels["Magnetometer"]

    # Common time span (intersection across all channels).
    t0_ns = int(loc["time"].min())
    t_end_ns = min(int(ch["time"].max()) for ch in channels.values())

    dt_ns = int(round(1e9 / target_hz))
    t_grid: NDArray[np.int64] = np.arange(t0_ns, t_end_ns, dt_ns, dtype=np.int64)
    t_s_full = (t_grid - t0_ns).astype(np.float64) / 1e9

    # --- GPS fields ---
    lat = _interp(loc["time"].values, loc["latitude"].values, t_grid)
    lon = _interp(loc["time"].values, loc["longitude"].values, t_grid)
    hacc = _interp(loc["time"].values, loc["horizontalAccuracy"].values, t_grid)
    sacc = _interp(loc["time"].values, loc["speedAccuracy"].values, t_grid)
    bacc = _interp(loc["time"].values, loc["bearingAccuracy"].values, t_grid)
    gps_spd = _interp(loc["time"].values, loc["speed"].values, t_grid)
    gps_brg = _interp(loc["time"].values, loc["bearing"].values, t_grid)

    # --- IMU fields ---
    ax = _interp(acc["time"].values, acc["x"].values, t_grid)
    ay = _interp(acc["time"].values, acc["y"].values, t_grid)
    az = _interp(acc["time"].values, acc["z"].values, t_grid)

    gx = _interp(gyr["time"].values, gyr["x"].values, t_grid)
    gy = _interp(gyr["time"].values, gyr["y"].values, t_grid)
    gz = _interp(gyr["time"].values, gyr["z"].values, t_grid)

    gv_x = _interp(grav["time"].values, grav["x"].values, t_grid)
    gv_y = _interp(grav["time"].values, grav["y"].values, t_grid)
    gv_z = _interp(grav["time"].values, grav["z"].values, t_grid)

    # --- Orientation quaternion ---
    qw_col, qx_col, qy_col, qz_col = _orientation_cols(ori)
    qw = _interp(ori["time"].values, ori[qw_col].values, t_grid)
    qx = _interp(ori["time"].values, ori[qx_col].values, t_grid)
    qy = _interp(ori["time"].values, ori[qy_col].values, t_grid)
    qz = _interp(ori["time"].values, ori[qz_col].values, t_grid)

    # --- Magnetometer (µT) ---
    mx = _interp(mag["time"].values, mag["x"].values, t_grid)
    my = _interp(mag["time"].values, mag["y"].values, t_grid)
    mz = _interp(mag["time"].values, mag["z"].values, t_grid)

    # --- ENU projection ---
    px_m, py_m = wgs84_to_enu(lat, lon, lat0_deg, lon0_deg)

    # --- GPS interpolated flag ---
    # A tick is a real fix iff a Location row falls within ±50 ms of it.
    real_fix_ns = loc["time"].values.astype(np.float64)
    tol_ns = _GPS_FIX_TOLERANCE_S * 1e9
    gps_interp = np.ones(len(t_grid), dtype=bool)
    for rt in real_fix_ns:
        gps_interp[np.abs(t_grid.astype(np.float64) - rt) <= tol_ns] = False

    # --- Assemble DataFrame ---
    df = pd.DataFrame(
        {
            "t_s": t_s_full,
            "time_ns": t_grid,
            "px_m": px_m,
            "py_m": py_m,
            "lat_wgs84": lat,
            "lon_wgs84": lon,
            "horizontal_accuracy_m": hacc,
            "speed_accuracy_mps": sacc,
            "bearing_accuracy_deg": bacc,
            "gps_speed_mps": gps_spd,
            "gps_bearing_deg": gps_brg,
            "ax_mps2": ax,
            "ay_mps2": ay,
            "az_mps2": az,
            "gx_rps": gx,
            "gy_rps": gy,
            "gz_rps": gz,
            "grav_x": gv_x,
            "grav_y": gv_y,
            "grav_z": gv_z,
            "quat_w": qw,
            "quat_x": qx,
            "quat_y": qy,
            "quat_z": qz,
            "mag_x_uT": mx,
            "mag_y_uT": my,
            "mag_z_uT": mz,
            "gps_interpolated": gps_interp,
        }
    )

    # --- Drop warm-up; renumber t_s from 0.00 ---
    df = df[df["t_s"] >= warmup_s].copy().reset_index(drop=True)
    t_offset = df["t_s"].iloc[0]
    df["t_s"] = np.round(df["t_s"] - t_offset, 2)

    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    logger.info(
        "[%s] [FR-1.2 ingest] INFO  %d rows, %.1f s, mean horiz-acc %.1f m",
        now,
        len(df),
        float(df["t_s"].iloc[-1]),
        float(df["horizontal_accuracy_m"].mean()),
    )
    return df
