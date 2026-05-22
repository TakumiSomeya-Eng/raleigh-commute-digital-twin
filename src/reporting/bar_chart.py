"""FR-11.3 -- Inline SVG bar chart of the six penalty components.

No external assets; color ramp green (low penalty) -> red (high penalty).

Implemented in task T5.2.

Usage:
    from reporting.bar_chart import generate_svg
    svg_str = generate_svg(score_doc)
"""

from __future__ import annotations

_LABELS: dict[str, str] = {
    "jerk": "Jerk",
    "harsh_brake": "Harsh braking",
    "lat_accel": "Lateral accel",
    "speed": "Speed compliance",
    "deviation": "Route deviation",
    "lane_change": "Lane changes",
}

# Chart geometry
_W = 520  # total SVG width
_BAR_H = 28  # height of each bar
_BAR_GAP = 10  # vertical gap between bars
_LABEL_W = 130  # width reserved for left-side labels
_MAX_BAR_W = 300  # maximum bar width (= penalty 1.0)
_TOP_PAD = 30  # space for axis label at top
_LEFT_PAD = 10  # left margin


def _penalty_color(raw: float) -> str:
    """Interpolate dark-green (0) -> amber (0.5) -> dark-red (1) for light backgrounds."""
    if raw <= 0.5:
        t = raw / 0.5
        r = int(0x18 + t * (0xB0 - 0x18))
        g = int(0x79 + t * (0x60 - 0x79))
        b = int(0x5A * (1 - t))
    else:
        t = (raw - 0.5) / 0.5
        r = int(0xB0 + t * (0x99 - 0xB0))
        g = int(0x60 * (1 - t))
        b = int(t * 0x1B)
    return f"rgb({r},{g},{b})"


def _score_color(score: float) -> str:
    if score >= 80:
        return "#006450"
    if score >= 60:
        return "#a05000"
    return "#8b1a1a"


def generate_svg(score_doc: dict) -> str:
    """Return an inline SVG string for the six-component bar chart.

    Parameters
    ----------
    score_doc:
        Parsed score.json dict (TRD sec.1.8).

    Returns
    -------
    str
        A complete ``<svg>...</svg>`` element suitable for inline embedding.
    """
    components = score_doc.get("components", {})
    aggregate_raw = float(score_doc.get("aggregate_raw", 0.0))
    score_0_100 = float(score_doc.get("score_0_100", 100.0))

    names = ("jerk", "harsh_brake", "lat_accel", "speed", "deviation", "lane_change")
    n = len(names)
    score_row_h = _BAR_H + 20
    svg_h = _TOP_PAD + n * (_BAR_H + _BAR_GAP) + score_row_h + 20

    parts: list[str] = []
    score_c = _score_color(score_0_100)

    parts.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{_W}" height="{svg_h}" '
        f'role="img" aria-label="Component penalty chart">'
    )
    parts.append(
        f'<text x="{_LEFT_PAD + _LABEL_W + _MAX_BAR_W // 2}" y="18" '
        f'fill="#888" font-size="11" font-family="Arial, sans-serif" '
        f'text-anchor="middle">penalty (0 = perfect, 1 = worst)</text>'
    )

    for i, name in enumerate(names):
        comp = components.get(name, {})
        raw = float(comp.get("raw", 0.0))
        weighted = float(comp.get("weighted", 0.0))
        label = _LABELS.get(name, name)
        color = _penalty_color(raw)

        y = _TOP_PAD + i * (_BAR_H + _BAR_GAP)
        bar_w = int(raw * _MAX_BAR_W)
        bar_x = _LEFT_PAD + _LABEL_W

        parts.append(
            f'<text x="{bar_x - 6}" y="{y + _BAR_H // 2 + 4}" '
            f'fill="#1a1a1a" font-size="12" font-family="Arial, sans-serif" '
            f'text-anchor="end">{label}</text>'
        )
        parts.append(
            f'<rect x="{bar_x}" y="{y}" width="{_MAX_BAR_W}" height="{_BAR_H}" '
            f'fill="#e0ddd8" rx="0"/>'
        )
        if bar_w > 0:
            parts.append(
                f'<rect x="{bar_x}" y="{y}" width="{bar_w}" height="{_BAR_H}" '
                f'fill="{color}" rx="0">'
                f"<title>{label}: raw={raw:.3f}, weighted={weighted:.3f}</title>"
                f"</rect>"
            )
        val_x = bar_x + max(bar_w + 5, 5)
        parts.append(
            f'<text x="{val_x}" y="{y + _BAR_H // 2 + 4}" '
            f'fill="#888" font-size="11" font-family="Courier New, monospace">'
            f"{raw:.3f}</text>"
        )

    # Aggregate score row
    agg_y = _TOP_PAD + n * (_BAR_H + _BAR_GAP) + 12
    bar_x = _LEFT_PAD + _LABEL_W
    parts.append(
        f'<line x1="{bar_x}" y1="{agg_y - 6}" '
        f'x2="{bar_x + _MAX_BAR_W}" y2="{agg_y - 6}" '
        f'stroke="#e0ddd8" stroke-width="1"/>'
    )
    parts.append(
        f'<text x="{bar_x - 6}" y="{agg_y + _BAR_H // 2 + 4}" '
        f'fill="#1a1a1a" font-size="13" font-family="Arial, sans-serif" '
        f'font-weight="500" text-anchor="end">Score</text>'
    )
    score_bar_w = int((1.0 - aggregate_raw) * _MAX_BAR_W)
    agg_bar_color = _penalty_color(aggregate_raw)
    parts.append(
        f'<rect x="{bar_x}" y="{agg_y}" width="{_MAX_BAR_W}" height="{_BAR_H}" '
        f'fill="#e0ddd8" rx="0"/>'
    )
    if score_bar_w > 0:
        parts.append(
            f'<rect x="{bar_x}" y="{agg_y}" width="{score_bar_w}" height="{_BAR_H}" '
            f'fill="{agg_bar_color}" rx="0"/>'
        )
    parts.append(
        f'<text x="{bar_x + score_bar_w + 8}" y="{agg_y + _BAR_H // 2 + 4}" '
        f'fill="{score_c}" font-size="14" font-family="Courier New, monospace" font-weight="500">'
        f"{score_0_100:.1f} / 100</text>"
    )

    parts.append("</svg>")
    return "\n".join(parts)
