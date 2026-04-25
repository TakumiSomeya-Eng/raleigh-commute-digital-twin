"""FR-1.4 — Schema-validated Parquet reader / writer.

Single entry point for all Parquet I/O in this project.
Enforces Snappy compression, a fixed row-group size, and the metadata
keys listed in TRD §1.11 (trip_id, git_sha, schema_version,
generated_at_utc).

See: TRD sec.1.11, FRD FR-1.4
"""

from __future__ import annotations

import datetime
import subprocess
from pathlib import Path
from typing import TypeVar

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from pydantic import BaseModel, ValidationError

from data_engine.errors import SchemaValidationError

M = TypeVar("M", bound=BaseModel)

_SCHEMA_VERSION = "1.0"
# 100,000 rows ≈ 1,000 s ≈ 16.7 min at 100 Hz (TRD §1.11).
_ROW_GROUP_SIZE = 100_000


def _git_sha() -> str:
    """Return short HEAD SHA, or 'unknown' if git is unavailable."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()
    except Exception:
        return "unknown"


def write_parquet(
    df: pd.DataFrame,
    path: Path,
    schema_cls: type[BaseModel],
    trip_id: str,
    extra_metadata: dict[str, str] | None = None,
) -> None:
    """Write *df* to a schema-validated Parquet file.

    Validates the first row against *schema_cls* and checks the entire
    DataFrame for NaNs in float columns before writing.

    Args:
        df: DataFrame to serialise.
        path: Output file path (parent directory is created if absent).
        schema_cls: Pydantic model for schema validation.
        trip_id: Written to the ``trip_id`` Parquet metadata key.
        extra_metadata: Additional string key-value pairs for file metadata
            (e.g. ``{"base_trip_id": "day2", "seed": "42"}``).

    Raises:
        SchemaValidationError: On schema violation or NaN in float columns.
    """
    if len(df) == 0:
        raise SchemaValidationError(schema_cls.__name__, "Empty DataFrame")

    # Smoke-check the first row through pydantic.
    try:
        schema_cls(**df.iloc[0].to_dict())
    except ValidationError as exc:
        raise SchemaValidationError(schema_cls.__name__, str(exc)) from exc

    # Reject NaNs in float columns (TRD §1.2: NaN-free after warm-up drop).
    float_cols = df.select_dtypes(include="float").columns.tolist()
    nan_cols = [c for c in float_cols if df[c].isna().any()]
    if nan_cols:
        raise SchemaValidationError(schema_cls.__name__, f"NaN in columns: {nan_cols}")

    path.parent.mkdir(parents=True, exist_ok=True)

    table = pa.Table.from_pandas(df, preserve_index=False)
    kv: dict[bytes, bytes] = {
        k.encode(): v.encode()
        for k, v in {
            "trip_id": trip_id,
            "git_sha": _git_sha(),
            "schema_version": _SCHEMA_VERSION,
            "generated_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            **(extra_metadata or {}),
        }.items()
    }
    # Merge with any existing schema metadata (e.g. pandas index info).
    existing = table.schema.metadata or {}
    table = table.replace_schema_metadata({**existing, **kv})

    pq.write_table(
        table,
        path,
        compression="snappy",
        row_group_size=_ROW_GROUP_SIZE,
    )


def read_parquet(
    path: Path,
    schema_cls: type[M] | None = None,
) -> pd.DataFrame:
    """Read a Parquet file and optionally validate its first row.

    Args:
        path: Path to the Parquet file.
        schema_cls: If provided, the first row is validated against this
            pydantic model.

    Returns:
        DataFrame with the file contents.

    Raises:
        SchemaValidationError: If *schema_cls* is given and validation fails.
    """
    df = pd.read_parquet(path)
    if schema_cls is not None and len(df) > 0:
        try:
            schema_cls(**df.iloc[0].to_dict())
        except ValidationError as exc:
            raise SchemaValidationError(schema_cls.__name__, str(exc)) from exc
    return df
