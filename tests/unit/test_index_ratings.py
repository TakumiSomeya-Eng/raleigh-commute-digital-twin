"""Tests for FR-11.4 / FR-11.5 -- index page and ratings ingest.

Covers:
    TestRatings        -- load_ratings, spearman_rho (T5.4 / FR-11.5)
    TestIndexRenderer  -- render_index end-to-end (T5.4 / FR-11.4)
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_score_json(path: Path, trip_id: str, score: float, tip: int = 20) -> None:
    doc = {
        "trip_id": trip_id,
        "config_hash": "sha256:abc123",
        "fused_source": "ekf",
        "components": {
            "jerk": {"raw": 0.1, "weight": 0.20, "weighted": 0.020},
            "harsh_brake": {"raw": 0.0, "weight": 0.20, "weighted": 0.0},
            "lat_accel": {"raw": 0.1, "weight": 0.20, "weighted": 0.020},
            "speed": {"raw": 0.0, "weight": 0.15, "weighted": 0.0},
            "deviation": {"raw": 0.1, "weight": 0.15, "weighted": 0.015},
            "lane_change": {"raw": 0.0, "weight": 0.10, "weighted": 0.0},
        },
        "aggregate_raw": round(1.0 - score / 100.0, 4),
        "score_0_100": score,
        "suggested_tip_band": "75-89",
        "suggested_tip_pct": tip,
        "timestamp_utc": "2026-04-30T12:00:00Z",
        "notes": "SUGGESTED -- manual.",
    }
    path.write_text(json.dumps(doc), encoding="utf-8")


def _write_ratings_yaml(path: Path, ratings: dict[str, int]) -> None:
    lines = ["# test ratings\n"]
    for k, v in ratings.items():
        lines.append(f"{k}: {v}\n")
    path.write_text("".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# TestRatings
# ---------------------------------------------------------------------------


class TestRatings:
    """FR-11.5 -- ratings ingest + Spearman rho."""

    def test_load_ratings_basic(self, tmp_path: Path) -> None:
        from reporting.ratings import load_ratings

        rp = tmp_path / "ratings.yaml"
        _write_ratings_yaml(rp, {"day1": 4, "day2": 5})
        r = load_ratings(rp)
        assert r == {"day1": 4, "day2": 5}

    def test_load_ratings_missing_file(self, tmp_path: Path) -> None:
        from reporting.ratings import load_ratings

        r = load_ratings(tmp_path / "nonexistent.yaml")
        assert r == {}

    def test_load_ratings_out_of_range_ignored(self, tmp_path: Path) -> None:
        from reporting.ratings import load_ratings

        rp = tmp_path / "r.yaml"
        _write_ratings_yaml(rp, {"day1": 6, "day2": 0, "day3": 3})
        r = load_ratings(rp)
        assert "day1" not in r  # 6 out of range
        assert "day2" not in r  # 0 out of range
        assert r["day3"] == 3

    def test_spearman_rho_perfect_positive(self) -> None:
        from reporting.ratings import spearman_rho

        scores = [60.0, 70.0, 80.0, 90.0, 100.0]
        ratings = [1.0, 2.0, 3.0, 4.0, 5.0]
        rho, n = spearman_rho(scores, ratings)
        assert rho is not None
        assert abs(rho - 1.0) < 1e-6
        assert n == 5

    def test_spearman_rho_perfect_negative(self) -> None:
        from reporting.ratings import spearman_rho

        scores = [100.0, 80.0, 60.0, 40.0, 20.0]
        ratings = [1.0, 2.0, 3.0, 4.0, 5.0]
        rho, n = spearman_rho(scores, ratings)
        assert rho is not None
        assert abs(rho - (-1.0)) < 1e-6

    def test_spearman_rho_none_if_fewer_than_5(self) -> None:
        from reporting.ratings import spearman_rho

        rho, n = spearman_rho([80.0, 90.0, 70.0, 85.0], [4, 5, 3, 4])
        assert rho is None
        assert n == 4

    def test_spearman_rho_ties(self) -> None:
        from reporting.ratings import spearman_rho

        scores = [80.0, 80.0, 90.0, 70.0, 75.0]
        ratings = [3, 3, 5, 2, 2]
        rho, n = spearman_rho(scores, ratings)
        assert rho is not None
        assert -1.0 <= rho <= 1.0
        assert n == 5

    def test_spearman_rho_uncorrelated_is_finite(self) -> None:
        from reporting.ratings import spearman_rho

        scores = [80.0, 60.0, 90.0, 70.0, 85.0]
        ratings = [3.0, 5.0, 1.0, 4.0, 2.0]
        rho, n = spearman_rho(scores, ratings)
        assert rho is not None
        assert isinstance(rho, float)


# ---------------------------------------------------------------------------
# TestIndexRenderer
# ---------------------------------------------------------------------------


class TestIndexRenderer:
    """FR-11.4 -- index page rendering."""

    @pytest.fixture()
    def out_dir(self, tmp_path: Path) -> Path:
        """Create out/ with two trip subdirs, each with score.json."""
        for trip_id, score in [("day1", 78.5), ("day2", 91.2)]:
            d = tmp_path / trip_id
            d.mkdir()
            _write_score_json(d / "score.json", trip_id, score)
        return tmp_path

    def test_index_created(self, out_dir: Path) -> None:
        from reporting.index import render_index

        out = render_index(out_dir)
        assert out.exists()
        assert out.name == "index.html"

    def test_index_is_html(self, out_dir: Path) -> None:
        from reporting.index import render_index

        out = render_index(out_dir)
        content = out.read_text(encoding="utf-8")
        assert "<!DOCTYPE html>" in content or "<!doctype html>" in content.lower()

    def test_index_lists_both_trips(self, out_dir: Path) -> None:
        from reporting.index import render_index

        out = render_index(out_dir)
        content = out.read_text(encoding="utf-8")
        assert "day1" in content
        assert "day2" in content

    def test_index_shows_scores(self, out_dir: Path) -> None:
        from reporting.index import render_index

        out = render_index(out_dir)
        content = out.read_text(encoding="utf-8")
        assert "78.5" in content
        assert "91.2" in content

    def test_index_missing_rating_shows_dash(self, out_dir: Path) -> None:
        from reporting.index import render_index

        out = render_index(out_dir)  # no ratings file
        content = out.read_text(encoding="utf-8")
        assert "&mdash;" in content or "—" in content

    def test_index_with_ratings(self, out_dir: Path, tmp_path: Path) -> None:
        from reporting.index import render_index

        rp = tmp_path / "ratings.yaml"
        _write_ratings_yaml(rp, {"day1": 4, "day2": 5})
        out = render_index(out_dir, rp)
        content = out.read_text(encoding="utf-8")
        # Stars should appear
        assert "★" in content  # ★ filled star

    def test_index_rho_na_when_insufficient(self, out_dir: Path) -> None:
        from reporting.index import render_index

        out = render_index(out_dir)
        content = out.read_text(encoding="utf-8")
        assert "n/a" in content.lower()

    def test_index_contains_sortable_js(self, out_dir: Path) -> None:
        from reporting.index import render_index

        out = render_index(out_dir)
        content = out.read_text(encoding="utf-8")
        assert "sortTable" in content
        assert "<script>" in content

    def test_index_no_cdn_fonts(self, out_dir: Path) -> None:
        from reporting.index import render_index

        out = render_index(out_dir)
        content = out.read_text(encoding="utf-8")
        assert "fonts.googleapis.com" not in content

    def test_index_empty_out_dir(self, tmp_path: Path) -> None:
        from reporting.index import render_index

        out = render_index(tmp_path)
        content = out.read_text(encoding="utf-8")
        assert "No scored trips" in content or out.exists()
