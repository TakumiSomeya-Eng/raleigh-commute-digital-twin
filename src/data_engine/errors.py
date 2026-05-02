"""Shared exception types and exit-code taxonomy.

See: TRD sec.4.5
"""

from __future__ import annotations

from enum import IntEnum


class StageExitCode(IntEnum):
    """Exit code taxonomy for all CLI stages (TRD sec.4.5)."""

    SUCCESS = 0
    USER_ERROR = 1  # bad args, missing file, config validation fail
    DATA_ERROR = 2  # schema violation, NaN, empty input
    DEPENDENCY_ERROR = 3  # Valhalla unreachable, Docker service down
    GATE_FAILURE = 4  # KS-test failed, RMSE regressed
    IMPL_BUG = 64  # unreachable code, assertion failure


class MissingRequiredChannelError(Exception):
    """Raised when a required sensor channel is absent from the input CSV (FR-1.1)."""

    def __init__(self, channel: str) -> None:
        super().__init__(f"Required channel missing from input: {channel!r}")
        self.channel = channel


class SchemaValidationError(Exception):
    """Raised when a Parquet row fails pydantic schema validation (FR-1.4)."""

    def __init__(self, schema: str, detail: str) -> None:
        super().__init__(f"Schema validation failed for {schema!r}: {detail}")
        self.schema = schema
        self.detail = detail
