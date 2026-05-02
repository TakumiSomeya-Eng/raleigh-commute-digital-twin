"""FR-11.5 -- Subjective rating ingest and Spearman rho computation.

Reads config/ratings.yaml (gitignored, user-maintained) and merges into the index.

Implemented in task T5.4.

ratings.yaml format:
    # trip_id: 1..5  (1 = terrible, 5 = excellent)
    day1: 4
    day2: 5

Usage:
    from reporting.ratings import load_ratings, spearman_rho
    ratings = load_ratings(Path("config/ratings.yaml"))
    rho, n = spearman_rho(tool_scores, subjective_ratings)
"""

from __future__ import annotations

from pathlib import Path

import yaml

_MIN_TRIPS_FOR_RHO = 5  # Spearman rho requires >= 5 paired observations


def load_ratings(ratings_path: Path) -> dict[str, int]:
    """Load trip_id -> 1..5 subjective rating mapping from YAML.

    Parameters
    ----------
    ratings_path:
        Path to ``ratings.yaml``.  Missing file returns empty dict (graceful).

    Returns
    -------
    dict mapping trip_id -> rating int.
    """
    if not ratings_path.exists():
        return {}
    with open(ratings_path, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    result: dict[str, int] = {}
    for k, v in raw.items():
        try:
            rating = int(v)
            if 1 <= rating <= 5:
                result[str(k)] = rating
        except (TypeError, ValueError):
            pass
    return result


def spearman_rho(
    tool_scores: list[float],
    subjective_ratings: list[float],
) -> tuple[float | None, int]:
    """Compute Spearman rank correlation between tool scores and subjective ratings.

    Parameters
    ----------
    tool_scores:
        List of aggregate scores in [0, 100] (one per paired trip).
    subjective_ratings:
        List of 1..5 subjective ratings (same order as tool_scores).

    Returns
    -------
    (rho, n)
        rho: Spearman correlation [-1, 1] or None if n < _MIN_TRIPS_FOR_RHO.
        n: number of paired observations used.

    Notes
    -----
    Uses the textbook rank-based formula with average-rank tie handling.
    No external stats dependency required.
    """
    n = len(tool_scores)
    if n < _MIN_TRIPS_FOR_RHO or n != len(subjective_ratings):
        return None, n

    def _ranks(vals: list[float]) -> list[float]:
        sorted_idx = sorted(range(len(vals)), key=lambda i: vals[i])
        rank: list[float] = [0.0] * len(vals)
        i = 0
        while i < len(sorted_idx):
            j = i
            while j < len(sorted_idx) - 1 and vals[sorted_idx[j + 1]] == vals[sorted_idx[j]]:
                j += 1
            avg_rank = (i + j) / 2.0 + 1.0
            for k in range(i, j + 1):
                rank[sorted_idx[k]] = avg_rank
            i = j + 1
        return rank

    rx = _ranks(tool_scores)
    ry = _ranks(subjective_ratings)
    mean_rx = sum(rx) / n
    mean_ry = sum(ry) / n
    num = sum((rx[i] - mean_rx) * (ry[i] - mean_ry) for i in range(n))
    den_x = sum((r - mean_rx) ** 2 for r in rx) ** 0.5
    den_y = sum((r - mean_ry) ** 2 for r in ry) ** 0.5
    if den_x == 0 or den_y == 0:
        return None, n
    rho = num / (den_x * den_y)
    return round(float(rho), 4), n
