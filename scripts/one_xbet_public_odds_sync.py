#!/usr/bin/env python3
"""Sync confirmed public 1xBet match-winner odds into manual_1xbet_odds.csv.

This is deliberately browser-free. It uses public LineFeed service endpoints and
only writes odds when the event and the pick side can be matched confidently.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import subprocess
import sys
import time
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlencode


BASE_DIR = Path(__file__).resolve().parents[1]
REPORTS_DIR = BASE_DIR / "reports"
DEFAULT_MANUAL_ODDS = BASE_DIR / "data" / "manual_1xbet_odds.csv"

DEFAULT_BASE_URLS = [
    "https://q1ayxwi7tuwrn.bar",
    "https://1xbet.com",
]
DEFAULT_ODDS_HISTORY = BASE_DIR / "data" / "one_xbet_odds_history.csv"
DEFAULT_LINEFEED_SNAPSHOT = BASE_DIR / "data" / "one_xbet_linefeed_snapshot.csv"

try:
    from sports_strategy_profiles import sport_ids
    SPORT_IDS = sport_ids()
except Exception:  # pragma: no cover
    SPORT_IDS = {
        "football": 1,
        "hockey": 2,
        "icehockey": 2,
        "ice_hockey": 2,
        "ice hockey": 2,
        "basketball": 3,
        "tennis": 4,
        "handball": 8,
        "tabletennis": 10,
        "table_tennis": 10,
        "table tennis": 10,
        "volleyball": 6,
        "baseball": 5,
        "cricket": 66,
        "americanfootball": 13,
        "american football": 13,
        "futsal": 14,
        "darts": 21,
        "snooker": 30,
    }

MARKET_MAP = {
    1: {"group": 1, "home_t": 1, "away_t": 3, "name": "1X2"},
    2: {"group": 1, "home_t": 1, "away_t": 3, "name": "1X2"},
    3: {"group": 101, "home_t": 401, "away_t": 402, "name": "Match Winner"},
    4: {"group": 1, "home_t": 1, "away_t": 3, "name": "Match Winner"},
    5: {"group": 1, "home_t": 1, "away_t": 3, "name": "Match Winner"},
    6: {"group": 1, "home_t": 1, "away_t": 3, "name": "Match Winner"},
    8: {"group": 1, "home_t": 1, "away_t": 3, "name": "1X2"},
    10: {"group": 1, "home_t": 1, "away_t": 3, "name": "Match Winner"},
    13: {"group": 1, "home_t": 1, "away_t": 3, "name": "Match Winner"},
    14: {"group": 1, "home_t": 1, "away_t": 3, "name": "Match Winner"},
    21: {"group": 1, "home_t": 1, "away_t": 3, "name": "Match Winner"},
    30: {"group": 1, "home_t": 1, "away_t": 3, "name": "Match Winner"},
    66: {"group": 1, "home_t": 1, "away_t": 3, "name": "Match Winner"},
}

SYNC_ACTIONS = {
    "ENTER_NOW_AFTER_SOURCE_CHECK",
    "PRICE_TARGET_NEAR",
    "PRICE_TARGET_WAIT",
}


def _strip_accents(text: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch))


def _normalize(text: Any) -> str:
    raw = "" if text is None else str(text)
    raw = _strip_accents(raw).lower()
    raw = raw.replace("&", " and ")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\s]", " ", raw)).strip()


def _norm_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", _normalize(value))


def _tokens(text: Any) -> List[str]:
    drop = {
        "fc",
        "sc",
        "cf",
        "bc",
        "ac",
        "afc",
        "bk",
        "fk",
        "kk",
        "club",
        "basketball",
        "united",
        "town",
        "city",
        "the",
    }
    return [tok for tok in _normalize(text).split() if tok and tok not in drop]


def _score_side(target: Any, candidate: Any) -> int:
    target_norm = _normalize(target)
    candidate_norm = _normalize(candidate)
    if not target_norm or not candidate_norm:
        return 0
    if target_norm == candidate_norm:
        return 4
    target_tokens = set(_tokens(target))
    candidate_tokens = set(_tokens(candidate))
    if target_tokens and target_tokens.issubset(candidate_tokens):
        return 3
    if target_tokens and candidate_tokens:
        overlap = len(target_tokens & candidate_tokens) / max(1, len(target_tokens))
        if overlap >= 0.67:
            return 2
        if overlap >= 0.5:
            return 1
    if target_norm in candidate_norm or candidate_norm in target_norm:
        return 1
    return 0


def _target_date(value: str) -> date:
    value = (value or "today").strip().lower()
    today = date.today()
    if value == "today":
        return today
    if value == "tomorrow":
        return today + timedelta(days=1)
    return date.fromisoformat(value)


def _event_date(ts: Any) -> Optional[date]:
    if ts in (None, ""):
        return None
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).date()
    except Exception:
        return None


def _manual_key(row: Dict[str, Any]) -> Tuple[str, str, str, str, str]:
    return (
        str(row.get("Date") or "").strip()[:10],
        _norm_key(row.get("Sport")),
        _norm_key(row.get("Home")),
        _norm_key(row.get("Away")),
        _norm_key(row.get("Pick")),
    )


def _base_urls() -> List[str]:
    configured = os.environ.get("ONE_XBET_PUBLIC_BASE_URLS") or os.environ.get("ONE_XBET_PUBLIC_BASE_URL") or ""
    urls = [item.strip().rstrip("/") for item in configured.split(",") if item.strip()]
    for url in DEFAULT_BASE_URLS:
        if url not in urls:
            urls.append(url)
    return urls


def _curl_json(url: str, params: Dict[str, Any], timeout_s: int) -> Dict[str, Any]:
    full_url = f"{url}?{urlencode(params)}"
    cmd = [
        "curl",
        "-sS",
        "-L",
        "-A",
        "Mozilla/5.0",
        "--connect-timeout",
        str(max(2, min(timeout_s, 10))),
        "--max-time",
        str(timeout_s),
        full_url,
    ]
    proc = subprocess.run(cmd, check=False, capture_output=True)
    if proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", errors="replace").strip()
        stdout = proc.stdout.decode("utf-8", errors="replace").strip()
        raise RuntimeError((stderr or stdout or f"curl rc={proc.returncode}").strip())
    raw = proc.stdout
    text = raw.decode("utf-8-sig", errors="replace").strip()
    return json.loads(text)


def _service_get(path: str, params: Dict[str, Any], timeout_s: int) -> Tuple[Dict[str, Any], str]:
    last_error: Optional[Exception] = None
    for base in _base_urls():
        url = f"{base}{path}"
        try:
            payload = _curl_json(url, params, timeout_s)
            if payload.get("Success") is True:
                return payload, base
            last_error = RuntimeError(str(payload.get("Error") or payload.get("ErrorCode") or "api error"))
        except Exception as exc:
            last_error = exc
    raise RuntimeError(str(last_error or "all 1xBet public API bases failed"))


@dataclass
class MatchedEvent:
    event_id: Any
    canonical_id: Any
    home: str
    away: str
    league: str
    event_date: Optional[date]
    score: int
    base_url: str


def _search_events(query: str, timeout_s: int) -> Tuple[List[Dict[str, Any]], str]:
    payload, base_url = _service_get(
        "/service-api/LineFeed/Web_SearchZip",
        {"text": query, "limit": 25, "lng": "en"},
        timeout_s,
    )
    events = payload.get("Value") or []
    return [ev for ev in events if isinstance(ev, dict)], base_url


def _best_event(
    row: Dict[str, Any],
    target: date,
    timeout_s: int,
    min_score: int,
    *,
    broad_queries: bool,
) -> Optional[MatchedEvent]:
    sport_id = SPORT_IDS.get(_normalize(row.get("Sport")))
    if sport_id is None:
        return None

    home = str(row.get("Home") or "")
    away = str(row.get("Away") or "")
    queries = [f"{home} {away}", f"{away} {home}"]
    if broad_queries:
        queries.extend([home, away])
    best: Optional[MatchedEvent] = None
    best_date_delta = 999
    seen: set[str] = set()
    for query in queries:
        key = _normalize(query)
        if not key or key in seen:
            continue
        seen.add(key)
        try:
            events, base_url = _search_events(query, timeout_s)
        except Exception:
            continue
        for ev in events:
            try:
                if int(ev.get("SI")) != sport_id:
                    continue
            except Exception:
                continue
            ev_date = _event_date(ev.get("S"))
            date_delta = abs((ev_date - target).days) if ev_date else 0
            if ev_date and date_delta > 1:
                continue
            ev_home = str(ev.get("O1E") or ev.get("O1") or "")
            ev_away = str(ev.get("O2E") or ev.get("O2") or "")
            for swapped in (False, True):
                cand_home = ev_away if swapped else ev_home
                cand_away = ev_home if swapped else ev_away
                score = _score_side(home, cand_home) + _score_side(away, cand_away)
                if score < min_score:
                    continue
                if best is None or score > best.score or (score == best.score and date_delta < best_date_delta):
                    best = MatchedEvent(
                        event_id=ev.get("I"),
                        canonical_id=ev.get("CI"),
                        home=ev_home,
                        away=ev_away,
                        league=str(ev.get("LE") or ev.get("L") or ""),
                        event_date=ev_date,
                        score=score,
                        base_url=base_url,
                    )
                    best_date_delta = date_delta
    return best


def _event_from_row(row: Dict[str, Any], target: date) -> Optional[MatchedEvent]:
    event_id = row.get("OneXBetEventId") or row.get("OneXBetManualEventId")
    canonical_id = row.get("OneXBetCanonicalId") or row.get("OneXBetManualCanonicalId")
    if event_id in (None, "") and canonical_id in (None, ""):
        return None

    raw_date = row.get("OneXBetDate") or row.get("OneXBetManualEventDate") or row.get("Date")
    event_date = target
    try:
        parsed = datetime.fromisoformat(str(raw_date).strip()[:10])
        event_date = parsed.date()
    except Exception:
        event_date = target

    return MatchedEvent(
        event_id=event_id,
        canonical_id=canonical_id,
        home=str(row.get("Home") or ""),
        away=str(row.get("Away") or ""),
        league=str(row.get("OneXBetLeague") or row.get("OneXBetManualLeague") or row.get("League") or ""),
        event_date=event_date,
        score=9,
        base_url=str(row.get("OneXBetPublicBase") or "row_event_id"),
    )


def _linefeed_confirmation_from_row(
    row: Dict[str, Any],
    target: date,
    checked_at: str,
    *,
    reason: str,
) -> Optional[Dict[str, Any]]:
    event_id = row.get("OneXBetEventId") or row.get("OneXBetManualEventId")
    canonical_id = row.get("OneXBetCanonicalId") or row.get("OneXBetManualCanonicalId")
    odds = _as_float(row.get("OneXBetManualOdds") or row.get("PickOdds"))
    source = str(row.get("OneXBetManualSource") or row.get("Source") or "")
    if odds is None or odds <= 1.0:
        return None
    if not event_id and not canonical_id:
        return None
    if "LINEFEED" not in source.upper() and "1xbet_public_linefeed" not in source:
        return None

    league = row.get("OneXBetLeague") or row.get("OneXBetManualLeague") or row.get("League") or ""
    event_date = str(row.get("OneXBetDate") or row.get("OneXBetManualEventDate") or row.get("Date") or target.isoformat())[:10]
    start_utc = row.get("OneXBetStartUtc") or row.get("OneXBetManualStartUtc") or ""
    side = row.get("Side") or ""
    note = (
        f"event_id={event_id or ''}; canonical_id={canonical_id or ''}; "
        f"league={league}; event_date={event_date}; start_utc={start_utc}; "
        f"match_score={row.get('OneXBetManualMatchScore') or row.get('OneXBetMatchScore') or 8}; "
        f"side={side}; base={row.get('OneXBetPublicBase') or ''}; "
        f"api=Get1x2_VZip; fallback_reason={str(reason).replace(';', ',')[:120]}"
    )
    return {
        "Date": str(row.get("Date") or target.isoformat())[:10],
        "Sport": row.get("Sport") or "",
        "Home": row.get("Home") or "",
        "Away": row.get("Away") or "",
        "Pick": row.get("Pick") or "",
        "OneXBetOdds": odds,
        "Market": "LineFeed Main Market",
        "CheckedAt": row.get("OneXBetManualCheckedAt") or checked_at,
        "Source": "1XBET_PUBLIC_LINEFEED",
        "Note": note,
        "SnapshotAt": checked_at,
        "EventId": event_id,
        "CanonicalId": canonical_id,
        "League": league,
        "EventDate": event_date,
        "StartUtc": start_utc,
        "Side": side,
        "TargetOdds": row.get("MinEntryOdds"),
        "OldOdds": row.get("PickOdds"),
    }


def _snapshot_pick_odds(row: Dict[str, Any], snap: Dict[str, Any]) -> Tuple[Optional[float], str]:
    pick = row.get("Pick")
    pick_norm = _normalize(pick)
    if pick_norm in {"draw", "x", "tie"}:
        return _as_float(snap.get("DrawOdds")), "draw"
    home_score = _score_side(pick, snap.get("Home"))
    away_score = _score_side(pick, snap.get("Away"))
    if home_score <= 0 and away_score <= 0:
        return None, "pick side not matched in snapshot"
    if home_score >= away_score:
        return _as_float(snap.get("HomeOdds")), "home"
    return _as_float(snap.get("AwayOdds")), "away"


def _snapshot_confirmation(
    row: Dict[str, Any],
    snapshot_rows: List[Dict[str, Any]],
    target: date,
    checked_at: str,
    *,
    min_score: int,
    reason: str,
) -> Optional[Dict[str, Any]]:
    sport_id = SPORT_IDS.get(_normalize(row.get("Sport")))
    if sport_id is None:
        return None

    home = str(row.get("Home") or "")
    away = str(row.get("Away") or "")
    best: Optional[Tuple[int, Dict[str, Any], float, str]] = None
    for snap in snapshot_rows:
        if str(snap.get("Date") or "")[:10] != target.isoformat():
            continue
        try:
            if int(float(snap.get("SportId") or 0)) != sport_id:
                continue
        except Exception:
            if _normalize(snap.get("Sport")) != _normalize(row.get("Sport")):
                continue
        snap_home = str(snap.get("Home") or "")
        snap_away = str(snap.get("Away") or "")
        normal_score = _score_side(home, snap_home) + _score_side(away, snap_away)
        swapped_score = _score_side(home, snap_away) + _score_side(away, snap_home)
        score = max(normal_score, swapped_score)
        if score < min_score:
            continue
        odds, side = _snapshot_pick_odds(row, snap)
        if odds is None or odds <= 1.0:
            continue
        if best is None or score > best[0]:
            best = (score, snap, odds, side)

    if best is None:
        return None

    score, snap, odds, side = best
    note = (
        f"event_id={snap.get('EventId') or ''}; canonical_id={snap.get('CanonicalId') or ''}; "
        f"league={snap.get('League') or ''}; event_date={snap.get('Date') or target.isoformat()}; "
        f"start_utc={snap.get('StartUtc') or ''}; match_score={score}; side={side}; "
        f"base={snap.get('PublicBase') or ''}; api=Get1x2_VZip_snapshot; "
        f"fallback_reason={str(reason).replace(';', ',')[:120]}"
    )
    return {
        "Date": str(row.get("Date") or target.isoformat())[:10],
        "Sport": row.get("Sport") or "",
        "Home": row.get("Home") or "",
        "Away": row.get("Away") or "",
        "Pick": row.get("Pick") or "",
        "OneXBetOdds": odds,
        "Market": "LineFeed Snapshot Main Market",
        "CheckedAt": snap.get("SnapshotAt") or checked_at,
        "Source": "1XBET_LINEFEED_SNAPSHOT",
        "Note": note,
        "SnapshotAt": checked_at,
        "EventId": snap.get("EventId") or "",
        "CanonicalId": snap.get("CanonicalId") or "",
        "League": snap.get("League") or "",
        "EventDate": snap.get("Date") or target.isoformat(),
        "StartUtc": snap.get("StartUtc") or "",
        "Side": side,
        "TargetOdds": row.get("MinEntryOdds"),
        "OldOdds": row.get("PickOdds"),
    }


def _flatten_event_lists(value: Any) -> Iterable[Dict[str, Any]]:
    if isinstance(value, dict):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from _flatten_event_lists(item)


def _fetch_game(event: MatchedEvent, timeout_s: int) -> Dict[str, Any]:
    ids = [event.event_id, event.canonical_id]
    last_error: Optional[Exception] = None
    for event_id in ids:
        if event_id in (None, ""):
            continue
        try:
            payload, _ = _service_get(
                "/service-api/LineFeed/GetGameZip",
                {
                    "id": event_id,
                    "lng": "en",
                    "isSubGames": "true",
                    "GroupEvents": "true",
                    "countevents": 250,
                    "grMode": 4,
                    "marketType": 1,
                },
                timeout_s,
            )
            value = payload.get("Value") or {}
            if isinstance(value, dict) and (value.get("GE") or value.get("SC") or value.get("SS") or value.get("O1") or value.get("O2")):
                return value
        except Exception as exc:
            last_error = exc
    raise RuntimeError(str(last_error or "GetGameZip returned no grouped markets"))


def _main_market_odds(row: Dict[str, Any], game: Dict[str, Any]) -> Tuple[Optional[float], str, str]:
    sport_id = SPORT_IDS.get(_normalize(row.get("Sport")))
    if sport_id is None:
        return None, "", "unsupported sport"
    market = MARKET_MAP.get(sport_id)
    if not market:
        return None, "", "unsupported market"

    pick = row.get("Pick")
    home = game.get("O1E") or game.get("O1") or ""
    away = game.get("O2E") or game.get("O2") or ""
    home_score = _score_side(pick, home)
    away_score = _score_side(pick, away)
    if home_score <= 0 and away_score <= 0:
        return None, "", f"pick side not matched: {pick}"
    wanted_t = market["home_t"] if home_score >= away_score else market["away_t"]
    side = "home" if wanted_t == market["home_t"] else "away"

    for ge in game.get("GE") or []:
        if not isinstance(ge, dict) or ge.get("G") != market["group"]:
            continue
        for odd in _flatten_event_lists(ge.get("E")):
            if odd.get("T") == wanted_t and odd.get("C") is not None:
                try:
                    return float(odd["C"]), str(market["name"]), side
                except Exception:
                    return None, str(market["name"]), "bad odds value"
    return None, str(market["name"]), f"market not found for T={wanted_t}"


def _read_csv(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _as_float(value: Any) -> Optional[float]:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except Exception:
        return None


def _write_manual(path: Path, rows: List[Dict[str, Any]]) -> None:
    fields = ["Date", "Sport", "Home", "Away", "Pick", "OneXBetOdds", "Market", "CheckedAt", "Source", "Note"]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def _append_history(path: Path, rows: List[Dict[str, Any]]) -> None:
    fields = [
        "SnapshotAt",
        "Date",
        "Sport",
        "Home",
        "Away",
        "Pick",
        "OneXBetOdds",
        "Market",
        "EventId",
        "CanonicalId",
        "League",
        "EventDate",
        "StartUtc",
        "Side",
        "TargetOdds",
        "OldOdds",
        "Source",
    ]
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not path.exists() or path.stat().st_size == 0
    with path.open("a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        if write_header:
            writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def _default_report_csv(target: date) -> Path:
    return REPORTS_DIR / f"daily_1xbet_value_advisor_{target.isoformat()}.csv"


def _default_report_md(target: date) -> Path:
    return REPORTS_DIR / f"1xbet_public_odds_sync_{target.isoformat()}.md"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sync public 1xBet odds for daily advisor rows without a visible browser.")
    parser.add_argument("--date", default="today")
    parser.add_argument("--input-csv", default="")
    parser.add_argument("--manual-odds", default=str(DEFAULT_MANUAL_ODDS))
    parser.add_argument("--history-out", default=str(DEFAULT_ODDS_HISTORY))
    parser.add_argument("--linefeed-snapshot", default=str(DEFAULT_LINEFEED_SNAPSHOT))
    parser.add_argument("--out-report", default="")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--min-score", type=int, default=3)
    parser.add_argument("--sleep", type=float, default=0.2)
    parser.add_argument("--include-no-bet-price-too-low", action="store_true", default=True)
    parser.add_argument(
        "--include-odds-floor",
        type=float,
        default=1.30,
        help="Also verify rows whose current odds are at or above this floor, even if the model action is NO_BET.",
    )
    parser.add_argument("--broad-queries", action="store_true", default=True, help="Also search by each team name alone. Slower and more ambiguous.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    target = _target_date(args.date)
    input_csv = Path(args.input_csv) if args.input_csv else _default_report_csv(target)
    manual_path = Path(args.manual_odds)
    out_report = Path(args.out_report) if args.out_report else _default_report_md(target)
    history_path = Path(args.history_out)
    snapshot_rows = _read_csv(Path(args.linefeed_snapshot))
    source_rows = _read_csv(input_csv)
    manual_rows = _read_csv(manual_path)
    manual_by_key = {_manual_key(row): row for row in manual_rows if any(row.values())}

    actions = set(SYNC_ACTIONS)
    if args.include_no_bet_price_too_low:
        actions.add("NO_BET_PRICE_TOO_LOW")

    candidates: List[Dict[str, Any]] = []
    seen_candidates: set[Tuple[str, str, str, str, str]] = set()
    for row in source_rows:
        if SPORT_IDS.get(_normalize(row.get("Sport"))) is None:
            continue
        odds = _as_float(row.get("PickOdds"))
        action_matches = str(row.get("ActionVerdict") or "") in actions
        odds_floor_matches = bool(args.include_odds_floor and odds is not None and odds >= args.include_odds_floor)
        if not (action_matches or odds_floor_matches):
            continue
        key = _manual_key(row)
        if key in seen_candidates:
            continue
        seen_candidates.add(key)
        candidates.append(row)
        if len(candidates) >= max(0, args.limit):
            break

    checked_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    confirmed: List[Dict[str, Any]] = []
    blocked: List[Dict[str, Any]] = []

    for row in candidates:
        label = f"{row.get('Home')} vs {row.get('Away')} | {row.get('Pick')}"
        try:
            event = _event_from_row(row, target)
            if not event:
                event = _best_event(row, target, args.timeout, args.min_score, broad_queries=args.broad_queries)
            if not event:
                fallback = _snapshot_confirmation(
                    row,
                    snapshot_rows,
                    target,
                    checked_at,
                    min_score=args.min_score,
                    reason="NO_CONFIDENT_EVENT_MATCH",
                )
                if fallback:
                    manual_by_key[_manual_key(fallback)] = fallback
                    confirmed.append(
                        {
                            **fallback,
                            "label": label,
                            "min_entry": row.get("MinEntryOdds"),
                            "old_odds": row.get("PickOdds"),
                        }
                    )
                else:
                    blocked.append({"label": label, "reason": "NO_CONFIDENT_EVENT_MATCH"})
                continue
            game = _fetch_game(event, args.timeout)
            odds, market, side_or_reason = _main_market_odds(row, game)
            if odds is None or odds <= 1.0:
                fallback = _linefeed_confirmation_from_row(
                    row,
                    target,
                    checked_at,
                    reason=side_or_reason or "NO_MAIN_MARKET_ODDS",
                )
                if not fallback:
                    fallback = _snapshot_confirmation(
                        row,
                        snapshot_rows,
                        target,
                        checked_at,
                        min_score=args.min_score,
                        reason=side_or_reason or "NO_MAIN_MARKET_ODDS",
                    )
                if fallback:
                    manual_by_key[_manual_key(fallback)] = fallback
                    confirmed.append(
                        {
                            **fallback,
                            "label": label,
                            "min_entry": row.get("MinEntryOdds"),
                            "old_odds": row.get("PickOdds"),
                        }
                    )
                else:
                    blocked.append({"label": label, "reason": side_or_reason or "NO_MAIN_MARKET_ODDS"})
                continue
            start_utc = ""
            if game.get("S") not in (None, ""):
                try:
                    start_utc = datetime.fromtimestamp(int(game["S"]), tz=timezone.utc).isoformat(timespec="seconds")
                except Exception:
                    start_utc = ""
            note = (
                f"event_id={event.event_id}; canonical_id={event.canonical_id}; "
                f"league={event.league}; event_date={event.event_date}; "
                f"start_utc={start_utc}; "
                f"match_score={event.score}; side={side_or_reason}; base={event.base_url}; "
                f"api=Web_SearchZip+GetGameZip"
            )
            out = {
                "Date": str(row.get("Date") or target.isoformat())[:10],
                "Sport": row.get("Sport") or "",
                "Home": row.get("Home") or "",
                "Away": row.get("Away") or "",
                "Pick": row.get("Pick") or "",
                "OneXBetOdds": odds,
                "Market": market,
                "CheckedAt": checked_at,
                "Source": "1XBET_PUBLIC_API",
                "Note": note,
            }
            manual_by_key[_manual_key(out)] = out
            confirmed.append(
                {
                    **out,
                    "label": label,
                    "min_entry": row.get("MinEntryOdds"),
                    "old_odds": row.get("PickOdds"),
                    "SnapshotAt": checked_at,
                    "EventId": event.event_id,
                    "CanonicalId": event.canonical_id,
                    "League": event.league,
                    "EventDate": event.event_date,
                    "StartUtc": start_utc,
                    "Side": side_or_reason,
                    "TargetOdds": row.get("MinEntryOdds"),
                    "OldOdds": row.get("PickOdds"),
                }
            )
        except Exception as exc:
            fallback = _linefeed_confirmation_from_row(row, target, checked_at, reason=str(exc))
            if not fallback:
                fallback = _snapshot_confirmation(
                    row,
                    snapshot_rows,
                    target,
                    checked_at,
                    min_score=args.min_score,
                    reason=str(exc),
                )
            if fallback:
                manual_by_key[_manual_key(fallback)] = fallback
                confirmed.append(
                    {
                        **fallback,
                        "label": label,
                        "min_entry": row.get("MinEntryOdds"),
                        "old_odds": row.get("PickOdds"),
                    }
                )
            else:
                blocked.append({"label": label, "reason": str(exc)[:240]})
        if args.sleep:
            time.sleep(args.sleep)

    _write_manual(manual_path, list(manual_by_key.values()))
    _append_history(history_path, confirmed)

    out_report.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# 1xBet public odds sync",
        f"- Date: {target.isoformat()}",
        f"- Input CSV: {input_csv}",
        f"- Manual odds file: {manual_path}",
        f"- Odds history file: {history_path}",
        f"- Linefeed snapshot file: {args.linefeed_snapshot}",
        f"- Linefeed snapshot rows available: {len(snapshot_rows)}",
        f"- Candidates checked: {len(candidates)}",
        f"- Odds floor included: {args.include_odds_floor if args.include_odds_floor else 'disabled'}",
        f"- Confirmed odds written: {len(confirmed)}",
        f"- Blocked/unconfirmed: {len(blocked)}",
        "",
        "## Confirmed",
    ]
    if confirmed:
        for item in confirmed:
            lines.append(
                f"- {item['label']}: 1xBet={item['OneXBetOdds']} | target={item.get('min_entry')} | "
                f"old={item.get('old_odds')} | {item['Note']}"
            )
    else:
        lines.append("- None")
    lines.extend(["", "## Blocked"])
    if blocked:
        for item in blocked:
            lines.append(f"- {item['label']}: {item['reason']}")
    else:
        lines.append("- None")
    out_report.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"checked={len(candidates)} confirmed={len(confirmed)} blocked={len(blocked)}")
    print(f"Wrote {manual_path}")
    print(f"Wrote {out_report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
