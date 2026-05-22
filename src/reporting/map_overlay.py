"""FR-11.2 -- Folium map overlay: actual (red) vs ideal (green) trajectories.

Clickable markers for harsh-braking events.

Implemented in task T5.3.

Usage:
    from reporting.map_overlay import generate_map_html
    html_fragment = generate_map_html(fused_df, ideal_df, score_doc,
                                      lat0=lat0, lon0=lon0, events=events)

ENU -> WGS-84 conversion is delegated to data_engine.projection.enu_to_wgs84
(flat-earth / equirectangular, 111 132.954 m/deg).  config/data_gen.yaml is
the single source of truth for the anchor; callers must supply lat0/lon0
(obtained via data_engine.projection.load_anchor()).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import pandas as pd
from data_engine.projection import enu_to_wgs84

if TYPE_CHECKING:
    pass


def generate_map_html(
    fused: pd.DataFrame,
    ideal: pd.DataFrame | None,
    score_doc: dict,
    *,
    lat0: float,
    lon0: float,
    events: list[dict] | None = None,
    width: str = "100%",
    height: str = "420px",
) -> str:
    """Generate a Folium Leaflet map and return its HTML as a string fragment.

    Parameters
    ----------
    fused:
        Fused filter output parquet (columns: t_s, px_m, py_m, v_mps, psi_rad, ...).
    ideal:
        Ideal trajectory parquet (columns: t_s, px_m, py_m, ...) or None.
    score_doc:
        Parsed score.json dict (for trip metadata in tooltips).
    lat0, lon0:
        ENU anchor from config/data_gen.yaml (via data_engine.projection.load_anchor).
    events:
        Harsh-brake event list from scoring.components.harsh_brake_penalty.
        Each dict must contain: t_s, decel_mps2, lat, lon.
    width, height:
        CSS dimensions for the map ``<div>``.

    Returns
    -------
    str
        The ``<div>...</div>`` HTML fragment with embedded Leaflet JS.
    """
    import folium

    px = fused["px_m"].to_numpy(dtype=float)
    py = fused["py_m"].to_numpy(dtype=float)
    actual_lats, actual_lons = enu_to_wgs84(px, py, lat0, lon0)

    center_lat = float(np.mean(actual_lats))
    center_lon = float(np.mean(actual_lons))

    m = folium.Map(
        location=[center_lat, center_lon],
        zoom_start=15,
        tiles="OpenStreetMap",
        width=width,
        height=height,
    )

    # Actual trajectory -- red polyline (subsample to keep file size down)
    step = max(1, len(actual_lats) // 2000)
    actual_coords = list(zip(actual_lats[::step], actual_lons[::step], strict=False))
    folium.PolyLine(
        actual_coords,
        color="#e64553",
        weight=3,
        opacity=0.85,
        tooltip="Actual trajectory",
    ).add_to(m)

    # Ideal trajectory -- green polyline (optional; may be absent)
    if ideal is not None and "px_m" in ideal.columns and "py_m" in ideal.columns:
        ipx = ideal["px_m"].to_numpy(dtype=float)
        ipy = ideal["py_m"].to_numpy(dtype=float)
        ideal_lats, ideal_lons = enu_to_wgs84(ipx, ipy, lat0, lon0)
        step_i = max(1, len(ideal_lats) // 2000)
        ideal_coords = list(zip(ideal_lats[::step_i], ideal_lons[::step_i], strict=False))
        folium.PolyLine(
            ideal_coords,
            color="#40a02b",
            weight=3,
            opacity=0.85,
            tooltip="Ideal trajectory",
            dash_array="6 4",
        ).add_to(m)

    # Harsh-brake markers (supplied by caller; already enriched with lat/lon)
    for ev in events or []:
        if "lat" not in ev or "lon" not in ev:
            continue
        popup_html = (
            f"<b>Harsh brake</b><br>"
            f"t = {ev['t_s']:.1f} s<br>"
            f"decel = {ev['decel_mps2']:.2f} m/s&sup2;"
        )
        folium.Marker(
            location=[ev["lat"], ev["lon"]],
            popup=folium.Popup(popup_html, max_width=200),
            tooltip=f"Harsh brake ({ev['decel_mps2']:.1f} m/s2)",
            icon=folium.Icon(color="orange", icon="warning-sign", prefix="glyphicon"),
        ).add_to(m)

    # Start / end markers
    if len(actual_lats) > 0:
        folium.CircleMarker(
            [actual_lats[0], actual_lons[0]],
            radius=7,
            color="#1e66f5",
            fill=True,
            fill_color="#1e66f5",
            tooltip="Trip start",
        ).add_to(m)
        folium.CircleMarker(
            [actual_lats[-1], actual_lons[-1]],
            radius=7,
            color="#d20f39",
            fill=True,
            fill_color="#d20f39",
            tooltip="Trip end",
        ).add_to(m)

    # Extract inner HTML from the Folium map
    raw_html: str = m._repr_html_()  # -- folium public rendering API
    return raw_html
