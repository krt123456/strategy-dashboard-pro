#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Optional


def _safe_int(value: Any) -> Optional[int]:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _extract_competitors(event: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    comps = {"home": {}, "away": {}}
    for comp in event.get("competitors", []):
        qualifier = comp.get("qualifier")
        if qualifier in comps:
            comps[qualifier] = comp
    return comps


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--daily-dir", default="data/raw/sportradar_tabletennis/daily")
    ap.add_argument("--out", default="data/processed/sportradar_tabletennis_matches.csv")
    args = ap.parse_args()

    daily_dir = Path(args.daily_dir)
    rows: List[Dict[str, Any]] = []

    for path in sorted(daily_dir.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        for item in payload.get("summaries", []):
            event = item.get("sport_event", {})
            status = item.get("sport_event_status", {})
            context = event.get("sport_event_context", {})
            competition = context.get("competition", {})
            category = context.get("category", {})
            season = context.get("season", {})

            competitors = _extract_competitors(event)
            home = competitors.get("home", {})
            away = competitors.get("away", {})

            rows.append(
                {
                    "event_id": event.get("id"),
                    "start_time": event.get("start_time"),
                    "competition_id": competition.get("id"),
                    "competition_name": competition.get("name"),
                    "competition_type": competition.get("type"),
                    "competition_gender": competition.get("gender"),
                    "category_id": category.get("id"),
                    "category_name": category.get("name"),
                    "season_id": season.get("id"),
                    "season_name": season.get("name"),
                    "status": status.get("status"),
                    "match_status": status.get("match_status"),
                    "home_id": home.get("id"),
                    "home_name": home.get("name"),
                    "away_id": away.get("id"),
                    "away_name": away.get("name"),
                    "home_score": _safe_int(status.get("home_score")),
                    "away_score": _safe_int(status.get("away_score")),
                    "winner_id": status.get("winner_id"),
                    "period_scores": json.dumps(status.get("period_scores", []), ensure_ascii=False),
                }
            )

    if not rows:
        print("No rows found in daily summaries.")
        return 1

    import pandas as pd

    df = pd.DataFrame(rows)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_path, index=False)
    print(f"Wrote {len(df)} rows to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
