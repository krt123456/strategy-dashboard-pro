#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import yaml

from oddspapi_client import get_json, load_api_keys


def _daterange(start: datetime, end: datetime, window_days: int) -> Iterable[Tuple[datetime, datetime]]:
    current = start
    while current < end:
        window_end = min(current + timedelta(days=window_days), end)
        yield current, window_end
        current = window_end


def _match_any(patterns: List[str], text: str) -> bool:
    for pattern in patterns:
        if re.search(pattern, text, flags=re.IGNORECASE):
            return True
    return False


def _load_patterns(path: str | None) -> Tuple[List[str], List[str]]:
    if not path:
        return [], []
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    include = data.get("include") or []
    exclude = data.get("exclude") or []
    return list(include), list(exclude)


def _pick_sport_id(sports: List[Dict[str, Any]], sport_id: Optional[int], sport_name: Optional[str]) -> int:
    if sport_id is not None:
        return int(sport_id)
    if not sports:
        raise RuntimeError("No sports returned from OddsPapi.")
    target = (sport_name or "table tennis").strip().lower()
    for sport in sports:
        name = str(sport.get("sportName", sport.get("name", ""))).lower()
        slug = str(sport.get("slug", "")).lower()
        if target in name or target in slug:
            return int(sport.get("sportId", sport.get("id")))
    # fallback: exact match on name
    for sport in sports:
        if str(sport.get("sportName", sport.get("name", ""))).lower() == target:
            return int(sport.get("sportId", sport.get("id")))
    raise RuntimeError(f"Could not find sport id for '{target}'.")


def _select_tournaments(
    tournaments: List[Dict[str, Any]],
    include_patterns: List[str],
    exclude_patterns: List[str],
    explicit_ids: List[int],
) -> List[Dict[str, Any]]:
    if explicit_ids:
        wanted = {int(x) for x in explicit_ids}
        return [t for t in tournaments if int(t.get("tournamentId", t.get("id"))) in wanted]
    if not include_patterns and not exclude_patterns:
        return tournaments
    selected = []
    for t in tournaments:
        name = " ".join(
            [
                str(t.get("tournamentName", t.get("name", ""))),
                str(t.get("tournamentSlug", "")),
                str(t.get("categoryName", "")),
            ]
        ).strip()
        if include_patterns and not _match_any(include_patterns, name):
            continue
        if exclude_patterns and _match_any(exclude_patterns, name):
            continue
        selected.append(t)
    return selected


def _fetch_tournaments(api_keys: list[str], sport_id: int) -> List[Dict[str, Any]]:
    params = {"sportId": str(sport_id)}
    data = get_json("tournaments", params, api_keys=api_keys)
    if isinstance(data, list):
        return data
    return data.get("tournaments", []) if isinstance(data, dict) else []


def _fetch_fixtures(
    api_keys: list[str],
    tournament_id: int,
    start: datetime,
    end: datetime,
) -> List[Dict[str, Any]]:
    params = {
        "tournamentId": str(tournament_id),
        "from": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "to": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    data = get_json("fixtures", params, api_keys=api_keys)
    if isinstance(data, list):
        return data
    return data.get("fixtures", []) if isinstance(data, dict) else []


def _fetch_odds_for_fixture(
    api_keys: list[str],
    fixture_id: str,
    bookmakers: str | None,
    odds_format: str,
    verbosity: int,
) -> Dict[str, Any]:
    params = {
        "fixtureId": fixture_id,
        "oddsFormat": odds_format,
        "verbosity": str(verbosity),
    }
    if bookmakers:
        params["bookmakers"] = bookmakers
    return get_json("odds", params, api_keys=api_keys)


def _fetch_odds_by_tournaments(
    api_keys: list[str],
    tournament_ids: List[int],
    bookmaker: str | None,
    odds_format: str,
    verbosity: int,
    from_date: Optional[str],
    to_date: Optional[str],
) -> Any:
    params = {
        "tournamentIds": ",".join(str(t) for t in tournament_ids),
        "oddsFormat": odds_format,
        "verbosity": str(verbosity),
    }
    if bookmaker:
        params["bookmaker"] = bookmaker
    if from_date:
        params["from"] = from_date
    if to_date:
        params["to"] = to_date
    return get_json("odds-by-tournaments", params, api_keys=api_keys)


def _iso_date(date_str: Optional[str]) -> Optional[str]:
    if not date_str:
        return None
    return f"{date_str}T00:00:00Z"


def _fetch_scores(api_keys: list[str], fixture_id: str) -> Dict[str, Any]:
    params = {"fixtureId": fixture_id}
    return get_json("scores", params, api_keys=api_keys)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api-key", default=None)
    ap.add_argument("--api-key-file", default=None)
    ap.add_argument("--sport-id", type=int, default=None)
    ap.add_argument("--sport-name", default="Table Tennis")
    ap.add_argument("--start-date", required=True, help="YYYY-MM-DD")
    ap.add_argument("--end-date", required=True, help="YYYY-MM-DD (exclusive)")
    ap.add_argument("--window-days", type=int, default=7)
    ap.add_argument("--out-dir", default="data/raw/oddspapi_tabletennis")
    ap.add_argument("--filter-file", default="data/oddspapi_tabletennis_targets.yaml")
    ap.add_argument("--tournament-ids", default=None, help="Comma list to force specific tournament ids")
    ap.add_argument("--max-tournaments", type=int, default=0)
    ap.add_argument("--max-fixtures", type=int, default=0)
    ap.add_argument("--sleep", type=float, default=0.0)
    ap.add_argument("--fetch-odds", action="store_true")
    ap.add_argument("--fetch-scores", action="store_true")
    ap.add_argument("--only-has-odds", action="store_true", default=True)
    ap.add_argument("--use-odds-by-tournaments", action="store_true")
    ap.add_argument("--bookmakers", default=None, help="Comma-separated bookmaker slugs")
    ap.add_argument("--odds-format", default="decimal")
    ap.add_argument("--verbosity", type=int, default=3)
    ap.add_argument("--tournament-chunk", type=int, default=5)
    args = ap.parse_args()

    api_keys = load_api_keys(args.api_key, args.api_key_file)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Fetch sports list and resolve sport id.
    sports = get_json("sports", {}, api_keys=api_keys)
    sports_path = out_dir / "sports.json"
    sports_path.write_text(json.dumps(sports, ensure_ascii=False), encoding="utf-8")

    sport_id = _pick_sport_id(sports if isinstance(sports, list) else sports.get("sports", []), args.sport_id, args.sport_name)

    # Fetch tournaments.
    tournaments = _fetch_tournaments(api_keys, sport_id)
    tournaments_path = out_dir / "tournaments.json"
    tournaments_path.write_text(json.dumps(tournaments, ensure_ascii=False), encoding="utf-8")

    include_patterns, exclude_patterns = _load_patterns(args.filter_file)
    explicit_ids = []
    if args.tournament_ids:
        explicit_ids = [int(x) for x in args.tournament_ids.split(",") if x.strip().isdigit()]
    selected = _select_tournaments(tournaments, include_patterns, exclude_patterns, explicit_ids)
    if args.max_tournaments:
        selected = selected[: args.max_tournaments]

    fixtures_dir = out_dir / "fixtures"
    fixtures_dir.mkdir(parents=True, exist_ok=True)
    odds_dir = out_dir / "odds"
    odds_dir.mkdir(parents=True, exist_ok=True)
    odds_by_tournaments_dir = out_dir / "odds_by_tournaments"
    odds_by_tournaments_dir.mkdir(parents=True, exist_ok=True)
    scores_dir = out_dir / "scores"
    scores_dir.mkdir(parents=True, exist_ok=True)

    start = datetime.fromisoformat(args.start_date)
    end = datetime.fromisoformat(args.end_date)

    all_fixtures: Dict[str, Dict[str, Any]] = {}
    for t in selected:
        tid = int(t.get("tournamentId", t.get("id")))
        for win_start, win_end in _daterange(start, end, args.window_days):
            key = f"{tid}_{win_start.strftime('%Y%m%d')}_{win_end.strftime('%Y%m%d')}"
            out_path = fixtures_dir / f"{key}.json"
            if out_path.exists():
                data = json.loads(out_path.read_text(encoding="utf-8"))
            else:
                try:
                    data = _fetch_fixtures(api_keys, tid, win_start, win_end)
                except Exception as exc:
                    errors_path = fixtures_dir / "_errors.log"
                    errors_path.write_text("", encoding="utf-8") if not errors_path.exists() else None
                    with errors_path.open("a", encoding="utf-8") as logf:
                        logf.write(
                            f"{tid}_{win_start.strftime('%Y%m%d')}_{win_end.strftime('%Y%m%d')}\t"
                            f"{type(exc).__name__}\t{exc}\n"
                        )
                    if args.sleep:
                        time.sleep(args.sleep)
                    continue
                out_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
                if args.sleep:
                    time.sleep(args.sleep)
            if isinstance(data, list):
                for fixture in data:
                    fid = fixture.get("id") or fixture.get("fixtureId")
                    if fid is None:
                        continue
                    all_fixtures[str(fid)] = fixture
            if args.max_fixtures and len(all_fixtures) >= args.max_fixtures:
                break
        if args.max_fixtures and len(all_fixtures) >= args.max_fixtures:
            break

    if args.use_odds_by_tournaments and selected:
        bookmaker = None
        if args.bookmakers:
            bookmaker = args.bookmakers.split(",")[0].strip()
        ids = [int(t.get("tournamentId", t.get("id"))) for t in selected if t.get("tournamentId", t.get("id")) is not None]
        for idx in range(0, len(ids), args.tournament_chunk):
            chunk = ids[idx : idx + args.tournament_chunk]
            stamp = f"{chunk[0]}_{chunk[-1]}_{args.start_date}_{args.end_date}"
            out_path = odds_by_tournaments_dir / f"{stamp}.json"
            if out_path.exists():
                continue
            payload = _fetch_odds_by_tournaments(
                api_keys,
                chunk,
                bookmaker,
                args.odds_format,
                args.verbosity,
                _iso_date(args.start_date),
                _iso_date(args.end_date),
            )
            out_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            if args.sleep:
                time.sleep(args.sleep)

    if args.fetch_odds:
        for fid, fixture in all_fixtures.items():
            if args.only_has_odds and not fixture.get("hasOdds"):
                continue
            out_path = odds_dir / f"{fid}.json"
            if out_path.exists():
                continue
            try:
                odds_payload = _fetch_odds_for_fixture(
                    api_keys,
                    fid,
                    args.bookmakers,
                    args.odds_format,
                    args.verbosity,
                )
            except Exception as exc:
                errors_path = odds_dir / "_errors.log"
                errors_path.write_text("", encoding="utf-8") if not errors_path.exists() else None
                with errors_path.open("a", encoding="utf-8") as logf:
                    logf.write(f"{fid}\t{type(exc).__name__}\t{exc}\n")
                if args.sleep:
                    time.sleep(args.sleep)
                continue
            out_path.write_text(json.dumps(odds_payload, ensure_ascii=False), encoding="utf-8")
            if args.sleep:
                time.sleep(args.sleep)

    if args.fetch_scores:
        for fid in all_fixtures.keys():
            out_path = scores_dir / f"{fid}.json"
            if out_path.exists():
                continue
            payload = _fetch_scores(api_keys, fid)
            out_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            if args.sleep:
                time.sleep(args.sleep)

    has_odds = sum(1 for fixture in all_fixtures.values() if fixture.get("hasOdds"))
    print(f"Selected tournaments: {len(selected)} | Fixtures: {len(all_fixtures)} | hasOdds: {has_odds}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
