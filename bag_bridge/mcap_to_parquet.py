"""Convert /fused/odom from an MCAP bag back to Parquet for Python evaluation.

Language boundary: C++ fusion code only reads MCAP; Python eval code only reads Parquet.
This bridge is the one-way crossing from the ROS side to the Python side.

CLI usage:
    python -m bag_bridge.mcap_to_parquet \\
        --bag  out/day2/fused_ekf/         (bag directory or .mcap file)
        --out  out/day2/fused_ekf.parquet

Output Parquet schema:
    t_s, px_m, py_m, v_mps, psi_rad, psi_dot_rps, cov_xx, cov_yy, cov_yaw

Implemented in task T2.8.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd
from mcap.reader import make_reader

from bag_bridge._cdr import parse_odometry_cdr

ODOM_TOPIC = "/fused/odom"

_COLUMNS = [
    "t_s",
    "px_m",
    "py_m",
    "v_mps",
    "psi_rad",
    "psi_dot_rps",
    "cov_xx",
    "cov_yy",
    "cov_yaw",
]


def _resolve_mcap(bag: Path) -> Path:
    """Return a single .mcap file path given a bag directory or .mcap file."""
    if bag.is_file() and bag.suffix == ".mcap":
        return bag
    if bag.is_dir():
        mcap_files = sorted(bag.glob("*.mcap"))
        if not mcap_files:
            raise FileNotFoundError(f"No .mcap files found in {bag}")
        return mcap_files[0]
    raise FileNotFoundError(f"Bag not found: {bag}")


def convert(bag: Path, out_path: Path) -> Path:
    """Read /fused/odom from a bag, write Parquet to out_path, return out_path.

    Args:
        bag:      Path to a .mcap file or a rosbag2 directory.
        out_path: Destination .parquet file path.

    Returns:
        out_path (written file).
    """
    mcap_file = _resolve_mcap(bag)
    rows: list[dict[str, float]] = []

    with mcap_file.open("rb") as f:
        reader = make_reader(f)
        for _schema, channel, message in reader.iter_messages(topics=[ODOM_TOPIC]):
            if channel.topic != ODOM_TOPIC:
                continue
            rows.append(parse_odometry_cdr(message.data))

    if not rows:
        raise ValueError(f"No messages found on {ODOM_TOPIC} in {mcap_file}")

    df = pd.DataFrame(rows, columns=_COLUMNS)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out_path, index=False)
    return out_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Convert /fused/odom from MCAP bag to Parquet (FR-3.2 bridge)"
    )
    p.add_argument("--bag", required=True, type=Path, help="bag directory or .mcap file")
    p.add_argument("--out", required=True, type=Path, help="output .parquet path")
    return p


def main(argv: list[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)
    out = convert(args.bag, args.out)
    sys.stdout.write(f"[mcap_to_parquet] wrote {out} ({out.stat().st_size} bytes)\n")


if __name__ == "__main__":
    main()
