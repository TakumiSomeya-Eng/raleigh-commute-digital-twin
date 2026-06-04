"""Tests for src/reporting/folium_animation.py and src/reporting/compare.py.

Coverage targets (folium-animation.md §テスト規則):
1. add_trajectory_animation returns a folium.Map
2. TimestampedGeoJson features count equals input row count
3. Harsh-brake marker count matches score.json harsh_brake_events
4. Comparison report HTML contains all three style scores
5. score_color returns correct band for boundary values
"""

from __future__ import annotations

from pathlib import Path

import folium
import pandas as pd
import pytest

from src.reporting.compare import render_comparison_report
from src.reporting.folium_animation import (
    HARSH_BRAKE_THRESHOLD_MPS2,
    add_harsh_brake_markers,
    add_trajectory_animation,
    score_color,
)

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

ENU_ANCHOR = (35.773, -78.610)


def _make_fused_df(n: int = 5, *, include_harsh: bool = False) -> pd.DataFrame:
    """Minimal fused DataFrame with t_s, px_m, py_m, v_mps."""
    t = [float(i) for i in range(n)]
    px = [float(i * 10) for i in range(n)]
    py = [float(i * 5) for i in range(n)]
    if include_harsh:
        # Speed drops sharply at index 2 so diff/dt < -HARSH_BRAKE_THRESHOLD.
        # Index 3 keeps the same speed so only one row triggers the threshold.
        drop = HARSH_BRAKE_THRESHOLD_MPS2 + 1.0
        v = [20.0, 20.0, 20.0 - drop, 20.0 - drop, 20.0 - drop]
    else:
        v = [10.0] * n
    return pd.DataFrame({"t_s": t, "px_m": px, "py_m": py, "v_mps": v})


def _make_ideal_df(n: int = 5) -> pd.DataFrame:
    t = [float(i) for i in range(n)]
    px = [float(i * 9) for i in range(n)]
    py = [float(i * 4) for i in range(n)]
    return pd.DataFrame({"t_s": t, "px_m": px, "py_m": py})


# ---------------------------------------------------------------------------
# Test 1: add_trajectory_animation returns folium.Map
# ---------------------------------------------------------------------------


class TestAddTrajectoryAnimation:
    def test_returns_folium_map(self):
        m = folium.Map(location=list(ENU_ANCHOR), zoom_start=14)
        fused = _make_fused_df()
        ideal = _make_ideal_df()
        result = add_trajectory_animation(m, fused, ideal, ENU_ANCHOR, style="actual")
        assert isinstance(result, folium.Map)

    def test_returns_same_map_object(self):
        m = folium.Map(location=list(ENU_ANCHOR), zoom_start=14)
        fused = _make_fused_df()
        ideal = _make_ideal_df()
        result = add_trajectory_animation(m, fused, ideal, ENU_ANCHOR, style="ideal")
        assert result is m

    # Test 2: TimestampedGeoJson feature count equals input row count
    def test_feature_count_matches_row_count(self):
        m = folium.Map(location=list(ENU_ANCHOR), zoom_start=14)
        n = 7
        fused = _make_fused_df(n)
        ideal = _make_ideal_df(n)
        add_trajectory_animation(m, fused, ideal, ENU_ANCHOR, style="actual")

        # Extract TimestampedGeoJson from the map's children
        from folium.plugins import TimestampedGeoJson

        tgj_list = [
            child for child in m._children.values() if isinstance(child, TimestampedGeoJson)
        ]
        assert len(tgj_list) >= 1, "No TimestampedGeoJson added to map"
        # The first one corresponds to fused_df
        tgj = tgj_list[0]
        import json as _json

        data = tgj.data if isinstance(tgj.data, dict) else _json.loads(tgj.data)
        features = data["features"]
        assert len(features) == n


# ---------------------------------------------------------------------------
# Test 3: Harsh-brake marker count
# ---------------------------------------------------------------------------


class TestAddHarshBrakeMarkers:
    def test_no_markers_when_no_harsh_brake(self):
        m = folium.Map(location=list(ENU_ANCHOR), zoom_start=14)
        fused = _make_fused_df(include_harsh=False)
        result = add_harsh_brake_markers(m, fused, ENU_ANCHOR)
        assert isinstance(result, folium.Map)

        circle_markers = [
            c for c in result._children.values() if isinstance(c, folium.CircleMarker)
        ]
        assert len(circle_markers) == 0

    def test_marker_count_matches_harsh_brake_events(self):
        """One harsh braking event injected → exactly one marker."""
        m = folium.Map(location=list(ENU_ANCHOR), zoom_start=14)
        fused = _make_fused_df(5, include_harsh=True)
        add_harsh_brake_markers(m, fused, ENU_ANCHOR)

        circle_markers = [c for c in m._children.values() if isinstance(c, folium.CircleMarker)]
        assert len(circle_markers) == 1

    def test_marker_count_matches_score_json_field(self):
        """Marker count equals score.json harsh_brake_events when events are injected."""
        # Build a fused_df that has exactly 2 harsh brake events
        t = [0.0, 1.0, 2.0, 3.0, 4.0, 5.0]
        v = [
            20.0,
            20.0 - (HARSH_BRAKE_THRESHOLD_MPS2 + 1.0),  # event at idx 1
            5.0,
            5.0,
            5.0,
            5.0 - (HARSH_BRAKE_THRESHOLD_MPS2 + 1.0),  # event at idx 5
        ]
        px = [float(i * 10) for i in range(6)]
        py = [float(i * 5) for i in range(6)]
        fused = pd.DataFrame({"t_s": t, "px_m": px, "py_m": py, "v_mps": v})

        # Compute expected harsh count the same way the function does
        df_copy = fused.copy()
        df_copy["ax"] = df_copy["v_mps"].diff() / df_copy["t_s"].diff()
        expected_count = int((df_copy["ax"] < -HARSH_BRAKE_THRESHOLD_MPS2).sum())

        score_json = {"harsh_brake_events": expected_count}

        m = folium.Map(location=list(ENU_ANCHOR), zoom_start=14)
        add_harsh_brake_markers(m, fused, ENU_ANCHOR)

        circle_markers = [c for c in m._children.values() if isinstance(c, folium.CircleMarker)]
        assert len(circle_markers) == score_json["harsh_brake_events"]


# ---------------------------------------------------------------------------
# Test 4: render_comparison_report HTML contains all 3 style scores
# ---------------------------------------------------------------------------


class TestRenderComparisonReport:
    def test_html_contains_all_scores(self, tmp_path: Path):
        styles = ["calm", "normal", "aggressive"]
        score_jsons = [
            {"aggregate_0_100": 82, "tip_pct": 20},
            {"aggregate_0_100": 64, "tip_pct": 15},
            {"aggregate_0_100": 31, "tip_pct": 0},
        ]
        out = render_comparison_report(styles, score_jsons, tmp_path / "compare.html")
        assert out.exists()
        html = out.read_text(encoding="utf-8")
        for sj in score_jsons:
            assert str(sj["aggregate_0_100"]) in html

    def test_html_contains_all_style_names(self, tmp_path: Path):
        styles = ["calm", "normal", "aggressive"]
        score_jsons = [
            {"aggregate_0_100": 82, "tip_pct": 20},
            {"aggregate_0_100": 64, "tip_pct": 15},
            {"aggregate_0_100": 31, "tip_pct": 0},
        ]
        out = render_comparison_report(styles, score_jsons, tmp_path / "compare2.html")
        html = out.read_text(encoding="utf-8")
        for style in styles:
            assert style in html

    def test_returns_path_object(self, tmp_path: Path):
        styles = ["calm"]
        score_jsons = [{"aggregate_0_100": 70, "tip_pct": 15}]
        out = render_comparison_report(styles, score_jsons, tmp_path / "out.html")
        assert isinstance(out, Path)


# ---------------------------------------------------------------------------
# Test 5: score_color boundary values
# ---------------------------------------------------------------------------


class TestScoreColor:
    @pytest.mark.parametrize(
        "score, expected_label",
        [
            (95.0, "Excellent"),
            (90.0, "Excellent"),
            (89.9, "Good"),
            (75.0, "Good"),
            (74.9, "Fair"),
            (60.0, "Fair"),
            (59.9, "Poor"),
            (45.0, "Poor"),
            (44.9, "Unsafe"),
            (0.0, "Unsafe"),
        ],
    )
    def test_correct_band(self, score: float, expected_label: str):
        result = score_color(score)
        assert result["label"] == expected_label

    def test_returns_dict_with_required_keys(self):
        result = score_color(70.0)
        assert "bg" in result
        assert "text" in result
        assert "label" in result

    def test_below_zero_returns_unsafe(self):
        result = score_color(-1.0)
        assert result["label"] == "Unsafe"
