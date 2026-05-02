"""Tests for FR-10.7 / FR-10.8 — aggregate scoring and tip lookup.

Covers:
    TestTipLookup        — tip band selection from config (10 tests)
    TestComputeAggregate — weighted aggregate maths (5 tests)
    TestBuildScoreJson   — end-to-end score.json builder (11 tests)
    TestConfigHash       — sha256 hash stability (3 tests)
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import ClassVar

import numpy as np
import pandas as pd
import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SCORING_YAML_CONTENT = """
weights:
  jerk: 0.20
  harsh_brake: 0.20
  lat_accel: 0.20
  speed: 0.15
  deviation: 0.15
  lane_change: 0.10

saturation:
  jerk_mean_mps3: 3.0
  harsh_brake_epm: 2.0
  lat_accel_sq_mean_m2ps4: 4.0
  speed_sq_mean_mps2: 4.0
  deviation_mean_m: 3.0
  lane_change_epm: 2.0

speed:
  tolerance_mps: 0.89

lane_change:
  yaw_window_s: 3.0
  yaw_threshold_rad: 0.26
  sustained_window_s: 2.0
  lat_displacement_m: 2.0

tip_bands:
  - min_score: 90
    max_score: 100
    tip_pct: 25
    label: Excellent
  - min_score: 75
    max_score: 89
    tip_pct: 20
    label: Good
  - min_score: 50
    max_score: 74
    tip_pct: 15
    label: Fair
  - min_score: 0
    max_score: 49
    tip_pct: 10
    label: Poor
"""

_IDEAL_YAML_CONTENT = """
speed_profile:
  v_max_mps: 20.0
"""


def _write_tmp_yaml(content: str) -> Path:
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False, encoding="utf-8")
    f.write(content)
    f.close()
    return Path(f.name)


def _make_calm_fused(n: int = 600) -> pd.DataFrame:
    """Straight, constant-speed fused parquet (minimal penalties)."""
    t = np.linspace(0.0, (n - 1) / 100.0, n)
    v = np.full(n, 8.0)
    px = np.linspace(0.0, 8.0 * (n - 1) / 100.0, n)
    py = np.zeros(n)
    return pd.DataFrame(
        {
            "t_s": t,
            "px_m": px,
            "py_m": py,
            "v_mps": v,
            "psi_rad": np.zeros(n),
            "psi_dot_rps": np.zeros(n),
            "cov_xx": np.full(n, 0.1),
            "cov_yy": np.full(n, 0.1),
            "cov_yaw": np.full(n, 0.01),
        }
    )


def _make_calm_ideal(n: int = 600) -> pd.DataFrame:
    """Matching ideal trajectory for calm fused data."""
    t = np.linspace(0.0, (n - 1) / 100.0, n)
    return pd.DataFrame(
        {
            "t_s": t,
            "v_mps": np.full(n, 8.0),
            "a_lon_mps2": np.zeros(n),
            "a_lat_mps2": np.zeros(n),
            "j_lon_mps3": np.zeros(n),
            "psi_rad": np.zeros(n),
            "psi_dot_rps": np.zeros(n),
        }
    )


def _make_calm_ref(n: int = 600) -> pd.DataFrame:
    """Reference path matching calm fused straight path."""
    px = np.linspace(0.0, 8.0 * (n - 1) / 100.0, n)
    py = np.zeros(n)
    # s_m = cumulative arc-length; straight line so s_m == px
    s_m = px.copy()
    return pd.DataFrame(
        {
            "s_m": s_m,
            "px_m": px,
            "py_m": py,
            "speed_limit_mps": np.full(n, 13.4),  # ~30 mph
        }
    )


# ---------------------------------------------------------------------------
# TestTipLookup
# ---------------------------------------------------------------------------


class TestTipLookup:
    """FR-10.8 — tip band lookup."""

    def setup_method(self):
        self.cfg = _write_tmp_yaml(_SCORING_YAML_CONTENT)

    def teardown_method(self):
        self.cfg.unlink(missing_ok=True)

    def _lookup(self, score: float) -> dict:
        from scoring.tip_lookup import lookup_tip

        return lookup_tip(score, self.cfg)

    def test_excellent_band(self):
        result = self._lookup(95.0)
        assert result["tip_pct"] == 25
        assert result["band"] == "90-100"

    def test_good_band(self):
        result = self._lookup(82.0)
        assert result["tip_pct"] == 20
        assert result["band"] == "75-89"

    def test_fair_band(self):
        result = self._lookup(60.0)
        assert result["tip_pct"] == 15
        assert result["band"] == "50-74"

    def test_poor_band(self):
        result = self._lookup(30.0)
        assert result["tip_pct"] == 10
        assert result["band"] == "0-49"

    def test_boundary_score_zero(self):
        result = self._lookup(0.0)
        assert result["tip_pct"] == 10  # Poor

    def test_boundary_score_100(self):
        result = self._lookup(100.0)
        assert result["tip_pct"] == 25  # Excellent

    def test_boundary_89(self):
        result = self._lookup(89.0)
        assert result["tip_pct"] == 20  # Good (75-89 inclusive)

    def test_boundary_90(self):
        result = self._lookup(90.0)
        assert result["tip_pct"] == 25  # Excellent (90-100)

    def test_disclaimer_always_present(self):
        for score in (0.0, 50.0, 89.0, 100.0):
            r = self._lookup(score)
            assert "SUGGESTED" in r["notes"]
            assert "manual" in r["notes"]

    def test_label_nonempty(self):
        for score in (10.0, 60.0, 82.0, 95.0):
            r = self._lookup(score)
            assert isinstance(r["label"], str) and r["label"]


# ---------------------------------------------------------------------------
# TestComputeAggregate
# ---------------------------------------------------------------------------


class TestComputeAggregate:
    """Weighted aggregate maths (no I/O)."""

    _NAMES: ClassVar = ("jerk", "harsh_brake", "lat_accel", "speed", "deviation", "lane_change")
    _W: ClassVar = {n: 1.0 / 6.0 for n in _NAMES}

    def _agg(self, raw: dict, weights: dict):
        from scoring.aggregate import compute_aggregate

        return compute_aggregate(raw, weights)

    def test_all_zero_penalties(self):
        raw = {n: 0.0 for n in self._NAMES}
        agg, _ = self._agg(raw, self._W)
        assert agg == pytest.approx(0.0)

    def test_all_one_penalties(self):
        raw = {n: 1.0 for n in self._NAMES}
        agg, _ = self._agg(raw, self._W)
        assert agg == pytest.approx(1.0, rel=1e-6)

    def test_unit_interval(self):
        rng = np.random.default_rng(42)
        for _ in range(20):
            raw = {n: float(rng.random()) for n in self._NAMES}
            agg, _ = self._agg(raw, self._W)
            assert 0.0 <= agg <= 1.0

    def test_components_schema(self):
        raw = {n: 0.5 for n in self._NAMES}
        _, detail = self._agg(raw, self._W)
        for name in self._NAMES:
            assert name in detail
            assert set(detail[name].keys()) == {"raw", "weight", "weighted"}

    def test_weights_respected(self):
        w = {
            "jerk": 0.40,
            "harsh_brake": 0.15,
            "lat_accel": 0.15,
            "speed": 0.10,
            "deviation": 0.10,
            "lane_change": 0.10,
        }
        raw = {
            "jerk": 1.0,
            "harsh_brake": 0.0,
            "lat_accel": 0.0,
            "speed": 0.0,
            "deviation": 0.0,
            "lane_change": 0.0,
        }
        agg, _ = self._agg(raw, w)
        assert agg == pytest.approx(0.40)


# ---------------------------------------------------------------------------
# TestBuildScoreJson
# ---------------------------------------------------------------------------


class TestBuildScoreJson:
    """End-to-end score.json builder."""

    def setup_method(self):
        self.scoring_cfg = _write_tmp_yaml(_SCORING_YAML_CONTENT)
        self.ideal_cfg = _write_tmp_yaml(_IDEAL_YAML_CONTENT)
        n = 600
        self.fused = _make_calm_fused(n)
        self.ideal = _make_calm_ideal(n)
        self.ref = _make_calm_ref(n)

    def teardown_method(self):
        self.scoring_cfg.unlink(missing_ok=True)
        self.ideal_cfg.unlink(missing_ok=True)

    def _build(self, **kw):
        from scoring.aggregate import build_score_json

        defaults = {
            "trip_id": "day2",
            "fused_source": "ekf",
            "fused": self.fused,
            "ideal": self.ideal,
            "reference_path": self.ref,
            "config_path": self.scoring_cfg,
            "ideal_config_path": self.ideal_cfg,
        }
        defaults.update(kw)
        return build_score_json(**defaults)

    def test_schema_keys_present(self):
        doc = self._build()
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
        assert required.issubset(doc.keys())

    def test_trip_id_passthrough(self):
        doc = self._build(trip_id="day1")
        assert doc["trip_id"] == "day1"

    def test_fused_source_passthrough(self):
        doc = self._build(fused_source="ukf")
        assert doc["fused_source"] == "ukf"

    def test_six_components_present(self):
        doc = self._build()
        for name in ("jerk", "harsh_brake", "lat_accel", "speed", "deviation", "lane_change"):
            assert name in doc["components"]

    def test_component_penalties_in_unit_interval(self):
        doc = self._build()
        for name, sub in doc["components"].items():
            assert 0.0 <= sub["raw"] <= 1.0, f"{name} raw={sub['raw']} out of [0,1]"

    def test_score_in_0_100(self):
        doc = self._build()
        assert 0.0 <= doc["score_0_100"] <= 100.0

    def test_calm_drive_score_above_80(self):
        doc = self._build()
        assert doc["score_0_100"] >= 80.0, f"Calm drive score too low: {doc['score_0_100']}"

    def test_config_hash_starts_with_sha256(self):
        doc = self._build()
        assert doc["config_hash"].startswith("sha256:")

    def test_notes_disclaimer(self):
        doc = self._build()
        assert "SUGGESTED" in doc["notes"]
        assert "manual" in doc["notes"]

    def test_json_serialisable(self):
        doc = self._build()
        serialised = json.dumps(doc)
        recovered = json.loads(serialised)
        assert recovered["trip_id"] == doc["trip_id"]

    def test_aggregate_consistency(self):
        """score_0_100 == round(100 * (1 - aggregate_raw), 4)."""
        doc = self._build()
        expected = round(100.0 * (1.0 - doc["aggregate_raw"]), 4)
        assert doc["score_0_100"] == pytest.approx(expected, abs=1e-3)


# ---------------------------------------------------------------------------
# TestConfigHash
# ---------------------------------------------------------------------------


class TestConfigHash:
    """sha256 hash properties."""

    def _hash(self, scoring: Path, ideal: Path | None = None):
        from scoring.aggregate import _config_hash

        return _config_hash(scoring, ideal)

    def test_hash_changes_with_content(self):
        cfg1 = _write_tmp_yaml(_SCORING_YAML_CONTENT)
        cfg2 = _write_tmp_yaml(_SCORING_YAML_CONTENT + "\n# extra")
        try:
            assert self._hash(cfg1) != self._hash(cfg2)
        finally:
            cfg1.unlink(missing_ok=True)
            cfg2.unlink(missing_ok=True)

    def test_hash_stable_for_same_content(self):
        cfg = _write_tmp_yaml(_SCORING_YAML_CONTENT)
        try:
            assert self._hash(cfg) == self._hash(cfg)
        finally:
            cfg.unlink(missing_ok=True)

    def test_hash_includes_both_configs(self):
        scoring = _write_tmp_yaml(_SCORING_YAML_CONTENT)
        ideal_a = _write_tmp_yaml(_IDEAL_YAML_CONTENT)
        ideal_b = _write_tmp_yaml(_IDEAL_YAML_CONTENT + "\n# changed")
        try:
            assert self._hash(scoring, ideal_a) != self._hash(scoring, ideal_b)
        finally:
            scoring.unlink(missing_ok=True)
            ideal_a.unlink(missing_ok=True)
            ideal_b.unlink(missing_ok=True)
