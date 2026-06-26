#!/usr/bin/env python3
"""Resolve finished matches from API-Sports for sports that betexplorer/flashscore
miss (hockey, handball, volleyball, baseball, basketball, football lower-divisions).

API-Sports free plan: 100 req/day per sport API. One date request returns ALL games
that date (finished + scheduled), so ~1-3 req/sport/day. Auth: header x-apisports-key.
Key read from env API_SPORTS_KEY or project .env (never committed).

Reuse the fuzzy matcher + pick-side/draw logic from resolve_results_betexplorer.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_DIR = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_DIR / "data" / "betting_journal.db"

# sport -> (api base, version). Football is v3 (/fixtures); others are v1 (/games).
SPORTS: Dict[str, str] = {
    "football": "https://v3.football.api-sports.io",
    "basketball": "https://v1.basketball.api-sports.io",
    "baseball": "https://v1.baseball.api-sports.io",
    "hockey": "https://v1.hockey.api-sports.io",
    "handball": "https://v1.handball.api-sports.io",
    "volleyball": "https://v1.volleyball.api-sports.io",
}

DRAW_SPORTS = {"football", "soccer", "hockey", "icehockey", "handball", "futsal", "cricket"}


def _norm(s: str) -> str:
    return (s or "").lower().replace(" ", "").replace("-", "")


def load_key() -> str:
    k = os.environ.get("API_SPORTS_KEY", "").strip()
    if k:
        return k
    envf = PROJECT_DIR / ".env"
    if envf.exists():
        for line in envf.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("API_SPORTS_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def _score(val: Any) -> Optional[int]:
    """api-sports score value may be int, {total:int}, or {fulltime:[h,a]}."""
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return int(val)
    if isinstance(val, dict):
        for k in ("total", "fulltime", "current"):
            v = val.get(k)
            if isinstance(v, (int, float)):
                return int(v)
            if isinstance(v, list) and v:
                try:
                    return int(v[0])
                except Exception:
                    pass
    return None


def fetch_games(sport: str, date_str: str, key: str) -> List[dict]:
    base = SPORTS[sport]
    path = "/fixtures" if sport == "football" else "/games"
    url = f"{base}{path}?date={date_str}"
    import requests
    r = requests.get(url, headers={"x-apisports-key": key}, timeout=25)
    data = r.json()
    if data.get("errors"):
        # errors can be empty dict or a message
        if isinstance(data["errors"], dict) and not data["errors"]:
            pass
        else:
            print(f"    {sport} {date_str} errors: {str(data['errors'])[:80]}")
    return data.get("response", []) or []


def _parse_game(g: dict, sport: str) -> Optional[dict]:
    """Extract (home, away, home_pts, away_pts, finished) from a game/fixture."""
    if sport == "football":
        teams = g.get("teams", {})
        goals = g.get("goals", {})
        status = (g.get("fixture", {}).get("status", {}) or {})
        finished = str(status.get("long", "")).lower().startswith("match finished") or status.get("short") in ("FT", "AET", "PEN")
        return {
            "home": teams.get("home", {}).get("name", ""),
            "away": teams.get("away", {}).get("name", ""),
            "home_pts": _score(goals.get("home")),
            "away_pts": _score(goals.get("away")),
            "finished": bool(finished),
        }
    teams = g.get("teams", {})
    scores = g.get("scores", {})
    status = str(g.get("status", {}).get("long") or g.get("status") or "").lower()
    finished = "finished" in status or status in ("ft", "game finished", "match finished", "after over time", "after penalties")
    return {
        "home": teams.get("home", {}).get("name", ""),
        "away": teams.get("away", {}).get("name", ""),
        "home_pts": _score(scores.get("home")),
        "away_pts": _score(scores.get("away")),
        "finished": bool(finished),
    }


# reuse betexplorer resolver's name similarity + pick-side
def _name_similarity(a, b):
    import resolve_results_betexplorer as be
    return be._name_similarity(a, b)


def _pick_side(pick, home, away, sport):
    import resolve_results_betexplorer as be
    return be._pick_side(pick, home, away, sport)


def _match(pred, results, min_score=4):
    pdate = pred["match_date"]
    ph, pa = pred["home"], pred["away"]
    best = None
    for r in results:
        if not r.get("finished"):
            continue
        if r["home_pts"] is None or r["away_pts"] is None:
            continue
        normal = _name_similarity(ph, r["home"]) + _name_similarity(pa, r["away"])
        swapped = _name_similarity(ph, r["away"]) + _name_similarity(pa, r["home"])
        score = max(normal, swapped)
        if score < min_score:
            continue
        if best is None or score > best[0]:
            best = (score, r)
    return best[1] if best else None


def resolve(target_date: str, days_back: int = 3) -> Dict[str, int]:
    key = load_key()
    stats = {"checked": 0, "resolved": 0, "no_match": 0, "no_score": 0, "no_key": 0}
    if not key:
        print("API_SPORTS_KEY not set (env or .env). Skipping.")
        stats["no_key"] = 1
        return stats

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    cutoff = (datetime.date.fromisoformat(target_date) - datetime.timedelta(days=days_back)).isoformat()
    rows = c.execute(
        "SELECT id, strategy, source, match_date, sport, home, away, pick "
        "FROM predictions WHERE match_date >= ? AND id NOT IN (SELECT prediction_id FROM results)",
        (cutoff,),
    ).fetchall()
    # group by (sport, date)
    by_sd: Dict[tuple, list] = {}
    for pid, strat, src, mdate, sport, home, away, pick in rows:
        ns = _norm(sport)
        if ns not in SPORTS:
            continue
        by_sd.setdefault((ns, mdate), []).append(
            {"id": pid, "match_date": mdate, "sport": ns, "home": home, "away": away, "pick": pick}
        )

    # fetch games per (sport, date) once
    games_cache: Dict[tuple, list] = {}
    for (sport, mdate) in by_sd:
        games_cache[(sport, mdate)] = [g for g in (_parse_game(x, sport) for x in fetch_games(sport, mdate, key)) if g and g["home"]]

    from betting_journal import add_result
    for (sport, mdate), preds in by_sd.items():
        results = games_cache.get((sport, mdate), [])
        finished = [r for r in results if r.get("finished")]
        for pred in preds:
            stats["checked"] += 1
            matched = _match(pred, results)
            if matched is None:
                stats["no_match"] += 1
                continue
            hp, ap = matched["home_pts"], matched["away_pts"]
            side = _pick_side(pred["pick"], pred["home"], pred["away"], sport)
            if side == "unknown":
                stats["no_score"] += 1
                continue
            nsport = _norm(sport)
            home_won = hp > ap
            is_draw = hp == ap
            # tie in a sport that can't draw = bad data, skip
            if is_draw and nsport not in DRAW_SPORTS:
                stats["no_score"] += 1
                continue
            if side == "home":
                won = home_won                  # draw -> home_won False -> lose
            elif side == "away":
                won = (not home_won) and (not is_draw)   # away wins only if away strictly leads
            else:  # 'draw' pick (rare for our home/away bets)
                won = is_draw
            try:
                add_result(pred["id"], hp, ap, bool(won), "api_sports")
                stats["resolved"] += 1
            except Exception as e:
                print(f"    add_result err: {str(e)[:60]}")
                stats["no_score"] += 1
    conn.close()
    print(f"api-sports resolve: checked={stats['checked']} resolved={stats['resolved']} "
          f"no_match={stats['no_match']} no_score={stats['no_score']} (sports: {sorted({s for s,_ in by_sd})})")
    return stats


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=datetime.date.today().isoformat())
    ap.add_argument("--days-back", type=int, default=3)
    args = ap.parse_args()
    resolve(args.date, days_back=args.days_back)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
