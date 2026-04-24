"""Parquet schema definitions — single source of truth for all inter-stage tabular data.

Every Parquet file written by this project has a corresponding pydantic model here.
All readers and writers must round-trip through these models.

Implemented in task T1.1.
See: TRD §1.2 – §1.7
"""
