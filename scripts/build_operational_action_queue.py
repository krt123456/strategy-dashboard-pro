#!/usr/bin/env python3
"""Build a ranked action queue from all daily decision reports."""
from __future__ import annotations

import argparse
import csv
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Tuple
from zoneinfo import ZoneInfo

BASE_DIR = Path(__file__).resolve().parents[1]
REPORTS_DIR = BASE_DIR / "reports"
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


def _as_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except Exception:
        return None


def _parse_dt(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw)
        return dt if dt.tzinfo else dt.replace(tzinfo=LOCAL_TZ)
    except Exception:
        return None


def _add_price_actions(rows: List[Dict[str, Any]], out: List[Dict[str, Any]]) -> None:
    priority_map = {
        "HIGH_NEAR_TARGET": 20,
        "MEDIUM_NEAR_TARGET": 35,
        "LOW_WAIT_PRICE_MOVE": 65,
        "RECHECK_PRICE_AGE": 25,
        "LOW_UNCONFIRMED_EVENT": 75,
    }
    for row in rows:
        priority = str(row.get("Priority") or "")
        action = "WATCH_PRICE"
        if priority == "LOW_UNCONFIRMED_EVENT":
            action = "CONFIRM_EVENT_BEFORE_PRICE_WATCH"
        elif priority == "RECHECK_PRICE_AGE":
            action = "REFRESH_1XBET_PRICE"
        out.append(
            {
                "PriorityScore": priority_map.get(priority, 70),
                "Action": action,
                "Sport": row.get("Sport"),
                "Match": f"{row.get('Home')} vs {row.get('Away')}",
                "Pick": row.get("Pick"),
                "DueLocal": row.get("StartTimeLocal") or "",
                "Reason": f"current={row.get('CurrentOdds')} target={row.get('TargetOdds')} need={row.get('NeededOddsIncreasePct')}%",
                "SourceReport": "price_target_watchlist",
            }
        )


def _add_result_actions(rows: List[Dict[str, Any]], out: List[Dict[str, Any]]) -> None:
    now = datetime.now(LOCAL_TZ)
    for row in rows:
        action = str(row.get("RecheckAction") or "")
        if action == "DONE":
            continue
        due = _parse_dt(row.get("RecheckAfterLocal"))
        due_label = row.get("RecheckAfterLocal") or ""
        if action == "RECHECK_NOW":
            priority = 10
        elif action == "RECHECK_AFTER_FINISH" and due and due <= now:
            priority = 12
            action = "RECHECK_NOW"
        elif action == "RECHECK_AFTER_FINISH":
            priority = 45
        elif action == "SOURCE_OR_EVENT_ID_REQUIRED":
            priority = 80
        else:
            priority = 60
        out.append(
            {
                "PriorityScore": priority,
                "Action": action,
                "Sport": row.get("Sport"),
                "Match": f"{row.get('Home')} vs {row.get('Away')}",
                "Pick": row.get("Pick"),
                "DueLocal": due_label,
                "Reason": f"result_status={row.get('ResultStatus')} source={row.get('ResultSource')}",
                "SourceReport": "result_recheck_schedule",
            }
        )


def _add_source_actions(rows: List[Dict[str, Any]], out: List[Dict[str, Any]]) -> None:
    for row in rows:
        grade = str(row.get("CoverageGrade") or "")
        if grade == "A_DATA_COMPLETE":
            continue
        priority = 55 if grade == "C_SOURCE_GAPS" else 40
        out.append(
            {
                "PriorityScore": priority,
                "Action": "IMPROVE_SOURCE_COVERAGE",
                "Sport": row.get("Sport"),
                "Match": f"{row.get('Home')} vs {row.get('Away')}",
                "Pick": row.get("Pick"),
                "DueLocal": "",
                "Reason": f"grade={grade} missing={row.get('MissingCoverage')}",
                "SourceReport": "source_coverage",
            }
        )


def _add_guard_actions(rows: List[Dict[str, Any]], out: List[Dict[str, Any]]) -> None:
    priority_map = {
        "APPROVED_FOR_HUMAN_REVIEW": 5,
        "RECHECK_RESULT_STATUS": 10,
        "ODDS_FLOOR_MANUAL_CHECK": 18,
        "RECHECK_1XBET_PRICE": 20,
        "WATCH_PRICE_ONLY": 32,
        "IMPROVE_SOURCE_COVERAGE": 38,
        "STRATEGY_LAB_ONLY": 70,
    }
    action_map = {
        "APPROVED_FOR_HUMAN_REVIEW": "FINAL_REVIEW_BEFORE_ENTRY",
        "RECHECK_RESULT_STATUS": "RECHECK_NOW",
        "ODDS_FLOOR_MANUAL_CHECK": "CONFIRM_1XBET_ODDS_FLOOR",
        "RECHECK_1XBET_PRICE": "REFRESH_1XBET_PRICE",
        "WATCH_PRICE_ONLY": "WATCH_PRICE",
        "IMPROVE_SOURCE_COVERAGE": "IMPROVE_SOURCE_COVERAGE",
        "STRATEGY_LAB_ONLY": "BUILD_SPORT_STRATEGY_MODEL",
    }
    for row in rows:
        decision = str(row.get("FinalDecision") or "")
        if decision not in priority_map:
            continue
        priority = priority_map[decision]
        if decision == "WATCH_PRICE_ONLY":
            need_pct = _as_float(row.get("DistanceToTargetPct"))
            if need_pct is None:
                current = _as_float(row.get("CurrentOdds"))
                target = _as_float(row.get("TargetOdds"))
                if current and target:
                    need_pct = ((target / current) - 1.0) * 100.0
            if need_pct is not None:
                if need_pct <= 1.0:
                    priority = 20
                elif need_pct <= 3.0:
                    priority = 32
                else:
                    priority = 35
        out.append(
            {
                "PriorityScore": priority,
                "Action": action_map[decision],
                "Sport": row.get("Sport"),
                "Match": f"{row.get('Home')} vs {row.get('Away')}",
                "Pick": row.get("Pick"),
                "DueLocal": row.get("StartTimeLocal") or "",
                "Reason": f"guard={decision} score={row.get('GuardScore')} vetoes={row.get('HardVetoes')}",
                "SourceReport": "final_decision_guard",
            }
        )


def _add_context_actions(rows: List[Dict[str, Any]], out: List[Dict[str, Any]]) -> None:
    for row in rows:
        sport = str(row.get("Sport") or "")
        gate = str(row.get("ContextGate") or "")
        if not gate.startswith("BLOCKED"):
            continue
        priority = _as_float(row.get("PriorityScore"))
        if priority is None:
            priority = 44 if sport == "tennis" else 48
        out.append(
            {
                "PriorityScore": int(priority),
                "Action": f"COLLECT_{sport.upper()}_CONTEXT",
                "Sport": sport,
                "Match": row.get("Match"),
                "Pick": row.get("Pick"),
                "DueLocal": "",
                "Reason": f"gate={gate} missing={row.get('MissingContext')} target={row.get('TargetOverrideFile')}",
                "SourceReport": "context_collection_worklist",
            }
        )


def _dedupe(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen: set[Tuple[str, str, str]] = set()
    for row in rows:
        key = (
            str(row.get("Action") or ""),
            str(row.get("Match") or ""),
            str(row.get("Pick") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def _build(target: date) -> List[Dict[str, Any]]:
    actions: List[Dict[str, Any]] = []
    _add_guard_actions(_read_csv(REPORTS_DIR / f"final_decision_guard_{target.isoformat()}.csv"), actions)
    _add_context_actions(_read_csv(REPORTS_DIR / f"context_collection_worklist_{target.isoformat()}.csv"), actions)
    _add_price_actions(_read_csv(REPORTS_DIR / f"price_target_watchlist_{target.isoformat()}.csv"), actions)
    _add_result_actions(_read_csv(REPORTS_DIR / f"result_recheck_schedule_{target.isoformat()}.csv"), actions)
    _add_source_actions(_read_csv(REPORTS_DIR / f"source_coverage_{target.isoformat()}.csv"), actions)
    actions.sort(key=lambda r: (int(r["PriorityScore"]), str(r.get("DueLocal") or "9999"), str(r.get("Match") or "")))
    return _dedupe(actions)


def _write_csv(rows: List[Dict[str, Any]], path: Path) -> None:
    fields = ["PriorityScore", "Action", "Sport", "Match", "Pick", "DueLocal", "Reason", "SourceReport"]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _write_md(rows: List[Dict[str, Any]], target: date, path: Path) -> None:
    lines = [
        "# Operational action queue",
        f"- Date: {target.isoformat()}",
        f"- Actions: {len(rows)}",
        "- Lower priority score means do it earlier.",
        "",
        "| # | Priority | Action | Match | Pick | Due | Reason |",
        "| ---: | ---: | --- | --- | --- | --- | --- |",
    ]
    for idx, row in enumerate(rows[:100], start=1):
        lines.append(
            f"| {idx} | {row.get('PriorityScore')} | {row.get('Action')} | {row.get('Match')} | "
            f"{row.get('Pick')} | {row.get('DueLocal')} | {row.get('Reason')} |"
        )
    if not rows:
        lines.append("| - | - | - | - | - | - | - |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build operational action queue.")
    parser.add_argument("--date", default="today")
    parser.add_argument("--out-csv", default="")
    parser.add_argument("--out-md", default="")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    target = _target_date(args.date)
    rows = _build(target)
    out_csv = Path(args.out_csv) if args.out_csv else REPORTS_DIR / f"operational_action_queue_{target.isoformat()}.csv"
    out_md = Path(args.out_md) if args.out_md else REPORTS_DIR / f"operational_action_queue_{target.isoformat()}.md"
    _write_csv(rows, out_csv)
    _write_md(rows, target, out_md)
    print(f"Wrote {out_csv}")
    print(f"Wrote {out_md}")
    print(f"actions={len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
