"""FR-11.4 -- Trip-list index page generator.

Globs all score.json files and renders out/reports/index.html with client-side sort.

Implemented in task T5.4.

CLI usage:
    python -m reporting index --out-dir out --ratings config/ratings.yaml
"""

from __future__ import annotations

import argparse
import datetime
import json
import sys
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

_TEMPLATES_DIR = Path(__file__).parent / "templates"
_DEFAULT_RATINGS = Path("config/ratings.yaml")


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


def _log(msg: str) -> None:
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    sys.stdout.write(f"[{ts}] [FR-11.4 index] INFO  {msg}\n")
    sys.stdout.flush()


# ---------------------------------------------------------------------------
# Trip row builder
# ---------------------------------------------------------------------------


def _build_trip_rows(
    out_dir: Path,
    ratings: dict[str, int],
) -> list[dict]:
    """Glob out_dir/*/score.json and build a list of table-row dicts.

    Each row:
        trip_id, score_0_100, aggregate_raw, suggested_tip_pct,
        suggested_tip_band, timestamp_utc, fused_source,
        rating (int|None), report_link (relative path or None)
    """
    rows: list[dict] = []
    for score_path in sorted(out_dir.glob("*/score.json")):
        try:
            doc = json.loads(score_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue

        trip_id = doc.get("trip_id", score_path.parent.name)
        report_path = score_path.parent / "report.html"
        # Relative link from out/reports/ -> ../trip_id/report.html
        report_link = f"../{trip_id}/report.html" if report_path.exists() else None

        rows.append(
            {
                "trip_id": trip_id,
                "score_0_100": doc.get("score_0_100", 0.0),
                "aggregate_raw": doc.get("aggregate_raw", 1.0),
                "suggested_tip_pct": doc.get("suggested_tip_pct", 0),
                "suggested_tip_band": doc.get("suggested_tip_band", "—"),
                "timestamp_utc": doc.get("timestamp_utc", ""),
                "fused_source": doc.get("fused_source", "ekf"),
                "rating": ratings.get(trip_id),
                "report_link": report_link,
            }
        )

    return rows


# ---------------------------------------------------------------------------
# Spearman rho for the index
# ---------------------------------------------------------------------------


def _compute_rho(rows: list[dict]) -> dict:
    """Compute Spearman rho from rows that have both a score and a rating.

    Returns dict with keys: rho (float|None), n (int), display (str).
    """
    from reporting.ratings import spearman_rho

    paired_scores: list[float] = []
    paired_ratings: list[float] = []
    for row in rows:
        if row["rating"] is not None:
            paired_scores.append(float(row["score_0_100"]))
            paired_ratings.append(float(row["rating"]))

    rho, n = spearman_rho(paired_scores, paired_ratings)
    if rho is None:
        display = f"n/a (need >= 5 rated trips, have {n})"
    else:
        display = f"{rho:+.3f} (n={n})"

    return {"rho": rho, "n": n, "display": display}


# ---------------------------------------------------------------------------
# Core render function
# ---------------------------------------------------------------------------


def render_index(
    out_dir: Path,
    ratings_path: Path | None = None,
) -> Path:
    """Render out/reports/index.html listing all scored trips.

    Parameters
    ----------
    out_dir:
        Root output directory (contains trip subdirectories with score.json).
    ratings_path:
        Path to ratings.yaml; defaults to config/ratings.yaml.

    Returns
    -------
    Path to the written index.html.
    """
    from reporting.ratings import load_ratings

    rp = ratings_path or _DEFAULT_RATINGS
    _log(f"loading ratings from {rp}")
    ratings = load_ratings(rp)

    _log(f"scanning {out_dir} for score.json files")
    rows = _build_trip_rows(out_dir, ratings)
    _log(f"found {len(rows)} scored trip(s)")

    rho_info = _compute_rho(rows)
    _log(f"Spearman rho: {rho_info['display']}")

    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATES_DIR)),
        autoescape=True,
    )
    template = env.get_template("index.html.j2")

    generated_at = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    html = template.render(
        rows=rows,
        rho_info=rho_info,
        generated_at=generated_at,
        n_trips=len(rows),
        n_rated=rho_info["n"],
    )

    reports_dir = out_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    out_path = reports_dir / "index.html"
    out_path.write_text(html, encoding="utf-8")
    _log(f"wrote {out_path} ({out_path.stat().st_size / 1024:.1f} KB)")
    return out_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Render trip-list index page (FR-11.4)")
    p.add_argument("--out-dir", type=Path, default=Path("out"))
    p.add_argument("--ratings", type=Path, default=None, help="ratings.yaml path")
    return p


def main(argv: list[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)
    render_index(args.out_dir, args.ratings)


if __name__ == "__main__":
    main()
