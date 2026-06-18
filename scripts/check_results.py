#!/usr/bin/env python3
"""Result checker — checks match results from free sources."""
from __future__ import annotations

import json
import sqlite3
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR / "scripts"))

from betting_journal import add_result, get_unresolved_predictions


def check_basketball_results(target_date: str):
    """Check ESPN for basketball results."""
    unresolved = get_unresolved_predictions(target_date)
    bball = [p for p in unresolved if p.get("sport") == "basketball"]
    if not bball:
        return 0

    resolved = 0
    for league in ["nba", "wnba"]:
        try:
            url = f"https://site.api.espn.com/apis/site/v2/sports/basketball/{league}/scoreboard"
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=10) as r:
                data = json.loads(r.read())

            for event in data.get("events", []):
                if not event.get("status", {}).get("type", {}).get("completed"):
                    continue
                competitions = event.get("competitions", [{}])
                if not competitions:
                    continue
                comp = competitions[0]
                competitors = comp.get("competitors", [])
                if len(competitors) < 2:
                    continue

                home = competitors[0].get("team", {}).get("displayName", "")
                away = competitors[1].get("team", {}).get("displayName", "")
                home_score = int(competitors[0].get("score", 0))
                away_score = int(competitors[1].get("score", 0))
                home_won = home_score > away_score

                for pred in bball:
                    if pred["home"] in home or home in pred["home"] or \
                       pred["away"] in away or away in pred["away"]:
                        pick_won = (pred["pick"] in home and home_won) or \
                                   (pred["pick"] in away and not home_won) or \
                                   (pred["pick"] in pred["home"] and home_won) or \
                                   (pred["pick"] in pred["away"] and not home_won)
                        add_result(pred["id"], home_score, away_score, pick_won, "espn")
                        resolved += 1
        except Exception:
            continue

    return resolved


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", type=str, default=None)
    args = parser.parse_args()

    target = args.date or datetime.now().strftime("%Y-%m-%d")
    print(f"Checking results for {target}...")

    count = check_basketball_results(target)
    print(f"Resolved: {count}")


if __name__ == "__main__":
    main()
