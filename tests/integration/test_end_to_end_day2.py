"""End-to-end smoke test: full pipeline on day2 (T4.8).

Validates that each pipeline stage has produced output files with the correct
schemas, and — when Valhalla is reachable — runs the ideal-driver + scoring
pipeline to verify score.json (FR-10.7).

Mark: pytest.mark.integration  (skipped in normal unit-test runs)

Two phases:
  A. Static schema checks   — P1-P3 outputs in out/day2/ (no external services)
  B. Scoring smoke test     — runs ideal pipeline + scoring in a tmp_path;
                              skipped if Valhalla is not reachable at
                              http://localhost:8002
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

pytestmark = pytest.mark.integration

_REPO_ROOT: Path = Path(__file__).parents[2]
_OUT_DAY2: Path = _REPO_ROOT / "out" / "day2"
_SRC: Path = _REPO_ROOT / "src"
_CONFIG_DIR: Path = _REPO_ROOT / "config"
_VALHALLA_URL: str = "http://localhost:8002"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _valhalla_reachable(url: str = _VALHALLA_URL, timeout: float = 2.0) -> bool:
    """Return True if Valhalla status endpoint responds within *timeout* s."""
    try:
        import urllib.request

        req = urllib.request.urlopen(f"{url}/status", timeout=timeout)
        return req.getcode() < 500
    except Exception:
        return False


def _run(args: list[str], cwd: Path = _REPO_ROOT) -> subprocess.CompletedProcess:
    """Run a subprocess; raise on non-zero exit."""
    env_copy = {"PYTHONPATH": str(_SRC)}
    import os

    env = {**os.environ, **env_copy}
    result = subprocess.run(
        [sys.executable, *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Command {args[0]} failed (rc={result.returncode}):\n"
            f"STDOUT: {result.stdout[-2000:]}\n"
            f"STDERR: {result.stderr[-2000:]}"
        )
    return result


# ---------------------------------------------------------------------------
# Phase A — static schema checks on existing P1-P3 outputs
# ---------------------------------------------------------------------------


class TestPipelineOutputSchemas:
    """Verify P1-P3 stage outputs exist and match expected schemas."""

    def _require_out_day2(self) -> None:
        if not _OUT_DAY2.exists():
            pytest.skip(f"out/day2/ not found at {_OUT_DAY2} — run P1-P3 pipeline first.")

    # --- aligned_100hz.parquet (P1 / FR-1.5) ---

    def test_aligned_parquet_exists(self) -> None:
        self._require_out_day2()
        assert (_OUT_DAY2 / "aligned_100hz.parquet").exists()

    def test_aligned_parquet_schema(self) -> None:
        self._require_out_day2()
        df = pd.read_parquet(_OUT_DAY2 / "aligned_100hz.parquet")
        required = {
            "t_s",
            "px_m",
            "py_m",
            "lat_wgs84",
            "lon_wgs84",
            "gps_speed_mps",
            "ax_mps2",
            "ay_mps2",
            "az_mps2",
        }
        missing = required - set(df.columns)
        assert not missing, f"aligned_100hz missing columns: {missing}"
        assert len(df) > 10_000, f"aligned_100hz too short: {len(df)} rows"

    # --- fused_ekf.parquet (P2 / FR-4.2) ---

    def test_fused_ekf_exists(self) -> None:
        self._require_out_day2()
        assert (_OUT_DAY2 / "fused_ekf.parquet").exists()

    def test_fused_ekf_schema(self) -> None:
        self._require_out_day2()
        df = pd.read_parquet(_OUT_DAY2 / "fused_ekf.parquet")
        required = {"t_s", "px_m", "py_m", "v_mps", "psi_rad", "psi_dot_rps"}
        missing = required - set(df.columns)
        assert not missing, f"fused_ekf missing columns: {missing}"
        assert len(df) > 50_000, f"fused_ekf too short: {len(df)} rows"

    # --- ground_truth.parquet (P3 / FR-6.2) ---

    def test_ground_truth_exists(self) -> None:
        self._require_out_day2()
        assert (_OUT_DAY2 / "ground_truth.parquet").exists()

    def test_ground_truth_schema(self) -> None:
        self._require_out_day2()
        df = pd.read_parquet(_OUT_DAY2 / "ground_truth.parquet")
        required = {"t_s", "px_m", "py_m", "v_mps", "psi_rad"}
        missing = required - set(df.columns)
        assert not missing, f"ground_truth missing columns: {missing}"

    # --- rmse_report_ekf.json (P3 / FR-6.4) ---

    def test_rmse_report_exists(self) -> None:
        self._require_out_day2()
        assert (_OUT_DAY2 / "rmse_report_ekf.json").exists()

    def test_rmse_report_schema(self) -> None:
        self._require_out_day2()
        data = json.loads((_OUT_DAY2 / "rmse_report_ekf.json").read_text())
        required = {"trip_id", "filter", "overall_rmse_m", "gps_only_rmse_m", "improvement_pct"}
        missing = required - set(data.keys())
        assert not missing, f"rmse_report missing keys: {missing}"

    def test_rmse_improvement_positive(self) -> None:
        """EKF RMSE should be better than GPS-only (P3 gate: improvement > 0)."""
        self._require_out_day2()
        data = json.loads((_OUT_DAY2 / "rmse_report_ekf.json").read_text())
        assert (
            data["improvement_pct"] > 0.0
        ), f"EKF provides no improvement over GPS-only: {data['improvement_pct']:.1f}%"

    # --- filter_comparison.json (P3 / FR-6.5) ---

    def test_filter_comparison_exists(self) -> None:
        self._require_out_day2()
        assert (_OUT_DAY2 / "filter_comparison.json").exists()

    def test_filter_comparison_schema(self) -> None:
        self._require_out_day2()
        data = json.loads((_OUT_DAY2 / "filter_comparison.json").read_text())
        assert "trip_id" in data
        assert "overall" in data


# ---------------------------------------------------------------------------
# Phase B — scoring smoke test (requires Valhalla)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def score_doc(tmp_path_factory: pytest.TempPathFactory) -> dict:
    """Run ideal pipeline + scoring in a fresh tmp dir; return score.json dict.

    Skipped if:
    - out/day2/fused_ekf.parquet does not exist (P2 not yet run)
    - Valhalla is not reachable at http://localhost:8002
    """
    fused_src = _OUT_DAY2 / "fused_ekf.parquet"
    if not fused_src.exists():
        pytest.skip(f"fused_ekf.parquet not found at {fused_src} — run P2 first.")

    if not _valhalla_reachable():
        pytest.skip(
            f"Valhalla not reachable at {_VALHALLA_URL} — "
            "start the container with `docker compose up valhalla` first."
        )

    out_dir = tmp_path_factory.mktemp("score_day2")
    trace_dir = out_dir / "day2"
    trace_dir.mkdir()

    # Copy fused_ekf.parquet into the temp tree so ideal_driver can find it.
    shutil.copy2(fused_src, trace_dir / "fused_ekf.parquet")

    # Stage 1: map-match via Valhalla → route_matched.parquet
    _run(
        [
            "-m",
            "ideal_driver",
            "match",
            "--trace",
            "day2",
            "--out-dir",
            str(out_dir),
            "--config",
            str(_CONFIG_DIR / "data_gen.yaml"),
            "--url",
            _VALHALLA_URL,
        ]
    )
    assert (trace_dir / "route_matched.parquet").exists(), "route_matched.parquet missing"

    # Stage 2: road centerline → reference_path.parquet (skip Overpass enrichment)
    _run(
        [
            "-m",
            "ideal_driver",
            "ref",
            "--trace",
            "day2",
            "--out-dir",
            str(out_dir),
            "--config",
            str(_CONFIG_DIR / "ideal.yaml"),
            "--speed-limits",
            str(_CONFIG_DIR / "speed_limits.yaml"),
            "--skip-overpass",
        ]
    )
    assert (trace_dir / "reference_path.parquet").exists(), "reference_path.parquet missing"

    # Stage 3: ideal speed profile → ideal_speed.parquet
    _run(
        [
            "-m",
            "ideal_driver",
            "speed",
            "--trace",
            "day2",
            "--out-dir",
            str(out_dir),
            "--config",
            str(_CONFIG_DIR / "ideal.yaml"),
        ]
    )
    assert (trace_dir / "ideal_speed.parquet").exists(), "ideal_speed.parquet missing"

    # Stage 4: quintic trajectory synthesis → ideal_trajectory.parquet
    _run(
        [
            "-m",
            "ideal_driver",
            "traj",
            "--trace",
            "day2",
            "--out-dir",
            str(out_dir),
            "--config",
            str(_CONFIG_DIR / "ideal.yaml"),
        ]
    )
    assert (trace_dir / "ideal_trajectory.parquet").exists(), "ideal_trajectory.parquet missing"

    # Stage 5: scoring → score.json
    _run(
        [
            "-m",
            "scoring",
            "score",
            "--trace",
            "day2",
            "--filter",
            "ekf",
            "--out-dir",
            str(out_dir),
            "--config",
            str(_CONFIG_DIR / "scoring.yaml"),
            "--ideal-config",
            str(_CONFIG_DIR / "ideal.yaml"),
        ]
    )
    score_path = trace_dir / "score.json"
    assert score_path.exists(), "score.json missing"

    return json.loads(score_path.read_text())


class TestScoringSmoke:
    """Verify score.json schema and value assertions (Valhalla-gated)."""

    def test_schema_keys(self, score_doc: dict) -> None:
        required = {
            "trip_id",
            "config_hash",
            "fused_source",
            "components",
            "aggregate_raw",
            "score_0_100",
            "suggested_tip_band",
            "suggested_tip_pct",
            "timestamp_utc",
            "notes",
        }
        missing = required - set(score_doc.keys())
        assert not missing, f"score.json missing keys: {missing}"

    def test_trip_id(self, score_doc: dict) -> None:
        assert score_doc["trip_id"] == "day2"

    def test_six_components(self, score_doc: dict) -> None:
        names = ("jerk", "harsh_brake", "lat_accel", "speed", "deviation", "lane_change")
        for name in names:
            assert name in score_doc["components"], f"component {name!r} missing"

    def test_component_penalties_in_unit_interval(self, score_doc: dict) -> None:
        for name, sub in score_doc["components"].items():
            assert 0.0 <= sub["raw"] <= 1.0, f"{name} raw={sub['raw']} out of [0,1]"

    def test_aggregate_raw_lt_03(self, score_doc: dict) -> None:
        """Day2 is a calm commute — aggregate penalty should be < 0.3."""
        agg = score_doc["aggregate_raw"]
        assert agg < 0.3, f"aggregate_raw={agg:.4f} >= 0.3 — drive scored poorly"

    def test_score_above_80(self, score_doc: dict) -> None:
        """Day2 calm drive should score >= 80 / 100."""
        s = score_doc["score_0_100"]
        assert s >= 80.0, f"score_0_100={s:.1f} < 80 — unexpectedly low for calm drive"

    def test_aggregate_consistency(self, score_doc: dict) -> None:
        """score_0_100 == round(100 * (1 - aggregate_raw), 4)."""
        expected = round(100.0 * (1.0 - score_doc["aggregate_raw"]), 4)
        assert abs(score_doc["score_0_100"] - expected) < 0.01

    def test_config_hash_format(self, score_doc: dict) -> None:
        assert score_doc["config_hash"].startswith("sha256:")

    def test_notes_disclaimer(self, score_doc: dict) -> None:
        assert "SUGGESTED" in score_doc["notes"]
        assert "manual" in score_doc["notes"]

    def test_tip_pct_is_integer(self, score_doc: dict) -> None:
        assert isinstance(score_doc["suggested_tip_pct"], int)

    def test_json_serialisable(self, score_doc: dict) -> None:
        recovered = json.loads(json.dumps(score_doc))
        assert recovered["trip_id"] == score_doc["trip_id"]
