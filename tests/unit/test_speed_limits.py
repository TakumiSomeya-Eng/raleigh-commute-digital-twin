"""Unit tests for FR-9.2 SpeedLimitLookup and _parse_maxspeed (T4.2).

All Overpass HTTP calls are mocked.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parents[2] / "src"))

from ideal_driver.speed_limits import SpeedLimitLookup, _parse_maxspeed

_MPH_TO_MPS = 0.44704
_KMH_TO_MPS = 1.0 / 3.6


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(
    tmp_path: Path,
    urban_default_mps: float = 13.4,
    corridors: dict | None = None,
) -> Path:
    data = {"urban_default_mps": urban_default_mps, "corridors": corridors or {}}
    p = tmp_path / "speed_limits.yaml"
    p.write_text(yaml.dump(data), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# _parse_maxspeed
# ---------------------------------------------------------------------------


class TestParseMaxspeed:
    def test_bare_integer_is_kmh(self):
        result = _parse_maxspeed("50")
        assert result == pytest.approx(50 * _KMH_TO_MPS)

    def test_kmh_explicit(self):
        result = _parse_maxspeed("60 km/h")
        assert result == pytest.approx(60 * _KMH_TO_MPS)

    def test_kmh_no_space(self):
        result = _parse_maxspeed("80kmh")
        assert result == pytest.approx(80 * _KMH_TO_MPS)

    def test_mph(self):
        result = _parse_maxspeed("30 mph")
        assert result == pytest.approx(30 * _MPH_TO_MPS)

    def test_mph_no_space(self):
        result = _parse_maxspeed("45mph")
        assert result == pytest.approx(45 * _MPH_TO_MPS)

    def test_none_tag_returns_none(self):
        assert _parse_maxspeed("none") is None

    def test_unlimited_returns_none(self):
        assert _parse_maxspeed("unlimited") is None

    def test_signals_returns_none(self):
        assert _parse_maxspeed("signals") is None

    def test_empty_string_returns_none(self):
        assert _parse_maxspeed("") is None

    def test_unknown_string_returns_none(self):
        assert _parse_maxspeed("walk") is None

    def test_decimal_value(self):
        result = _parse_maxspeed("48.3")
        assert result == pytest.approx(48.3 * _KMH_TO_MPS)


# ---------------------------------------------------------------------------
# SpeedLimitLookup — YAML fallback (skip_overpass=True)
# ---------------------------------------------------------------------------


class TestSpeedLimitLookupYamlFallback:
    def test_urban_default_when_no_corridors(self, tmp_path):
        cfg = _make_config(tmp_path, urban_default_mps=13.4)
        sl = SpeedLimitLookup(cfg, skip_overpass=True)
        assert sl.get(99999) == pytest.approx(13.4)

    def test_corridor_override_returned(self, tmp_path):
        cfg = _make_config(tmp_path, corridors={111: 22.352})  # 50 mph
        sl = SpeedLimitLookup(cfg, skip_overpass=True)
        assert sl.get(111) == pytest.approx(22.352)

    def test_unknown_way_falls_back_to_default(self, tmp_path):
        cfg = _make_config(tmp_path, urban_default_mps=8.9, corridors={111: 22.352})
        sl = SpeedLimitLookup(cfg, skip_overpass=True)
        assert sl.get(9999) == pytest.approx(8.9)

    def test_lookup_multiple_ids(self, tmp_path):
        cfg = _make_config(tmp_path, urban_default_mps=13.4, corridors={10: 8.9, 20: 17.9})
        sl = SpeedLimitLookup(cfg, skip_overpass=True)
        result = sl.lookup([10, 20, 99])
        assert result[10] == pytest.approx(8.9)
        assert result[20] == pytest.approx(17.9)
        assert result[99] == pytest.approx(13.4)

    def test_result_is_memoised(self, tmp_path):
        cfg = _make_config(tmp_path)
        sl = SpeedLimitLookup(cfg, skip_overpass=True)
        sl.get(42)
        sl.get(42)  # second call should not crash
        assert 42 in sl._cache

    def test_empty_corridors_block_in_yaml(self, tmp_path):
        """corridors: null in YAML should not crash."""
        p = tmp_path / "speed_limits.yaml"
        p.write_text("urban_default_mps: 13.4\ncorridors:\n", encoding="utf-8")
        sl = SpeedLimitLookup(p, skip_overpass=True)
        assert sl.get(1) == pytest.approx(13.4)


# ---------------------------------------------------------------------------
# SpeedLimitLookup — Overpass path (mocked HTTP)
# ---------------------------------------------------------------------------


class TestSpeedLimitLookupOverpass:
    def _overpass_response(self, elements: list[dict]) -> MagicMock:
        mock = MagicMock()
        mock.status_code = 200
        mock.json.return_value = {"elements": elements}
        return mock

    def test_overpass_maxspeed_used_when_present(self, tmp_path):
        cfg = _make_config(tmp_path)
        sl = SpeedLimitLookup(cfg, skip_overpass=False)
        el = [{"id": 555, "tags": {"maxspeed": "50"}}]
        with patch(
            "ideal_driver.speed_limits.requests.post", return_value=self._overpass_response(el)
        ):
            result = sl.get(555)
        assert result == pytest.approx(50 * _KMH_TO_MPS)

    def test_overpass_mph_tag(self, tmp_path):
        cfg = _make_config(tmp_path)
        sl = SpeedLimitLookup(cfg, skip_overpass=False)
        el = [{"id": 777, "tags": {"maxspeed": "35 mph"}}]
        with patch(
            "ideal_driver.speed_limits.requests.post", return_value=self._overpass_response(el)
        ):
            result = sl.get(777)
        assert result == pytest.approx(35 * _MPH_TO_MPS)

    def test_overpass_failure_falls_back_to_yaml_corridor(self, tmp_path):
        cfg = _make_config(tmp_path, corridors={333: 17.9})
        sl = SpeedLimitLookup(cfg, skip_overpass=False)
        with patch(
            "ideal_driver.speed_limits.requests.post", side_effect=ConnectionError("refused")
        ):
            result = sl.get(333)
        assert result == pytest.approx(17.9)

    def test_overpass_500_falls_back_to_default(self, tmp_path):
        cfg = _make_config(tmp_path, urban_default_mps=13.4)
        sl = SpeedLimitLookup(cfg, skip_overpass=False)
        bad = MagicMock()
        bad.status_code = 500
        with patch("ideal_driver.speed_limits.requests.post", return_value=bad):
            result = sl.get(9001)
        assert result == pytest.approx(13.4)

    def test_overpass_missing_maxspeed_tag_falls_back(self, tmp_path):
        """Way without maxspeed tag -> default."""
        cfg = _make_config(tmp_path, urban_default_mps=13.4)
        sl = SpeedLimitLookup(cfg, skip_overpass=False)
        el = [{"id": 444, "tags": {}}]  # no maxspeed
        with patch(
            "ideal_driver.speed_limits.requests.post", return_value=self._overpass_response(el)
        ):
            result = sl.get(444)
        assert result == pytest.approx(13.4)

    def test_overpass_result_is_cached(self, tmp_path):
        cfg = _make_config(tmp_path)
        sl = SpeedLimitLookup(cfg, skip_overpass=False)
        el = [{"id": 100, "tags": {"maxspeed": "60"}}]
        mock_post = MagicMock(return_value=self._overpass_response(el))
        with patch("ideal_driver.speed_limits.requests.post", mock_post):
            sl.get(100)
            sl.get(100)  # second call -- should use cache, not re-query
        assert mock_post.call_count == 1
