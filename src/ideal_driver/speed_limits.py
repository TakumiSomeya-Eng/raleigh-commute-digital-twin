"""FR-9.2 — Speed limit lookup from OSM way IDs.

Priority (per TRD §9.2):
1. OSM ``maxspeed`` tag via Overpass API
2. Hand-coded corridors in ``config/speed_limits.yaml``
3. Urban default (30 mph = 13.4 m/s)
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

import requests
import yaml

# ---------------------------------------------------------------------------
# Unit-conversion constants
# ---------------------------------------------------------------------------

_MPH_TO_MPS: float = 0.44704  # 1 mph -> m/s
_KMH_TO_MPS: float = 1.0 / 3.6  # 1 km/h -> m/s

_OVERPASS_URL = "https://overpass-api.de/api/interpreter"
_OVERPASS_TIMEOUT_S = 20  # per-query HTTP timeout


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def _parse_maxspeed(tag: str) -> float | None:
    """Parse an OSM ``maxspeed`` tag value to m/s.

    Recognised formats:
    - ``"30 mph"`` / ``"30mph"``
    - ``"50"``   (bare integer -> km/h by OSM convention)
    - ``"50 km/h"`` / ``"50 kmh"``
    - ``"none"`` / ``"unlimited"`` / ``"signals"`` -> None (unknown)

    Returns None when the tag cannot be converted.
    """
    tag = tag.strip().lower()
    if not tag or tag in ("none", "unlimited", "signals", "variable", "walk"):
        return None

    # mph form
    m = re.match(r"^(\d+(?:\.\d+)?)\s*mph$", tag)
    if m:
        return float(m.group(1)) * _MPH_TO_MPS

    # km/h form (with or without explicit unit)
    m = re.match(r"^(\d+(?:\.\d+)?)\s*(?:km/h|kmh)?$", tag)
    if m:
        return float(m.group(1)) * _KMH_TO_MPS

    return None


# ---------------------------------------------------------------------------
# SpeedLimitLookup
# ---------------------------------------------------------------------------


class SpeedLimitLookup:
    """Resolve speed limits for OSM way IDs (FR-9.2).

    Parameters
    ----------
    config_path:
        Path to ``config/speed_limits.yaml``.
    overpass_url:
        Overpass API endpoint (testable by injection).
    skip_overpass:
        When True, skip the Overpass query and go straight to YAML /
        default fallback.  Useful for offline testing and CI.
    """

    def __init__(
        self,
        config_path: Path,
        overpass_url: str = _OVERPASS_URL,
        skip_overpass: bool = False,
    ) -> None:
        with open(config_path, encoding="utf-8") as fh:
            cfg: dict[str, Any] = yaml.safe_load(fh) or {}
        self._default_mps: float = float(cfg.get("urban_default_mps", 13.4))
        self._corridors: dict[int, float] = {
            int(k): float(v) for k, v in (cfg.get("corridors") or {}).items()
        }
        self._overpass_url = overpass_url
        self._skip_overpass = skip_overpass
        self._cache: dict[int, float] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def lookup(self, way_ids: list[int]) -> dict[int, float]:
        """Return ``{way_id: speed_mps}`` for every ID in *way_ids*.

        Missing IDs fall back through the priority chain and are memoised.
        """
        uncached = [w for w in set(way_ids) if w not in self._cache]

        if uncached and not self._skip_overpass:
            osm_data = self._query_overpass(uncached)
            for wid, spd in osm_data.items():
                self._cache[wid] = spd

        result: dict[int, float] = {}
        for wid in way_ids:
            if wid in self._cache:
                result[wid] = self._cache[wid]
            elif wid in self._corridors:
                spd = self._corridors[wid]
                self._cache[wid] = spd
                result[wid] = spd
            else:
                self._cache[wid] = self._default_mps
                result[wid] = self._default_mps
        return result

    def get(self, way_id: int) -> float:
        """Return the speed limit for a single *way_id*."""
        return self.lookup([way_id])[way_id]

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _query_overpass(self, way_ids: list[int]) -> dict[int, float]:
        """Query Overpass API for ``maxspeed`` tags.

        Returns an empty dict on any network / parse failure (graceful
        degradation to YAML / default fallback).
        """
        id_list = ",".join(str(w) for w in way_ids)
        query = f"[out:json][timeout:{_OVERPASS_TIMEOUT_S}];" f"way(id:{id_list});out tags;"
        try:
            resp = requests.post(
                self._overpass_url,
                data={"data": query},
                timeout=_OVERPASS_TIMEOUT_S + 5,
            )
            if resp.status_code != 200:
                sys.stderr.write(
                    f"Overpass HTTP {resp.status_code} -- speed-limit fallback to YAML\n"
                )
                return {}
            result: dict[int, float] = {}
            for el in resp.json().get("elements", []):
                wid = int(el["id"])
                raw = el.get("tags", {}).get("maxspeed", "")
                if raw:
                    spd = _parse_maxspeed(raw)
                    if spd is not None:
                        result[wid] = spd
            return result
        except Exception as exc:  # (broad on purpose -- many failure modes)
            sys.stderr.write(f"Overpass query failed ({exc!r}) -- speed-limit fallback to YAML\n")
            return {}
