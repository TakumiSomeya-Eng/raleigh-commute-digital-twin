"""Export score.json files to Evidence.dev CSV sources.

Usage:
    py -3.10 scripts/export_to_evidence.py

Reads:  out/sumo_{style}/score.json  (for each style in STYLES)
Writes: C:/evd/sources/driving/scores.csv
        C:/evd/sources/driving/components.csv

Run this after any pipeline re-score, then restart the Evidence dev server.
"""

from __future__ import annotations

import csv
import datetime
import json
import sys
from pathlib import Path

STYLES: list[str] = ["calm", "normal", "aggressive"]
PROJECT_ROOT = Path(__file__).resolve().parent.parent
EVIDENCE_SOURCES = Path("C:/evd/sources/driving")


def _log(msg: str) -> None:
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    sys.stdout.write(f"[{ts}] [export_to_evidence] INFO  {msg}\n")


def main() -> None:
    EVIDENCE_SOURCES.mkdir(parents=True, exist_ok=True)

    scores_rows: list[dict] = []
    comp_rows: list[dict] = []

    for style in STYLES:
        path = PROJECT_ROOT / "out" / f"sumo_{style}" / "score.json"
        if not path.exists():
            _log(f"WARNING: {path} not found — skipping {style}")
            continue

        sj = json.loads(path.read_text(encoding="utf-8"))

        scores_rows.append(
            {
                "style": style,
                "score": round(float(sj["score_0_100"]), 2),
                "tip_pct": int(sj["suggested_tip_pct"]),
                "tip_band": sj["suggested_tip_band"],
                "trip_id": sj["trip_id"],
                "timestamp": sj["timestamp_utc"],
            }
        )

        for comp_name, meta in sj.get("components", {}).items():
            comp_rows.append(
                {
                    "style": style,
                    "component": comp_name,
                    "raw": round(float(meta["raw"]), 4),
                    "weight": float(meta["weight"]),
                    "weighted": round(float(meta["weighted"]), 4),
                    "score_pct": round(float(meta["raw"]) * 100, 1),
                }
            )

    # Write scores.csv
    scores_path = EVIDENCE_SOURCES / "scores.csv"
    with open(scores_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(scores_rows[0].keys()))
        writer.writeheader()
        writer.writerows(scores_rows)
    _log(f"wrote {len(scores_rows)} rows -> {scores_path}")

    # Write components.csv
    comp_path = EVIDENCE_SOURCES / "components.csv"
    with open(comp_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(comp_rows[0].keys()))
        writer.writeheader()
        writer.writerows(comp_rows)
    _log(f"wrote {len(comp_rows)} rows -> {comp_path}")

    _log("Done. Now run: cd C:\\evd && npm run sources && npm run dev")


if __name__ == "__main__":
    main()
