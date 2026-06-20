#!/usr/bin/env python3
"""Export a locked forecast into advisor-like rows for result checking."""
from __future__ import annotations

import argparse
import csv
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Tuple

BASE_DIR = Path(__file__).resolve().parents[1]
REPORTS_DIR = BASE_DIR / "reports"
LOCK_DIR = REPORTS_DIR / "locked_forecasts"


def _target_date(value: str) -> date:
    raw = (value or "yesterday").strip().lower()
    today = date.today()
    if raw in {"today", "اليوم"}:
        return today
    if raw in {"yesterday", "امس", "أمس"}:
        return today - timedelta(days=1)
    if raw in {"tomorrow", "غدا", "غداً"}:
        return today + timedelta(days=1)
    return date.fromisoformat(raw)


def _read_csv(path: Path) -> List[Dict[str, Any]]:
    if not path.exists() or path.stat().st_size <= 0:
        return []
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _row_key(row: Dict[str, Any]) -> Tuple[str, str, str, str, str]:
    return (
        str(row.get("Date") or row.get("ForecastDate") or "").strip()[:10],
        _norm(row.get("Sport")),
        _norm(row.get("Home")),
        _norm(row.get("Away")),
        _norm(row.get("Pick")),
    )


def _parse_note_fields(note: Any) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for part in str(note or "").split(";"):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key and value:
            out[key] = value
    return out


def _manual_odds_index(path: Path) -> Dict[Tuple[str, str, str, str, str], Dict[str, str]]:
    out: Dict[Tuple[str, str, str, str, str], Dict[str, str]] = {}
    for row in _read_csv(path):
        fields = _parse_note_fields(row.get("Note"))
        out[_row_key(row)] = {
            "OneXBetManualOdds": str(row.get("OneXBetOdds") or ""),
            "OneXBetManualCheckedAt": str(row.get("CheckedAt") or ""),
            "OneXBetManualSource": str(row.get("Source") or ""),
            "OneXBetManualEventId": fields.get("event_id", ""),
            "OneXBetManualCanonicalId": fields.get("canonical_id", ""),
            "OneXBetManualLeague": fields.get("league", ""),
            "OneXBetManualEventDate": fields.get("event_date", ""),
            "OneXBetManualStartUtc": fields.get("start_utc", ""),
        }
    return out


def _linefeed_history_index(path: Path) -> Dict[Tuple[str, str, str, str, str], Dict[str, str]]:
    out: Dict[Tuple[str, str, str, str, str], Dict[str, str]] = {}
    match_fallback: Dict[Tuple[str, str, str, str], Dict[str, str]] = {}
    for row in _read_csv(path):
        # Older snapshots and candidate-watch appends share the same file but
        # not the same semantic columns. Detect candidate-watch rows first.
        source = str(row.get("Source") or "")
        if source.startswith("1xbet_public_linefeed_sport_"):
            rec = {
                "Date": str(row.get("Date") or ""),
                "Sport": str(row.get("Sport") or ""),
                "League": str(row.get("SportId") or ""),
                "Home": str(row.get("League") or ""),
                "Away": str(row.get("Home") or ""),
                "Pick": str(row.get("Away") or ""),
                "OneXBetManualOdds": str(row.get("CanonicalId") or ""),
                "OneXBetManualCheckedAt": str(row.get("AwayOdds") or ""),
                "OneXBetManualSource": source,
                "OneXBetManualEventId": str(row.get("StartUtc") or ""),
                "OneXBetManualCanonicalId": str(row.get("HomeOdds") or ""),
                "OneXBetManualLeague": str(row.get("SportId") or ""),
                "OneXBetManualEventDate": str(row.get("Date") or ""),
                "OneXBetManualStartUtc": str(row.get("DrawOdds") or ""),
            }
            out[_row_key(rec)] = rec
            continue

        if str(row.get("Source") or "") == "1XBET_PUBLIC_LINEFEED":
            rec = {
                "Date": str(row.get("Date") or ""),
                "Sport": str(row.get("Sport") or ""),
                "League": str(row.get("League") or ""),
                "Home": str(row.get("Home") or ""),
                "Away": str(row.get("Away") or ""),
                "Pick": "",
                "OneXBetManualOdds": "",
                "OneXBetManualCheckedAt": str(row.get("SnapshotAt") or ""),
                "OneXBetManualSource": str(row.get("Source") or ""),
                "OneXBetManualEventId": str(row.get("EventId") or ""),
                "OneXBetManualCanonicalId": str(row.get("CanonicalId") or ""),
                "OneXBetManualLeague": str(row.get("League") or ""),
                "OneXBetManualEventDate": str(row.get("Date") or ""),
                "OneXBetManualStartUtc": str(row.get("StartUtc") or ""),
            }
            match_key = (
                str(rec.get("Date") or "").strip()[:10],
                _norm(rec.get("Sport")),
                _norm(rec.get("Home")),
                _norm(rec.get("Away")),
            )
            match_fallback[match_key] = rec
    for key, rec in match_fallback.items():
        # Store a match-level fallback under a key with empty pick.
        out[(key[0], key[1], key[2], key[3], "")] = rec
    return out


def _merge_source_meta(*items: Dict[str, str]) -> Dict[str, str]:
    merged: Dict[str, str] = {}
    for item in items:
        for key, value in item.items():
            if value and not merged.get(key):
                merged[key] = value
    return merged


def _event_meta(row: Dict[str, Any], manual_rows: Dict[Tuple[str, str, str, str, str], Dict[str, str]], linefeed_rows: Dict[Tuple[str, str, str, str, str], Dict[str, str]]) -> Dict[str, str]:
    key = _row_key(row)
    fallback_key = (key[0], key[1], key[2], key[3], "")
    return _merge_source_meta(manual_rows.get(key, {}), linefeed_rows.get(key, {}), linefeed_rows.get(fallback_key, {}))


def _export_rows(
    lock_rows: List[Dict[str, Any]],
    manual_rows: Dict[Tuple[str, str, str, str, str], Dict[str, str]],
    linefeed_rows: Dict[Tuple[str, str, str, str, str], Dict[str, str]],
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for row in lock_rows:
        manual = _event_meta(row, manual_rows, linefeed_rows)
        out = {
            "Rank": row.get("Rank") or "",
            "Sport": row.get("Sport") or "",
            "Date": row.get("ForecastDate") or "",
            "League": row.get("League") or "",
            "Home": row.get("Home") or "",
            "Away": row.get("Away") or "",
            "Pick": row.get("Pick") or "",
            "Prob": row.get("Prob") or "",
            "PickOdds": row.get("CurrentOdds") or "",
            "LocalOdds": row.get("CurrentOdds") or "",
            "OneXBetManualOdds": manual.get("OneXBetManualOdds") or row.get("CurrentOdds") or "",
            "OneXBetManualCheckedAt": manual.get("OneXBetManualCheckedAt") or row.get("LockedAt") or "",
            "OneXBetManualSource": manual.get("OneXBetManualSource") or "",
            "OneXBetManualEventId": manual.get("OneXBetManualEventId") or "",
            "OneXBetManualCanonicalId": manual.get("OneXBetManualCanonicalId") or "",
            "OneXBetManualLeague": manual.get("OneXBetManualLeague") or row.get("League") or "",
            "OneXBetManualEventDate": manual.get("OneXBetManualEventDate") or row.get("ForecastDate") or "",
            "OneXBetManualStartUtc": manual.get("OneXBetManualStartUtc") or "",
            "OneXBetOddsAgeMin": "",
            "OneXBetOddsFreshness": row.get("OneXBetFreshness") or "",
            "OneXBetOddsMaxAgeMin": "",
            "OneXBetStartUtc": manual.get("OneXBetManualStartUtc") or "",
            "MinutesToStart": "",
            "EventTimingStatus": row.get("EventTimingStatus") or "",
            "OddsSourceUsed": row.get("OneXBetStatus") or "",
            "FairOdds": "",
            "MinEntryOdds": row.get("TargetOdds") or "",
            "PriceGapPct": "",
            "EVPercent": row.get("EVPercent") or "",
            "StakePct": "0",
            "StakeAmount": "0",
            "ActionVerdict": "LOCKED_FORECAST_REVIEW",
            "EntryReadiness": row.get("EntryReadiness") or "",
            "GateBlockers": row.get("HardVetoes") or "",
            "BrainScore": row.get("GuardScore") or "",
            "ProbabilitySource": "locked_forecast",
            "Decision": row.get("FinalDecision") or "",
            "ValueVerdict": row.get("ValueVerdict") or "",
            "OneXBetStatus": row.get("OneXBetStatus") or "",
            "OneXBetEventId": manual.get("OneXBetManualEventId") or "",
            "OneXBetCanonicalId": manual.get("OneXBetManualCanonicalId") or "",
            "OneXBetLeague": manual.get("OneXBetManualLeague") or row.get("League") or "",
            "RankScore": row.get("GuardScore") or "",
            "OddsFlag": "",
            "Source": "locked_forecast",
            "StrategyGate": row.get("StrategyGate") or "",
        }
        rows.append(out)
    return rows


def _write_csv(rows: List[Dict[str, Any]], path: Path) -> None:
    fields = [
        "Rank",
        "Sport",
        "Date",
        "League",
        "Home",
        "Away",
        "Pick",
        "Prob",
        "PickOdds",
        "LocalOdds",
        "OneXBetManualOdds",
        "OneXBetManualCheckedAt",
        "OneXBetManualSource",
        "OneXBetManualEventId",
        "OneXBetManualCanonicalId",
        "OneXBetManualLeague",
        "OneXBetManualEventDate",
        "OneXBetManualStartUtc",
        "OneXBetOddsAgeMin",
        "OneXBetOddsFreshness",
        "OneXBetOddsMaxAgeMin",
        "OneXBetStartUtc",
        "MinutesToStart",
        "EventTimingStatus",
        "OddsSourceUsed",
        "FairOdds",
        "MinEntryOdds",
        "PriceGapPct",
        "EVPercent",
        "StakePct",
        "StakeAmount",
        "ActionVerdict",
        "EntryReadiness",
        "GateBlockers",
        "BrainScore",
        "ProbabilitySource",
        "Decision",
        "ValueVerdict",
        "OneXBetStatus",
        "OneXBetEventId",
        "OneXBetCanonicalId",
        "OneXBetLeague",
        "RankScore",
        "OddsFlag",
        "Source",
        "StrategyGate",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export locked forecasts for check_prediction_results.")
    parser.add_argument("--date", default="yesterday")
    parser.add_argument("--lock-csv", default="")
    parser.add_argument("--manual-odds", default=str(BASE_DIR / "data" / "manual_1xbet_odds.csv"))
    parser.add_argument("--linefeed-history", default=str(BASE_DIR / "data" / "one_xbet_linefeed_history.csv"))
    parser.add_argument("--out-csv", default="")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    target = _target_date(args.date)
    day = target.isoformat()
    lock_csv = Path(args.lock_csv) if args.lock_csv else LOCK_DIR / f"forecast_lock_{day}.csv"
    out_csv = Path(args.out_csv) if args.out_csv else REPORTS_DIR / f"locked_forecast_result_input_{day}.csv"
    rows = _export_rows(
        _read_csv(lock_csv),
        _manual_odds_index(Path(args.manual_odds)),
        _linefeed_history_index(Path(args.linefeed_history)),
    )
    _write_csv(rows, out_csv)
    with_event = sum(1 for row in rows if row.get("OneXBetManualEventId") or row.get("OneXBetEventId"))
    print(f"Wrote {out_csv}")
    print(f"locked_rows={len(rows)} with_event_id={with_event}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
