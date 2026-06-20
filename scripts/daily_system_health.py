#!/usr/bin/env python3
"""Daily operational health report for the 1xBet strategy advisor."""
from __future__ import annotations

import argparse
import csv
import re
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple
from zoneinfo import ZoneInfo

BASE_DIR = Path(__file__).resolve().parents[1]
REPORTS_DIR = BASE_DIR / "reports"
LOCAL_TZ = ZoneInfo("Africa/Algiers")

try:
    from one_xbet_status import is_confirmed_1xbet_status
except Exception:  # pragma: no cover
    def is_confirmed_1xbet_status(value: object) -> bool:
        return str(value or "") in {"AUTO_MATCHED", "PUBLIC_ODDS_CONFIRMED"}


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


def _safe_int(value: Any) -> int:
    try:
        return int(float(value))
    except Exception:
        return 0


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except Exception:
        return 0.0


def _count(rows: Iterable[Dict[str, Any]], field: str) -> Counter[str]:
    return Counter(str(r.get(field) or "EMPTY") for r in rows)


def _blocker_counter(rows: Iterable[Dict[str, Any]]) -> Counter[str]:
    out: Counter[str] = Counter()
    for row in rows:
        for blocker in str(row.get("GateBlockers") or "").split(";"):
            blocker = blocker.strip()
            if blocker and blocker != "none":
                out[blocker] += 1
    return out


def _parse_daily_pick_md(path: Path) -> Dict[str, int]:
    metrics = {
        "football_pool_before": 0,
        "football_pool_after": 0,
        "draw_trap_rejections": 0,
        "stale_history_leagues": 0,
    }
    if not path.exists():
        return metrics
    text = path.read_text(encoding="utf-8", errors="ignore")
    patterns = {
        "football_pool_before": r"Qualifying pool before safety:\s*(\d+)",
        "football_pool_after": r"Qualifying pool after safety:\s*(\d+)",
        "draw_trap_rejections": r"Draw-trap safety rejections:\s*(\d+)",
        "stale_history_leagues": r"Leagues skipped for stale history:\s*(\d+)",
    }
    for key, pattern in patterns.items():
        match = re.search(pattern, text)
        if match:
            metrics[key] = int(match.group(1))
    return metrics


def _file_status(path: Path) -> str:
    if not path.exists():
        return "MISSING"
    if path.stat().st_size <= 0:
        return "EMPTY"
    return "OK"


def _relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(BASE_DIR))
    except Exception:
        return str(path)


def _advisor_metrics(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    enter = [r for r in rows if str(r.get("ValueVerdict") or "").startswith("ENTER")]
    recheck = [r for r in rows if str(r.get("ValueVerdict") or "").startswith("RECHECK")]
    price_target = [r for r in rows if str(r.get("EntryReadiness") or "") == "PRICE_TARGET_ONLY"]
    manual = [r for r in rows if str(r.get("OneXBetStatus") or "").startswith("NEEDS")]
    confirmed = [r for r in rows if is_confirmed_1xbet_status(r.get("OneXBetStatus"))]
    fresh = [r for r in rows if str(r.get("OneXBetOddsFreshness") or "") == "FRESH"]
    stale = [r for r in rows if str(r.get("OneXBetOddsFreshness") or "") == "STALE"]
    missing_price = [r for r in rows if "missing_price_or_probability" in str(r.get("GateBlockers") or "")]
    return {
        "advisor_rows": len(rows),
        "enter_candidates": len(enter),
        "recheck_blocked": len(recheck),
        "price_target_only": len(price_target),
        "manual_1xbet_needed": len(manual),
        "confirmed_1xbet_events": len(confirmed),
        "fresh_1xbet_prices": len(fresh),
        "stale_1xbet_prices": len(stale),
        "missing_price_rows": len(missing_price),
        "value_verdict_counts": _count(rows, "ValueVerdict"),
        "readiness_counts": _count(rows, "EntryReadiness"),
        "blockers": _blocker_counter(rows),
    }


def _result_metrics(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    official = [r for r in rows if str(r.get("OfficialEntry") or "").lower() == "yes"]
    finished = [r for r in rows if str(r.get("PickOutcome") or "") in {"CORRECT", "WRONG"}]
    pending = [r for r in rows if str(r.get("PickOutcome") or "") == "PENDING"]
    return {
        "result_rows": len(rows),
        "official_entries": len(official),
        "finished_raw": len(finished),
        "pending_results": len(pending),
        "result_status_counts": _count(rows, "ResultStatus"),
        "outcome_counts": _count(rows, "PickOutcome"),
    }


def _memory_metrics(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    finished = [r for r in rows if str(r.get("PickOutcome") or "") in {"CORRECT", "WRONG"}]
    official = [r for r in finished if str(r.get("OfficialEntry") or "").lower() == "yes"]
    correct = [r for r in finished if str(r.get("PickOutcome") or "") == "CORRECT"]
    return {
        "memory_finished_rows": len(finished),
        "memory_official_finished": len(official),
        "memory_raw_accuracy": (len(correct) / len(finished)) if finished else None,
    }


def _action_metrics(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    first = rows[0] if rows else {}
    return {
        "action_queue_rows": len(rows),
        "top_action": first.get("Action") or "",
        "top_action_priority": _safe_int(first.get("PriorityScore")),
        "top_action_match": first.get("Match") or "",
    }


def _context_metrics(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    blocked = [row for row in rows if str(row.get("ContextGate") or "").startswith("BLOCKED")]
    sports = _count(rows, "Sport")
    gates = _count(rows, "ContextGate")
    return {
        "context_worklist_rows": len(rows),
        "context_blocked_rows": len(blocked),
        "context_sport_counts": sports,
        "context_gate_counts": gates,
    }


def _context_completion_metrics(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    incomplete = [row for row in rows if str(row.get("Status") or "") != "COMPLETE_READY_FOR_GATE_REBUILD"]
    bad_names = [row for row in rows if str(row.get("Status") or "") == "BAD_MATCH_NAME"]
    return {
        "context_override_rows": len(rows),
        "context_override_incomplete_rows": len(incomplete),
        "context_override_bad_name_rows": len(bad_names),
        "context_override_status_counts": _count(rows, "Status"),
    }


def _guard_metrics(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    first = rows[0] if rows else {}
    decisions = _count(rows, "FinalDecision")
    return {
        "decision_guard_rows": len(rows),
        "guard_approved_review": decisions.get("APPROVED_FOR_HUMAN_REVIEW", 0),
        "guard_odds_floor_manual_check": decisions.get("ODDS_FLOOR_MANUAL_CHECK", 0),
        "guard_watch_price": decisions.get("WATCH_PRICE_ONLY", 0),
        "guard_recheck_price": decisions.get("RECHECK_1XBET_PRICE", 0),
        "guard_improve_source": decisions.get("IMPROVE_SOURCE_COVERAGE", 0),
        "guard_no_entry": decisions.get("NO_ENTRY", 0),
        "guard_decision_counts": decisions,
        "top_guard_decision": first.get("FinalDecision") or "",
        "top_guard_match": f"{first.get('Home')} vs {first.get('Away')}" if first else "",
        "top_guard_score": _safe_int(first.get("GuardScore")),
    }


def _odds_floor_audit_metrics(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    statuses = _count(rows, "Status")
    return {
        "odds_floor_audit_rows": len(rows),
        "odds_floor_unresolved": statuses.get("UNCONFIRMED_NEEDS_REPAIR", 0),
        "odds_floor_approved_review": statuses.get("APPROVED_REVIEW", 0),
        "odds_floor_confirmed_blocked": statuses.get("CONFIRMED_BLOCKED_BY_GUARD", 0),
        "odds_floor_time_blocked": statuses.get("TIME_BLOCKED_NO_REPAIR", 0),
        "odds_floor_audit_counts": statuses,
    }


def _decision(metrics: Dict[str, Any]) -> Tuple[str, List[str]]:
    actions: List[str] = []
    if metrics.get("advisor_csv_status") != "OK" or metrics["advisor_rows"] <= 0:
        state = "PIPELINE_INCOMPLETE"
        actions.append("Rebuild the daily advisor report before reviewing any candidate.")
    elif metrics.get("results_csv_status") != "OK":
        state = "RESULT_TRACKER_MISSING"
        actions.append("Run the prediction result checker before reviewing performance or pending status.")
    elif metrics.get("odds_floor_unresolved", 0) > 0:
        state = "ODDS_FLOOR_REPAIR_REQUIRED"
        actions.append("Some 1.30+ rows are still unconfirmed; run public sync/linefeed snapshot before reviewing them.")
    elif metrics.get("decision_guard_csv_status") == "OK" and metrics.get("guard_approved_review", 0) > 0:
        state = "ENTRY_REVIEW_REQUIRED"
        actions.append("Final guard has reviewable candidates; verify live 1xBet price and independent sources before any action.")
    elif (
        metrics["enter_candidates"] > 0
        and metrics.get("decision_guard_csv_status") == "OK"
        and metrics.get("guard_approved_review", 0) <= 0
    ):
        state = "ENTRY_BLOCKED_BY_GUARD"
        actions.append("The model produced entry candidates, but the final guard blocked them; fix the exact guard vetoes first.")
    elif metrics["enter_candidates"] > 0:
        state = "ENTRY_REVIEW_REQUIRED"
        actions.append("Review entry candidates with current 1xBet price and independent sources before any action.")
    elif metrics["recheck_blocked"] > 0:
        state = "RECHECK_PRICES"
        actions.append("Refresh 1xBet prices for recheck-blocked candidates.")
    elif metrics["price_target_only"] > 0:
        state = "NO_ENTRY_PRICE_TOO_LOW"
        actions.append("Do not enter now; monitor only if 1xBet price reaches MinEntryOdds.")
    else:
        state = "NO_ENTRY"
        actions.append("No current candidate passes value and gate checks.")

    if metrics["stale_1xbet_prices"] > 0:
        actions.append("Refresh stale 1xBet prices before relying on any candidate.")
    if metrics["manual_1xbet_needed"] > metrics["confirmed_1xbet_events"]:
        actions.append("Improve 1xBet event matching or increase public odds sync coverage for unconfirmed events.")
    if metrics["missing_price_rows"] > 0:
        actions.append("Prioritize missing-price candidates only after a reliable price source is found.")
    if metrics["stale_history_leagues"] > 0:
        actions.append("Football has stale-history skipped leagues; keep those leagues excluded until result data refresh succeeds.")
    if metrics["memory_finished_rows"] < 10:
        actions.append("Prediction memory is cold; keep thresholds conservative until at least 10 finished rows are recorded.")
    if metrics.get("context_blocked_rows", 0) > 0:
        actions.append(f"Sport context blocked rows: {metrics.get('context_blocked_rows')} need verified context before any sport promotion/backtest review.")
    if metrics.get("context_override_incomplete_rows", 0) > 0:
        actions.append(f"Context override rows still incomplete: {metrics.get('context_override_incomplete_rows')} must be filled before rebuilding sport gates.")
    if metrics.get("context_override_bad_name_rows", 0) > 0:
        actions.append(f"Bad/placeholder participant names: {metrics.get('context_override_bad_name_rows')} must be dropped or repaired upstream.")
    if metrics.get("decision_guard_csv_status") != "OK":
        actions.append("Build the final decision guard before giving a final daily verdict.")
    elif metrics.get("guard_watch_price", 0) > 0:
        actions.append(f"Final guard watch-only candidates: {metrics.get('guard_watch_price')} until price reaches target.")
    if metrics.get("guard_odds_floor_manual_check", 0) > 0 and metrics.get("odds_floor_unresolved", 0) > 0:
        actions.append(f"Odds-floor manual checks: {metrics.get('guard_odds_floor_manual_check')} candidates at or above 1.30 need 1xBet event/price confirmation.")
    elif metrics.get("guard_odds_floor_manual_check", 0) > 0 and metrics.get("odds_floor_time_blocked", 0) > 0:
        actions.append("Past/started odds-floor manual checks are time-blocked; do not repair live price for entry, only repair result/source history if needed.")
    if metrics.get("odds_floor_unresolved", 0) > 0:
        actions.append(f"Odds-floor audit unresolved rows: {metrics.get('odds_floor_unresolved')} need repair before review.")
    if metrics.get("action_queue_rows", 0) > 0:
        actions.append(f"Next queued action: {metrics.get('top_action')} for {metrics.get('top_action_match')}.")
    return state, actions


def _counter_lines(counter: Counter[str], limit: int = 12) -> List[str]:
    if not counter:
        return ["- none: 0"]
    return [f"- {key}: {value}" for key, value in counter.most_common(limit)]


def _write_md(metrics: Dict[str, Any], path: Path) -> None:
    lines = [
        "# Daily system health",
        f"- Date: {metrics['date']}",
        f"- Checked at: {metrics['checked_at']}",
        f"- Operational state: `{metrics['operational_state']}`",
        "",
        "## Core Metrics",
        f"- Advisor rows: {metrics['advisor_rows']}",
        f"- Enter candidates: {metrics['enter_candidates']}",
        f"- Recheck blocked: {metrics['recheck_blocked']}",
        f"- Price-target only: {metrics['price_target_only']}",
        f"- Confirmed 1xBet events: {metrics['confirmed_1xbet_events']}",
        f"- Need manual 1xBet/event check: {metrics['manual_1xbet_needed']}",
        f"- Fresh 1xBet prices: {metrics['fresh_1xbet_prices']}",
        f"- Stale 1xBet prices: {metrics['stale_1xbet_prices']}",
        f"- Missing price/probability rows: {metrics['missing_price_rows']}",
        f"- Official entries in result tracker: {metrics['official_entries']}",
        f"- Finished raw results in tracker: {metrics['finished_raw']}",
        f"- Prediction memory finished rows: {metrics['memory_finished_rows']}",
        f"- Final guard rows: {metrics['decision_guard_rows']}",
        f"- Final guard approved for review: {metrics['guard_approved_review']}",
        f"- Final guard 1.30+ manual checks: {metrics['guard_odds_floor_manual_check']}",
        f"- Final guard watch-price only: {metrics['guard_watch_price']}",
        f"- Final guard improve-source: {metrics['guard_improve_source']}",
        f"- Odds-floor audit rows: {metrics['odds_floor_audit_rows']}",
        f"- Odds-floor unresolved 1.30+: {metrics['odds_floor_unresolved']}",
        f"- Odds-floor approved review: {metrics['odds_floor_approved_review']}",
        f"- Odds-floor confirmed but blocked: {metrics['odds_floor_confirmed_blocked']}",
        f"- Odds-floor time blocked: {metrics['odds_floor_time_blocked']}",
        f"- Operational action queue rows: {metrics['action_queue_rows']}",
        f"- Context worklist rows: {metrics['context_worklist_rows']}",
        f"- Context blocked rows: {metrics['context_blocked_rows']}",
        f"- Context override rows: {metrics['context_override_rows']}",
        f"- Context override incomplete rows: {metrics['context_override_incomplete_rows']}",
        f"- Context override bad-name rows: {metrics['context_override_bad_name_rows']}",
        f"- Top queued action: {metrics['top_action']} | {metrics['top_action_match']}",
        f"- Top guard decision: {metrics['top_guard_decision']} | {metrics['top_guard_match']} | score={metrics['top_guard_score']}",
        f"- Football stale-history leagues skipped: {metrics['stale_history_leagues']}",
        "",
        "## Required Actions",
        *[f"- {item}" for item in metrics["actions"]],
        "",
        "## Gate Blockers",
        *_counter_lines(metrics["blockers"]),
        "",
        "## Entry Readiness",
        *_counter_lines(metrics["readiness_counts"]),
        "",
        "## Value Verdicts",
        *_counter_lines(metrics["value_verdict_counts"]),
        "",
        "## Final Guard Decisions",
        *_counter_lines(metrics["guard_decision_counts"]),
        "",
        "## Odds-Floor Audit",
        *_counter_lines(metrics["odds_floor_audit_counts"]),
        "",
        "## Sport Context Gates",
        *_counter_lines(metrics["context_gate_counts"]),
        "",
        "## Sport Context Worklist",
        *_counter_lines(metrics["context_sport_counts"]),
        "",
        "## Context Override Completion",
        *_counter_lines(metrics["context_override_status_counts"]),
        "",
        "## Result Status",
        *_counter_lines(metrics["result_status_counts"]),
        "",
        "## Files",
        f"- Advisor CSV: `{metrics['advisor_csv']}` ({metrics['advisor_csv_status']})",
        f"- Results CSV: `{metrics['results_csv']}` ({metrics['results_csv_status']})",
        f"- Memory CSV: `{metrics['memory_csv']}` ({metrics['memory_csv_status']})",
        f"- Final guard CSV: `{metrics['decision_guard_csv']}` ({metrics['decision_guard_csv_status']})",
        f"- Odds-floor audit CSV: `{metrics['odds_floor_audit_csv']}` ({metrics['odds_floor_audit_csv_status']})",
        f"- Action queue CSV: `{metrics['action_queue_csv']}` ({metrics['action_queue_csv_status']})",
        f"- Context worklist CSV: `{metrics['context_worklist_csv']}` ({metrics['context_worklist_csv_status']})",
        f"- Context completion CSV: `{metrics['context_completion_csv']}` ({metrics['context_completion_csv_status']})",
        f"- Football picks MD: `{metrics['football_picks_md']}` ({metrics['football_picks_md_status']})",
        "",
        "## Rule",
        "- This report is an operational gate. It does not create entries; it explains whether the daily pipeline is trustworthy enough to review candidates.",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_csv(metrics: Dict[str, Any], path: Path) -> None:
    flat = {
        k: v
        for k, v in metrics.items()
        if not isinstance(v, (Counter, list, dict))
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(flat.keys()))
        writer.writeheader()
        writer.writerow(flat)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build daily system health report.")
    parser.add_argument("--date", default="today")
    parser.add_argument("--out-md", default="")
    parser.add_argument("--out-csv", default="")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    target = _target_date(args.date)
    advisor_csv = REPORTS_DIR / f"daily_1xbet_value_advisor_{target.isoformat()}.csv"
    results_csv = REPORTS_DIR / f"prediction_results_{target.isoformat()}.csv"
    football_picks_md = REPORTS_DIR / f"daily_picks_{target.isoformat()}.md"
    memory_csv = BASE_DIR / "data" / "prediction_result_memory.csv"
    decision_guard_csv = REPORTS_DIR / f"final_decision_guard_{target.isoformat()}.csv"
    odds_floor_audit_csv = REPORTS_DIR / f"odds_floor_confirmation_audit_{target.isoformat()}.csv"
    action_queue_csv = REPORTS_DIR / f"operational_action_queue_{target.isoformat()}.csv"
    context_worklist_csv = REPORTS_DIR / f"context_collection_worklist_{target.isoformat()}.csv"
    context_completion_csv = REPORTS_DIR / f"context_override_completion_{target.isoformat()}.csv"

    advisor_rows = _read_csv(advisor_csv)
    result_rows = _read_csv(results_csv)
    memory_rows = _read_csv(memory_csv)
    metrics: Dict[str, Any] = {
        "date": target.isoformat(),
        "checked_at": datetime.now(LOCAL_TZ).isoformat(timespec="seconds"),
        "advisor_csv": _relative(advisor_csv),
        "advisor_csv_status": _file_status(advisor_csv),
        "results_csv": _relative(results_csv),
        "results_csv_status": _file_status(results_csv),
        "memory_csv": _relative(memory_csv),
        "memory_csv_status": _file_status(memory_csv),
        "decision_guard_csv": _relative(decision_guard_csv),
        "decision_guard_csv_status": _file_status(decision_guard_csv),
        "odds_floor_audit_csv": _relative(odds_floor_audit_csv),
        "odds_floor_audit_csv_status": _file_status(odds_floor_audit_csv),
        "football_picks_md": _relative(football_picks_md),
        "football_picks_md_status": _file_status(football_picks_md),
        "action_queue_csv": _relative(action_queue_csv),
        "action_queue_csv_status": _file_status(action_queue_csv),
        "context_worklist_csv": _relative(context_worklist_csv),
        "context_worklist_csv_status": _file_status(context_worklist_csv),
        "context_completion_csv": _relative(context_completion_csv),
        "context_completion_csv_status": _file_status(context_completion_csv),
    }
    metrics.update(_advisor_metrics(advisor_rows))
    metrics.update(_result_metrics(result_rows))
    metrics.update(_memory_metrics(memory_rows))
    metrics.update(_guard_metrics(_read_csv(decision_guard_csv)))
    metrics.update(_odds_floor_audit_metrics(_read_csv(odds_floor_audit_csv)))
    metrics.update(_action_metrics(_read_csv(action_queue_csv)))
    metrics.update(_context_metrics(_read_csv(context_worklist_csv)))
    metrics.update(_context_completion_metrics(_read_csv(context_completion_csv)))
    metrics.update(_parse_daily_pick_md(football_picks_md))
    state, actions = _decision(metrics)
    metrics["operational_state"] = state
    metrics["actions"] = actions

    out_md = Path(args.out_md) if args.out_md else REPORTS_DIR / f"daily_system_health_{target.isoformat()}.md"
    out_csv = Path(args.out_csv) if args.out_csv else REPORTS_DIR / f"daily_system_health_{target.isoformat()}.csv"
    _write_md(metrics, out_md)
    _write_csv(metrics, out_csv)
    print(f"Wrote {out_md}")
    print(f"Wrote {out_csv}")
    print(f"state={state} enter={metrics['enter_candidates']} price_target={metrics['price_target_only']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
