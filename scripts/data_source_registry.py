#!/usr/bin/env python3
"""Unified data-source registry + cache for the strategy tournament.

Gathers fixtures + implied probabilities from every working source, normalizes
them into one schema, tags each with its source, and caches the result so the
tournament (variant x source matrix) always has fresh, local data to run on.

Sources (auto-detected, gracefully skipped if unavailable):
  - betexplorer_basketball : basketball fixtures with market prob (CSV)
  - xbet_linefeed          : 1xBet niche-market fixtures with odds (CSV)
  - betexplorer_multi      : betexplorer per-sport landing fixtures (scrape)
  - espn                   : ESPN US-sport scoreboards (no key)
  - api_sports (scaffold)  : activates when API_SPORTS_KEY env is set
  - the_odds_api (scaffold): activates when ODDS_API_KEY env is set

Run daily; the cache grows the historical pool the tournament learns from.
"""
from __future__ import annotations

import csv
import json
import os
import re
import time
from datetime import date, datetime, timezone
from html import unescape
from pathlib import Path
from typing import Dict, List, Optional

PROJECT_DIR = Path(__file__).resolve().parent.parent
CACHE_DIR = PROJECT_DIR / "data" / "cache"
BBALL_CSV = PROJECT_DIR / "data" / "basketball_betexplorer_current.csv"
LINEFEED_CSV = PROJECT_DIR / "data" / "one_xbet_linefeed_snapshot.csv"

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"
BASE_URL = "https://www.betexplorer.com"

# betexplorer landing slugs that yield team/fixture markup.
BE_SPORTS = {
    "basketball": "basketball",
    "tennis": "tennis",
    "volleyball": "volleyball",
    "baseball": "baseball",
    "handball": "handball",
    "ice-hockey": "ice-hockey",
}


def _odds_to_prob(odds: float) -> float:
    return 1.0 / odds if odds and odds > 1 else 0.0


def _http(url: str, timeout: int = 20) -> Optional[str]:
    try:
        import requests
        r = requests.get(url, headers={"User-Agent": UA}, timeout=timeout)
        if r.status_code == 200:
            return r.text
    except Exception:
        return None
    return None


def source_betexplorer_basketball() -> List[dict]:
    """Basketball fixtures with market-implied probabilities."""
    if not BBALL_CSV.exists():
        return []
    out = []
    with BBALL_CSV.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            ph = _odds_to_prob(float(r["MARKET_PROB_home"])) if r.get("MARKET_PROB_home") else 0.0
            pa = _odds_to_prob(float(r["MARKET_PROB_away"])) if r.get("MARKET_PROB_away") else 0.0
            if not (0 < ph < 1 and 0 < pa < 1):
                continue
            out.append({
                "source": "betexplorer_basketball",
                "sport": "basketball",
                "league": r.get("league", ""),
                "date": (r.get("GAME_DATE_EST") or "")[:10],
                "home": r.get("HOME_TEAM_NAME", ""),
                "away": r.get("VISITOR_TEAM_NAME", ""),
                "home_prob": round(ph, 4),
                "away_prob": round(pa, 4),
            })
    return out


def source_betexplorer_bball_fixtures() -> List[dict]:
    """UPCOMING basketball fixtures from betexplorer per-league files, WITH REAL
    1x2 odds (OddH/OddA). This is the live feed for the summer leagues (NBL1,
    WNBA, BSN, CEBL, ...) that other sources lack odds for. Carries both
    home_odds/away_odds (for the bball_* strategies) and prob (for variants)."""
    fix_dir = PROJECT_DIR / "data" / "raw" / "betexplorer_basketball_fixtures"
    if not fix_dir.exists():
        return []
    out = []
    import glob as _glob
    for fn in _glob.glob(str(fix_dir / "*.csv")):
        league = fn.split("/")[-1].replace(".csv", "")
        try:
            with open(fn, encoding="utf-8") as f:
                for r in csv.DictReader(f):
                    try:
                        ho = float(r.get("OddH") or 0)
                        ao = float(r.get("OddA") or 0)
                    except Exception:
                        continue
                    if ho <= 1 or ao <= 1:
                        continue
                    ph, pa = _odds_to_prob(ho), _odds_to_prob(ao)
                    if not (0 < ph < 1 and 0 < pa < 1):
                        continue
                    out.append({
                        "source": "betexplorer_bball_fixtures",
                        "sport": "basketball",
                        "league": league.replace("_", " ").title(),
                        "date": (r.get("Date") or "")[:10],
                        "home": r.get("HomeTeam", ""),
                        "away": r.get("AwayTeam", ""),
                        "home_prob": round(ph, 4),
                        "away_prob": round(pa, 4),
                        "home_odds": round(ho, 3),
                        "away_odds": round(ao, 3),
                    })
        except Exception:
            continue
    return out


def _load_linefeed_movement() -> dict:
    """Read the linefeed history; return per-match OPENING odds (earliest snapshot) +
    snapshot count, keyed by (date, lower(home), lower(away)). Used to compute odds
    movement (steam moves) — a genuine independent sharp-money signal."""
    hist = PROJECT_DIR / "data" / "one_xbet_linefeed_history.csv"
    snaps: Dict[tuple, list] = {}
    if not hist.exists():
        return snaps
    with hist.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            try:
                ho, ao = float(r["HomeOdds"]), float(r["AwayOdds"])
            except Exception:
                continue
            k = ((r.get("Date") or "")[:10],
                 (r.get("Home") or "").strip().lower(),
                 (r.get("Away") or "").strip().lower())
            snaps.setdefault(k, []).append(((r.get("SnapshotAt") or ""), ho, ao))
    move = {}
    for k, lst in snaps.items():
        if len(lst) < 2:
            continue
        lst.sort()  # chronological by SnapshotAt (ISO string)
        move[k] = (lst[0][1], lst[0][2], len(lst))  # open_home, open_away, n_snapshots
    return move


def source_xbet_linefeed() -> List[dict]:
    """1xBet linefeed fixtures (niche markets) with raw odds -> probs.

    Additive enrichment (existing home_prob/away_prob keys unchanged, so no strategy
    breaks): home_odds/away_odds/draw_odds/draw_prob (real 1xBet 3-way odds) and
    home_move/away_move (odds movement vs opening snapshot; negative = shortened =
    steam move / sharp money), computed from one_xbet_linefeed_history.csv."""
    if not LINEFEED_CSV.exists():
        return []
    movement = _load_linefeed_movement()
    out = []
    with LINEFEED_CSV.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            ho, ao = r.get("HomeOdds"), r.get("AwayOdds")
            if not ho or not ao:
                continue
            try:
                ho_f, ao_f = float(ho), float(ao)
            except Exception:
                continue
            ph, pa = _odds_to_prob(ho_f), _odds_to_prob(ao_f)
            if not (0 < ph < 1 and 0 < pa < 1):
                continue
            rec = {
                "source": "xbet_linefeed",
                "sport": (r.get("Sport") or "").lower(),
                "league": r.get("League", ""),
                "date": (r.get("Date") or "")[:10],
                "start_utc": (r.get("StartUtc") or "").strip(),  # ISO ts → match status (live/upcoming/ended)
                "home": r.get("Home", ""),
                "away": r.get("Away", ""),
                "home_prob": round(ph, 4),
                "away_prob": round(pa, 4),
                "home_odds": round(ho_f, 3),
                "away_odds": round(ao_f, 3),
            }
            do = r.get("DrawOdds")
            if do:
                try:
                    rec["draw_odds"] = round(float(do), 3)
                    rec["draw_prob"] = round(_odds_to_prob(float(do)), 4)
                except Exception:
                    pass
            k = (rec["date"], rec["home"].strip().lower(), rec["away"].strip().lower())
            mv = movement.get(k)
            if mv:
                open_h, open_a, nsnap = mv
                if open_h > 1 and open_a > 1:
                    rec["home_move"] = round((ho_f - open_h) / open_h, 4)
                    rec["away_move"] = round((ao_f - open_a) / open_a, 4)
                    rec["odds_snaps"] = nsnap
            out.append(rec)
    return out


def source_betexplorer_multi() -> List[dict]:
    """betexplorer per-sport landing pages — fixtures with team names + odds
    where betexplorer renders them inline. Broad coverage across sports."""
    out = []
    for sport, slug in BE_SPORTS.items():
        html = _http(f"{BASE_URL}/{slug}/")
        if not html:
            continue
        # match rows: teamLine spans + any data-odd buttons in the same row
        for tr in re.finditer(r"<tr[^>]*>.*?</tr>", html, re.DOTALL):
            row = tr.group(0)
            home_m = re.search(r'table-main__teamLine--home[^>]*>(.*?)</span>', row, re.DOTALL)
            away_m = re.search(r'table-main__teamLine--away[^>]*>(.*?)</span>', row, re.DOTALL)
            if not home_m or not away_m:
                continue
            home = unescape(re.sub(r"<[^>]+>", "", home_m.group(1))).strip()
            away = unescape(re.sub(r"<[^>]+>", "", away_m.group(1))).strip()
            if not home or not away:
                continue
            odds = re.findall(r'data-odd="([0-9.]+)"', row)
            if len(odds) >= 2:
                ph, pa = _odds_to_prob(float(odds[0])), _odds_to_prob(float(odds[-1]))
            else:
                ph = pa = 0.0
            out.append({
                "source": "betexplorer_multi",
                "sport": sport,
                "league": "",
                "date": date.today().isoformat(),
                "home": home,
                "away": away,
                "home_prob": round(ph, 4),
                "away_prob": round(pa, 4),
            })
        time.sleep(0.4)
    return out


def source_espn() -> List[dict]:
    """ESPN scoreboards — US major sports, no key. Probs derived from money
    implied by the scoreboard where available (otherwise skipped)."""
    out = []
    for sport, league in [("basketball", "nba"), ("baseball", "mlb"),
                          ("hockey", "nhl"), ("football", "nfl")]:
        html = _http(f"https://site.api.espn.com/apis/site/v2/sports/{sport}/{league}/scoreboard")
        if not html:
            continue
        try:
            data = json.loads(html)
        except Exception:
            continue
        for ev in data.get("events", []):
            comps = ev.get("competitions", [{}])
            if not comps:
                continue
            comp = comps[0]
            competitors = comp.get("competitors", [])
            if len(competitors) < 2:
                continue
            home = competitors[0].get("team", {}).get("displayName", "")
            away = competitors[1].get("team", {}).get("displayName", "")
            # ESPN gives probabilities occasionally under 'probability'
            ph = pa = 0.0
            for c in competitors:
                prob = c.get("probability")
                if prob is None:
                    continue
                if c.get("homeAway") == "home":
                    ph = float(prob)
                else:
                    pa = float(prob)
            out.append({
                "source": "espn",
                "sport": sport,
                "league": league.upper(),
                "date": (ev.get("date") or "")[:10],
                "home": home, "away": away,
                "home_prob": round(ph, 4), "away_prob": round(pa, 4),
            })
        time.sleep(0.3)
    return out


def source_api_sports_scaffold() -> List[dict]:
    """api-sports.io — activates when API_SPORTS_KEY is set. Free 100/day."""
    key = os.environ.get("API_SPORTS_KEY")
    if not key:
        return []
    out = []
    for endpoint in ["https://v1.basketball.api-sports.io/games?date=" + date.today().isoformat()]:
        try:
            import requests
            r = requests.get(endpoint, headers={"x-apisports-key": key}, timeout=15)
            if r.status_code != 200:
                continue
            for g in r.json().get("response", []):
                teams = g.get("teams", {})
                home = teams.get("home", {}).get("name", "")
                away = teams.get("away", {}).get("name", "")
                out.append({
                    "source": "api_sports", "sport": "basketball",
                    "league": g.get("league", {}).get("name", ""),
                    "date": (g.get("date") or "")[:10],
                    "home": home, "away": away,
                    "home_prob": 0.0, "away_prob": 0.0,
                })
        except Exception:
            continue
    return out


def source_the_odds_api_scaffold() -> List[dict]:
    """the-odds-api.com — activates when ODDS_API_KEY is set. Free 500/month."""
    key = os.environ.get("ODDS_API_KEY")
    if not key:
        return []
    out = []
    for sport_key in ["basketball_nba", "baseball_mlb", "tennis_atp"]:
        try:
            import requests
            r = requests.get(
                f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds/",
                params={"apiKey": key, "regions": "eu", "markets": "h2h", "oddsFormat": "decimal"},
                timeout=15,
            )
            if r.status_code != 200:
                continue
            for ev in r.json():
                home = ev.get("home_team", "")
                away = ev.get("away_team", "")
                # average h2h odds across bookmakers
                ho, ao, n = 0.0, 0.0, 0
                for bk in ev.get("bookmakers", []):
                    for mkt in bk.get("markets", []):
                        if mkt.get("key") != "h2h":
                            continue
                        for o in mkt.get("outcomes", []):
                            if o["name"] == home:
                                ho += o["price"]; n += 1
                            elif o["name"] == away:
                                ao += o["price"]
                if n:
                    ho /= n; ao = ao / n if ao else 0.0
                out.append({
                    "source": "the_odds_api", "sport": sport_key.split("_")[0],
                    "league": sport_key, "date": (ev.get("commence_time") or "")[:10],
                    "home": home, "away": away,
                    "home_prob": round(_odds_to_prob(ho), 4),
                    "away_prob": round(_odds_to_prob(ao), 4),
                })
        except Exception:
            continue
    return out


SOURCES = {
    "betexplorer_basketball": source_betexplorer_basketball,
    "betexplorer_bball_fixtures": source_betexplorer_bball_fixtures,
    "xbet_linefeed": source_xbet_linefeed,
    "betexplorer_multi": source_betexplorer_multi,
    "espn": source_espn,
    "api_sports": source_api_sports_scaffold,
    "the_odds_api": source_the_odds_api_scaffold,
}


def gather_all(active_only: bool = True) -> dict:
    """Fetch from every source, return {source: [fixtures]} + cache to disk."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M")
    day = date.today().isoformat()
    result: Dict[str, List[dict]] = {}
    for name, fn in SOURCES.items():
        try:
            fixtures = fn()
        except Exception as exc:
            print(f"  ✗ {name}: {str(exc)[:80]}")
            fixtures = []
        result[name] = fixtures
        status = f"{len(fixtures)} fixtures" if fixtures else "empty/unavailable"
        print(f"  {name}: {status}")
        if active_only and not fixtures:
            continue
    cache_path = CACHE_DIR / f"sources_{day}.json"
    cache_path.write_text(json.dumps({"fetched_at": stamp, "sources": result}, indent=2), encoding="utf-8")
    print(f"\nCached → {cache_path}")
    # also maintain an append-only history for the tournament to learn from
    hist_path = CACHE_DIR / "sources_history.jsonl"
    with hist_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"fetched_at": stamp, "summary": {k: len(v) for k, v in result.items()}}) + "\n")
    return result


def load_latest_cache() -> dict:
    caches = sorted(CACHE_DIR.glob("sources_*.json"))
    if not caches:
        return {}
    return json.loads(caches[-1].read_text(encoding="utf-8"))


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Gather + cache fixtures from all data sources.")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()
    print(f"Gathering fixtures from {len(SOURCES)} sources ({date.today()})...")
    data = gather_all()
    total = sum(len(v) for v in data.values())
    print(f"\nTotal cached fixtures: {total} across {sum(1 for v in data.values() if v)} active sources.")
    print("\nTo unlock more sources, set env vars:")
    print("  API_SPORTS_KEY  — free signup at https://api-sports.io  (100 req/day)")
    print("  ODDS_API_KEY    — free signup at https://the-odds-api.com (500 req/month)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
