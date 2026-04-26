"""CDR (Common Data Representation) serializer and parser for ROS 2 messages.

Serializers (parquet_to_mcap):
  sensor_msgs/msg/NavSatFix
  sensor_msgs/msg/Imu
  sensor_msgs/msg/MagneticField

Parser (mcap_to_parquet):
  nav_msgs/msg/Odometry  →  parse_odometry_cdr()

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
# _CdrReader — minimal parser for nav_msgs/msg/Odometry
# ---------------------------------------------------------------------------


class _CdrReader:
    """Sequential CDR byte reader; alignment is data-section-relative (after 4-byte header)."""

    def __init__(self, data: bytes) -> None:
        self._buf = data
        self._pos: int = 4  # skip CDR encapsulation header

    def _data_pos(self) -> int:
        return self._pos - 4

    def _align(self, n: int) -> None:
        self._pos += (-self._data_pos()) % n

    def int32(self) -> int:
        self._align(4)
        (v,) = struct.unpack_from("<i", self._buf, self._pos)
        self._pos += 4
        return v

    def uint32(self) -> int:
        self._align(4)
        (v,) = struct.unpack_from("<I", self._buf, self._pos)
        self._pos += 4
        return v

    def float64(self) -> float:
        self._align(8)
        (v,) = struct.unpack_from("<d", self._buf, self._pos)
        self._pos += 8
        return v

    def float64_array(self, n: int) -> list[float]:
        return [self.float64() for _ in range(n)]

    def string(self) -> str:
        length = self.uint32()  # includes null terminator
        s = self._buf[self._pos : self._pos + length - 1].decode("utf-8")
        self._pos += length
        return s


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


def serialize_odometry(
    sec: int,
    nanosec: int,
    frame_id: str,
    child_frame_id: str,
    px: float,
    py: float,
    pz: float,
    qx: float,
    qy: float,
    qz: float,
    qw: float,
    pose_cov: list[float],
    vx: float,
    vy: float,
    vz: float,
    wx: float,
    wy: float,
    wz: float,
    twist_cov: list[float],
) -> bytes:
    """Serialize a nav_msgs/msg/Odometry message to CDR bytes."""
    w = _CdrWriter()
    _write_header(w, sec, nanosec, frame_id)
    w.string(child_frame_id)
    _write_vector3(w, px, py, pz)  # position
    w.float64(qx)  # orientation (x y z w)
    w.float64(qy)
    w.float64(qz)
    w.float64(qw)
    w.float64_array(pose_cov)  # pose covariance [36]
    _write_vector3(w, vx, vy, vz)  # twist linear
    _write_vector3(w, wx, wy, wz)  # twist angular
    w.float64_array(twist_cov)  # twist covariance [36]
    return w.build()


def parse_odometry_cdr(data: bytes) -> dict[str, float]:
    """Parse nav_msgs/msg/Odometry CDR bytes into a flat dict.

    Returns keys: t_s, px_m, py_m, v_mps, psi_rad, psi_dot_rps,
                  cov_xx, cov_yy, cov_yaw.
    """
    import math

    r = _CdrReader(data)

    # std_msgs/Header
    sec = r.int32()
    nanosec = r.uint32()
    _frame_id = r.string()
    _child = r.string()

    # geometry_msgs/PoseWithCovariance / Pose / Point
    px_m = r.float64()
    py_m = r.float64()
    _pz = r.float64()

    # geometry_msgs/Quaternion: field order x, y, z, w
    _qx = r.float64()
    _qy = r.float64()
    qz = r.float64()
    qw = r.float64()

    # pose covariance[36] — extract indices 0 (cov_xx), 7 (cov_yy), 35 (cov_yaw)
    pose_cov = r.float64_array(36)

    # geometry_msgs/TwistWithCovariance / Twist
    v_mps = r.float64()  # linear.x
    _vy = r.float64()
    _vz = r.float64()
    _wx = r.float64()
    _wy = r.float64()
    psi_dot_rps = r.float64()  # angular.z

    return {
        "t_s": sec + nanosec * 1e-9,
        "px_m": px_m,
        "py_m": py_m,
        "v_mps": v_mps,
        "psi_rad": 2.0 * math.atan2(qz, qw),
        "psi_dot_rps": psi_dot_rps,
        "cov_xx": pose_cov[0],
        "cov_yy": pose_cov[7],
        "cov_yaw": pose_cov[35],
    }
