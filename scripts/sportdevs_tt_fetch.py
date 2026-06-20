#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import yaml

from sportdevs_tt_client import API_BASE, build_headers, dump_json, get_json, load_api_key


MATCHES_ENDPOINT = f"{API_BASE}/matches"
ODDS_ENDPOINT = f"{API_BASE}/odds/full-time-results"
LEAGUES_ENDPOINT = f"{API_BASE}/leagues"


def _normalize(text: str) -> str:
    return "".join(c for c in text.lower().strip() if c.isalnum() or c.isspace())


def _load_patterns(path: Optional[Path]) -> List[str]:
    if not path or not path.exists():
        return []
    cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    patterns = [str(x).strip().lower() for x in cfg.get("patterns", []) if x]
    return patterns


def _league_allowed(name: str, patterns: List[str]) -> bool:
    if not patterns:
        return True
    normalized = _normalize(name)
    return any(pat in normalized for pat in patterns)


def daterange(start: datetime, end: datetime) -> Iterable[datetime]:
    current = start
    while current < end:
        yield current
        current += timedelta(days=1)


def fetch_matches_for_day(
    headers: Dict[str, str],
    day: datetime,
    patterns: List[str],
    sleep: float,
) -> List[Dict[str, Any]]:
    start = day.strftime("%Y-%m-%dT00:00:00Z")
    end = (day + timedelta(days=1)).strftime("%Y-%m-%dT00:00:00Z")
    offset = 0
    limit = 50
    all_matches: List[Dict[str, Any]] = []
    while True:
        params = [
            ("start_time", f"gte.{start}"),
            ("start_time", f"lt.{end}"),
            ("limit", limit),
            ("offset", offset),
        ]
        data = get_json(MATCHES_ENDPOINT, headers=headers, params=params)
        batch = data.get("data", data)
        if not isinstance(batch, list):
            break
        for item in batch:
            league = str(item.get("league_name") or "")
            if _league_allowed(league, patterns):
                all_matches.append(item)
        if len(batch) < limit:
            break
        offset += limit
        if sleep:
            time.sleep(sleep)
    return all_matches


def fetch_odds_for_match(headers: Dict[str, str], match_id: int, sleep: float) -> Any:
    params = {"match_id": f"eq.{match_id}", "is_live": "eq.false"}
    data = get_json(ODDS_ENDPOINT, headers=headers, params=params)
    if sleep:
        time.sleep(sleep)
    return data


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api-key", default=None)
    ap.add_argument("--api-key-file", default=None)
    ap.add_argument("--start-date", required=True)
    ap.add_argument("--end-date", required=True)
    ap.add_argument("--out-dir", default="data/raw/sportdevs_tabletennis")
    ap.add_argument("--patterns-file", default="data/sportdevs_tabletennis_league_patterns.yaml")
    ap.add_argument("--fetch-odds", action="store_true")
    ap.add_argument("--sleep", type=float, default=0.0)
    ap.add_argument("--max-matches", type=int, default=0)
    args = ap.parse_args()

    api_key = load_api_key(args.api_key, args.api_key_file)
    headers = build_headers(api_key)

    patterns = _load_patterns(Path(args.patterns_file))

    out_dir = Path(args.out_dir)
    matches_dir = out_dir / "matches"
    odds_dir = out_dir / "odds_full_time"
    matches_dir.mkdir(parents=True, exist_ok=True)
    odds_dir.mkdir(parents=True, exist_ok=True)

    start = datetime.fromisoformat(args.start_date)
    end = datetime.fromisoformat(args.end_date)

    all_matches: List[Dict[str, Any]] = []
    for day in daterange(start, end):
        day_key = day.strftime("%Y-%m-%d")
        out_path = matches_dir / f"{day_key}.json"
        if out_path.exists():
            with out_path.open("r", encoding="utf-8") as f:
                day_matches = json.load(f)
        else:
            day_matches = fetch_matches_for_day(headers, day, patterns, args.sleep)
            dump_json(out_path, day_matches)
        all_matches.extend(day_matches)
        if args.max_matches and len(all_matches) >= args.max_matches:
            all_matches = all_matches[: args.max_matches]
            break

    if args.fetch_odds:
        seen = set()
        for match in all_matches:
            match_id = match.get("id")
            if match_id is None or match_id in seen:
                continue
            seen.add(match_id)
            out_path = odds_dir / f"{match_id}.json"
            if out_path.exists():
                continue
            data = fetch_odds_for_match(headers, int(match_id), args.sleep)
            dump_json(out_path, data)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
