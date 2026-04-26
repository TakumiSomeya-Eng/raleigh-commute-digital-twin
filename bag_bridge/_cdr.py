"""Minimal CDR (Common Data Representation) serializer for ROS 2 messages.

Only the three message types used by parquet_to_mcap are implemented:
  sensor_msgs/msg/NavSatFix
  sensor_msgs/msg/Imu
  sensor_msgs/msg/MagneticField

CDR encoding: little-endian, encapsulation header = 0x00 0x01 0x00 0x00.
Alignment is computed from offset 0 of the data section (after the 4-byte header).

Reference: OMG CDR spec; ROS 2 MCAP CDR encoding.
"""

from __future__ import annotations

import struct


class _CdrWriter:
    """Append-only CDR byte buffer with alignment padding."""

    _CDR_HEADER = b"\x00\x01\x00\x00"  # CDR_LE encapsulation

    def __init__(self) -> None:
        self._buf = bytearray(self._CDR_HEADER)

    def _pos(self) -> int:
        """Bytes written to the data section (excludes the 4-byte header)."""
        return len(self._buf) - 4

    def _pad(self, alignment: int) -> None:
        n = (-self._pos()) % alignment
        self._buf.extend(b"\x00" * n)

    # --- primitives ---

    def int8(self, v: int) -> None:
        self._buf.extend(struct.pack("<b", v))

    def uint8(self, v: int) -> None:
        self._buf.extend(struct.pack("<B", v))

    def uint16(self, v: int) -> None:
        self._pad(2)
        self._buf.extend(struct.pack("<H", v))

    def uint32(self, v: int) -> None:
        self._pad(4)
        self._buf.extend(struct.pack("<I", v))

    def float64(self, v: float) -> None:
        self._pad(8)
        self._buf.extend(struct.pack("<d", v))

    def float64_array(self, values: list[float]) -> None:
        for v in values:
            self.float64(v)

    def string(self, s: str) -> None:
        b = s.encode("utf-8") + b"\x00"
        self.uint32(len(b))
        self._buf.extend(b)

    def build(self) -> bytes:
        return bytes(self._buf)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ZEROS_9 = [0.0] * 9
_STATUS_FIX: int = 0
_SERVICE_GPS: int = 1
_COV_TYPE_DIAGONAL_KNOWN: int = 2


def _write_header(w: _CdrWriter, sec: int, nanosec: int, frame_id: str) -> None:
    w.uint32(sec)
    w.uint32(nanosec)
    w.string(frame_id)


def _write_vector3(w: _CdrWriter, x: float, y: float, z: float) -> None:
    w.float64(x)
    w.float64(y)
    w.float64(z)


# ---------------------------------------------------------------------------
# Public serializers
# ---------------------------------------------------------------------------


def serialize_navsatfix(
    sec: int,
    nanosec: int,
    frame_id: str,
    lat: float,
    lon: float,
    alt: float,
    hacc_m: float,
) -> bytes:
    """Serialize a sensor_msgs/msg/NavSatFix message to CDR bytes."""
    w = _CdrWriter()
    _write_header(w, sec, nanosec, frame_id)
    # NavSatStatus
    w.int8(_STATUS_FIX)
    w.uint16(_SERVICE_GPS)
    # fix data
    w.float64(lat)
    w.float64(lon)
    w.float64(alt)
    # position_covariance (diagonal: [sx^2, sx^2, sv^2, 0...])
    sx2 = hacc_m * hacc_m
    cov = [sx2, 0.0, 0.0, 0.0, sx2, 0.0, 0.0, 0.0, 9.0]
    w.float64_array(cov)
    w.uint8(_COV_TYPE_DIAGONAL_KNOWN)
    return w.build()


def serialize_imu(
    sec: int,
    nanosec: int,
    frame_id: str,
    qw: float,
    qx: float,
    qy: float,
    qz: float,
    ax: float,
    ay: float,
    az: float,
    gx: float,
    gy: float,
    gz: float,
    gyro_cov: list[float],
) -> bytes:
    """Serialize a sensor_msgs/msg/Imu message to CDR bytes."""
    w = _CdrWriter()
    _write_header(w, sec, nanosec, frame_id)
    # Quaternion: geometry_msgs field order is x, y, z, w
    w.float64(qx)
    w.float64(qy)
    w.float64(qz)
    w.float64(qw)
    # orientation_covariance: [0] = -1 flags orientation as unavailable
    oc = [-1.0] + [0.0] * 8
    w.float64_array(oc)
    # angular_velocity
    _write_vector3(w, gx, gy, gz)
    w.float64_array(gyro_cov)
    # linear_acceleration
    _write_vector3(w, ax, ay, az)
    # linear_acceleration_covariance: [0] = -1 (not provided by Sensor Logger)
    ac = [-1.0] + [0.0] * 8
    w.float64_array(ac)
    return w.build()


def serialize_magnetic_field(
    sec: int,
    nanosec: int,
    frame_id: str,
    mx: float,
    my: float,
    mz: float,
) -> bytes:
    """Serialize a sensor_msgs/msg/MagneticField message to CDR bytes.

    Sensor Logger reports in µT; ROS convention is Tesla (× 1e-6).
    """
    w = _CdrWriter()
    _write_header(w, sec, nanosec, frame_id)
    _write_vector3(w, mx * 1e-6, my * 1e-6, mz * 1e-6)
    w.float64_array(_ZEROS_9)  # covariance unknown
    return w.build()
