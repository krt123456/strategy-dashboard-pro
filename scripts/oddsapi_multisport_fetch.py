#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests

API_BASE = "https://api.odds-api.io/v3"


def daterange(start: datetime, end: datetime) -> Iterable[datetime]:
    cur = start
    while cur < end:
        yield cur
        cur += timedelta(days=1)


def _iso_day(dt: datetime) -> Tuple[str, str]:
    start = dt.strftime("%Y-%m-%dT00:00:00Z")
    end = (dt + timedelta(days=1)).strftime("%Y-%m-%dT00:00:00Z")
    return start, end


def load_keys(keys_dir: Path) -> List[str]:
    keys: List[str] = []
    seen = set()
    for path in sorted(keys_dir.glob("*.txt")):
        key = path.read_text(encoding="utf-8").strip()
        if not key:
            continue
        if key in seen:
            continue
        seen.add(key)
        keys.append(key)
    if not keys:
        raise RuntimeError(f"No API keys found in {keys_dir}")
    return keys


class KeyPool:
    def __init__(self, keys: List[str], cooldown_seconds: int = 60) -> None:
        self._keys = keys
        self._i = 0
        self._cooldown = {}
        self._cooldown_seconds = cooldown_seconds

    def next_key(self) -> Optional[str]:
        now = time.time()
        for _ in range(len(self._keys)):
            key = self._keys[self._i]
            self._i = (self._i + 1) % len(self._keys)
            until = self._cooldown.get(key, 0)
            if now >= until:
                return key
        return None

    def cooldown(self, key: str) -> None:
        self._cooldown[key] = time.time() + self._cooldown_seconds


def request_json(path: str, params: Dict[str, str], pool: KeyPool, timeout: int, retries: int) -> Any:
    url = f"{API_BASE}/{path.lstrip('/')}"
    last_err = None
    for _ in range(retries):
        key = pool.next_key()
        if not key:
            time.sleep(1.0)
            continue
        params["apiKey"] = key
        try:
            resp = requests.get(url, params=params, timeout=timeout)
        except Exception as exc:
            last_err = exc
            continue
        if resp.status_code == 200:
            return resp.json()
        if resp.status_code == 429:
            pool.cooldown(key)
            last_err = RuntimeError("rate_limited")
            continue
        last_err = RuntimeError(f"status {resp.status_code}")
    raise RuntimeError(f"Odds-API request failed: {last_err}")


def fetch_events_for_day(
    pool: KeyPool,
    sport: str,
    day: datetime,
    limit: int,
    timeout: int,
    retries: int,
) -> List[Dict[str, Any]]:
    start, end = _iso_day(day)
    params = {"sport": sport, "from": start, "to": end, "limit": str(limit)}
    try:
        data = request_json("events", params, pool, timeout=timeout, retries=retries)
    except RuntimeError:
        return []
    return data if isinstance(data, list) else []


def fetch_odds_for_event(
    pool: KeyPool,
    event_id: int,
    bookmakers: str,
    timeout: int,
    retries: int,
) -> Dict[str, Any]:
    params = {"eventId": str(event_id), "bookmakers": bookmakers}
    try:
        data = request_json("odds", params, pool, timeout=timeout, retries=retries)
    except RuntimeError:
        return {}
    return data if isinstance(data, dict) else {}


def _slug(text: str) -> str:
    text = text.strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-") or "group"


def _parse_groups(groups_text: str | None, default: str) -> List[str]:
    if groups_text:
        return [g.strip() for g in groups_text.split(";") if g.strip()]
    return [default]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keys-dir", default="/home/luna/Desktop/odds-api")
    ap.add_argument("--sports", default="football,basketball,tennis,ice-hockey,table-tennis")
    ap.add_argument("--bookmakers", default="Bet365,Betfair Exchange")
    ap.add_argument("--bookmakers-groups", default=None, help="Semicolon-separated groups, each is comma-separated list.")
    ap.add_argument("--start-date", required=True, help="YYYY-MM-DD")
    ap.add_argument("--end-date", required=True, help="YYYY-MM-DD (exclusive)")
    ap.add_argument("--out-dir", default="data/raw/oddsapi_multisport")
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--timeout", type=int, default=30)
    ap.add_argument("--retries", type=int, default=5)
    ap.add_argument("--sleep", type=float, default=0.05)
    ap.add_argument("--fetch-odds", action="store_true")
    ap.add_argument("--pending-only", action="store_true", help="Fetch odds only for pending events.")
    ap.add_argument("--reset-events", action="store_true")
    ap.add_argument("--reset-odds", action="store_true")
    args = ap.parse_args()

    keys = load_keys(Path(args.keys_dir))
    pool = KeyPool(keys)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    events_root = out_dir / "_events"
    events_root.mkdir(parents=True, exist_ok=True)

    sports = [s.strip() for s in args.sports.split(",") if s.strip()]
    start = datetime.fromisoformat(args.start_date)
    end = datetime.fromisoformat(args.end_date)

    groups = _parse_groups(args.bookmakers_groups, args.bookmakers)
    group_slugs = {g: _slug(g) for g in groups}
    summary = {
        "sports": {},
        "groups": groups,
        "start": args.start_date,
        "end": args.end_date,
    }

    for sport in sports:
        events_dir = events_root / sport
        events_dir.mkdir(parents=True, exist_ok=True)

        all_events: List[Dict[str, Any]] = []
        for day in daterange(start, end):
            day_key = day.strftime("%Y-%m-%d")
            out_path = events_dir / f"{day_key}.json"
            if out_path.exists() and not args.reset_events:
                data = json.loads(out_path.read_text(encoding="utf-8"))
            else:
                data = fetch_events_for_day(pool, sport, day, args.limit, args.timeout, args.retries)
                out_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            if isinstance(data, list):
                all_events.extend(data)
            if args.sleep:
                time.sleep(args.sleep)

        sport_summary = {"events": len(all_events), "groups": {}}

        for group in groups:
            group_slug = group_slugs[group]
            group_dir = out_dir / group_slug / sport
            odds_dir = group_dir / "odds"
            odds_dir.mkdir(parents=True, exist_ok=True)

            fetched_odds = 0
            skipped_odds = 0
            if args.fetch_odds:
                seen = set()
                for item in all_events:
                    if args.pending_only and str(item.get("status")) != "pending":
                        continue
                    event_id = item.get("id")
                    if event_id is None or event_id in seen:
                        continue
                    seen.add(event_id)
                    out_path = odds_dir / f"{event_id}.json"
                    if out_path.exists() and not args.reset_odds:
                        skipped_odds += 1
                        continue
                    odds = fetch_odds_for_event(pool, int(event_id), group, args.timeout, args.retries)
                    out_path.write_text(json.dumps(odds, ensure_ascii=False), encoding="utf-8")
                    fetched_odds += 1
                    if args.sleep:
                        time.sleep(args.sleep)

            sport_summary["groups"][group_slug] = {
                "bookmakers": group,
                "odds_fetched": fetched_odds,
                "odds_skipped": skipped_odds,
            }

        summary["sports"][sport] = sport_summary

    summary_path = Path("reports") / "oddsapi_multisport_fetch_summary.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
