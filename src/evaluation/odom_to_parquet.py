"""Bridge: convert /fused/odom recorded in an MCAP bag back to Parquet.

Used after bag replay so Python evaluation code can consume filter output
without reading MCAP directly (TRD §2.6 language-boundary contract).

Implemented in task T2.8.
"""
