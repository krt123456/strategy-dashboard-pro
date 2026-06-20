#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Optional


def _safe_float(val: Any) -> Optional[float]:
    try:
        if val is None:
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


def _load_results(daily_dir: Path) -> Dict[str, Dict[str, Any]]:
    results: Dict[str, Dict[str, Any]] = {}
    if not daily_dir.exists():
        return results
    for path in daily_dir.glob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        for item in payload.get("summaries", []):
            event = item.get("sport_event", {})
            status = item.get("sport_event_status", {})
            event_id = event.get("id")
            if not event_id:
                continue
            results[event_id] = {
                "status": status.get("status"),
                "match_status": status.get("match_status"),
                "home_score": status.get("home_score"),
                "away_score": status.get("away_score"),
                "winner_id": status.get("winner_id"),
                "period_scores": status.get("period_scores", []),
            }
    return results


def _extract_probs(item: Dict[str, Any]) -> Optional[Dict[str, float]]:
    markets = item.get("markets", [])
    for market in markets:
        if market.get("name") != "2way":
            continue
        outcomes = market.get("outcomes", [])
        home_prob = away_prob = None
        for outcome in outcomes:
            name = outcome.get("name")
            prob = _safe_float(outcome.get("probability"))
            if prob is None:
                continue
            if name == "home_team_winner":
                home_prob = prob
            elif name == "away_team_winner":
                away_prob = prob
        if home_prob is not None and away_prob is not None:
            # probabilities are in percent
            return {"home": home_prob / 100.0, "away": away_prob / 100.0}
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prob-dir", default="data/raw/sportradar_tabletennis/probabilities")
    ap.add_argument("--daily-dir", default="data/raw/sportradar_tabletennis/daily")
    ap.add_argument("--out", default="data/processed/sportradar_tabletennis_prob_dataset.csv")
    args = ap.parse_args()

    prob_dir = Path(args.prob_dir)
    if not prob_dir.exists():
        print("Missing probabilities directory.")
        return 1
    results = _load_results(Path(args.daily_dir))

    rows: List[Dict[str, Any]] = []
    for path in prob_dir.glob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        for item in payload.get("sport_event_probabilities", []):
            event = item.get("sport_event", {})
            event_id = event.get("id")
            if not event_id:
                continue
            probs = _extract_probs(item)
            if not probs:
                continue
            context = event.get("sport_event_context", {})
            competition = context.get("competition", {})
            category = context.get("category", {})
            season = context.get("season", {})
            competitors = event.get("competitors", [])
            home = next((c for c in competitors if c.get("qualifier") == "home"), {})
            away = next((c for c in competitors if c.get("qualifier") == "away"), {})

            res = results.get(event_id, {})
            home_score = res.get("home_score")
            away_score = res.get("away_score")
            actual = None
            if home_score is not None and away_score is not None:
                actual = "H" if home_score > away_score else "A"

            rows.append(
                {
                    "event_id": event_id,
                    "start_time": event.get("start_time"),
                    "competition_id": competition.get("id"),
                    "competition_name": competition.get("name"),
                    "category_name": category.get("name"),
                    "season_id": season.get("id"),
                    "season_name": season.get("name"),
                    "home_id": home.get("id"),
                    "home_name": home.get("name"),
                    "away_id": away.get("id"),
                    "away_name": away.get("name"),
                    "home_prob": probs["home"],
                    "away_prob": probs["away"],
                    "home_score": home_score,
                    "away_score": away_score,
                    "actual": actual,
                }
            )

    if not rows:
        print("No probability rows found.")
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
