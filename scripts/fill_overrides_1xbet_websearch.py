#!/usr/bin/env python3
"""Fill 1XBet name overrides for football/basketball via 1xbet Web_SearchZip."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import time
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests
import sys

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from app.engine import NAME_OVERRIDES_1XBET, compute_range, compute_range_basketball

SEARCH_URL = "https://1xbet.com/service-api/LineFeed/Web_SearchZip"
LINEFEED_URL = "https://1xbet.com/service-api/LineFeed/Get1x2_VZip"
DEFAULT_CACHE = BASE_DIR / "data" / "tmp" / "1xbet_search_cache.json"

SPORT_IDS = {
    "football": 1,
    "basketball": 3,
}


def _strip_accents(text: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch))


def _normalize(text: str) -> str:
    if not text:
        return ""
    text = _strip_accents(text).lower()
    text = text.replace("&", " and ")
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _tokens(text: str) -> List[str]:
    drop = {"fc", "sc", "cf", "bc", "ac", "afc", "bk", "fk", "kl", "jk", "basketball", "club", "the"}
    return [t for t in _normalize(text).split() if t and t not in drop]


def _team_variants(name: str) -> List[str]:
    if not name:
        return []
    variants = []
    base = name.strip()
    variants.append(base)
    # remove parentheses content
    cleaned = re.sub(r"\([^)]*\)", "", base).strip()
    if cleaned and cleaned not in variants:
        variants.append(cleaned)
    # remove common short tokens
    tokens = [t for t in re.split(r"\s+", cleaned) if t]
    drop = {"fc", "sc", "cf", "bc", "ac", "afc", "bk", "kk", "bc", "bc.", "club"}
    filtered = [t for t in tokens if _normalize(t) not in drop and len(_normalize(t)) > 1]
    if filtered:
        short = " ".join(filtered)
        if short not in variants:
            variants.append(short)
        if len(filtered) >= 2:
            tail = " ".join(filtered[-2:])
            if tail not in variants:
                variants.append(tail)
    return variants


def _score_side(target: str, candidate: str) -> int:
    if not target or not candidate:
        return 0
    norm_t = _normalize(target)
    norm_c = _normalize(candidate)
    if norm_t == norm_c:
        return 3
    tok_t = set(_tokens(target))
    tok_c = set(_tokens(candidate))
    if tok_t and tok_t.issubset(tok_c):
        return 2
    if tok_t and tok_c and (len(tok_t & tok_c) / max(1, len(tok_t)) >= 0.6):
        return 1
    return 0


def _parse_date(value: Any) -> Optional[date]:
    if value is None or value == "":
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    text = str(value)
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).date()
        except Exception:
            continue
    try:
        return datetime.fromisoformat(text).date()
    except Exception:
        return None


def _event_date(ts: Any) -> Optional[date]:
    if ts is None:
        return None
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).date()
    except Exception:
        return None


def _compact_event(ev: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "O1": ev.get("O1"),
        "O2": ev.get("O2"),
        "O1E": ev.get("O1E"),
        "O2E": ev.get("O2E"),
        "S": ev.get("S"),
        "SI": ev.get("SI"),
        "SN": ev.get("SN"),
        "L": ev.get("L"),
        "LE": ev.get("LE"),
        "LI": ev.get("LI"),
        "I": ev.get("I"),
    }


@dataclass
class MatchResult:
    home: str
    away: str
    home_1x: Optional[str]
    away_1x: Optional[str]
    score: int
    swapped: bool
    event_id: Optional[int]
    event_date: Optional[str]
    league: Optional[str]


@dataclass
class TeamResult:
    team: str
    team_1x: Optional[str]
    score: int
    source_event: Optional[int]


def _best_match(
    home: str,
    away: str,
    target_date: Optional[date],
    events: Iterable[Dict[str, Any]],
    sport_id: int,
    max_date_delta: int,
) -> Optional[MatchResult]:
    best: Optional[MatchResult] = None
    best_score = -1
    best_date_delta = 999
    for ev in events:
        if not isinstance(ev, dict):
            continue
        if ev.get("SI") is not None and int(ev.get("SI")) != sport_id:
            continue
        ev_date = _event_date(ev.get("S"))
        if target_date and ev_date:
            delta = abs((ev_date - target_date).days)
            if delta > max_date_delta:
                continue
        else:
            delta = 0
        ev_home = ev.get("O1E") or ev.get("O1") or ""
        ev_away = ev.get("O2E") or ev.get("O2") or ""

        for swapped in (False, True):
            h = ev_home if not swapped else ev_away
            a = ev_away if not swapped else ev_home
            score = _score_side(home, h) + _score_side(away, a)
            if score > best_score or (score == best_score and delta < best_date_delta):
                best_score = score
                best_date_delta = delta
                best = MatchResult(
                    home=home,
                    away=away,
                    home_1x=h or None,
                    away_1x=a or None,
                    score=score,
                    swapped=swapped,
                    event_id=ev.get("I"),
                    event_date=ev_date.isoformat() if ev_date else None,
                    league=ev.get("LE") or ev.get("L"),
                )
    return best


def _best_team_name(
    team: str,
    events: Iterable[Dict[str, Any]],
    sport_id: int,
) -> Optional[TeamResult]:
    best_score = -1
    best_name: Optional[str] = None
    best_event: Optional[int] = None
    for ev in events:
        if not isinstance(ev, dict):
            continue
        if ev.get("SI") is not None and int(ev.get("SI")) != sport_id:
            continue
        for key in ("O1E", "O1", "O2E", "O2"):
            candidate = ev.get(key) or ""
            score = _score_side(team, candidate)
            if score > best_score:
                best_score = score
                best_name = candidate or None
                best_event = ev.get("I")
    if best_name is None:
        return None
    return TeamResult(team=team, team_1x=best_name, score=best_score, source_event=best_event)


def _load_cache(path: Path) -> Dict[str, Any]:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_cache(path: Path, cache: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def _search(
    session: requests.Session,
    text: str,
    *,
    limit: int,
    timeout_s: float,
    retries: int,
    sleep_s: float,
    cache: Dict[str, Any],
) -> List[Dict[str, Any]]:
    key = text.strip().lower()
    if key in cache:
        return cache[key]
    params = {"text": text, "limit": limit, "lng": "en"}
    last_exc: Optional[Exception] = None
    for attempt in range(retries + 1):
        try:
            resp = session.get(SEARCH_URL, params=params, timeout=timeout_s)
            resp.raise_for_status()
            payload = resp.json()
            items = payload.get("Value") or []
            compact = [_compact_event(ev) for ev in items if isinstance(ev, dict)]
            cache[key] = compact
            break
        except Exception as exc:
            last_exc = exc
            if attempt < retries:
                time.sleep(min(0.5 * (attempt + 1), 2.0))
                continue
            cache[key] = []
    if last_exc and not cache.get(key):
        # fallback to curl
        try:
            from urllib.parse import urlencode

            url = f"{SEARCH_URL}?{urlencode(params)}"
            cmd = ["curl", "-s", "-L", "-A", "Mozilla/5.0", "--max-time", str(int(timeout_s)), url]
            res = subprocess.run(cmd, check=False, capture_output=True, text=True)
            if res.stdout:
                payload = json.loads(res.stdout)
                items = payload.get("Value") or []
                compact = [_compact_event(ev) for ev in items if isinstance(ev, dict)]
                cache[key] = compact
        except Exception as exc:
            last_exc = exc
    if last_exc and not cache.get(key):
        print(f"warn: 1xbet search failed for '{text}': {last_exc}")
    if sleep_s:
        time.sleep(sleep_s)
    return cache.get(key, [])


def _fetch_linefeed(
    session: requests.Session,
    *,
    sport_id: int,
    count: int,
    mode: int,
    timeout_s: float,
    retries: int,
) -> List[Dict[str, Any]]:
    params = {"sports": sport_id, "count": count, "lng": "en", "mode": mode}
    last_exc: Optional[Exception] = None
    for attempt in range(retries + 1):
        try:
            resp = session.get(LINEFEED_URL, params=params, timeout=timeout_s)
            resp.raise_for_status()
            payload = resp.json()
            items = payload.get("Value") or []
            return [_compact_event(ev) for ev in items if isinstance(ev, dict)]
        except Exception as exc:
            last_exc = exc
            if attempt < retries:
                time.sleep(min(0.5 * (attempt + 1), 2.0))
                continue
    # fallback to curl if requests keeps timing out
    try:
        from urllib.parse import urlencode

        url = f"{LINEFEED_URL}?{urlencode(params)}"
        cmd = ["curl", "-s", "-L", "-A", "Mozilla/5.0", "--max-time", str(int(timeout_s)), url]
        res = subprocess.run(cmd, check=False, capture_output=True, text=True)
        if res.stdout:
            payload = json.loads(res.stdout)
            items = payload.get("Value") or []
            return [_compact_event(ev) for ev in items if isinstance(ev, dict)]
    except Exception as exc:
        last_exc = exc
    if last_exc:
        print(f"warn: linefeed fetch failed for sport {sport_id}: {last_exc}")
    return []


def _iter_matches(picks) -> Iterable[Dict[str, Any]]:
    if picks is None:
        return
    for row in picks.to_dict("records"):
        home = str(row.get("Home") or "").strip()
        away = str(row.get("Away") or "").strip()
        if not home or not away:
            continue
        yield {
            "date": row.get("Date"),
            "league": row.get("League"),
            "home": home,
            "away": away,
        }


def _iter_teams(picks) -> Iterable[str]:
    if picks is None:
        return
    seen = set()
    for row in picks.to_dict("records"):
        for key in ("Home", "Away"):
            name = str(row.get(key) or "").strip()
            if not name or name in seen:
                continue
            seen.add(name)
            yield name


def _league_team_map(picks) -> Dict[str, List[str]]:
    leagues: Dict[str, set[str]] = {}
    if picks is None:
        return {}
    for row in picks.to_dict("records"):
        league = str(row.get("League") or "").strip()
        if not league:
            continue
        for key in ("Home", "Away"):
            name = str(row.get(key) or "").strip()
            if not name:
                continue
            leagues.setdefault(league, set()).add(name)
    return {k: sorted(v) for k, v in leagues.items()}


def main() -> int:
    parser = argparse.ArgumentParser(description="Fill 1XBet name overrides using 1xbet Web_SearchZip.")
    parser.add_argument("--start", help="Start date YYYY-MM-DD")
    parser.add_argument("--end", help="End date YYYY-MM-DD")
    parser.add_argument("--sports", default="football,basketball")
    parser.add_argument("--season", default="2526", help="Football season code")
    parser.add_argument("--limit", type=int, default=30, help="Search results limit")
    parser.add_argument("--timeout", type=float, default=25.0, help="HTTP timeout in seconds")
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--sleep", type=float, default=0.2, help="Sleep between requests")
    parser.add_argument("--max-date-delta", type=int, default=1)
    parser.add_argument("--min-score", type=int, default=4)
    parser.add_argument("--min-score-team", type=int, default=2)
    parser.add_argument("--mode", choices=("team", "match"), default="team")
    parser.add_argument("--use-linefeed", action="store_true", help="Try LineFeed Get1x2_VZip as fallback")
    parser.add_argument("--linefeed-count", type=int, default=200)
    parser.add_argument("--linefeed-mode", type=int, default=4)
    parser.add_argument("--linefeed-timeout", type=float, default=10.0)
    parser.add_argument("--linefeed-retries", type=int, default=2)
    parser.add_argument("--cache", default=str(DEFAULT_CACHE))
    parser.add_argument("--force", action="store_true", help="Overwrite existing overrides")
    args = parser.parse_args()

    end = date.fromisoformat(args.end) if args.end else date.today()
    start = date.fromisoformat(args.start) if args.start else end - timedelta(days=90)
    if start > end:
        start, end = end, start

    sports = [s.strip().lower() for s in args.sports.split(",") if s.strip()]
    cache_path = Path(args.cache)
    cache = _load_cache(cache_path)

    overrides: Dict[str, Dict[str, str]] = {}
    if NAME_OVERRIDES_1XBET.exists():
        overrides = json.loads(NAME_OVERRIDES_1XBET.read_text(encoding="utf-8"))
    for sport in sports:
        overrides.setdefault(sport, {})

    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0"})

    report_rows: List[Dict[str, Any]] = []

    for sport in sports:
        sport_id = SPORT_IDS.get(sport)
        if sport_id is None:
            continue
        if sport == "football":
            picks = compute_range(start, end, auto_update_future=False, season_code=args.season).picks
        elif sport == "basketball":
            picks = compute_range_basketball(start, end).picks
        else:
            picks = None
        if args.mode == "team":
            unmatched = []
            for team in _iter_teams(picks):
                if not team:
                    continue
                if not args.force and team in overrides[sport]:
                    continue
                result = None
                for query in _team_variants(team):
                    events = _search(
                        session,
                        query,
                        limit=args.limit,
                        timeout_s=args.timeout,
                        retries=args.retries,
                        sleep_s=args.sleep,
                        cache=cache,
                    )
                    result = _best_team_name(team, events, sport_id)
                    if result and result.score >= args.min_score_team:
                        break
                if not result or result.score < args.min_score_team:
                    report_rows.append(
                        {
                            "sport": sport,
                            "mode": "team",
                            "team": team,
                            "team_1x": None,
                            "match_score": result.score if result else 0,
                            "matched": False,
                        }
                    )
                    unmatched.append(team)
                    continue
                overrides[sport][team] = result.team_1x or team
                report_rows.append(
                    {
                        "sport": sport,
                        "mode": "team",
                        "team": team,
                        "team_1x": result.team_1x,
                        "match_score": result.score,
                        "event_id": result.source_event,
                        "matched": True,
                    }
                )
            # league-based search to reduce per-team timeouts
            league_map = _league_team_map(picks)
            for league, teams in league_map.items():
                remaining = [t for t in teams if args.force or t not in overrides[sport]]
                if not remaining:
                    continue
                queries = [league]
                if sport == "basketball":
                    queries = [f"{league} basketball", league]
                for query in queries:
                    events = _search(
                        session,
                        query,
                        limit=max(args.limit, 30),
                        timeout_s=args.timeout,
                        retries=args.retries,
                        sleep_s=args.sleep,
                        cache=cache,
                    )
                    if not events:
                        continue
                    for team in list(remaining):
                        if not args.force and team in overrides[sport]:
                            continue
                        result = _best_team_name(team, events, sport_id)
                        if result and result.score >= args.min_score_team:
                            overrides[sport][team] = result.team_1x or team
                            report_rows.append(
                                {
                                    "sport": sport,
                                    "mode": "league_search",
                                    "league": league,
                                    "team": team,
                                    "team_1x": result.team_1x,
                                    "match_score": result.score,
                                    "event_id": result.source_event,
                                    "matched": True,
                                }
                            )
                            remaining.remove(team)
                    if not remaining:
                        break

            if args.use_linefeed and unmatched:
                events = _fetch_linefeed(
                    session,
                    sport_id=sport_id,
                    count=args.linefeed_count,
                    mode=args.linefeed_mode,
                    timeout_s=args.linefeed_timeout,
                    retries=args.linefeed_retries,
                )
                if events:
                    for team in list(unmatched):
                        if not args.force and team in overrides[sport]:
                            continue
                        result = _best_team_name(team, events, sport_id)
                        if result and result.score >= args.min_score_team:
                            overrides[sport][team] = result.team_1x or team
                            report_rows.append(
                                {
                                    "sport": sport,
                                    "mode": "linefeed_team",
                                    "team": team,
                                    "team_1x": result.team_1x,
                                    "match_score": result.score,
                                    "event_id": result.source_event,
                                    "matched": True,
                                }
                            )
            continue

        for match in _iter_matches(picks):
            target_date = _parse_date(match["date"])
            home = match["home"]
            away = match["away"]

            need_home = args.force or home not in overrides[sport]
            need_away = args.force or away not in overrides[sport]
            if not (need_home or need_away):
                continue

            queries = [f"{home} {away}", home, away]
            best: Optional[MatchResult] = None
            for query in queries:
                events = _search(
                    session,
                    query,
                    limit=args.limit,
                    timeout_s=args.timeout,
                    retries=args.retries,
                    sleep_s=args.sleep,
                    cache=cache,
                )
                candidate = _best_match(
                    home,
                    away,
                    target_date,
                    events,
                    sport_id,
                    max_date_delta=args.max_date_delta,
                )
                if candidate and (best is None or candidate.score > best.score):
                    best = candidate
                if best and best.score >= args.min_score:
                    break

            if not best or best.score < args.min_score:
                report_rows.append(
                    {
                        "sport": sport,
                        "mode": "match",
                        "date": target_date.isoformat() if target_date else None,
                        "league": match.get("league"),
                        "home": home,
                        "away": away,
                        "match_score": best.score if best else 0,
                        "matched": False,
                    }
                )
                continue

            home_1x = best.home_1x
            away_1x = best.away_1x
            if home_1x and (args.force or home not in overrides[sport]):
                overrides[sport][home] = home_1x
            if away_1x and (args.force or away not in overrides[sport]):
                overrides[sport][away] = away_1x

            report_rows.append(
                {
                    "sport": sport,
                    "mode": "match",
                    "date": target_date.isoformat() if target_date else None,
                    "league": match.get("league"),
                    "home": home,
                    "away": away,
                    "home_1x": home_1x,
                    "away_1x": away_1x,
                    "match_score": best.score,
                    "swapped": best.swapped,
                    "event_id": best.event_id,
                    "event_date": best.event_date,
                    "event_league": best.league,
                    "matched": True,
                }
            )

    NAME_OVERRIDES_1XBET.parent.mkdir(parents=True, exist_ok=True)
    NAME_OVERRIDES_1XBET.write_text(json.dumps(overrides, ensure_ascii=False, indent=2), encoding="utf-8")
    _save_cache(cache_path, cache)

    report_path = BASE_DIR / "reports" / f"name_overrides_1xbet_websearch_{start}_{end}.csv"
    if report_rows:
        try:
            import pandas as pd

            pd.DataFrame(report_rows).to_csv(report_path, index=False)
        except Exception:
            report_path.write_text(json.dumps(report_rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"updated {NAME_OVERRIDES_1XBET}")
    print(f"report {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
