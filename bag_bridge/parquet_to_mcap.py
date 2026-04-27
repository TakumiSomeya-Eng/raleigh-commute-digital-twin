"""FR-3.1, FR-3.2 — Convert aligned_100hz.parquet to a ROS 2 MCAP bag.

Publishes four topics:
  /gps/fix    sensor_msgs/msg/NavSatFix    ~1 Hz  (gps_interpolated == False rows only)
  /gps/speed  std_msgs/msg/Float64         ~1 Hz  (GPS speed in m/s, same rows as /gps/fix)
  /imu/data   sensor_msgs/msg/Imu          100 Hz (every row)
  /mag        sensor_msgs/msg/MagneticField  50 Hz (every 2nd row)

Emits a sidecar trip.metadata.yaml with SHA-256 checksum and message counts.

CLI usage:
    python -m bag_bridge.parquet_to_mcap \\
        --parquet  out/day2/aligned_100hz.parquet \\
        --noise-fit config/noise_fit_day2.yaml \\
        --out-dir  out/day2

See: TRD §2.1, FRD FR-3.1, FR-3.2
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import logging
import sys
from pathlib import Path
from typing import Any

import pandas as pd
import yaml
from mcap.writer import Writer

from bag_bridge._cdr import (
    serialize_float64_msg,
    serialize_imu,
    serialize_magnetic_field,
    serialize_navsatfix,
)
from bag_bridge._ros2_schemas import (
    FLOAT64_SCHEMA,
    IMU_SCHEMA,
    MAGNETIC_FIELD_SCHEMA,
    NAVSATFIX_SCHEMA,
)

logger = logging.getLogger(__name__)

# Vertical position variance (m²) used when altitude accuracy is unavailable.
# 3 m (1-sigma) is a conservative GPS altitude estimate for consumer-grade receivers.
_ALTITUDE_VAR_M2: float = 9.0

# Frame IDs written into each message header.
_FRAME_GPS = "gps"
_FRAME_IMU = "imu"
_FRAME_MAG = "mag"


# ---------------------------------------------------------------------------
# Noise-fit loading
# ---------------------------------------------------------------------------


def _load_gyro_cov(noise_fit_path: Path) -> list[float]:
    """Load the diagonal angular-velocity covariance (9 floats) from a noise_fit YAML.

    Uses fitted Gaussian scale² for gx/gy/gz.  Off-diagonal elements = 0.
    """
    with open(noise_fit_path, encoding="utf-8") as fh:
        nf = yaml.safe_load(fh)
    ch = nf["channels"]
    sx = float(ch["gx_rps"]["scale"])
    sy = float(ch["gy_rps"]["scale"])
    sz = float(ch["gz_rps"]["scale"])
    return [
        sx * sx,
        0.0,
        0.0,
        0.0,
        sy * sy,
        0.0,
        0.0,
        0.0,
        sz * sz,
    ]


# ---------------------------------------------------------------------------
# MCAP writing
# ---------------------------------------------------------------------------


def _ns_to_sec_nanosec(time_ns: int) -> tuple[int, int]:
    sec = int(time_ns) // 1_000_000_000
    nanosec = int(time_ns) % 1_000_000_000
    return sec, nanosec


def convert(
    parquet_path: Path,
    noise_fit_path: Path,
    out_dir: Path,
) -> Path:
    """Convert *parquet_path* to an MCAP bag in *out_dir*.

    Returns the path to the written ``trip.mcap`` file.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    mcap_path = out_dir / "trip.mcap"
    meta_path = out_dir / "trip.metadata.yaml"

    _ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    sys.stdout.write(f"[{_ts}] [FR-3.1 bag] INFO  reading {parquet_path}\n")

    df = pd.read_parquet(parquet_path)
    gyro_cov = _load_gyro_cov(noise_fit_path)

    n_gps = 0
    n_imu = 0
    n_mag = 0
    n_spd = 0

    with open(mcap_path, "wb") as raw:
        writer = Writer(raw)
        writer.start(profile="ros2", library="bag_bridge/parquet_to_mcap")

        # Register schemas
        gps_schema_id = writer.register_schema(
            name="sensor_msgs/msg/NavSatFix",
            encoding="ros2msg",
            data=NAVSATFIX_SCHEMA.encode(),
        )
        imu_schema_id = writer.register_schema(
            name="sensor_msgs/msg/Imu",
            encoding="ros2msg",
            data=IMU_SCHEMA.encode(),
        )
        mag_schema_id = writer.register_schema(
            name="sensor_msgs/msg/MagneticField",
            encoding="ros2msg",
            data=MAGNETIC_FIELD_SCHEMA.encode(),
        )
        spd_schema_id = writer.register_schema(
            name="std_msgs/msg/Float64",
            encoding="ros2msg",
            data=FLOAT64_SCHEMA.encode(),
        )

        # Register channels
        gps_ch_id = writer.register_channel(
            topic="/gps/fix",
            message_encoding="cdr",
            schema_id=gps_schema_id,
        )
        imu_ch_id = writer.register_channel(
            topic="/imu/data",
            message_encoding="cdr",
            schema_id=imu_schema_id,
        )
        mag_ch_id = writer.register_channel(
            topic="/mag",
            message_encoding="cdr",
            schema_id=mag_schema_id,
        )
        spd_ch_id = writer.register_channel(
            topic="/gps/speed",
            message_encoding="cdr",
            schema_id=spd_schema_id,
        )

        for idx, row in enumerate(df.itertuples(index=False)):
            time_ns: int = int(row.time_ns)
            sec, nanosec = _ns_to_sec_nanosec(time_ns)

            # /imu/data — every row (100 Hz)
            imu_data = serialize_imu(
                sec=sec,
                nanosec=nanosec,
                frame_id=_FRAME_IMU,
                qw=float(row.quat_w),
                qx=float(row.quat_x),
                qy=float(row.quat_y),
                qz=float(row.quat_z),
                ax=float(row.ax_mps2),
                ay=float(row.ay_mps2),
                az=float(row.az_mps2),
                gx=float(row.gx_rps),
                gy=float(row.gy_rps),
                gz=float(row.gz_rps),
                gyro_cov=gyro_cov,
            )
            writer.add_message(
                channel_id=imu_ch_id,
                log_time=time_ns,
                data=imu_data,
                publish_time=time_ns,
            )
            n_imu += 1

            # /gps/fix — real GPS rows only (~1 Hz)
            if not bool(row.gps_interpolated):
                gps_data = serialize_navsatfix(
                    sec=sec,
                    nanosec=nanosec,
                    frame_id=_FRAME_GPS,
                    lat=float(row.lat_wgs84),
                    lon=float(row.lon_wgs84),
                    alt=0.0,
                    hacc_m=float(row.horizontal_accuracy_m),
                )
                writer.add_message(
                    channel_id=gps_ch_id,
                    log_time=time_ns,
                    data=gps_data,
                    publish_time=time_ns,
                )
                n_gps += 1

                # /gps/speed — GPS-reported speed (m/s), same cadence as /gps/fix
                writer.add_message(
                    channel_id=spd_ch_id,
                    log_time=time_ns,
                    data=serialize_float64_msg(float(row.gps_speed_mps)),
                    publish_time=time_ns,
                )
                n_spd += 1

            # /mag — every 2nd row (50 Hz)
            if idx % 2 == 0:
                mag_data = serialize_magnetic_field(
                    sec=sec,
                    nanosec=nanosec,
                    frame_id=_FRAME_MAG,
                    mx=float(row.mag_x_uT),
                    my=float(row.mag_y_uT),
                    mz=float(row.mag_z_uT),
                )
                writer.add_message(
                    channel_id=mag_ch_id,
                    log_time=time_ns,
                    data=mag_data,
                    publish_time=time_ns,
                )
                n_mag += 1

        writer.finish()

    # SHA-256 of the written MCAP
    sha256 = hashlib.sha256(mcap_path.read_bytes()).hexdigest()

    # Trip duration from first/last timestamp
    t0 = int(df["time_ns"].iloc[0])
    t1 = int(df["time_ns"].iloc[-1])
    duration_s = (t1 - t0) / 1e9

    # Infer trip_id from parquet metadata when available
    import pyarrow.parquet as pq  # local import to keep startup fast

    pf = pq.read_table(parquet_path)
    trip_id = (pf.schema.metadata or {}).get(b"trip_id", b"").decode()

    metadata: dict[str, Any] = {
        "trip_id": trip_id,
        "mcap_sha256": sha256,
        "duration_s": round(duration_s, 3),
        "n_gps": n_gps,
        "n_spd": n_spd,
        "n_imu": n_imu,
        "n_mag": n_mag,
        "generated_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }

    with open(meta_path, "w", encoding="utf-8", newline="\n") as fh:
        yaml.dump(metadata, fh, default_flow_style=False, sort_keys=False, allow_unicode=True)

    _ts2 = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    sys.stdout.write(
        f"[{_ts2}] [FR-3.1 bag] INFO  wrote {mcap_path}"
        f"  gps={n_gps}  spd={n_spd}  imu={n_imu}  mag={n_mag}\n"
    )
    sys.stdout.write(f"[{_ts2}] [FR-3.2 bag] INFO  metadata → {meta_path}\n")

    return mcap_path


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Convert aligned_100hz.parquet to a ROS 2 MCAP bag (FR-3.1)."
    )
    p.add_argument("--parquet", required=True, type=Path, help="Path to aligned_100hz.parquet")
    p.add_argument(
        "--noise-fit",
        required=True,
        type=Path,
        dest="noise_fit",
        help="Path to noise_fit_{trip_id}.yaml (for gyro covariance)",
    )
    p.add_argument(
        "--out-dir",
        required=True,
        type=Path,
        dest="out_dir",
        help="Output directory (trip.mcap and trip.metadata.yaml written here)",
    )
    return p


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = _build_parser().parse_args(argv)
    convert(
        parquet_path=args.parquet,
        noise_fit_path=args.noise_fit,
        out_dir=args.out_dir,
    )


if __name__ == "__main__":
    main()
