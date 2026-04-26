"""Integration test: headless EKF bag replay (FR-8.3).

Plays tests/fixtures/tiny_day2_60s.mcap through ekf_node and asserts:
  - >= 5500 /fused/odom messages received (60 s x 100 Hz x 0.9 tolerance)
  - Final ENU position within 50 m of the last GPS fix in the bag

Requires: ROS 2 Humble (rclpy), ekf_node built in the ROS 2 workspace.
Skip conditions:
  - rclpy not importable (no ROS 2 installation)
  - tests/fixtures/tiny_day2_60s.mcap does not exist (run `make fixture` first)
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Skip guards
# ---------------------------------------------------------------------------

rclpy = pytest.importorskip("rclpy", reason="rclpy not available — ROS 2 required")

FIXTURE_MCAP = Path(__file__).parents[2] / "tests" / "fixtures" / "tiny_day2_60s" / "trip.mcap"

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _require_fixture() -> None:
    if not FIXTURE_MCAP.exists():
        pytest.skip(
            f"Fixture not found: {FIXTURE_MCAP}\n" "Generate it with: make fixture TRACE=day2"
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _last_gps_enu(mcap_path: Path) -> tuple[float, float]:
    """Return the ENU position of the last GPS fix in the MCAP."""
    import math
    import struct

    from mcap.reader import make_reader

    r_earth = 6_371_000.0
    lat0 = math.radians(35.773)
    lon0 = math.radians(-78.610)

    last_lat = last_lon = None
    with open(mcap_path, "rb") as f:
        reader = make_reader(f)
        for _schema, _channel, message in reader.iter_messages(topics=["/gps/fix"]):
            # Parse CDR: skip header + stamp + frame_id string + NavSatStatus, then lat/lon

            buf = message.data
            off = 4 + 8  # CDR header + stamp
            # string frame_id
            data_pos = off - 4
            pad = (-data_pos) % 4
            off += pad
            (slen,) = struct.unpack_from("<I", buf, off)
            off += 4 + slen
            # NavSatStatus int8 + uint16
            off += 1
            data_pos = off - 4
            pad = (-data_pos) % 2
            off += pad + 2
            # float64 lat/lon
            data_pos = off - 4
            pad = (-data_pos) % 8
            off += pad
            lat, lon = struct.unpack_from("<dd", buf, off)
            last_lat, last_lon = lat, lon

    assert last_lat is not None, "No GPS messages found in fixture"
    px = (math.radians(last_lon) - lon0) * math.cos(lat0) * r_earth
    py = (math.radians(last_lat) - lat0) * r_earth
    return px, py


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------


class TestHeadlessEkf:
    """Run ekf_node against tiny_day2_60s.mcap and check odom output."""

    def test_message_count_and_position(self) -> None:
        import math

        import rclpy
        from nav_msgs.msg import Odometry

        rclpy.init()
        node = rclpy.create_node("test_headless_ekf")

        odom_msgs: list[Odometry] = []

        def _cb(msg: Odometry) -> None:
            odom_msgs.append(msg)

        sub = node.create_subscription(Odometry, "/fused/odom", _cb, 200)

        # Play bag in a subprocess.
        bag_dir = str(FIXTURE_MCAP.parent)
        bag_proc = subprocess.Popen(
            ["ros2", "bag", "play", bag_dir, "--clock", "--rate", "5.0"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        try:
            # Spin for up to 30 s wall-clock (60 s bag at 5x rate = 12 s).
            deadline = time.monotonic() + 30.0
            while time.monotonic() < deadline and bag_proc.poll() is None:
                rclpy.spin_once(node, timeout_sec=0.1)

            # Drain any remaining queued messages.
            for _ in range(50):
                rclpy.spin_once(node, timeout_sec=0.05)

        finally:
            bag_proc.terminate()
            bag_proc.wait(timeout=5)
            node.destroy_subscription(sub)
            node.destroy_node()
            rclpy.shutdown()

        # --- Assertions ---
        n_msgs = len(odom_msgs)
        assert n_msgs >= 5500, (
            f"Expected >= 5500 /fused/odom messages, got {n_msgs}. "
            "EKF may have crashed or not initialized."
        )

        # Final position vs GPS ground truth.
        last_odom = odom_msgs[-1]
        px_ekf = last_odom.pose.pose.position.x
        py_ekf = last_odom.pose.pose.position.y

        px_gps, py_gps = _last_gps_enu(FIXTURE_MCAP)
        dist = math.hypot(px_ekf - px_gps, py_ekf - py_gps)
        assert dist < 50.0, (
            f"EKF final position ({px_ekf:.1f}, {py_ekf:.1f}) is {dist:.1f} m "
            f"from GPS final ({px_gps:.1f}, {py_gps:.1f}) — exceeds 50 m threshold."
        )
