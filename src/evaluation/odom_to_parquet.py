"""Bridge: convert /fused/odom recorded in an MCAP bag back to Parquet.

Used after bag replay so Python evaluation code can consume filter output
without reading MCAP directly (TRD §2.6 language-boundary contract).

Thin wrapper around bag_bridge.mcap_to_parquet — all logic lives there.

Implemented in task T2.8.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd


def convert(bag: Path, out_path: Path) -> Path:
    """Read /fused/odom from bag, write Parquet, return out_path.

    Delegates to bag_bridge.mcap_to_parquet.convert().
    """
    from bag_bridge.mcap_to_parquet import convert as _bridge_convert

    return _bridge_convert(bag, out_path)


def load(parquet_path: Path) -> pd.DataFrame:
    """Load a fused odom Parquet file into a DataFrame."""
    return pd.read_parquet(parquet_path)
