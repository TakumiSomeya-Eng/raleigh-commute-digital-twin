"""Regenerate tests/fixtures/ slices from processed data.

Usage:
    python scripts/make_fixtures.py --trace day2 --out-dir tests/fixtures

Generates:
    tests/fixtures/tiny_day2_60s/trip.mcap   — first 60 s of day2 bag (6000 rows @ 100 Hz)

Requires:
    out/{trace}/aligned_100hz.parquet  (produced by `make data TRACE={trace}`)
    config/noise_fit_{trace}.yaml      (produced by `make fit TRACE={trace}`)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def make_tiny_mcap(trace: str, out_dir: Path) -> None:
    """Slice first 60 s (6000 rows) of aligned_100hz.parquet and write MCAP."""
    import pandas as pd

    from bag_bridge.parquet_to_mcap import convert

    parquet_in = Path(f"out/{trace}/aligned_100hz.parquet")
    noise_fit = Path(f"config/noise_fit_{trace}.yaml")
    out_subdir = out_dir / f"tiny_{trace}_60s"

    if not parquet_in.exists():
        sys.stderr.write(
            f"[make_fixtures] ERROR: {parquet_in} not found. "
            f"Run `make data TRACE={trace}` first.\n"
        )
        sys.exit(1)

    if not noise_fit.exists():
        sys.stderr.write(
            f"[make_fixtures] ERROR: {noise_fit} not found. "
            f"Run `make fit TRACE={trace}` first.\n"
        )
        sys.exit(1)

    # Slice to first 6000 rows (60 s at 100 Hz) and write a temp parquet.
    df = pd.read_parquet(parquet_in)
    df_slice = df.iloc[:6000].copy()

    tmp_parquet = out_subdir / "aligned_100hz_60s.parquet"
    out_subdir.mkdir(parents=True, exist_ok=True)
    df_slice.to_parquet(tmp_parquet, index=False)

    convert(
        parquet_path=tmp_parquet,
        noise_fit_path=noise_fit,
        out_dir=out_subdir,
    )

    tmp_parquet.unlink()
    sys.stdout.write(f"[make_fixtures] wrote {out_subdir}/trip.mcap  ({len(df_slice)} rows)\n")


def main() -> None:
    p = argparse.ArgumentParser(description="Regenerate test fixtures from processed data.")
    p.add_argument("--trace", default="day2", help="Trace name (default: day2)")
    p.add_argument("--out-dir", type=Path, default=Path("tests/fixtures"), dest="out_dir")
    args = p.parse_args()

    sys.path.insert(0, "src")  # allow bag_bridge import without install
    make_tiny_mcap(args.trace, args.out_dir)


if __name__ == "__main__":
    main()
