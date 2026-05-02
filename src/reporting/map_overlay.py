"""FR-11.2 -- Folium map overlay: actual (red) vs ideal (green) trajectories.

Clickable markers for harsh-braking events.

Implemented in task T5.3.

Usage:
    from reporting.map_overlay import generate_map_html
    html_fragment = generate_map_html(fused_df, ideal_df, score_doc)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

if TYPE_CHECKING:
    pass

# Threshold for harsh-brake detection (matches scoring.components logic).
# Deceleration magnitude below which an event is NOT flagged.
_HARSH_BRAKE_THRESHOLD_MPS2 = 3.0  # |a_lon| >= this -> harsh brake

# ENU anchor (must match config/data_gen.yaml)
_LAT0_DEG = 35.773
_LON0_DEG = -78.610
_R_EARTH = 6_371_000.0


def _enu_to_latlon(px: np.ndarray, py: np.ndarray) -> tuple[list[float], list[float]]:
    """Convert ENU (m) arrays to lat/lon degrees using the project ENU anchor."""
    import math

    lat0 = math.radians(_LAT0_DEG)
    lon0 = math.radians(_LON0_DEG)
    lats = (py / _R_EARTH + lat0) * (180.0 / math.pi)
    lons = (px / (_R_EARTH * math.cos(lat0)) + lon0) * (180.0 / math.pi)
    return lats.tolist(), lons.tolist()


def _find_harsh_brake_events(
    fused: pd.DataFrame,
    threshold: float = _HARSH_BRAKE_THRESHOLD_MPS2,
) -> list[dict]:
    """Return list of harsh-brake events as {t_s, lat, lon, decel_mps2} dicts."""
    v = fused["v_mps"].to_numpy(dtype=float)
    t = fused["t_s"].to_numpy(dtype=float)
    px = fused["px_m"].to_numpy(dtype=float)
    py = fused["py_m"].to_numpy(dtype=float)

    a_lon = np.gradient(v, t)

    events: list[dict] = []
    in_event = False
    for i in range(len(a_lon)):
        decel = -a_lon[i]
        if decel >= threshold and not in_event:
            lats, lons = _enu_to_latlon(np.array([px[i]]), np.array([py[i]]))
            events.append(
                {
                    "t_s": float(t[i]),
                    "lat": lats[0],
                    "lon": lons[0],
                    "decel_mps2": float(decel),
                }
            )
            in_event = True
        elif decel < threshold:
            in_event = False

    return events


def generate_map_html(
    fused: pd.DataFrame,
    ideal: pd.DataFrame | None,
    score_doc: dict,
    *,
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
    actual_lats, actual_lons = _enu_to_latlon(px, py)

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
        ideal_lats, ideal_lons = _enu_to_latlon(ipx, ipy)
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

    # Harsh-brake markers
    events = _find_harsh_brake_events(fused)
    for ev in events:
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
    if actual_lats:
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
