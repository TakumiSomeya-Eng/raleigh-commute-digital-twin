"""Orchestrate sensor-fusion run: play MCAP bag, record /fused/odom, convert to Parquet.

Usage (called by Makefile `fuse` target):
    PYTHONPATH=src python scripts/run_fuse.py \\
        --trace day2 --filter ekf --out-dir out

Steps:
    1. Validate that out/{trace}/trip.mcap exists.
    2. Launch the chosen filter node via ros2 launch.
    3. Simultaneously record /fused/odom to out/{trace}/fused_{filter}_bag/.
    4. Wait for bag playback to finish; terminate the recording.
    5. Convert the recorded bag to out/{trace}/fused_{filter}.parquet.

Requires a sourced ROS 2 environment (ros2 on PATH).
"""

from __future__ import annotations

import argparse
import datetime
import shutil
import subprocess
import sys
import time
from pathlib import Path


def _log(tag: str, msg: str) -> None:
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    sys.stdout.write(f"[{ts}] [{tag}] INFO  {msg}\n")
    sys.stdout.flush()


def _require_ros2() -> None:
    if shutil.which("ros2") is None:
        sys.stderr.write("ERROR: ros2 not found on PATH — source your ROS 2 workspace first.\n")
        sys.exit(1)


def run_fuse(trace: str, filter_name: str, out_dir: Path) -> Path:
    """Run full fusion pipeline; return path to output Parquet file."""
    trip_mcap = out_dir / trace / "trip.mcap"
    if not trip_mcap.exists():
        sys.stderr.write(f"ERROR: {trip_mcap} not found — run `make bag TRACE={trace}` first.\n")
        sys.exit(1)

    bag_out_dir = out_dir / trace / f"fused_{filter_name}_bag"
    parquet_out = out_dir / trace / f"fused_{filter_name}.parquet"
    # Remove stale bag directory so recorder starts fresh (idempotent re-runs).
    if bag_out_dir.exists():
        shutil.rmtree(bag_out_dir)
    bag_out_dir.mkdir(parents=True, exist_ok=True)

    _log("FR-4.2 fuse", f"TRACE={trace} FILTER={filter_name}")
    _log("FR-4.2 fuse", f"trip={trip_mcap}")

    # Launch filter node + bag playback (ros2 launch already wires both).
    launch_cmd = [
        "ros2",
        "launch",
        "localization",
        f"{filter_name}.launch.py",
        f"bag:={trip_mcap}",
    ]

    # Record /fused/odom.
    record_cmd = [
        "ros2",
        "bag",
        "record",
        "-o",
        str(bag_out_dir),
        "/fused/odom",
    ]

    _log("FR-4.2 fuse", f"starting recorder -> {bag_out_dir}")
    recorder = subprocess.Popen(record_cmd)
    # Brief pause so the recorder subscribes before messages start.
    time.sleep(1.0)

    _log("FR-4.2 fuse", f"starting launch: {' '.join(launch_cmd)}")
    launcher = subprocess.Popen(launch_cmd)

    # Wait for launcher to exit (bag playback finishes).
    launcher.wait()
    _log("FR-4.2 fuse", "bag playback finished")

    # Give recorder a moment to flush, then terminate.
    time.sleep(0.5)
    recorder.terminate()
    try:
        recorder.wait(timeout=5)
    except subprocess.TimeoutExpired:
        recorder.kill()
    _log("FR-4.2 fuse", "recorder stopped")

    # Convert recorded bag to Parquet.
    _log("FR-4.2 fuse", f"converting bag -> {parquet_out}")
    from bag_bridge.mcap_to_parquet import convert

    convert(bag_out_dir, parquet_out)
    _log("FR-4.2 fuse", f"done: {parquet_out} ({parquet_out.stat().st_size} bytes)")
    return parquet_out


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Run EKF/UKF fusion and record /fused/odom")
    p.add_argument("--trace", required=True, help="trace name (e.g. day2)")
    p.add_argument("--filter", dest="filter_name", default="ekf", help="ekf or ukf")
    p.add_argument("--out-dir", type=Path, default=Path("out"), help="output root dir")
    return p


def main(argv: list[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)
    _require_ros2()
    run_fuse(args.trace, args.filter_name, args.out_dir)


if __name__ == "__main__":
    main()
