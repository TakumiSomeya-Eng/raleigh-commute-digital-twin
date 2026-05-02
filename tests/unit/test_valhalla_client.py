"""Unit tests for FR-9.1 Valhalla Meili client (T4.1).

All tests mock HTTP calls; no live Valhalla service required.
Integration tests (live Valhalla) are marked @pytest.mark.integration.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parents[2] / "src"))

from ideal_driver.valhalla_client import (
    _MAX_SNAP_DISTANCE_M,
    _build_meili_payload,
    _call_meili,
    _parse_meili_response,
    match_trace,
)

# ---------------------------------------------------------------------------
# Constants / helpers
# ---------------------------------------------------------------------------

_LAT0 = 35.773
_LON0 = -78.610


def _make_fused(n: int = 100, hz: float = 100.0, t0: float = 1.0e9) -> pd.DataFrame:
    """Synthetic fused parquet DataFrame moving east at 10 m/s."""
    dt = 1.0 / hz
    t = np.arange(n) * dt + t0
    px = np.arange(n) * 10.0 * dt  # 10 m/s east
    py = np.zeros(n)
    return pd.DataFrame(
        {
            "t_s": t,
            "px_m": px,
            "py_m": py,
            "v_mps": np.full(n, 10.0),
            "psi_rad": np.zeros(n),
            "psi_dot_rps": np.zeros(n),
            "cov_xx": np.ones(n),
            "cov_yy": np.ones(n),
            "cov_yaw": np.full(n, 0.01),
        }
    )


def _fake_meili_response(lats, lons, way_id: int = 12345678) -> dict:
    """Synthetic /trace_attributes response -- all points matched."""
    matched_points = [
        {
            "lat": float(la),
            "lon": float(lo),
            "type": "matched",
            "edge_index": 0,
            "distance_from_trace_point": 1.5,
        }
        for la, lo in zip(lats, lons, strict=False)
    ]
    edges = [{"way_id": way_id}]
    return {"matched_points": matched_points, "edges": edges}


# ---------------------------------------------------------------------------
# _build_meili_payload
# ---------------------------------------------------------------------------


class TestBuildMeiliPayload:
    def test_shape_length(self):
        lats = np.array([35.773, 35.774])
        lons = np.array([-78.610, -78.609])
        times = np.array([0.0, 0.2])
        payload = _build_meili_payload(lats, lons, times)
        assert len(payload["shape"]) == 2

    def test_shape_fields(self):
        lats = np.array([35.773])
        lons = np.array([-78.610])
        times = np.array([0.0])
        payload = _build_meili_payload(lats, lons, times)
        pt = payload["shape"][0]
        assert "lat" in pt and "lon" in pt and "time" in pt

    def test_costing_is_auto(self):
        payload = _build_meili_payload(np.array([35.773]), np.array([-78.610]), np.array([0.0]))
        assert payload["costing"] == "auto"

    def test_shape_match_is_map_snap(self):
        payload = _build_meili_payload(np.array([35.773]), np.array([-78.610]), np.array([0.0]))
        assert payload["shape_match"] == "map_snap"


# ---------------------------------------------------------------------------
# _parse_meili_response
# ---------------------------------------------------------------------------


class TestParseMeiliResponse:
    def _arrays(self, n: int = 5):
        t = np.arange(n, dtype=float) * 0.2 + 1.0e9
        px = np.arange(n, dtype=float) * 2.0
        py = np.zeros(n)
        return t, px, py

    def test_all_matched(self):
        n = 5
        t, px, py = self._arrays(n)
        from data_engine.projection import enu_to_wgs84

        lats, lons = enu_to_wgs84(px, py, _LAT0, _LON0)
        resp = _fake_meili_response(lats, lons, way_id=999)
        rows = _parse_meili_response(resp, t, px, py, _LAT0, _LON0)
        assert len(rows) == n
        for r in rows:
            assert r["osm_way_id"] == 999
            assert r["match_confidence"] > 0.0
            assert r["distance_from_road_m"] == pytest.approx(1.5)

    def test_unmatched_type_gives_zero_confidence(self):
        n = 3
        t, px, py = self._arrays(n)
        resp = {
            "matched_points": [
                {
                    "lat": 35.773,
                    "lon": -78.610,
                    "type": "unmatched",
                    "edge_index": 0,
                    "distance_from_trace_point": 0.0,
                }
            ]
            * n,
            "edges": [{"way_id": 1}],
        }
        rows = _parse_meili_response(resp, t, px, py, _LAT0, _LON0)
        for r in rows:
            assert r["match_confidence"] == 0.0
            assert r["osm_way_id"] == 0

    def test_empty_response_gives_unmatched(self):
        n = 3
        t, px, py = self._arrays(n)
        rows = _parse_meili_response({}, t, px, py, _LAT0, _LON0)
        assert len(rows) == n
        for r in rows:
            assert r["match_confidence"] == 0.0

    def test_large_distance_gives_zero_confidence(self):
        n = 1
        t, px, py = self._arrays(n)
        resp = {
            "matched_points": [
                {
                    "lat": 35.773,
                    "lon": -78.610,
                    "type": "matched",
                    "edge_index": 0,
                    "distance_from_trace_point": _MAX_SNAP_DISTANCE_M + 1.0,
                }
            ],
            "edges": [{"way_id": 1}],
        }
        rows = _parse_meili_response(resp, t, px, py, _LAT0, _LON0)
        assert rows[0]["match_confidence"] == 0.0

    def test_snapped_position_close_to_input(self):
        """Snapped ENU should be within a few metres of the input position."""
        t = np.array([1.0e9])
        px = np.array([100.0])
        py = np.array([50.0])
        from data_engine.projection import enu_to_wgs84

        lats, lons = enu_to_wgs84(px, py, _LAT0, _LON0)
        resp = _fake_meili_response(lats, lons)
        rows = _parse_meili_response(resp, t, px, py, _LAT0, _LON0)
        assert abs(rows[0]["snapped_px_m"] - 100.0) < 1.0
        assert abs(rows[0]["snapped_py_m"] - 50.0) < 1.0

    def test_way_id_zero_when_no_edges(self):
        n = 1
        t, px, py = self._arrays(n)
        resp = {
            "matched_points": [
                {
                    "lat": 35.773,
                    "lon": -78.610,
                    "type": "matched",
                    "edge_index": 0,
                    "distance_from_trace_point": 1.0,
                }
            ],
            "edges": [],  # no edges
        }
        rows = _parse_meili_response(resp, t, px, py, _LAT0, _LON0)
        assert rows[0]["osm_way_id"] == 0


# ---------------------------------------------------------------------------
# _call_meili (mocked HTTP)
# ---------------------------------------------------------------------------


class TestCallMeili:
    def test_returns_parsed_json_on_200(self):
        expected = {"matched_points": [], "edges": []}
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = expected
        with patch("ideal_driver.valhalla_client.requests.post", return_value=mock_resp):
            result = _call_meili({"shape": []}, "http://localhost:8002")
        assert result == expected

    def test_returns_empty_dict_on_400(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.text = "Bad request"
        with patch("ideal_driver.valhalla_client.requests.post", return_value=mock_resp):
            result = _call_meili({}, "http://localhost:8002")
        assert result == {}

    def test_raises_on_connection_error(self):
        import requests as req_lib

        with (
            patch(
                "ideal_driver.valhalla_client.requests.post",
                side_effect=req_lib.exceptions.ConnectionError("refused"),
            ),
            patch("ideal_driver.valhalla_client.time.sleep"),
        ):
            with pytest.raises(ConnectionError):
                _call_meili({}, "http://localhost:8002")


# ---------------------------------------------------------------------------
# match_trace (mocked HTTP end-to-end)
# ---------------------------------------------------------------------------


class TestMatchTrace:
    def _fake_response_for_fused(self, fused: pd.DataFrame) -> dict:
        """Build a fake response where every point is matched."""
        from data_engine.projection import enu_to_wgs84

        stride = max(1, int(round(100.0 / 5.0)))
        sub = fused.iloc[::stride]
        lats, lons = enu_to_wgs84(sub.px_m.to_numpy(), sub.py_m.to_numpy(), _LAT0, _LON0)
        return _fake_meili_response(lats, lons)

    def test_output_has_correct_columns(self):
        fused = _make_fused(n=100)
        fake_resp = self._fake_response_for_fused(fused)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = fake_resp
        with patch("ideal_driver.valhalla_client.requests.post", return_value=mock_resp):
            df = match_trace(fused, _LAT0, _LON0)
        for col in (
            "t_s",
            "osm_way_id",
            "snapped_px_m",
            "snapped_py_m",
            "distance_from_road_m",
            "match_confidence",
        ):
            assert col in df.columns, f"missing column: {col}"

    def test_row_count_matches_subsampled(self):
        fused = _make_fused(n=200)
        fake_resp = self._fake_response_for_fused(fused)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = fake_resp
        with patch("ideal_driver.valhalla_client.requests.post", return_value=mock_resp):
            df = match_trace(fused, _LAT0, _LON0)
        stride = max(1, int(round(100.0 / 5.0)))
        expected = len(fused.iloc[::stride])
        assert len(df) == expected

    def test_all_points_matched_gives_high_confidence(self):
        fused = _make_fused(n=100)
        fake_resp = self._fake_response_for_fused(fused)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = fake_resp
        with patch("ideal_driver.valhalla_client.requests.post", return_value=mock_resp):
            df = match_trace(fused, _LAT0, _LON0)
        assert (df.match_confidence > 0.0).all()

    def test_timestamps_preserved(self):
        fused = _make_fused(n=100)
        fake_resp = self._fake_response_for_fused(fused)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = fake_resp
        with patch("ideal_driver.valhalla_client.requests.post", return_value=mock_resp):
            df = match_trace(fused, _LAT0, _LON0)
        stride = max(1, int(round(100.0 / 5.0)))
        expected_t = fused.t_s.to_numpy()[::stride]
        np.testing.assert_allclose(df.t_s.to_numpy(), expected_t)
