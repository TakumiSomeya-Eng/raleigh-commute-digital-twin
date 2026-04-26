"""Unit tests for bag_bridge: CDR serializers and parquet_to_mcap converter.

Tests cover:
  - _CdrWriter alignment and primitive encoding
  - serialize_navsatfix / serialize_imu / serialize_magnetic_field byte layout
  - convert() end-to-end: writes a valid MCAP with correct message counts
"""

from __future__ import annotations

import struct
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

from bag_bridge._cdr import (
    _CdrWriter,
    serialize_imu,
    serialize_magnetic_field,
    serialize_navsatfix,
)

# ---------------------------------------------------------------------------
# _CdrWriter — primitive encoding and alignment
# ---------------------------------------------------------------------------


class TestCdrWriter:
    def test_header_present(self) -> None:
        w = _CdrWriter()
        assert w.build()[:4] == b"\x00\x01\x00\x00"

    def test_uint8_no_padding(self) -> None:
        w = _CdrWriter()
        w.uint8(0xAB)
        data = w.build()[4:]
        assert data == bytes([0xAB])

    def test_uint32_aligned(self) -> None:
        w = _CdrWriter()
        w.uint8(0x01)  # pos=1 after write
        w.uint32(0xDEADBEEF)  # must pad to pos=4, then write 4 bytes
        data = w.build()[4:]
        # byte 0: 0x01, bytes 1-3: padding, bytes 4-7: DEADBEEF LE
        assert data[0] == 0x01
        assert data[1:4] == b"\x00\x00\x00"
        (val,) = struct.unpack_from("<I", data, 4)
        assert val == 0xDEADBEEF

    def test_float64_aligned(self) -> None:
        w = _CdrWriter()
        w.uint8(0x01)  # pos=1
        w.float64(3.14)  # must pad to pos=8
        data = w.build()[4:]
        assert data[1:8] == b"\x00" * 7
        (val,) = struct.unpack_from("<d", data, 8)
        assert abs(val - 3.14) < 1e-15

    def test_string(self) -> None:
        w = _CdrWriter()
        w.string("hi")
        data = w.build()[4:]
        # uint32 length = 3 (2 chars + null), then "hi\x00"
        (length,) = struct.unpack_from("<I", data, 0)
        assert length == 3
        assert data[4:7] == b"hi\x00"


# ---------------------------------------------------------------------------
# serialize_navsatfix
# ---------------------------------------------------------------------------


def _cdr_pad(off: int, alignment: int) -> int:
    """Return new offset after CDR-spec alignment.

    CDR pads relative to the data section (which starts at absolute offset 4).
    """
    data_pos = off - 4
    return off + ((-data_pos) % alignment)


class TestSerializeNavSatFix:
    def _parse(self, buf: bytes) -> dict:
        off = 4  # skip 4-byte CDR header
        sec, nanosec = struct.unpack_from("<II", buf, off)
        off += 8
        # string: uint32 length + bytes
        off = _cdr_pad(off, 4)
        (slen,) = struct.unpack_from("<I", buf, off)
        off += 4
        frame_id = buf[off : off + slen - 1].decode()
        off += slen
        # NavSatStatus: int8 status (no alignment)
        status = struct.unpack_from("<b", buf, off)[0]
        off += 1
        # uint16 service
        off = _cdr_pad(off, 2)
        service = struct.unpack_from("<H", buf, off)[0]
        off += 2
        # float64 lat/lon/alt
        off = _cdr_pad(off, 8)
        lat, lon, alt = struct.unpack_from("<ddd", buf, off)
        off += 24
        cov = list(struct.unpack_from("<9d", buf, off))
        off += 72
        cov_type = struct.unpack_from("<B", buf, off)[0]
        return {
            "sec": sec,
            "nanosec": nanosec,
            "frame_id": frame_id,
            "status": status,
            "service": service,
            "lat": lat,
            "lon": lon,
            "alt": alt,
            "cov": cov,
            "cov_type": cov_type,
        }

    def test_basic(self) -> None:
        buf = serialize_navsatfix(1_700_000_000, 123_456_789, "gps", 35.773, -78.610, 0.0, 2.0)
        parsed = self._parse(buf)
        assert parsed["sec"] == 1_700_000_000
        assert parsed["nanosec"] == 123_456_789
        assert parsed["frame_id"] == "gps"
        assert parsed["lat"] == pytest.approx(35.773)
        assert parsed["lon"] == pytest.approx(-78.610)
        assert parsed["alt"] == 0.0

    def test_covariance_diagonal(self) -> None:
        buf = serialize_navsatfix(0, 0, "gps", 0.0, 0.0, 0.0, hacc_m=3.0)
        parsed = self._parse(buf)
        cov = parsed["cov"]
        assert cov[0] == pytest.approx(9.0)  # 3² = 9
        assert cov[4] == pytest.approx(9.0)
        assert cov[8] == pytest.approx(9.0)  # altitude variance
        # off-diagonal must be zero
        assert cov[1] == cov[2] == cov[3] == 0.0

    def test_cov_type(self) -> None:
        buf = serialize_navsatfix(0, 0, "gps", 0.0, 0.0, 0.0, 1.0)
        parsed = self._parse(buf)
        assert parsed["cov_type"] == 2  # COVARIANCE_TYPE_DIAGONAL_KNOWN


# ---------------------------------------------------------------------------
# serialize_magnetic_field
# ---------------------------------------------------------------------------


class TestSerializeMagneticField:
    def test_ut_to_tesla(self) -> None:
        buf = serialize_magnetic_field(0, 0, "mag", 10.0, 20.0, 30.0)
        off = 4 + 8  # skip CDR header + timestamp
        off = _cdr_pad(off, 4)
        (slen,) = struct.unpack_from("<I", buf, off)
        off += 4 + slen
        off = _cdr_pad(off, 8)
        x, y, z = struct.unpack_from("<ddd", buf, off)
        assert x == pytest.approx(10.0e-6)
        assert y == pytest.approx(20.0e-6)
        assert z == pytest.approx(30.0e-6)


# ---------------------------------------------------------------------------
# serialize_imu
# ---------------------------------------------------------------------------


class TestSerializeImu:
    @staticmethod
    def _skip_header(buf: bytes) -> int:
        """Return offset just past the std_msgs/Header (stamp + frame_id string)."""
        off = 4 + 8  # CDR header + timestamp
        off = _cdr_pad(off, 4)
        (slen,) = struct.unpack_from("<I", buf, off)
        off += 4 + slen
        return off

    def test_orientation_cov_flag(self) -> None:
        """orientation_covariance[0] must be -1 (orientation not available)."""
        gyro_cov = [0.01] * 9
        buf = serialize_imu(
            0, 0, "imu", 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 9.81, 0.0, 0.0, 0.0, gyro_cov
        )
        off = self._skip_header(buf)
        off = _cdr_pad(off, 8)
        off += 4 * 8  # skip quaternion (qx qy qz qw)
        (oc0,) = struct.unpack_from("<d", buf, off)
        assert oc0 == pytest.approx(-1.0)

    def test_quaternion_field_order(self) -> None:
        """geometry_msgs/Quaternion field order is x, y, z, w."""
        gyro_cov = [0.0] * 9
        buf = serialize_imu(0, 0, "imu", 1.0, 2.0, 3.0, 4.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, gyro_cov)
        off = self._skip_header(buf)
        off = _cdr_pad(off, 8)
        qx, qy, qz, qw = struct.unpack_from("<dddd", buf, off)
        assert qx == pytest.approx(2.0)  # qx arg
        assert qy == pytest.approx(3.0)  # qy arg
        assert qz == pytest.approx(4.0)  # qz arg
        assert qw == pytest.approx(1.0)  # qw arg


# ---------------------------------------------------------------------------
# convert() — end-to-end smoke test
# ---------------------------------------------------------------------------


def _make_fake_parquet(path: Path, n_rows: int = 10) -> None:
    """Write a minimal aligned_100hz.parquet with required columns."""
    rng = np.random.default_rng(0)
    t0_ns = 1_700_000_000 * 1_000_000_000
    time_ns = np.arange(n_rows, dtype=np.int64) * 10_000_000 + t0_ns  # 100 Hz

    df = pd.DataFrame(
        {
            "time_ns": time_ns,
            "t_s": time_ns / 1e9,
            "lat_wgs84": rng.uniform(35.77, 35.78, n_rows),
            "lon_wgs84": rng.uniform(-78.62, -78.60, n_rows),
            "horizontal_accuracy_m": rng.uniform(1.0, 5.0, n_rows),
            "speed_accuracy_mps": rng.uniform(0.1, 0.5, n_rows),
            "bearing_accuracy_deg": rng.uniform(1.0, 10.0, n_rows),
            "gps_speed_mps": rng.uniform(0.0, 15.0, n_rows),
            "gps_bearing_deg": rng.uniform(0.0, 360.0, n_rows),
            "ax_mps2": rng.normal(0, 0.2, n_rows),
            "ay_mps2": rng.normal(0, 0.2, n_rows),
            "az_mps2": rng.normal(9.8, 0.3, n_rows),
            "gx_rps": rng.normal(0, 0.27, n_rows),
            "gy_rps": rng.normal(0, 0.28, n_rows),
            "gz_rps": rng.normal(0, 0.15, n_rows),
            "grav_x": rng.normal(0, 0.32, n_rows),
            "grav_y": rng.normal(0, 0.33, n_rows),
            "grav_z": rng.normal(9.8, 0.01, n_rows),
            "quat_w": np.ones(n_rows),
            "quat_x": np.zeros(n_rows),
            "quat_y": np.zeros(n_rows),
            "quat_z": np.zeros(n_rows),
            "mag_x_uT": rng.normal(20, 2, n_rows),
            "mag_y_uT": rng.normal(5, 2, n_rows),
            "mag_z_uT": rng.normal(-45, 2, n_rows),
            "px_m": rng.uniform(0, 100, n_rows),
            "py_m": rng.uniform(0, 100, n_rows),
            # GPS present only on rows 0, 5 (2 real GPS fixes)
            "gps_interpolated": [i not in (0, 5) for i in range(n_rows)],
        }
    )
    df.to_parquet(path, index=False)


def _make_fake_noise_fit(path: Path) -> None:
    data = {
        "schema_version": "1.0",
        "trip_id": "test",
        "n_samples": 100,
        "channels": {
            "gx_rps": {"dist": "gaussian", "loc": 0.0, "scale": 0.27},
            "gy_rps": {"dist": "gaussian", "loc": 0.0, "scale": 0.28},
            "gz_rps": {"dist": "gaussian", "loc": 0.0, "scale": 0.15},
        },
    }
    path.write_text(yaml.dump(data), encoding="utf-8")


class TestConvert:
    def test_mcap_created(self, tmp_path: Path) -> None:
        parquet = tmp_path / "aligned_100hz.parquet"
        noise_fit = tmp_path / "noise_fit_test.yaml"
        _make_fake_parquet(parquet)
        _make_fake_noise_fit(noise_fit)

        from bag_bridge.parquet_to_mcap import convert

        mcap_path = convert(parquet, noise_fit, tmp_path)

        assert mcap_path.exists()
        assert mcap_path.stat().st_size > 0

    def test_metadata_yaml(self, tmp_path: Path) -> None:
        parquet = tmp_path / "aligned_100hz.parquet"
        noise_fit = tmp_path / "noise_fit_test.yaml"
        _make_fake_parquet(parquet, n_rows=10)
        _make_fake_noise_fit(noise_fit)

        from bag_bridge.parquet_to_mcap import convert

        convert(parquet, noise_fit, tmp_path)

        meta_path = tmp_path / "trip.metadata.yaml"
        assert meta_path.exists()
        meta = yaml.safe_load(meta_path.read_text(encoding="utf-8"))

        assert "mcap_sha256" in meta
        assert len(meta["mcap_sha256"]) == 64  # hex SHA-256
        assert meta["n_imu"] == 10
        assert meta["n_gps"] == 2  # rows 0 and 5 are real GPS
        assert meta["n_mag"] == 5  # rows 0,2,4,6,8 (every 2nd)
        assert meta["duration_s"] == pytest.approx(0.09, abs=0.001)

    def test_sha256_matches_file(self, tmp_path: Path) -> None:
        import hashlib

        parquet = tmp_path / "aligned_100hz.parquet"
        noise_fit = tmp_path / "noise_fit_test.yaml"
        _make_fake_parquet(parquet)
        _make_fake_noise_fit(noise_fit)

        from bag_bridge.parquet_to_mcap import convert

        mcap_path = convert(parquet, noise_fit, tmp_path)

        meta = yaml.safe_load((tmp_path / "trip.metadata.yaml").read_text(encoding="utf-8"))
        expected = hashlib.sha256(mcap_path.read_bytes()).hexdigest()
        assert meta["mcap_sha256"] == expected
