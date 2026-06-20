#!/usr/bin/env python3
"""Check saved prediction outcomes without treating watchlist rows as entries.

The daily advisor can rank high-probability rows that still have stake=0. This
script keeps outcome accounting strict: only positive-stake ENTER rows count as
official entries, while all other finished rows are tracked as raw model signal.
"""
from __future__ import annotations

import argparse
import csv
import math
import re
import sys
import unicodedata
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from zoneinfo import ZoneInfo

BASE_DIR = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = BASE_DIR / "scripts"
REPORTS_DIR = BASE_DIR / "reports"
DEFAULT_MANUAL_ODDS = BASE_DIR / "data" / "manual_1xbet_odds.csv"
DEFAULT_MANUAL_RESULTS = BASE_DIR / "data" / "manual_results.csv"
DEFAULT_MEMORY = BASE_DIR / "data" / "prediction_result_memory.csv"
LOCAL_TZ = ZoneInfo("Africa/Algiers")

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

try:
    import one_xbet_public_odds_sync as one_xbet  # type: ignore
except Exception:  # pragma: no cover
    one_xbet = None


def _target_date(value: str) -> date:
    raw = (value or "today").strip().lower()
    today = date.today()
    if raw in {"today", "اليوم"}:
        return today
    if raw in {"tomorrow", "غدا", "غداً"}:
        return today + timedelta(days=1)
    return date.fromisoformat(raw)


def _as_float(value: Any) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        out = float(value)
        if math.isnan(out):
            return None
        return out
    except Exception:
        return None


def _strip_accents(value: Any) -> str:
    text = "" if value is None else str(value)
    text = "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch))
    return text.encode("ascii", "ignore").decode("ascii")


def _norm(value: Any) -> str:
    text = _strip_accents(value).lower().replace("&", " and ")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\s]", " ", text)).strip()


def _norm_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", _norm(value))


def _row_key(row: Dict[str, Any]) -> Tuple[str, str, str, str, str]:
    return (
        str(row.get("Date") or "").strip()[:10],
        _norm_key(row.get("Sport")),
        _norm_key(row.get("Home")),
        _norm_key(row.get("Away")),
        _norm_key(row.get("Pick")),
    )


def _match_key(row: Dict[str, Any]) -> Tuple[str, str, str, str]:
    return (
        str(row.get("Date") or "").strip()[:10],
        _norm_key(row.get("Sport")),
        _norm_key(row.get("Home")),
        _norm_key(row.get("Away")),
    )


def _parse_note_fields(note: Any) -> Dict[str, str]:
    fields: Dict[str, str] = {}
    for part in str(note or "").split(";"):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key and value:
            fields[key] = value
    return fields


def _read_csv(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _load_manual_odds_meta(path: Path) -> Dict[Tuple[str, str, str, str, str], Dict[str, str]]:
    meta: Dict[Tuple[str, str, str, str, str], Dict[str, str]] = {}
    for row in _read_csv(path):
        fields = _parse_note_fields(row.get("Note"))
        if not fields:
            continue
        fields["source"] = str(row.get("Source") or "manual_1xbet_odds.csv")
        fields["note"] = str(row.get("Note") or "")
        meta[_row_key(row)] = fields
    return meta


def _load_manual_results(path: Path) -> Dict[Tuple[str, str, str, str], Dict[str, Any]]:
    results: Dict[Tuple[str, str, str, str], Dict[str, Any]] = {}
    for row in _read_csv(path):
        home_score = _as_float(row.get("HomeScore"))
        away_score = _as_float(row.get("AwayScore"))
        status = str(row.get("Status") or "").strip().upper()
        if home_score is None or away_score is None:
            if status in {"SUSPENDED", "POSTPONED", "CANCELED", "CANCELLED", "ABANDONED", "NOT_STARTED"}:
                results[_match_key(row)] = row
            continue
        results[_match_key(row)] = row
    return results


def _advisor_csv_for_date(target: date) -> Path:
    return REPORTS_DIR / f"daily_1xbet_value_advisor_{target.isoformat()}.csv"


def _event_from_row(row: Dict[str, Any], meta: Dict[str, str]) -> Optional[Any]:
    if one_xbet is None:
        return None
    event_id = row.get("OneXBetEventId") or row.get("OneXBetManualEventId") or meta.get("event_id")
    canonical_id = row.get("OneXBetCanonicalId") or row.get("OneXBetManualCanonicalId") or meta.get("canonical_id")
    if not event_id and not canonical_id:
        return None
    return one_xbet.MatchedEvent(
        event_id=event_id,
        canonical_id=canonical_id,
        home=str(row.get("Home") or ""),
        away=str(row.get("Away") or ""),
        league=str(row.get("OneXBetLeague") or row.get("OneXBetManualLeague") or meta.get("league") or row.get("League") or ""),
        event_date=None,
        score=99,
        base_url=meta.get("base", ""),
    )


def _score_from_api_sc(sc: Any) -> Tuple[Optional[float], Optional[float]]:
    if sc is None:
        return None, None
    if isinstance(sc, dict):
        for key_pair in (("S1", "S2"), ("FS1", "FS2"), ("P1", "P2")):
            h = _as_float(sc.get(key_pair[0]))
            a = _as_float(sc.get(key_pair[1]))
            if h is not None and a is not None:
                return h, a
        nums: List[float] = []
        for value in sc.values():
            num = _as_float(value)
            if num is not None:
                nums.append(num)
        if len(nums) >= 2:
            return nums[0], nums[1]
    if isinstance(sc, list):
        nums = [_as_float(v) for v in sc]
        nums = [v for v in nums if v is not None]
        if len(nums) >= 2:
            return nums[0], nums[1]
    text = str(sc)
    found = re.findall(r"\d+(?:\.\d+)?", text)
    if len(found) >= 2:
        return float(found[0]), float(found[1])
    return None, None


def _api_status(row: Dict[str, Any], event: Any, timeout_s: int, now: datetime) -> Dict[str, Any]:
    if one_xbet is None:
        return {"ResultStatus": "API_UNAVAILABLE", "ResultSource": "none", "ResultNote": "one_xbet module import failed"}
    try:
        game = one_xbet._fetch_game(event, timeout_s)  # type: ignore[attr-defined]
    except Exception as exc:
        return {
            "ResultStatus": "RESULT_SOURCE_REQUIRED",
            "ResultSource": "1XBET_PUBLIC_API",
            "ResultNote": str(exc)[:240],
        }

    start_local = ""
    ts = game.get("S")
    if ts not in (None, ""):
        try:
            start_dt = datetime.fromtimestamp(int(ts), tz=timezone.utc).astimezone(LOCAL_TZ)
            start_local = start_dt.isoformat(timespec="minutes")
            if start_dt > now:
                return {
                    "ResultStatus": "NOT_STARTED",
                    "StartTimeLocal": start_local,
                    "ResultSource": "1XBET_PUBLIC_API",
                    "ResultNote": f"SS={game.get('SS')}",
                }
        except Exception:
            pass

    home_score, away_score = _score_from_api_sc(game.get("SC"))
    if home_score is not None and away_score is not None:
        return {
            "ResultStatus": "FINISHED_OR_LIVE_SCORE",
            "StartTimeLocal": start_local,
            "HomeScore": home_score,
            "AwayScore": away_score,
            "ResultSource": "1XBET_PUBLIC_API",
            "ResultNote": f"SS={game.get('SS')}; score_field=SC",
        }

    return {
        "ResultStatus": "STARTED_OR_RESULT_PENDING",
        "StartTimeLocal": start_local,
        "ResultSource": "1XBET_PUBLIC_API",
        "ResultNote": f"SS={game.get('SS')}; no score field",
    }


def _manual_result_status(row: Dict[str, Any], manual: Dict[str, Any]) -> Dict[str, Any]:
    home_score = _as_float(manual.get("HomeScore"))
    away_score = _as_float(manual.get("AwayScore"))
    status = str(manual.get("Status") or "FINISHED").strip().upper() or "FINISHED"
    return {
        "ResultStatus": status,
        "StartTimeLocal": manual.get("StartTimeLocal") or "",
        "HomeScore": home_score,
        "AwayScore": away_score,
        "ResultSource": manual.get("Source") or "manual_results.csv",
        "ResultNote": manual.get("Note") or "",
    }


def _manual_only_missing_status(row: Dict[str, Any], target: date) -> Dict[str, Any]:
    timing = str(row.get("EventTimingStatus") or "").strip().upper()
    minutes = _as_float(row.get("MinutesToStart"))
    is_today_or_future = target >= date.today()
    if is_today_or_future and (
        timing in {"SCHEDULED", "UNKNOWN_START", "CLOSE_TO_START"}
        or (minutes is not None and minutes > 0)
    ):
        return {
            "ResultStatus": "NOT_STARTED",
            "ResultSource": "manual_results.csv",
            "ResultNote": "No manual result yet; external result polling disabled by --manual-only and event is not a completed past-row.",
        }
    return {
        "ResultStatus": "MANUAL_RESULT_REQUIRED",
        "ResultSource": "manual_results.csv",
        "ResultNote": "No trusted manual result row yet; external result polling disabled by --manual-only.",
    }


def _parse_iso_datetime(value: Any) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=LOCAL_TZ)
    return parsed.astimezone(LOCAL_TZ)


def _scheduled_not_started_status(row: Dict[str, Any], target: date, now: datetime) -> Optional[Dict[str, Any]]:
    start_local = _parse_iso_datetime(row.get("OneXBetStartUtc") or row.get("OneXBetManualStartUtc"))
    timing = str(row.get("EventTimingStatus") or "").strip().upper()
    minutes = _as_float(row.get("MinutesToStart"))
    if start_local and start_local > now:
        return {
            "ResultStatus": "NOT_STARTED",
            "StartTimeLocal": start_local.isoformat(timespec="minutes"),
            "ResultSource": "advisor_schedule_guard",
            "ResultNote": f"Skipped live result polling because advisor start time is still in the future. timing={timing or 'n/a'}",
        }
    if target >= date.today() and minutes is not None and minutes > 0:
        return {
            "ResultStatus": "NOT_STARTED",
            "StartTimeLocal": start_local.isoformat(timespec="minutes") if start_local else "",
            "ResultSource": "advisor_schedule_guard",
            "ResultNote": f"Skipped live result polling because advisor minutes-to-start is still positive ({minutes:.1f}). timing={timing or 'n/a'}",
        }
    return None


def _picked_side(row: Dict[str, Any]) -> str:
    pick = _norm_key(row.get("Pick"))
    if pick in {"draw", "x"}:
        return "draw"
    if pick and pick == _norm_key(row.get("Home")):
        return "home"
    if pick and pick == _norm_key(row.get("Away")):
        return "away"
    if pick and _norm_key(row.get("Home")) in pick:
        return "home"
    if pick and _norm_key(row.get("Away")) in pick:
        return "away"
    return "unknown"


def _outcome(row: Dict[str, Any], home_score: Optional[float], away_score: Optional[float]) -> str:
    if home_score is None or away_score is None:
        return "PENDING"
    side = _picked_side(row)
    if side == "unknown":
        return "UNKNOWN_PICK_SIDE"
    if home_score == away_score:
        actual = "draw"
    elif home_score > away_score:
        actual = "home"
    else:
        actual = "away"
    return "CORRECT" if side == actual else "WRONG"


def _is_official_entry(row: Dict[str, Any]) -> bool:
    stake = _as_float(row.get("StakeAmount")) or 0.0
    verdict = str(row.get("ValueVerdict") or "")
    return stake > 0 and verdict.startswith("ENTER")


def _write_csv(rows: Iterable[Dict[str, Any]], path: Path) -> None:
    rows = list(rows)
    fields = [
        "CheckedAt",
        "Rank",
        "Sport",
        "Date",
        "League",
        "Home",
        "Away",
        "Pick",
        "OfficialEntry",
        "Decision",
        "ValueVerdict",
        "StakeAmount",
        "Prob",
        "PickOdds",
        "LocalOdds",
        "OneXBetManualOdds",
        "OneXBetManualCheckedAt",
        "OneXBetManualSource",
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
        "ActionVerdict",
        "EntryReadiness",
        "GateBlockers",
        "BrainScore",
        "ProbabilitySource",
        "OneXBetStatus",
        "RankScore",
        "OddsFlag",
        "Source",
        "StrategyGate",
        "ResultStatus",
        "StartTimeLocal",
        "HomeScore",
        "AwayScore",
        "PickOutcome",
        "EntryOutcome",
        "ResultSource",
        "ResultNote",
        "OneXBetEventId",
        "OneXBetCanonicalId",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _table(rows: List[Dict[str, Any]], limit: int = 30) -> List[str]:
    lines = [
        "| # | Match | Pick | Official | Status | Score | Outcome | Decision | Source |",
        "| ---: | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows[:limit]:
        score = ""
        if row.get("HomeScore") not in (None, "") and row.get("AwayScore") not in (None, ""):
            score = f"{row.get('HomeScore')}-{row.get('AwayScore')}"
        lines.append(
            f"| {row.get('Rank')} | {row.get('Home')} vs {row.get('Away')} | {row.get('Pick')} | "
            f"{row.get('OfficialEntry')} | {row.get('ResultStatus')} | {score} | {row.get('PickOutcome')} | "
            f"{row.get('ValueVerdict')} | {row.get('ResultSource')} |"
        )
    if not rows:
        lines.append("| - | - | - | - | - | - | - | - | - |")
    return lines


def _write_md(rows: List[Dict[str, Any]], target: date, advisor_csv: Path, path: Path) -> None:
    official = [r for r in rows if r["OfficialEntry"] == "yes"]
    official_final = [r for r in official if r["PickOutcome"] in {"CORRECT", "WRONG"}]
    raw_final = [r for r in rows if r["PickOutcome"] in {"CORRECT", "WRONG"}]
    pending = [r for r in rows if r["PickOutcome"] == "PENDING"]
    needs_source = [r for r in rows if r["ResultStatus"] == "RESULT_SOURCE_REQUIRED"]
    correct = sum(1 for r in raw_final if r["PickOutcome"] == "CORRECT")
    wrong = sum(1 for r in raw_final if r["PickOutcome"] == "WRONG")
    official_correct = sum(1 for r in official_final if r["PickOutcome"] == "CORRECT")
    official_wrong = sum(1 for r in official_final if r["PickOutcome"] == "WRONG")
    try:
        advisor_label = str(advisor_csv.resolve().relative_to(BASE_DIR))
    except Exception:
        advisor_label = str(advisor_csv)

    lines = [
        "# Prediction result check",
        f"- Date: {target.isoformat()}",
        f"- Checked at: {datetime.now(LOCAL_TZ).isoformat(timespec='seconds')}",
        f"- Advisor CSV: `{advisor_label}`",
        f"- Rows checked: {len(rows)}",
        f"- Official entries: {len(official)}",
        f"- Official finished: {len(official_final)} correct={official_correct} wrong={official_wrong}",
        f"- Raw finished: {len(raw_final)} correct={correct} wrong={wrong}",
        f"- Pending/not started/result needed: {len(pending)}",
        "",
        "## Official Entry Accounting",
        *_table(official_final),
        "",
        "## Finished Raw Picks",
        *_table(raw_final),
        "",
        "## Pending Or Not Started",
        *_table(pending),
        "",
        "## Needs Result Source",
        *_table(needs_source),
        "",
        "## Rule",
        "- Only rows with `OfficialEntry=yes` count as app entry performance.",
        "- Finished raw picks are kept for model learning, but they are not counted as an entered decision when stake was zero.",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _update_memory(rows: List[Dict[str, Any]], path: Path) -> None:
    finished = [r for r in rows if r.get("PickOutcome") in {"CORRECT", "WRONG"}]
    existing = _read_csv(path)
    by_key: Dict[Tuple[str, str, str, str, str], Dict[str, Any]] = {_row_key(r): r for r in existing}
    for row in finished:
        by_key[_row_key(row)] = row
    if not by_key:
        # Keep a real CSV header so health checks can distinguish "empty memory"
        # from "memory pipeline never ran".
        _write_csv([], path)
        return
    ordered = sorted(by_key.values(), key=lambda r: (_row_key(r), str(r.get("CheckedAt") or "")))
    _write_csv(ordered, path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check saved daily prediction results.")
    parser.add_argument("--date", default="today")
    parser.add_argument("--advisor-csv", default="")
    parser.add_argument("--manual-odds", default=str(DEFAULT_MANUAL_ODDS))
    parser.add_argument("--manual-results", default=str(DEFAULT_MANUAL_RESULTS))
    parser.add_argument("--out-csv", default="")
    parser.add_argument("--out-md", default="")
    parser.add_argument("--memory-csv", default=str(DEFAULT_MEMORY))
    parser.add_argument("--timeout", type=int, default=8)
    parser.add_argument(
        "--skip-strategy-lab-results",
        action="store_true",
        help="Do not poll result APIs for watch-only strategy-lab rows; keep them deferred until a local model is promoted.",
    )
    parser.add_argument(
        "--manual-only",
        action="store_true",
        help="Only grade rows found in manual_results.csv; do not poll external result APIs.",
    )
    parser.add_argument("--no-update-memory", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    target = _target_date(args.date)
    advisor_csv = Path(args.advisor_csv) if args.advisor_csv else _advisor_csv_for_date(target)
    rows = _read_csv(advisor_csv)
    if not rows:
        print(f"No advisor rows found: {advisor_csv}")
        return 1

    manual_results = _load_manual_results(Path(args.manual_results))
    odds_meta = _load_manual_odds_meta(Path(args.manual_odds))
    checked_at = datetime.now(LOCAL_TZ).isoformat(timespec="seconds")
    now = datetime.now(LOCAL_TZ)
    out_rows: List[Dict[str, Any]] = []
    for row in rows:
        meta = odds_meta.get(_row_key(row), {})
        status: Dict[str, Any]
        manual = manual_results.get(_match_key(row))
        scheduled = _scheduled_not_started_status(row, target, now)
        if manual:
            status = _manual_result_status(row, manual)
        elif scheduled:
            status = scheduled
        elif args.manual_only:
            status = _manual_only_missing_status(row, target)
        elif args.skip_strategy_lab_results and str(row.get("StrategyGate") or "").startswith("WATCH_ONLY"):
            status = {
                "ResultStatus": "STRATEGY_LAB_RESULT_DEFERRED",
                "ResultSource": "strategy_lab_guard",
                "ResultNote": "Watch-only sport; result polling deferred until local model/backtest/context gate exists.",
            }
        else:
            event = _event_from_row(row, meta)
            if event is not None:
                status = _api_status(row, event, args.timeout, now)
            else:
                status = {
                    "ResultStatus": "NO_EVENT_ID",
                    "ResultSource": "advisor_csv",
                    "ResultNote": "No 1xBet event id and no manual result.",
                }

        home_score = _as_float(status.get("HomeScore"))
        away_score = _as_float(status.get("AwayScore"))
        outcome = _outcome(row, home_score, away_score)
        official = _is_official_entry(row)
        out = {
            **row,
            **status,
            "CheckedAt": checked_at,
            "OfficialEntry": "yes" if official else "no",
            "PickOutcome": outcome,
            "EntryOutcome": outcome if official else "NOT_OFFICIAL_ENTRY",
            "OneXBetEventId": row.get("OneXBetEventId") or row.get("OneXBetManualEventId") or meta.get("event_id") or "",
            "OneXBetCanonicalId": row.get("OneXBetCanonicalId") or row.get("OneXBetManualCanonicalId") or meta.get("canonical_id") or "",
        }
        out_rows.append(out)

    out_csv = Path(args.out_csv) if args.out_csv else REPORTS_DIR / f"prediction_results_{target.isoformat()}.csv"
    out_md = Path(args.out_md) if args.out_md else REPORTS_DIR / f"prediction_results_{target.isoformat()}.md"
    _write_csv(out_rows, out_csv)
    _write_md(out_rows, target, advisor_csv, out_md)
    if not args.no_update_memory:
        _update_memory(out_rows, Path(args.memory_csv))

    official_entries = sum(1 for r in out_rows if r["OfficialEntry"] == "yes")
    raw_finished = sum(1 for r in out_rows if r["PickOutcome"] in {"CORRECT", "WRONG"})
    print(f"Wrote {out_csv}")
    print(f"Wrote {out_md}")
    print(f"official_entries={official_entries} raw_finished={raw_finished} rows={len(out_rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
