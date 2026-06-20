#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Optional


def _safe_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _load_events(events_dir: Path) -> Dict[int, Dict[str, Any]]:
    events: Dict[int, Dict[str, Any]] = {}
    for path in sorted(events_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(data, list):
            continue
        for item in data:
            event_id = item.get("id")
            if event_id is None:
                continue
            events[int(event_id)] = item
    return events


def _extract_ml(odds_payload: Dict[str, Any], bookmaker: str) -> Optional[Dict[str, float]]:
    bookmakers = odds_payload.get("bookmakers", {})
    if bookmaker not in bookmakers:
        return None
    markets = bookmakers.get(bookmaker, [])
    for market in markets:
        if market.get("name") != "ML":
            continue
        odds_list = market.get("odds", [])
        if not odds_list:
            continue
        ml = odds_list[0]
        home = _safe_float(ml.get("home"))
        away = _safe_float(ml.get("away"))
        if home is None or away is None:
            return None
        return {"home": home, "away": away}
    return None


def _implied_probs(home: float, away: float) -> Dict[str, float]:
    ph = 1.0 / home if home > 0 else 0.0
    pa = 1.0 / away if away > 0 else 0.0
    total = ph + pa
    if total <= 0:
        return {"home": 0.0, "away": 0.0, "overround": 0.0}
    return {"home": ph / total, "away": pa / total, "overround": total}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--events-dir", default="data/raw/oddsapi_tabletennis/events")
    ap.add_argument("--odds-dir", default="data/raw/oddsapi_tabletennis/odds")
    ap.add_argument("--bookmaker", default="Bet365")
    ap.add_argument("--out", default="data/processed/oddsapi_tabletennis_dataset.csv")
    args = ap.parse_args()

    events = _load_events(Path(args.events_dir))
    odds_dir = Path(args.odds_dir)
    rows: List[Dict[str, Any]] = []

    for path in sorted(odds_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        event_id = data.get("id")
        if event_id is None or int(event_id) not in events:
            continue
        ml = _extract_ml(data, args.bookmaker)
        if not ml:
            continue
        probs = _implied_probs(ml["home"], ml["away"])
        event = events[int(event_id)]
        scores = event.get("scores") or {}
        home_score = scores.get("home")
        away_score = scores.get("away")
        actual = None
        if isinstance(home_score, (int, float)) and isinstance(away_score, (int, float)):
            actual = "H" if home_score > away_score else "A"

        rows.append(
            {
                "event_id": int(event_id),
                "date": event.get("date"),
                "league": (event.get("league") or {}).get("name"),
                "league_slug": (event.get("league") or {}).get("slug"),
                "home": event.get("home"),
                "away": event.get("away"),
                "status": event.get("status"),
                "home_score": home_score,
                "away_score": away_score,
                "home_odds": ml["home"],
                "away_odds": ml["away"],
                "home_prob": probs["home"],
                "away_prob": probs["away"],
                "overround": probs["overround"],
                "actual": actual,
            }
        )

    if not rows:
        print("No odds rows found. Ensure odds JSON contains ML odds.")
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
