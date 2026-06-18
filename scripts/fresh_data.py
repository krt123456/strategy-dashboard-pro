#!/usr/bin/env python3
"""Fresh data fetcher — يجلب بيانات جديدة للسحابة.

يعمل على GitHub Actions بدون متصفح:
1. يحمل بيانات basketball من betexplorer
2. يحمل EPL من football-data.co.uk
3. يحمل نتائج من OpenLigaDB
"""
from __future__ import annotations

import csv
import json
import os
import sys
import urllib.request
from datetime import date, datetime
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent


def fetch_football_data_uk():
    """football-data.co.uk — EPL matches CSV."""
    url = "https://www.football-data.co.uk/mmz4281/2526/E0.csv"
    out = PROJECT_DIR / "data" / "epl_current.csv"
    try:
        urllib.request.urlretrieve(url, out)
        print(f"  ✓ EPL: {out.stat().st_size // 1024}KB")
        return True
    except Exception as e:
        print(f"  ✗ EPL: {e}")
        return False


def fetch_openligadb():
    """OpenLigaDB — Bundesliga results (free, no key)."""
    url = "https://api.openligadb.de/api/getmatchdata/bl1/2025"
    out = PROJECT_DIR / "data" / "bundesliga_results.json"
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read())
        out.write_text(json.dumps(data, indent=2))
        print(f"  ✓ Bundesliga: {len(data)} matches")
        return True
    except Exception as e:
        print(f"  ✗ Bundesliga: {e}")
        return False


def fetch_espn_scoreboard():
    """ESPN — NBA scores (free, no key)."""
    for sport, league in [("basketball", "nba"), ("basketball", "wnba")]:
        url = f"https://site.api.espn.com/apis/site/v2/sports/{sport}/{league}/scoreboard"
        out = PROJECT_DIR / "data" / f"espn_{league}.json"
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=10) as r:
                data = json.loads(r.read())
            out.write_text(json.dumps(data, indent=2))
            events = len(data.get("events", []))
            print(f"  ✓ ESPN {league}: {events} events")
        except Exception as e:
            print(f"  ✗ ESPN {league}: {e}")


def fetch_espn_schedule():
    """ESPN — upcoming schedule for predictions."""
    for sport, league in [("basketball", "nba"), ("basketball", "wnba")]:
        url = f"https://site.api.espn.com/apis/site/v2/sports/{sport}/{league}/scoreboard"
        out_path = PROJECT_DIR / "data" / f"espn_schedule_{league}.csv"
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=10) as r:
                data = json.loads(r.read())

            rows = []
            for event in data.get("events", []):
                competitions = event.get("competitions", [{}])
                if not competitions:
                    continue
                comp = competitions[0]
                competitors = comp.get("competitors", [])
                if len(competitors) < 2:
                    continue

                home_team = competitors[0].get("team", {})
                away_team = competitors[1].get("team", {})
                home = home_team.get("displayName", "?")
                away = away_team.get("displayName", "?")
                records = competitors[0].get("records", [{}])
                home_wins = records[0].get("summary", "0-0") if records else "0-0"

                status = event.get("status", {}).get("type", {}).get("completed", False)
                date_str = event.get("date", "")[:10]

                rows.append({
                    "Date": date_str,
                    "Home": home,
                    "Away": away,
                    "League": league.upper(),
                    "Completed": status,
                    "HomeRecord": home_wins,
                })

            if rows:
                with open(out_path, "w", newline="") as f:
                    writer = csv.DictWriter(f, fieldnames=rows[0].keys())
                    writer.writeheader()
                    writer.writerows(rows)
                print(f"  ✓ ESPN {league} schedule: {len(rows)} games → {out_path.name}")
        except Exception as e:
            print(f"  ✗ ESPN {league} schedule: {e}")


if __name__ == "__main__":
    print("📡 Fetching fresh data for cloud agent...")
    fetch_football_data_uk()
    fetch_openligadb()
    fetch_espn_scoreboard()
    fetch_espn_schedule()
    print("✓ Done")
