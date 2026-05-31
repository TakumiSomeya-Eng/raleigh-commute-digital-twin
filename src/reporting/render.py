"""FR-11.1 -- Per-trip HTML report renderer (Jinja2).

Produces a self-contained report.html from score.json and trajectory Parquets.

Implemented in task T5.1.

CLI usage:
    python -m reporting render --trace day2 --out-dir out
"""

from __future__ import annotations

import argparse
import datetime
import json
import sys
from pathlib import Path

try:
    from storage import StorageAdapter  # Phase 2 S3 adapter (T7.3)
except ImportError:
    StorageAdapter = None  # type: ignore[assignment,misc]

try:
    from storage import StorageAdapter  # Phase 2 S3 adapter (T7.3)
except ImportError:
    StorageAdapter = None  # type: ignore[assignment,misc]

import numpy as np
import pandas as pd
from jinja2 import Environment, FileSystemLoader

_TEMPLATES_DIR = Path(__file__).parent / "templates"
_MAX_REPORT_BYTES = 5 * 1024 * 1024  # 5 MB DoD limit
_DEFAULT_SCORING_YAML = Path(__file__).parent.parent.parent / "config" / "scoring.yaml"


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


def _log(msg: str) -> None:
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    sys.stdout.write(f"[{ts}] [FR-11.1 report] INFO  {msg}\n")
    sys.stdout.flush()


# ---------------------------------------------------------------------------
# Trip metadata helpers
# ---------------------------------------------------------------------------


def _trip_metadata(fused: pd.DataFrame) -> dict:
    """Extract duration, distance, max speed from fused parquet."""
    t = fused["t_s"].to_numpy(dtype=float)
    v = fused["v_mps"].to_numpy(dtype=float)
    px = fused["px_m"].to_numpy(dtype=float)
    py = fused["py_m"].to_numpy(dtype=float)

    duration_s = float(t[-1] - t[0]) if len(t) > 1 else 0.0
    dx = np.diff(px)
    dy = np.diff(py)
    distance_m = float(np.sum(np.sqrt(dx**2 + dy**2)))
    max_speed_mps = float(np.max(v)) if len(v) > 0 else 0.0

    def _hms(seconds: float) -> str:
        m, s = divmod(int(seconds), 60)
        h, m = divmod(m, 60)
        if h:
            return f"{h}h {m:02d}m {s:02d}s"
        return f"{m}m {s:02d}s"

    return {
        "duration_s": round(duration_s, 1),
        "duration_str": _hms(duration_s),
        "distance_m": round(distance_m, 0),
        "distance_km": round(distance_m / 1000.0, 2),
        "max_speed_mps": round(max_speed_mps, 1),
        "max_speed_kph": round(max_speed_mps * 3.6, 1),
        "max_speed_mph": round(max_speed_mps * 2.23694, 1),
    }


def _format_timestamp(timestamp_utc: str) -> str:
    """Parse ISO-8601Z string to a readable local-like string."""
    try:
        # Parse as naive then attach UTC (DTZ007 waiver: format string lacks %z)
        dt_naive = datetime.datetime.strptime(  # noqa: DTZ007
            timestamp_utc, "%Y-%m-%dT%H:%M:%SZ"
        )
        dt = dt_naive.replace(tzinfo=datetime.timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M UTC")
    except ValueError:
        return timestamp_utc


# ---------------------------------------------------------------------------
# Core render function
# ---------------------------------------------------------------------------


def render_report(
    trace: str,
    out_dir: Path,
    *,
    config_path: Path | None = None,
    ideal_config_path: Path | None = None,
) -> Path:
    """Render report.html for *trace* into *out_dir/trace/*.

    Parameters
    ----------
    trace:
        Trace name (e.g. "day2").
    out_dir:
        Root output directory; expects out_dir/trace/ sub-tree.
    config_path:
        Path to scoring.yaml (unused directly; referenced for completeness).
    ideal_config_path:
        Path to ideal.yaml (unused directly).

    Returns
    -------
    Path to the written report.html.
    """
    from data_engine.projection import enu_to_wgs84, load_anchor
    from scoring.components import harsh_brake_penalty

    from reporting.bar_chart import generate_svg
    from reporting.map_overlay import generate_map_html

    trace_dir = out_dir / trace

    # -- Load required inputs (S3-aware, T7.3) --
    store = StorageAdapter.from_env(out_dir=out_dir) if StorageAdapter else None

    def _read_pq(stage: str, filename: str) -> pd.DataFrame:
        if store and store.is_s3:
            return store.read_parquet(stage, trace, filename)
        return pd.read_parquet(trace_dir / filename)

    def _read_json(stage: str, filename: str) -> dict:
        if store and store.is_s3:
            return store.read_json(stage, trace, filename)
        p = trace_dir / filename
        return json.loads(p.read_text(encoding="utf-8"))

    _log(f"loading score.json (s3={store.is_s3 if store else False})")
    score_doc = _read_json("scores", "score.json")

    _log("loading fused_ekf")
    fused = _read_pq("fused", "fused_ekf.parquet")

    # Load ideal trajectory if available
    ideal: pd.DataFrame | None = None
    try:
        _log("loading ideal_trajectory")
        ideal = _read_pq("ideal", "ideal_trajectory.parquet")
    except Exception:
        _log("ideal_trajectory.parquet not found -- map will show actual only")

    # -- Compute trip metadata --
    _log("computing trip metadata")
    meta = _trip_metadata(fused)

    # -- Generate bar chart SVG --
    _log("generating component bar chart")
    bar_svg = generate_svg(score_doc)

    # -- Compute harsh-brake events (shared source for map and score) --
    lat0, lon0 = load_anchor()
    scoring_cfg = Path(config_path) if config_path is not None else _DEFAULT_SCORING_YAML
    _, brake_events = harsh_brake_penalty(fused, scoring_cfg)
    enriched_events: list[dict] = []
    for ev in brake_events:
        if "px_m" in ev:
            ev_lat, ev_lon = enu_to_wgs84(ev["px_m"], ev["py_m"], lat0, lon0)
            enriched_events.append({**ev, "lat": float(ev_lat), "lon": float(ev_lon)})
        else:
            enriched_events.append(ev)

    # -- Generate Folium map --
    _log("generating map overlay")
    map_html = generate_map_html(
        fused,
        ideal,
        score_doc,
        lat0=lat0,
        lon0=lon0,
        events=enriched_events,
    )

    # -- Render template --
    _log("rendering Jinja2 template")
    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATES_DIR)),
        autoescape=True,
    )
    template = env.get_template("report.html.j2")

    # Score color (green above 80, amber 60-79, red below 60)
    tip_pct = score_doc.get("suggested_tip_pct", 0)
    score_val = score_doc.get("score_0_100", 0.0)
    if score_val >= 80:
        score_color = "#006450"
    elif score_val >= 60:
        score_color = "#a05000"
    else:
        score_color = "#8b1a1a"

    context = {
        "score_doc": score_doc,
        "trip_id": score_doc.get("trip_id", trace),
        "score_0_100": score_val,
        "aggregate_raw": score_doc.get("aggregate_raw", 0.0),
        "suggested_tip_pct": tip_pct,
        "suggested_tip_band": score_doc.get("suggested_tip_band", "—"),
        "fused_source": score_doc.get("fused_source", "ekf"),
        "timestamp_str": _format_timestamp(score_doc.get("timestamp_utc", "")),
        "meta": meta,
        "bar_svg": bar_svg,
        "map_html": map_html,
        "notes": score_doc.get("notes", ""),
        "config_hash_short": score_doc.get("config_hash", "")[:16],
        "score_color": score_color,
    }

    html = template.render(**context)

    # -- Write output (S3-aware, T7.3) --
    html_bytes = html.encode("utf-8")
    size_kb = len(html_bytes) / 1024

    if store and store.is_s3:
        store.write_bytes(html_bytes, "reports", trace, "report.html", content_type="text/html")
        _log(f"uploaded reports/{trace}/report.html ({size_kb:.1f} KB)")
        out_path = trace_dir / "report.html"  # dummy for return type
    else:
        out_path = trace_dir / "report.html"
        out_path.write_text(html, encoding="utf-8")
        _log(f"wrote {out_path} ({size_kb:.1f} KB)")

    if len(html_bytes) > _MAX_REPORT_BYTES:
        sys.stderr.write(f"WARNING: report.html is {size_kb:.0f} KB -- exceeds 5 MB DoD limit.\n")

    return out_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Render per-trip HTML report (FR-11.1)")
    p.add_argument("--trace", required=True, help="trace name (e.g. day2)")
    p.add_argument("--out-dir", type=Path, default=Path("out"))
    p.add_argument("--config", type=Path, default=Path("config/scoring.yaml"))
    p.add_argument("--ideal-config", type=Path, default=Path("config/ideal.yaml"))
    return p


def main(argv: list[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)
    render_report(
        args.trace,
        args.out_dir,
        config_path=args.config,
        ideal_config_path=args.ideal_config,
    )


if __name__ == "__main__":
    main()
