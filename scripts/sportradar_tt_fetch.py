#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable, List

from sportradar_tt_client import get_json, load_keys


def _daterange(start: datetime, end: datetime) -> Iterable[datetime]:
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--key-files",
        default=",".join(
            [
                "/home/luna/Desktop/api/api.txt",
                "/home/luna/Desktop/api/api (Copy 2).txt",
                "/home/luna/Desktop/api/api (Copy 3).txt",
                "/home/luna/Desktop/api/api (Copy 4).txt",
                "/home/luna/Desktop/api/api (Copy 5).txt",
            ]
        ),
    )
    ap.add_argument("--access-level", default="trial")
    ap.add_argument("--language", default="en")
    ap.add_argument("--start-date", required=True, help="YYYY-MM-DD")
    ap.add_argument("--end-date", required=True, help="YYYY-MM-DD (inclusive)")
    ap.add_argument("--out-dir", default="data/raw/sportradar_tabletennis")
    ap.add_argument("--fetch-seasons", action="store_true")
    ap.add_argument("--fetch-competitions", action="store_true")
    ap.add_argument("--fetch-competition-seasons", action="store_true")
    ap.add_argument("--fetch-daily", action="store_true")
    ap.add_argument("--fetch-probabilities", action="store_true")
    ap.add_argument("--season-ids", default=None, help="Comma-separated season ids to fetch probabilities for.")
    ap.add_argument("--current-only", action="store_true")
    ap.add_argument("--sleep", type=float, default=0.0)
    args = ap.parse_args()

    keys = load_keys([k.strip() for k in args.key_files.split(",") if k.strip()])
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    access_level = args.access_level
    language = args.language

    competitions = None
    if args.fetch_competitions:
        competitions = get_json("competitions", access_level=access_level, language=language, keys=keys)
        (out_dir / "competitions.json").write_text(
            json.dumps(competitions, ensure_ascii=False), encoding="utf-8"
        )
        if args.sleep:
            time.sleep(args.sleep)

    seasons_payload = None
    if args.fetch_seasons:
        seasons = get_json("seasons", access_level=access_level, language=language, keys=keys)
        seasons_payload = seasons
        (out_dir / "seasons.json").write_text(json.dumps(seasons, ensure_ascii=False), encoding="utf-8")
        if args.sleep:
            time.sleep(args.sleep)
    elif args.fetch_probabilities:
        seasons_path = out_dir / "seasons.json"
        if seasons_path.exists():
            seasons_payload = json.loads(seasons_path.read_text(encoding="utf-8"))

    if args.fetch_competition_seasons:
        if competitions is None:
            competitions = get_json("competitions", access_level=access_level, language=language, keys=keys)
        comps = competitions.get("competitions", competitions)
        comp_dir = out_dir / "competition_seasons"
        comp_dir.mkdir(parents=True, exist_ok=True)
        for comp in comps:
            comp_id = comp.get("id")
            if not comp_id:
                continue
            path = comp_dir / f"{comp_id}.json"
            if path.exists():
                continue
            payload = get_json(
                f"competitions/{comp_id}/seasons", access_level=access_level, language=language, keys=keys
            )
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            if args.sleep:
                time.sleep(args.sleep)

    if args.fetch_daily:
        daily_dir = out_dir / "daily"
        daily_dir.mkdir(parents=True, exist_ok=True)
        start = datetime.fromisoformat(args.start_date)
        end = datetime.fromisoformat(args.end_date)
        for day in _daterange(start, end):
            stamp = day.strftime("%Y-%m-%d")
            path = daily_dir / f"{stamp}.json"
            if path.exists():
                continue
            payload = get_json(
                f"schedules/{stamp}/summaries", access_level=access_level, language=language, keys=keys
            )
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            if args.sleep:
                time.sleep(args.sleep)

    if args.fetch_probabilities:
        prob_dir = out_dir / "probabilities"
        prob_dir.mkdir(parents=True, exist_ok=True)
        season_ids = []
        if args.season_ids:
            season_ids = [s.strip() for s in args.season_ids.split(",") if s.strip()]
        else:
            seasons_data = seasons_payload.get("seasons", seasons_payload) if seasons_payload else []
            for season in seasons_data:
                sid = season.get("id")
                if not sid:
                    continue
                if args.current_only:
                    start = season.get("start_date")
                    end = season.get("end_date")
                    if start and end and not (args.start_date <= end and args.end_date >= start):
                        continue
                season_ids.append(sid)
        for sid in season_ids:
            path = prob_dir / f"{sid}.json"
            if path.exists():
                continue
            payload = get_json(
                f"seasons/{sid}/probabilities", access_level=access_level, language=language, keys=keys
            )
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            if args.sleep:
                time.sleep(args.sleep)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
