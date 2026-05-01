"""Tests for FR-11.1 / FR-11.2 / FR-11.3 -- reporting module.

Covers:
    TestBarChart     -- SVG bar chart generation (T5.2)
    TestMapOverlay   -- Folium map HTML generation (T5.3)
    TestRenderReport -- render.py end-to-end with tmp_path (T5.1)
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

_SCORE_DOC: dict = {
    "trip_id": "day2",
    "config_hash": "sha256:abcdef1234567890",
    "fused_source": "ekf",
    "components": {
        "jerk": {"raw": 0.10, "weight": 0.20, "weighted": 0.020},
        "harsh_brake": {"raw": 0.05, "weight": 0.20, "weighted": 0.010},
        "lat_accel": {"raw": 0.08, "weight": 0.20, "weighted": 0.016},
        "speed": {"raw": 0.02, "weight": 0.15, "weighted": 0.003},
        "deviation": {"raw": 0.15, "weight": 0.15, "weighted": 0.023},
        "lane_change": {"raw": 0.00, "weight": 0.10, "weighted": 0.000},
    },
    "aggregate_raw": 0.072,
    "score_0_100": 92.8,
    "suggested_tip_band": "90-100",
    "suggested_tip_pct": 25,
    "timestamp_utc": "2026-04-30T12:00:00Z",
    "notes": "SUGGESTED -- final tipping decision is manual.",
}


def _make_fused(n: int = 300) -> pd.DataFrame:
    t = np.linspace(0.0, (n - 1) / 100.0, n)
    px = np.linspace(0.0, 8.0 * (n - 1) / 100.0, n)
    return pd.DataFrame(
        {
            "t_s": t,
            "px_m": px,
            "py_m": np.zeros(n),
            "v_mps": np.full(n, 8.0),
            "psi_rad": np.zeros(n),
            "psi_dot_rps": np.zeros(n),
            "cov_xx": np.full(n, 0.1),
            "cov_yy": np.full(n, 0.1),
            "cov_yaw": np.full(n, 0.01),
        }
    )


def _make_ideal(n: int = 300) -> pd.DataFrame:
    t = np.linspace(0.0, (n - 1) / 100.0, n)
    px = np.linspace(0.0, 8.0 * (n - 1) / 100.0, n)
    return pd.DataFrame(
        {
            "t_s": t,
            "px_m": px,
            "py_m": np.zeros(n) + 1.0,
            "v_mps": np.full(n, 8.0),
            "a_lon_mps2": np.zeros(n),
            "a_lat_mps2": np.zeros(n),
            "j_lon_mps3": np.zeros(n),
            "psi_rad": np.zeros(n),
            "psi_dot_rps": np.zeros(n),
        }
    )


# ---------------------------------------------------------------------------
# TestBarChart
# ---------------------------------------------------------------------------


class TestBarChart:
    """FR-11.3 -- SVG bar chart."""

    def _svg(self, score: dict | None = None) -> str:
        from reporting.bar_chart import generate_svg

        return generate_svg(score if score is not None else _SCORE_DOC)

    def test_returns_svg_element(self) -> None:
        svg = self._svg()
        assert svg.strip().startswith("<svg")
        assert svg.strip().endswith("</svg>")

    def test_contains_all_component_labels(self) -> None:
        svg = self._svg()
        for label in (
            "Jerk",
            "Harsh braking",
            "Lateral accel",
            "Speed compliance",
            "Route deviation",
            "Lane changes",
        ):
            assert label in svg, f"Label {label!r} missing from SVG"

    def test_contains_score_value(self) -> None:
        svg = self._svg()
        assert "92.8" in svg

    def test_inline_no_external_refs(self) -> None:
        svg = self._svg()
        # SVG XML namespace (xmlns="http://www.w3.org/2000/svg") is fine;
        # what we forbid is external resource loading via src/href/url().
        assert 'src="http' not in svg, "SVG must not load external scripts"
        assert "url(http" not in svg, "SVG must not reference external URLs in url()"

    def test_zero_penalties_render(self) -> None:
        doc = dict(_SCORE_DOC)
        doc["aggregate_raw"] = 0.0
        doc["score_0_100"] = 100.0
        doc["components"] = {
            k: {"raw": 0.0, "weight": v["weight"], "weighted": 0.0}
            for k, v in _SCORE_DOC["components"].items()
        }
        svg = self._svg(doc)
        assert "100.0" in svg

    def test_max_penalties_render(self) -> None:
        doc = dict(_SCORE_DOC)
        doc["aggregate_raw"] = 1.0
        doc["score_0_100"] = 0.0
        doc["components"] = {
            k: {"raw": 1.0, "weight": v["weight"], "weighted": v["weight"]}
            for k, v in _SCORE_DOC["components"].items()
        }
        svg = self._svg(doc)
        assert "0.0" in svg


# ---------------------------------------------------------------------------
# TestMapOverlay
# ---------------------------------------------------------------------------


class TestMapOverlay:
    """FR-11.2 -- Folium map HTML."""

    def _map(self, ideal: pd.DataFrame | None = None) -> str:
        from reporting.map_overlay import generate_map_html

        return generate_map_html(_make_fused(), ideal, _SCORE_DOC)

    def test_returns_html_string(self) -> None:
        html = self._map()
        assert isinstance(html, str)
        assert len(html) > 100

    def test_contains_leaflet(self) -> None:
        html = self._map()
        assert "leaflet" in html.lower()

    def test_with_ideal_trajectory(self) -> None:
        html = self._map(ideal=_make_ideal())
        assert isinstance(html, str)
        assert len(html) > 100

    def test_without_ideal_trajectory(self) -> None:
        html = self._map(ideal=None)
        assert isinstance(html, str)

    def test_marker_for_harsh_brake(self) -> None:
        # Build fused with a hard deceleration event
        n = 300
        v = np.full(n, 10.0)
        # Sharp brake at sample 150: drop 6 m/s in 0.1 s = -60 m/s^2
        v[150:160] = np.linspace(10.0, 4.0, 10)
        fused = _make_fused(n)
        fused["v_mps"] = v
        from reporting.map_overlay import generate_map_html

        html = generate_map_html(fused, None, _SCORE_DOC)
        # Should produce at least one marker popup
        assert "Harsh brake" in html


# ---------------------------------------------------------------------------
# TestRenderReport
# ---------------------------------------------------------------------------


class TestRenderReport:
    """FR-11.1 -- full report rendering to file."""

    @pytest.fixture()
    def out_dir(self, tmp_path: Path) -> Path:
        trace_dir = tmp_path / "day2"
        trace_dir.mkdir()
        # Write score.json
        (trace_dir / "score.json").write_text(json.dumps(_SCORE_DOC, indent=2), encoding="utf-8")
        # Write fused parquet
        _make_fused().to_parquet(trace_dir / "fused_ekf.parquet")
        return tmp_path

    def test_report_created(self, out_dir: Path) -> None:
        from reporting.render import render_report

        out = render_report("day2", out_dir)
        assert out.exists()
        assert out.name == "report.html"

    def test_report_size_under_5mb(self, out_dir: Path) -> None:
        from reporting.render import render_report

        out = render_report("day2", out_dir)
        assert out.stat().st_size < 5 * 1024 * 1024

    def test_report_is_html(self, out_dir: Path) -> None:
        from reporting.render import render_report

        out = render_report("day2", out_dir)
        content = out.read_text(encoding="utf-8")
        assert "<!DOCTYPE html>" in content or "<!doctype html>" in content.lower()

    def test_report_contains_trip_id(self, out_dir: Path) -> None:
        from reporting.render import render_report

        out = render_report("day2", out_dir)
        assert "day2" in out.read_text(encoding="utf-8")

    def test_report_contains_score(self, out_dir: Path) -> None:
        from reporting.render import render_report

        out = render_report("day2", out_dir)
        content = out.read_text(encoding="utf-8")
        assert "92.8" in content

    def test_report_contains_disclaimer(self, out_dir: Path) -> None:
        from reporting.render import render_report

        out = render_report("day2", out_dir)
        content = out.read_text(encoding="utf-8")
        assert "SUGGESTED" in content

    def test_report_contains_tip_pct(self, out_dir: Path) -> None:
        from reporting.render import render_report

        out = render_report("day2", out_dir)
        content = out.read_text(encoding="utf-8")
        assert "25" in content  # tip_pct

    def test_report_main_layout_no_external_fonts(self, out_dir: Path) -> None:
        from reporting.render import render_report

        out = render_report("day2", out_dir)
        content = out.read_text(encoding="utf-8")
        # The main layout CSS must not pull external font CDNs.
        # (Folium map may use Leaflet/Bootstrap CDN for its own JS -- that is
        #  expected and allowed per FRD "no CDN deps except optional Leaflet tiles".)
        assert "fonts.googleapis.com" not in content, "No Google Fonts CDN allowed"

    def test_report_with_ideal(self, out_dir: Path) -> None:
        from reporting.render import render_report

        # Write ideal trajectory parquet
        _make_ideal().to_parquet(out_dir / "day2" / "ideal_trajectory.parquet")
        out = render_report("day2", out_dir)
        assert out.exists()
        assert out.stat().st_size < 5 * 1024 * 1024

    def test_missing_score_json_exits(self, tmp_path: Path) -> None:
        from reporting.render import render_report

        (tmp_path / "day2").mkdir()
        _make_fused().to_parquet(tmp_path / "day2" / "fused_ekf.parquet")
        with pytest.raises(SystemExit):
            render_report("day2", tmp_path)
