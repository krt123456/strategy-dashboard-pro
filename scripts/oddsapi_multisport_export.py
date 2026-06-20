#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


def _load_groups_map(config_path: Path) -> Dict[str, str]:
    if not config_path.exists():
        return {}
    data = json.loads(config_path.read_text(encoding="utf-8"))
    groups = data.get("groups", [])
    # group string -> slug
    mapping = {}
    for g in groups:
        slug = _slug(g)
        mapping[slug] = g
    return mapping


def _slug(text: str) -> str:
    import re

    text = text.strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-") or "group"


def _iter_event_files(events_root: Path) -> Iterable[Path]:
    if not events_root.exists():
        return []
    for sport_dir in sorted(events_root.iterdir()):
        if not sport_dir.is_dir():
            continue
        for p in sorted(sport_dir.glob("*.json")):
            yield p


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default="data/raw/oddsapi_multisport")
    ap.add_argument("--config", default="data/oddsapi_bookmakers_top10.json")
    ap.add_argument("--out", default="reports/oddsapi_multisport_snapshot.csv")
    args = ap.parse_args()

    data_dir = Path(args.data_dir)
    events_root = data_dir / "_events"
    groups_map = _load_groups_map(Path(args.config))

    rows: List[Dict[str, Any]] = []
    # iterate group dirs (exclude _events)
    for group_dir in sorted(data_dir.iterdir()):
        if not group_dir.is_dir():
            continue
        if group_dir.name == "_events":
            continue
        group_slug = group_dir.name
        group_name = groups_map.get(group_slug, group_slug)

        for sport_dir in sorted(group_dir.iterdir()):
            odds_dir = sport_dir / "odds"
            if not odds_dir.exists():
                continue
            sport = sport_dir.name
            for odds_path in sorted(odds_dir.glob("*.json")):
                try:
                    odds = json.loads(odds_path.read_text(encoding="utf-8"))
                except Exception:
                    continue
                if not isinstance(odds, dict):
                    continue

                event_id = odds.get("id") or odds.get("eventId") or odds.get("fixtureId")
                home = odds.get("home") or odds.get("participant1Name") or ""
                away = odds.get("away") or odds.get("participant2Name") or ""
                date = odds.get("date") or odds.get("startTime") or ""
                status = odds.get("status") or ""
                league = ""
                if isinstance(odds.get("league"), dict):
                    league = odds["league"].get("name") or odds["league"].get("slug") or ""

                bookmakers = odds.get("bookmakers") or odds.get("bookmakerOdds") or {}
                if not isinstance(bookmakers, dict):
                    continue

                for bm_name, markets in bookmakers.items():
                    if not isinstance(markets, list):
                        continue
                    for market in markets:
                        if not isinstance(market, dict):
                            continue
                        mname = market.get("name") or ""
                        updated = market.get("updatedAt") or ""
                        odds_list = market.get("odds") or []
                        if not isinstance(odds_list, list):
                            continue
                        for item in odds_list:
                            if not isinstance(item, dict):
                                continue
                            rows.append(
                                {
                                    "event_id": event_id,
                                    "sport": sport,
                                    "league": league,
                                    "date": date,
                                    "home": home,
                                    "away": away,
                                    "status": status,
                                    "group": group_name,
                                    "group_slug": group_slug,
                                    "bookmaker": bm_name,
                                    "market": mname,
                                    "updated_at": updated,
                                    "hdp": item.get("hdp", ""),
                                    "home_odds": item.get("home", ""),
                                    "away_odds": item.get("away", ""),
                                    "draw_odds": item.get("draw", ""),
                                    "over_odds": item.get("over", ""),
                                    "under_odds": item.get("under", ""),
                                }
                            )

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if rows:
        fieldnames = list(rows[0].keys())
        with out_path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
    else:
        out_path.write_text("", encoding="utf-8")
    print(f"Wrote {len(rows)} rows to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
