#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List

from oddsapi_tt_client import get_json, load_api_keys


def daterange(start: datetime, end: datetime) -> Iterable[datetime]:
    current = start
    while current < end:
        yield current
        current += timedelta(days=1)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch_events(
    api_keys: List[str],
    sport: str,
    bookmaker: str | None,
    day: datetime,
    limit: int,
    sleep: float,
    retries: int,
    backoff: float,
) -> List[Dict[str, Any]]:
    start = _iso(day)
    end = _iso(day + timedelta(days=1))
    params = {
        "sport": sport,
        "from": start,
        "to": end,
        "limit": str(limit),
    }
    if bookmaker:
        params["bookmaker"] = bookmaker
    data = get_json("events", params, api_keys=api_keys, retries=retries, backoff=backoff)
    if sleep:
        time.sleep(sleep)
    return data if isinstance(data, list) else []


def fetch_odds(
    api_keys: List[str],
    event_id: int,
    bookmakers: str,
    sleep: float,
    retries: int,
    backoff: float,
) -> Dict[str, Any]:
    params = {
        "eventId": str(event_id),
        "bookmakers": bookmakers,
    }
    try:
        data = get_json("odds", params, api_keys=api_keys, retries=retries, backoff=backoff)
    except RuntimeError:
        return {}
    if sleep:
        time.sleep(sleep)
    return data if isinstance(data, dict) else {}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api-key", default=None)
    ap.add_argument("--api-key-file", default=None)
    ap.add_argument("--sport", default="table-tennis")
    ap.add_argument("--bookmaker", default="Bet365", help="Deprecated (use --bookmakers).")
    ap.add_argument("--bookmakers", default="Bet365,SingBet")
    ap.add_argument("--start-date", required=True, help="YYYY-MM-DD")
    ap.add_argument("--end-date", required=True, help="YYYY-MM-DD (exclusive)")
    ap.add_argument("--out-dir", default="data/raw/oddsapi_tabletennis")
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--fetch-odds", action="store_true")
    ap.add_argument("--max-events", type=int, default=0, help="Stop after this many events (0 = no limit).")
    ap.add_argument("--max-odds-requests", type=int, default=0, help="Stop after this many odds API requests (0 = no limit).")
    ap.add_argument("--refetch-empty", action="store_true", help="Refetch existing odds files that contain an empty object.")
    ap.add_argument("--sleep", type=float, default=0.0)
    ap.add_argument("--retries", type=int, default=2)
    ap.add_argument("--backoff", type=float, default=1.0)
    args = ap.parse_args()

    api_keys = load_api_keys(args.api_key, args.api_key_file)

    out_dir = Path(args.out_dir)
    events_dir = out_dir / "events"
    odds_dir = out_dir / "odds"
    events_dir.mkdir(parents=True, exist_ok=True)
    odds_dir.mkdir(parents=True, exist_ok=True)

    start = datetime.fromisoformat(args.start_date)
    end = datetime.fromisoformat(args.end_date)

    all_events: List[Dict[str, Any]] = []
    bookmakers_param = args.bookmakers or args.bookmaker or ""
    for day in daterange(start, end):
        day_key = day.strftime("%Y-%m-%d")
        out_path = events_dir / f"{day_key}.json"
        if out_path.exists():
            data = json.loads(out_path.read_text(encoding="utf-8"))
        else:
            data = fetch_events(api_keys, args.sport, None, day, args.limit, args.sleep, args.retries, args.backoff)
            out_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        if isinstance(data, list):
            all_events.extend(data)
        if args.max_events and len(all_events) >= args.max_events:
            all_events = all_events[: args.max_events]
            break

    if args.fetch_odds:
        seen = set()
        odds_requests = 0
        for item in all_events:
            if str(item.get("status")) != "pending":
                continue
            event_id = item.get("id")
            if event_id is None or event_id in seen:
                continue
            seen.add(event_id)
            out_path = odds_dir / f"{event_id}.json"
            if out_path.exists():
                if not args.refetch_empty:
                    continue
                try:
                    existing = json.loads(out_path.read_text(encoding="utf-8") or "{}")
                except Exception:
                    existing = {}
                if existing:
                    continue
            if args.max_odds_requests and odds_requests >= args.max_odds_requests:
                break
            odds = fetch_odds(api_keys, int(event_id), bookmakers_param, args.sleep, args.retries, args.backoff)
            out_path.write_text(json.dumps(odds, ensure_ascii=False), encoding="utf-8")
            odds_requests += 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
