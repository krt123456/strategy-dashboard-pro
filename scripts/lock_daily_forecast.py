#!/usr/bin/env python3
"""Lock the daily forecast so tomorrow's result check compares against it."""
from __future__ import annotations

import argparse
import csv
import shutil
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple
from zoneinfo import ZoneInfo

BASE_DIR = Path(__file__).resolve().parents[1]
REPORTS_DIR = BASE_DIR / "reports"
LOCK_DIR = REPORTS_DIR / "locked_forecasts"
HISTORY_CSV = BASE_DIR / "data" / "locked_forecast_history.csv"
LOCAL_TZ = ZoneInfo("Africa/Algiers")


def _target_date(value: str) -> date:
    raw = (value or "today").strip().lower()
    today = date.today()
    if raw in {"today", "اليوم"}:
        return today
    if raw in {"tomorrow", "غدا", "غداً"}:
        return today + timedelta(days=1)
    return date.fromisoformat(raw)


def _read_csv(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _write_csv(rows: Iterable[Dict[str, Any]], path: Path, fields: List[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _key(row: Dict[str, Any]) -> Tuple[str, str, str, str, str]:
    return (
        str(row.get("Date") or "").strip()[:10],
        str(row.get("Sport") or "").strip().lower(),
        str(row.get("Home") or "").strip().lower(),
        str(row.get("Away") or "").strip().lower(),
        str(row.get("Pick") or "").strip().lower(),
    )


def _map_rows(rows: Iterable[Dict[str, Any]]) -> Dict[Tuple[str, str, str, str, str], Dict[str, Any]]:
    return {_key(row): row for row in rows}


def _as_float(value: Any) -> float:
    try:
        return float(value)
    except Exception:
        return 0.0


def _as_int(value: Any) -> int:
    try:
        return int(float(value))
    except Exception:
        return 0


def _lock_class(decision: str) -> str:
    return {
        "APPROVED_FOR_HUMAN_REVIEW": "REVIEW_CANDIDATE_LOCKED",
        "WATCH_PRICE_ONLY": "PRICE_WATCH_LOCKED",
        "ODDS_FLOOR_MANUAL_CHECK": "SOURCE_REPAIR_LOCKED",
        "IMPROVE_SOURCE_COVERAGE": "SOURCE_GAP_FORECAST_LOCKED",
        "RECHECK_1XBET_PRICE": "PRICE_RECHECK_LOCKED",
        "RECHECK_RESULT_STATUS": "RESULT_RECHECK_LOCKED",
        "STRATEGY_LAB_ONLY": "LAB_FORECAST_LOCKED",
        "NO_ENTRY": "NO_ENTRY_FORECAST_LOCKED",
    }.get(decision or "", "RAW_FORECAST_LOCKED")


def _learning_priority(row: Dict[str, Any]) -> int:
    decision = str(row.get("FinalDecision") or "")
    if decision == "APPROVED_FOR_HUMAN_REVIEW":
        return 100
    if decision == "WATCH_PRICE_ONLY":
        return 80
    if decision in {"ODDS_FLOOR_MANUAL_CHECK", "IMPROVE_SOURCE_COVERAGE"}:
        return 70
    if decision == "STRATEGY_LAB_ONLY":
        return 55
    return 35


def _build(target: date) -> List[Dict[str, Any]]:
    day = target.isoformat()
    locked_at = datetime.now(LOCAL_TZ).isoformat(timespec="seconds")
    evaluation_date = (target + timedelta(days=1)).isoformat()
    advisor = _read_csv(REPORTS_DIR / f"daily_1xbet_value_advisor_{day}.csv")
    guard = _map_rows(_read_csv(REPORTS_DIR / f"final_decision_guard_{day}.csv"))
    results = _map_rows(_read_csv(REPORTS_DIR / f"prediction_results_{day}.csv"))
    rechecks = _map_rows(_read_csv(REPORTS_DIR / f"result_recheck_schedule_{day}.csv"))

    rows: List[Dict[str, Any]] = []
    for row in advisor:
        guard_row = guard.get(_key(row), {})
        result_row = results.get(_key(row), {})
        recheck_row = rechecks.get(_key(row), {})
        decision = str(guard_row.get("FinalDecision") or "")
        official = "yes" if decision == "APPROVED_FOR_HUMAN_REVIEW" else "no"
        current_odds = guard_row.get("CurrentOdds") or row.get("PickOdds") or ""
        out = {
            "ForecastDate": day,
            "LockedAt": locked_at,
            "EvaluationDate": evaluation_date,
            "Rank": row.get("Rank") or "",
            "Sport": row.get("Sport") or "",
            "League": row.get("League") or "",
            "Home": row.get("Home") or "",
            "Away": row.get("Away") or "",
            "Pick": row.get("Pick") or "",
            "Prob": row.get("Prob") or "",
            "CurrentOdds": current_odds,
            "TargetOdds": guard_row.get("TargetOdds") or row.get("MinEntryOdds") or "",
            "EVPercent": guard_row.get("EVPercent") or row.get("EVPercent") or "",
            "ValueVerdict": row.get("ValueVerdict") or "",
            "EntryReadiness": row.get("EntryReadiness") or "",
            "FinalDecision": decision,
            "DecisionClass": guard_row.get("DecisionClass") or "",
            "LockClass": _lock_class(decision),
            "OfficialEntry": official,
            "GuardScore": guard_row.get("GuardScore") or "",
            "GuardLevel": guard_row.get("GuardLevel") or "",
            "HardVetoes": guard_row.get("HardVetoes") or "",
            "SoftWarnings": guard_row.get("SoftWarnings") or "",
            "OneXBetStatus": guard_row.get("OneXBetStatus") or row.get("OneXBetStatus") or "",
            "OneXBetFreshness": guard_row.get("OneXBetFreshness") or row.get("OneXBetOddsFreshness") or "",
            "EventTimingStatus": guard_row.get("EventTimingStatus") or row.get("EventTimingStatus") or "",
            "StartTimeLocal": result_row.get("StartTimeLocal") or recheck_row.get("StartTimeLocal") or "",
            "ResultStatusAtLock": result_row.get("ResultStatus") or "",
            "RecheckAction": recheck_row.get("RecheckAction") or "",
            "RecheckAfterLocal": recheck_row.get("RecheckAfterLocal") or "",
            "StrategyGate": row.get("StrategyGate") or guard_row.get("StrategyGate") or "",
            "MemorySignal": guard_row.get("MemorySignal") or "",
            "MemoryAccuracy": guard_row.get("MemoryAccuracy") or "",
            "LearningPriority": _learning_priority(guard_row),
            "ForecastPurpose": "LEARN_TOMORROW_RESULT" if official == "no" else "REVIEW_CANDIDATE_THEN_LEARN",
            "NextReviewRule": "Compare result tomorrow; promote only segments that beat sample gate without breaking source/value gates.",
        }
        rows.append(out)

    rows.sort(
        key=lambda r: (
            -_as_int(r.get("LearningPriority")),
            -_as_int(r.get("GuardScore")),
            -_as_float(r.get("Prob")),
            _as_int(r.get("Rank")) or 999999,
        )
    )
    return rows


FIELDS = [
    "ForecastDate",
    "LockedAt",
    "EvaluationDate",
    "Rank",
    "Sport",
    "League",
    "Home",
    "Away",
    "Pick",
    "Prob",
    "CurrentOdds",
    "TargetOdds",
    "EVPercent",
    "ValueVerdict",
    "EntryReadiness",
    "FinalDecision",
    "DecisionClass",
    "LockClass",
    "OfficialEntry",
    "GuardScore",
    "GuardLevel",
    "HardVetoes",
    "SoftWarnings",
    "OneXBetStatus",
    "OneXBetFreshness",
    "EventTimingStatus",
    "StartTimeLocal",
    "ResultStatusAtLock",
    "RecheckAction",
    "RecheckAfterLocal",
    "StrategyGate",
    "MemorySignal",
    "MemoryAccuracy",
    "LearningPriority",
    "ForecastPurpose",
    "NextReviewRule",
]


def _upsert_history(rows: List[Dict[str, Any]], path: Path) -> None:
    if not rows:
        return
    day = str(rows[0].get("ForecastDate") or "")
    existing = [r for r in _read_csv(path) if str(r.get("ForecastDate") or "") != day]
    _write_csv(existing + rows, path, FIELDS)


def _backup_if_exists(path: Path) -> None:
    if not path.exists() or path.stat().st_size <= 0:
        return
    stamp = datetime.now(LOCAL_TZ).strftime("%Y%m%d_%H%M%S")
    backup = path.with_name(f"{path.stem}.bak_{stamp}{path.suffix}")
    shutil.copy2(path, backup)


def _write_md(rows: List[Dict[str, Any]], target: date, path: Path) -> None:
    counts = Counter(str(r.get("LockClass") or "EMPTY") for r in rows)
    decisions = Counter(str(r.get("FinalDecision") or "EMPTY") for r in rows)
    sports = Counter(str(r.get("Sport") or "EMPTY") for r in rows)
    official = sum(1 for r in rows if str(r.get("OfficialEntry") or "") == "yes")

    lines = [
        "# Locked daily forecast",
        f"- Forecast date: {target.isoformat()}",
        f"- Evaluation date: {(target + timedelta(days=1)).isoformat()}",
        f"- Rows locked: {len(rows)}",
        f"- Official review entries: {official}",
        "- Rule: locked rows are saved for tomorrow's result check; they are not proof of entry unless `OfficialEntry=yes`.",
        "",
        "## Lock Classes",
        *([f"- {key}: {value}" for key, value in counts.most_common()] or ["- none: 0"]),
        "",
        "## Final Decisions",
        *([f"- {key}: {value}" for key, value in decisions.most_common()] or ["- none: 0"]),
        "",
        "## Sports",
        *([f"- {key}: {value}" for key, value in sports.most_common()] or ["- none: 0"]),
        "",
        "## Top Saved Forecasts",
        "| # | Class | Sport | Match | Pick | Prob | Odds | Decision | Vetoes |",
        "| ---: | --- | --- | --- | --- | ---: | ---: | --- | --- |",
    ]
    for idx, row in enumerate(rows[:40], start=1):
        lines.append(
            f"| {idx} | {row.get('LockClass')} | {row.get('Sport')} | {row.get('Home')} vs {row.get('Away')} | "
            f"{row.get('Pick')} | {row.get('Prob')} | {row.get('CurrentOdds')} | {row.get('FinalDecision')} | "
            f"{row.get('HardVetoes')} |"
        )
    if not rows:
        lines.append("| - | - | - | - | - | - | - | - | - |")

    lines.extend(
        [
            "",
            "## Tomorrow Review Protocol",
            "- Run `check_prediction_results.py --date {}` after matches finish.".format(target.isoformat()),
            "- Compare `PickOutcome` against this locked file, not against a later regenerated ranking.",
            "- Strengthen or promote a sport only after enough locked rows finish with stable source and value gates.",
            "- Keep strategy-lab rows out of official entry until their context model and backtest gate pass.",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Lock daily forecasts for tomorrow's learning loop.")
    parser.add_argument("--date", default="today")
    parser.add_argument("--out-csv", default="")
    parser.add_argument("--out-md", default="")
    parser.add_argument("--history-csv", default=str(HISTORY_CSV))
    parser.add_argument("--no-history", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    target = _target_date(args.date)
    rows = _build(target)
    out_csv = Path(args.out_csv) if args.out_csv else LOCK_DIR / f"forecast_lock_{target.isoformat()}.csv"
    out_md = Path(args.out_md) if args.out_md else LOCK_DIR / f"forecast_lock_{target.isoformat()}.md"
    _backup_if_exists(out_csv)
    _backup_if_exists(out_md)
    _write_csv(rows, out_csv, FIELDS)
    _write_md(rows, target, out_md)
    if not args.no_history:
        _upsert_history(rows, Path(args.history_csv))
    counts = Counter(str(row.get("LockClass") or "EMPTY") for row in rows)
    print(f"Wrote {out_csv}")
    print(f"Wrote {out_md}")
    if not args.no_history:
        print(f"Wrote {args.history_csv}")
    print("locked=" + " ".join(f"{key}={value}" for key, value in counts.most_common()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
