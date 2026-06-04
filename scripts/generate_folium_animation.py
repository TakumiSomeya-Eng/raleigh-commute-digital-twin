"""T8.10 — Generate Folium animation HTML for Video B.

Reads the three SUMO fused_ekf.parquet files (calm / normal / aggressive),
converts ENU positions to WGS-84, and produces an animated map with:
  - Growing trail (LineString that extends frame-by-frame)
  - Current-position dot moving along the route
  - Start (green) and End (checkered) markers
  - Harsh-brake event markers
  - Score legend overlay

Output: out/compare/folium_animation.html

Usage:
    py -3.10 scripts/generate_folium_animation.py
"""

from __future__ import annotations

import datetime
import json
import sys
from pathlib import Path

import folium
import pandas as pd
from folium.plugins import TimestampedGeoJson

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from data_engine.projection import enu_to_wgs84  # noqa: E402

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ENU_ANCHOR = (35.773, -78.610)
STYLES = ["calm", "normal", "aggressive"]
STYLE_COLORS: dict[str, str] = {
    "calm": "#16a34a",
    "normal": "#d97706",
    "aggressive": "#dc2626",
}
HARSH_BRAKE_THRESHOLD_MPS2 = 3.0
DOWNSAMPLE_HZ = 1  # 100 Hz → 1 Hz  (keep HTML small)
BASE_EPOCH_S = 1_767_225_600  # 2026-01-01T00:00:00Z


def _log(msg: str) -> None:
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    sys.stdout.write(f"[{ts}] [generate_folium_animation] INFO  {msg}\n")


def _load_fused(style: str) -> pd.DataFrame:
    path = ROOT / "out" / f"sumo_{style}" / "fused_ekf.parquet"
    if not path.exists():
        raise FileNotFoundError(f"Missing: {path}")
    df = pd.read_parquet(path)
    step = max(1, int(round(100 / DOWNSAMPLE_HZ)))
    return df.iloc[::step].reset_index(drop=True)


def _load_score(style: str) -> dict:
    path = ROOT / "out" / f"sumo_{style}" / "score.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _to_latlon(df: pd.DataFrame) -> list[tuple[float, float]]:
    lat0, lon0 = ENU_ANCHOR
    return [enu_to_wgs84(r.px_m, r.py_m, lat0, lon0) for r in df.itertuples()]


def _ts(t_s: float) -> str:
    """Convert elapsed seconds to ISO-8601 UTC string ending in Z."""
    epoch_ms = int((BASE_EPOCH_S + t_s) * 1000)
    dt = datetime.datetime.fromtimestamp(epoch_ms / 1000, tz=datetime.timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{epoch_ms % 1000:03d}Z"


def _build_features(
    df: pd.DataFrame,
    latlon: list[tuple[float, float]],
    color: str,
    style: str,
) -> list[dict]:
    """Emit one Point per frame (moving dot only). Static route line is drawn separately."""
    features: list[dict] = []
    for row, (lat, lon) in zip(df.itertuples(), latlon, strict=False):
        t = _ts(float(row.t_s))
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
                "properties": {
                    "time": t,
                    "style": {"color": color},
                    "icon": "circle",
                    "iconstyle": {
                        "fillColor": color,
                        "fillOpacity": 1.0,
                        "stroke": True,
                        "color": "#fff",
                        "weight": 1.5,
                        "radius": 7,
                    },
                    "popup": f"{style.capitalize()} | {row.t_s:.1f}s | {row.v_mps:.1f} m/s",
                },
            }
        )
    return features


def _harsh_brake_markers(
    m: folium.Map,
    df: pd.DataFrame,
    latlon: list[tuple[float, float]],
    style: str,
) -> int:
    df = df.copy()
    df["ax"] = df["v_mps"].diff() / df["t_s"].diff()
    count = 0
    for i in df.index[df["ax"] < -HARSH_BRAKE_THRESHOLD_MPS2]:
        if i < len(latlon):
            lat, lon = latlon[i]
            folium.CircleMarker(
                location=[lat, lon],
                radius=8,
                color="#7f1d1d",
                fill=True,
                fill_color="#ef4444",
                fill_opacity=0.8,
                popup=f"Harsh brake ({style}): {df.loc[i,'ax']:.1f} m/s²",
                tooltip="⚠️ Harsh brake",
            ).add_to(m)
            count += 1
    return count


def _score_badge(sj: dict, style: str, color: str) -> str:
    s = sj.get("score_0_100", "—")
    t = sj.get("suggested_tip_pct", "—")
    s_str = f"{s:.1f}" if isinstance(s, float) else str(s)
    return (
        f'<span style="display:inline-block;margin:3px 5px;padding:5px 11px;'
        f"background:{color};color:#fff;border-radius:6px;font-size:12px;"
        f'font-weight:700;font-family:Inter,system-ui,sans-serif;">'
        f"{style.capitalize()}&nbsp;{s_str}/100&nbsp;tip&nbsp;{t}%</span>"
    )


def main() -> None:
    out_path = ROOT / "out" / "compare" / "folium_animation.html"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    _log("Loading data...")

    all_lats: list[float] = []
    all_lons: list[float] = []
    scores: dict[str, dict] = {}

    # Collect all positions first to compute bounds
    dfs: dict[str, pd.DataFrame] = {}
    latlons: dict[str, list[tuple[float, float]]] = {}
    for style in STYLES:
        df = _load_fused(style)
        ll = _to_latlon(df)
        dfs[style] = df
        latlons[style] = ll
        scores[style] = _load_score(style)
        all_lats.extend(lat for lat, _ in ll)
        all_lons.extend(lon for _, lon in ll)

    # Map centred on route midpoint, zoom 16 (street level)
    clat = (min(all_lats) + max(all_lats)) / 2
    clon = (min(all_lons) + max(all_lons)) / 2
    m = folium.Map(location=[clat, clon], zoom_start=16, tiles="CartoDB positron")

    # Fit bounds with small padding
    pad = 0.001
    m.fit_bounds(
        [
            [min(all_lats) - pad, min(all_lons) - pad],
            [max(all_lats) + pad, max(all_lons) + pad],
        ]
    )

    all_features: list[dict] = []

    for style in STYLES:
        df = dfs[style]
        ll = latlons[style]
        color = STYLE_COLORS[style]

        _log(f"Building {style} ({len(df)} frames)...")

        # ── Start / End markers ───────────────────────────────────────────
        s_lat, s_lon = ll[0]
        e_lat, e_lon = ll[-1]

        folium.Marker(
            [s_lat, s_lon],
            popup=f"START — {style}",
            tooltip=f"Start ({style})",
            icon=folium.Icon(
                color="green" if style == "calm" else "orange" if style == "normal" else "red",
                icon="play",
                prefix="fa",
            ),
        ).add_to(m)

        folium.Marker(
            [e_lat, e_lon],
            popup=f"GOAL — {style}",
            tooltip=f"Goal ({style})",
            icon=folium.Icon(
                color="green" if style == "calm" else "orange" if style == "normal" else "red",
                icon="flag",
                prefix="fa",
            ),
        ).add_to(m)

        # ── Static route line (full path, drawn immediately) ─────────────
        folium.PolyLine(
            locations=[[lat, lon] for lat, lon in ll],
            color=color,
            weight=3,
            opacity=0.35,
            tooltip=f"{style.capitalize()} route",
        ).add_to(m)

        # ── Animated dot features (merged into single layer) ──────────────
        all_features.extend(_build_features(df, ll, color, style))

        # ── Harsh brake markers ───────────────────────────────────────────
        n = _harsh_brake_markers(m, df, ll, style)
        _log(f"  harsh-brake events: {n}")

    # Single TimestampedGeoJson for all styles (avoids timeline conflicts)
    TimestampedGeoJson(
        data=json.dumps({"type": "FeatureCollection", "features": all_features}),
        period="PT1S",
        duration="PT2S",
        auto_play=False,
        loop=True,
        max_speed=10,
        loop_button=True,
        time_slider_drag_update=True,
    ).add_to(m)

    # ── Score legend ──────────────────────────────────────────────────────
    badges = "".join(_score_badge(scores[s], s, STYLE_COLORS[s]) for s in STYLES)
    legend = f"""
    <div style="position:fixed;top:12px;right:12px;z-index:1000;
                background:rgba(255,255,255,0.96);border-radius:10px;
                padding:12px 14px;box-shadow:0 2px 12px rgba(0,0,0,0.15);
                font-family:Inter,system-ui,sans-serif;">
      <div style="font-size:11px;font-weight:700;color:#6b7280;
                  letter-spacing:0.08em;text-transform:uppercase;margin-bottom:7px;">
        Driver Scores
      </div>
      {badges}
      <div style="margin-top:8px;font-size:11px;color:#9ca3af;">
        ⚠️ Red dots = harsh braking &nbsp;|&nbsp; ▶ = start &nbsp; 🏁 = goal
      </div>
    </div>"""
    m.get_root().html.add_child(folium.Element(legend))

    # ── Title bar ─────────────────────────────────────────────────────────
    title = """
    <div style="position:fixed;top:12px;left:50px;z-index:1000;
                background:rgba(13,27,42,0.90);border-radius:8px;
                padding:8px 16px;color:#e2e8f0;
                font-family:Inter,system-ui,sans-serif;
                font-size:13px;font-weight:600;">
        Raleigh Commute Digital Twin — SUMO Synthetic Evaluation
    </div>"""
    m.get_root().html.add_child(folium.Element(title))

    m.save(str(out_path))
    _log(f"Saved -> {out_path}")
    _log("Open in browser. Press Play on the bottom timeline to animate.")


if __name__ == "__main__":
    main()
