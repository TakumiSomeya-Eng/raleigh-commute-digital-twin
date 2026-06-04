"""Folium animation helpers for Phase 3 Video B output.

Functions:
    add_trajectory_animation  -- animated TimestampedGeoJson trajectory layer
    add_harsh_brake_markers   -- red CircleMarkers at harsh braking events
    score_color               -- map aggregate score to CSS colour band
"""

from __future__ import annotations

import sys

import folium
import pandas as pd
from folium.plugins import TimestampedGeoJson

from src.data_engine.projection import enu_to_wgs84

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

HARSH_BRAKE_THRESHOLD_MPS2: float = 3.0  # matches config/scoring.yaml

SCORE_COLORS: dict[tuple[int, int], dict[str, str]] = {
    (90, 100): {"bg": "#DCFCE7", "text": "#166534", "label": "Excellent"},
    (75, 90): {"bg": "#DBEAFE", "text": "#1E3A8A", "label": "Good"},
    (60, 75): {"bg": "#FEF9C3", "text": "#713F12", "label": "Fair"},
    (45, 60): {"bg": "#FED7AA", "text": "#92400E", "label": "Poor"},
    (0, 45): {"bg": "#FEE2E2", "text": "#991B1B", "label": "Unsafe"},
}

_ACTUAL_COLOR = "#EF4444"
_IDEAL_COLOR = "#3B82F6"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def add_trajectory_animation(
    m: folium.Map,
    fused_df: pd.DataFrame,  # columns: t_s, px_m, py_m, v_mps
    ideal_df: pd.DataFrame,  # columns: t_s, px_m, py_m
    enu_anchor: tuple[float, float],  # (lat0, lon0)
    style: str = "actual",
) -> folium.Map:
    """Add animated trajectory to a Folium map via TimestampedGeoJson.

    Both fused (actual) and ideal trajectories are added as separate layers.
    The ``style`` parameter controls the colour of the *fused* trajectory.

    Args:
        m:          Target Folium Map.
        fused_df:   Actual (fused) trajectory with columns t_s, px_m, py_m, v_mps.
        ideal_df:   Ideal reference trajectory with columns t_s, px_m, py_m.
        enu_anchor: (lat0_deg, lon0_deg) ENU origin.
        style:      "actual" → red dots; "ideal" → blue dots (affects fused layer).

    Returns:
        The same map object with animation layers added.
    """
    lat0, lon0 = enu_anchor
    color = _ACTUAL_COLOR if style == "actual" else _IDEAL_COLOR

    # --- fused trajectory features ---
    fused_features: list[dict] = []
    for _, row in fused_df.iterrows():
        lat, lon = enu_to_wgs84(row["px_m"], row["py_m"], lat0, lon0)
        fused_features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
                "properties": {
                    "time": pd.Timestamp(row["t_s"], unit="s").isoformat(),
                    "style": {"color": color},
                    "icon": "circle",
                    "iconstyle": {
                        "fillColor": color,
                        "fillOpacity": 0.8,
                        "radius": 6,
                    },
                    "popup": f"v={row['v_mps']:.1f} m/s",
                },
            }
        )

    TimestampedGeoJson(
        data={"type": "FeatureCollection", "features": fused_features},
        period="PT0.1S",
        duration="PT0.5S",
        auto_play=True,
        loop=True,
        max_speed=10,
        loop_button=True,
        time_slider_drag_update=True,
    ).add_to(m)

    # --- ideal trajectory features (always blue) ---
    if not ideal_df.empty:
        ideal_features: list[dict] = []
        for _, row in ideal_df.iterrows():
            lat, lon = enu_to_wgs84(row["px_m"], row["py_m"], lat0, lon0)
            ideal_features.append(
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [lon, lat]},
                    "properties": {
                        "time": pd.Timestamp(row["t_s"], unit="s").isoformat(),
                        "style": {"color": _IDEAL_COLOR},
                        "icon": "circle",
                        "iconstyle": {
                            "fillColor": _IDEAL_COLOR,
                            "fillOpacity": 0.5,
                            "radius": 4,
                        },
                        "popup": "ideal",
                    },
                }
            )

        TimestampedGeoJson(
            data={"type": "FeatureCollection", "features": ideal_features},
            period="PT0.1S",
            duration="PT0.5S",
            auto_play=True,
            loop=True,
            max_speed=10,
            loop_button=True,
            time_slider_drag_update=True,
        ).add_to(m)

    sys.stdout.write(
        f"[folium_animation] add_trajectory_animation: {len(fused_features)} fused"
        f" + {len(ideal_df)} ideal features added (style={style})\n"
    )
    return m


def add_harsh_brake_markers(
    m: folium.Map,
    fused_df: pd.DataFrame,
    enu_anchor: tuple[float, float],
) -> folium.Map:
    """Add red CircleMarkers at harsh braking events.

    Braking magnitude is approximated as the finite-difference deceleration
    of the fused speed signal.

    Args:
        m:          Target Folium Map.
        fused_df:   Fused trajectory with columns t_s, px_m, py_m, v_mps.
        enu_anchor: (lat0_deg, lon0_deg) ENU origin.

    Returns:
        The same map object with markers added.
    """
    lat0, lon0 = enu_anchor
    df = fused_df.copy()
    df["ax"] = df["v_mps"].diff() / df["t_s"].diff()
    harsh = df[df["ax"] < -HARSH_BRAKE_THRESHOLD_MPS2]

    count = 0
    for _, row in harsh.iterrows():
        lat, lon = enu_to_wgs84(row["px_m"], row["py_m"], lat0, lon0)
        folium.CircleMarker(
            location=[lat, lon],
            radius=10,
            color=_ACTUAL_COLOR,
            fill=True,
            fill_color=_ACTUAL_COLOR,
            fill_opacity=0.6,
            popup=f"Harsh brake: {row['ax']:.1f} m/s²",
            tooltip="Harsh brake",
        ).add_to(m)
        count += 1

    sys.stdout.write(f"[folium_animation] add_harsh_brake_markers: {count} event(s) marked\n")
    return m


def score_color(score: float) -> dict:
    """Return CSS colour dict for the given aggregate score.

    Args:
        score: Aggregate score in [0, 100].

    Returns:
        dict with keys ``bg``, ``text``, ``label``.
    """
    for (lo, hi), style in SCORE_COLORS.items():
        if lo <= score < hi:
            return style
    # Fallback: anything outside [0, 100) → Unsafe band
    return SCORE_COLORS[(0, 45)]
