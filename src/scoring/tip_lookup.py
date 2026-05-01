"""FR-10.8 — Tip band lookup table (data-driven from config/scoring.yaml).

Maps aggregate score [0, 100] to a suggested tip percentage.
Output clearly labeled as "SUGGESTED — final tipping decision is manual."

Implemented in task T4.7.
"""

from __future__ import annotations

from pathlib import Path

import yaml

_CONFIG_PATH: Path = Path("config/scoring.yaml")


def lookup_tip(
    score_0_100: float,
    config_path: Path | None = None,
) -> dict:
    """Map an aggregate score to a suggested tip band.

    The lookup table is read from ``config/scoring.yaml`` (``tip_bands``
    section) so thresholds can be tuned without code changes.
    Bands are checked from the first entry downward; first match wins.

    Parameters
    ----------
    score_0_100:
        Aggregate driver score in [0, 100].
    config_path:
        Path to scoring.yaml; defaults to ``config/scoring.yaml``.

    Returns
    -------
    dict with keys:
        ``tip_pct``   : int   — suggested tip percentage (e.g. 20)
        ``band``      : str   — "min_score-max_score" label (e.g. "75-89")
        ``label``     : str   — human label (e.g. "Good")
        ``notes``     : str   — mandatory disclaimer
    """
    path = Path(config_path) if config_path is not None else _CONFIG_PATH
    with open(path, encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)

    bands = cfg.get("tip_bands", [])
    if not bands:
        raise ValueError(f"No tip_bands found in {path}")

    score = float(score_0_100)
    for band in bands:
        lo = float(band["min_score"])
        hi = float(band["max_score"])
        if lo <= score <= hi:
            return {
                "tip_pct": int(band["tip_pct"]),
                "band": f"{int(lo)}-{int(hi)}",
                "label": str(band["label"]),
                "notes": "SUGGESTED — final tipping decision is manual.",
            }

    # Fallback: use last band (lowest tier)
    last = bands[-1]
    lo = float(last["min_score"])
    hi = float(last["max_score"])
    return {
        "tip_pct": int(last["tip_pct"]),
        "band": f"{int(lo)}-{int(hi)}",
        "label": str(last["label"]),
        "notes": "SUGGESTED — final tipping decision is manual.",
    }
