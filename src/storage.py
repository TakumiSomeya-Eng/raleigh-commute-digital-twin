"""Storage abstraction layer — local filesystem or S3 (T7.1).

Provides transparent read/write for Parquet files and JSON blobs,
switching between local paths and S3 based on the S3_BUCKET
environment variable.

Design principles:
- Zero changes to existing Phase 1 code when S3_BUCKET is unset.
- When S3_BUCKET is set, all pipeline I/O goes through S3 using the
  prefix layout defined in FR-12.1.
- S3 paths follow the convention:
    s3://{bucket}/{stage}/{trip_id}/{filename}
  e.g. s3://rct-data-takumi2026/processed/day3/aligned_100hz.parquet

Usage:
    from storage import StorageAdapter
    store = StorageAdapter.from_env()

    # Write a DataFrame
    store.write_parquet(df, "processed", trip_id, "aligned_100hz.parquet")

    # Read a DataFrame
    df = store.read_parquet("fused", trip_id, "fused_ekf.parquet")

    # Write JSON
    store.write_json(data, "scores", trip_id, "score.json")

    # Read bytes (e.g. HTML templates, configs)
    content = store.read_bytes("reports", trip_id, "report.html")
"""

from __future__ import annotations

import io
import json
import os
import tempfile
from pathlib import Path
from typing import Any

import pandas as pd

# S3_BUCKET environment variable controls which backend is used.
_ENV_S3_BUCKET = "S3_BUCKET"
_ENV_AWS_REGION = "AWS_DEFAULT_REGION"


class StorageAdapter:
    """Unified read/write interface for local or S3 storage.

    Instantiate via ``StorageAdapter.from_env()`` — this reads S3_BUCKET
    from the environment and selects the appropriate backend automatically.
    """

    def __init__(self, bucket: str | None, out_dir: Path | None = None) -> None:
        self._bucket = bucket
        self._out_dir = out_dir or Path("out")
        self._s3: Any = None  # lazy boto3 client

        if bucket:
            import boto3  # noqa: PLC0415
            region = os.environ.get(_ENV_AWS_REGION, "us-east-1")
            self._s3 = boto3.client("s3", region_name=region)

    @classmethod
    def from_env(cls, out_dir: Path | None = None) -> "StorageAdapter":
        """Create from environment variables.

        If S3_BUCKET is set, uses S3 backend.
        Otherwise, uses local filesystem with out_dir as root.
        """
        bucket = os.environ.get(_ENV_S3_BUCKET)
        return cls(bucket=bucket, out_dir=out_dir)

    @property
    def is_s3(self) -> bool:
        """True when S3 backend is active."""
        return self._bucket is not None

    # ── Path helpers ──────────────────────────────────────────────────────────

    def local_path(self, stage: str, trip_id: str, filename: str) -> Path:
        """Return the local filesystem path for a given artifact."""
        return self._out_dir / trip_id / filename

    def s3_key(self, stage: str, trip_id: str, filename: str) -> str:
        """Return the S3 key for a given artifact (FR-12.1 prefix layout)."""
        return f"{stage}/{trip_id}/{filename}"

    # ── Parquet ───────────────────────────────────────────────────────────────

    def write_parquet(
        self,
        df: pd.DataFrame,
        stage: str,
        trip_id: str,
        filename: str,
        *,
        compression: str = "snappy",
    ) -> None:
        """Write a DataFrame as Parquet to local or S3 storage."""
        import pyarrow as pa  # noqa: PLC0415
        import pyarrow.parquet as pq  # noqa: PLC0415

        table = pa.Table.from_pandas(df, preserve_index=False)

        if self.is_s3:
            buf = io.BytesIO()
            pq.write_table(table, buf, compression=compression)
            buf.seek(0)
            key = self.s3_key(stage, trip_id, filename)
            self._s3.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=buf.getvalue(),
            )
        else:
            path = self.local_path(stage, trip_id, filename)
            path.parent.mkdir(parents=True, exist_ok=True)
            pq.write_table(table, path, compression=compression)

    def read_parquet(
        self,
        stage: str,
        trip_id: str,
        filename: str,
    ) -> pd.DataFrame:
        """Read a Parquet file from local or S3 storage."""
        if self.is_s3:
            key = self.s3_key(stage, trip_id, filename)
            response = self._s3.get_object(Bucket=self._bucket, Key=key)
            buf = io.BytesIO(response["Body"].read())
            return pd.read_parquet(buf)
        else:
            path = self.local_path(stage, trip_id, filename)
            return pd.read_parquet(path)

    # ── JSON ──────────────────────────────────────────────────────────────────

    def write_json(
        self,
        data: dict,  # type: ignore[type-arg]
        stage: str,
        trip_id: str,
        filename: str,
        *,
        indent: int = 2,
    ) -> None:
        """Write a dict as JSON to local or S3 storage."""
        body = json.dumps(data, indent=indent, ensure_ascii=False).encode("utf-8")
        if self.is_s3:
            key = self.s3_key(stage, trip_id, filename)
            self._s3.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=body,
                ContentType="application/json",
            )
        else:
            path = self.local_path(stage, trip_id, filename)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(body)

    def read_json(
        self,
        stage: str,
        trip_id: str,
        filename: str,
    ) -> dict:  # type: ignore[type-arg]
        """Read a JSON file from local or S3 storage."""
        if self.is_s3:
            key = self.s3_key(stage, trip_id, filename)
            response = self._s3.get_object(Bucket=self._bucket, Key=key)
            return json.loads(response["Body"].read())
        else:
            path = self.local_path(stage, trip_id, filename)
            return json.loads(path.read_bytes())

    # ── Raw bytes ─────────────────────────────────────────────────────────────

    def write_bytes(
        self,
        data: bytes,
        stage: str,
        trip_id: str,
        filename: str,
        content_type: str = "application/octet-stream",
    ) -> None:
        """Write raw bytes to local or S3 storage."""
        if self.is_s3:
            key = self.s3_key(stage, trip_id, filename)
            self._s3.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=data,
                ContentType=content_type,
            )
        else:
            path = self.local_path(stage, trip_id, filename)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)

    def read_bytes(
        self,
        stage: str,
        trip_id: str,
        filename: str,
    ) -> bytes:
        """Read raw bytes from local or S3 storage."""
        if self.is_s3:
            key = self.s3_key(stage, trip_id, filename)
            response = self._s3.get_object(Bucket=self._bucket, Key=key)
            return response["Body"].read()
        else:
            path = self.local_path(stage, trip_id, filename)
            return path.read_bytes()

    # ── CSV (raw input upload) ────────────────────────────────────────────────

    def download_raw_csv_dir(self, trip_id: str, local_dir: Path) -> None:
        """Download all CSVs from S3 raw/{trip_id}/ to a local temp directory.

        Used by the ingest stage to fetch Sensor Logger CSVs from S3.
        No-op when running locally (S3_BUCKET not set).
        """
        if not self.is_s3:
            return  # local run: CSVs are already on disk

        import boto3  # noqa: PLC0415
        paginator = self._s3.get_paginator("list_objects_v2")
        prefix = f"raw/{trip_id}/"
        local_dir.mkdir(parents=True, exist_ok=True)

        for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                filename = key.split("/")[-1]
                if not filename:
                    continue
                local_path = local_dir / filename
                self._s3.download_file(self._bucket, key, str(local_path))

    # ── URL helpers ───────────────────────────────────────────────────────────

    def public_url(
        self,
        stage: str,
        trip_id: str,
        filename: str,
        region: str = "us-east-1",
    ) -> str:
        """Return a best-effort URL for the artifact (for SNS notification)."""
        if self.is_s3:
            key = self.s3_key(stage, trip_id, filename)
            return f"https://{self._bucket}.s3.{region}.amazonaws.com/{key}"
        return str(self.local_path(stage, trip_id, filename))
