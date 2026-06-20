#!/usr/bin/env python3
"""Build a final decision guard for daily 1xBet candidates.

The guard is intentionally stricter than the model score. It converts every
candidate into an auditable operational decision before any human review.
"""
from __future__ import annotations

import argparse
import csv
import math
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Tuple

from sport_name_quality import participant_quality_flags

BASE_DIR = Path(__file__).resolve().parents[1]
REPORTS_DIR = BASE_DIR / "reports"

try:
    from one_xbet_status import is_confirmed_1xbet_status
except Exception:  # pragma: no cover
    def is_confirmed_1xbet_status(value: object) -> bool:
        return str(value or "") in {"AUTO_MATCHED", "PUBLIC_ODDS_CONFIRMED"}

PRICE_ONLY_VETOES = {"ev_below_minimum", "price_below_target"}
REMAINING_CONTEXT_SPORTS = {"hockey", "handball", "volleyball", "cricket", "americanfootball", "futsal", "darts", "snooker"}


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
        out = float(value)
        if math.isnan(out):
            return None
        return out
    except Exception:
        return None


def _as_int(value: Any) -> int:
    try:
        return int(float(value))
    except Exception:
        return 0


def _bucket_probability(value: Any) -> str:
    prob = _as_float(value)
    if prob is None:
        return "prob_unknown"
    if prob < 0.60:
        return "prob_<60"
    if prob < 0.70:
        return "prob_60_70"
    if prob < 0.80:
        return "prob_70_80"
    if prob < 0.90:
        return "prob_80_90"
    return "prob_90_plus"


def _bucket_odds(value: Any) -> str:
    odds = _as_float(value)
    if odds is None:
        return "odds_unknown"
    if odds <= 1.10:
        return "odds_<=1.10"
    if odds <= 1.30:
        return "odds_1.11_1.30"
    if odds <= 1.60:
        return "odds_1.31_1.60"
    if odds <= 2.00:
        return "odds_1.61_2.00"
    return "odds_>2.00"


def _gate_family(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw or raw.lower() == "nan":
        return "strategy_gate_unknown"
    return raw.split(":", 1)[0].strip() or "strategy_gate_unknown"


def _source_family(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw or raw.lower() == "nan":
        return "source_unknown"
    if raw.startswith("data/raw/"):
        parts = raw.split("/")
        return "/".join(parts[:3]) if len(parts) >= 3 else raw
    return raw.split("_sport_", 1)[0]


def _key(row: Dict[str, Any]) -> Tuple[str, str, str, str, str]:
    return (
        str(row.get("Date") or "").strip()[:10],
        str(row.get("Sport") or "").strip().lower(),
        str(row.get("Home") or "").strip().lower(),
        str(row.get("Away") or "").strip().lower(),
        str(row.get("Pick") or "").strip().lower(),
    )


def _map_rows(rows: List[Dict[str, Any]]) -> Dict[Tuple[str, str, str, str, str], Dict[str, Any]]:
    return {_key(row): row for row in rows}


def _split_items(value: Any) -> List[str]:
    out: List[str] = []
    for part in str(value or "").replace(",", ";").split(";"):
        item = part.strip()
        if item and item != "none":
            out.append(item)
    return out


def _add_unique(items: List[str], value: str) -> None:
    if value and value not in items:
        items.append(value)


def _coverage_penalty(grade: str) -> int:
    return {
        "A_DATA_COMPLETE": 0,
        "B_USABLE_WITH_CAUTION": 10,
        "C_SOURCE_GAPS": 25,
        "D_INSUFFICIENT": 40,
    }.get(grade, 30)


def _guard_level(score: int, decision: str) -> str:
    if decision == "APPROVED_FOR_HUMAN_REVIEW":
        return "A_REVIEWABLE"
    if decision in {"ODDS_FLOOR_MANUAL_CHECK", "WATCH_PRICE_ONLY", "RECHECK_1XBET_PRICE"}:
        return "B_MONITOR"
    if score >= 60:
        return "C_BLOCKED_FIXABLE"
    return "D_REJECTED_OR_UNSAFE"


def _memory_signal(row: Dict[str, Any], memory_rows: List[Dict[str, Any]]) -> Tuple[str, str]:
    checks = [
        ("sport", str(row.get("Sport") or "").strip()),
        ("strategy_gate", _gate_family(row.get("StrategyGate"))),
        ("probability_source", str(row.get("ProbabilitySource") or "").strip() or "unknown"),
        ("source_family", _source_family(row.get("Source"))),
        ("prob_bucket", _bucket_probability(row.get("Prob"))),
        ("odds_bucket", _bucket_odds(row.get("PickOdds"))),
        ("decision", str(row.get("Decision") or "").strip() or "unknown"),
        ("entry_readiness", str(row.get("EntryReadiness") or "").strip() or "unknown"),
        ("action_verdict", str(row.get("ActionVerdict") or "").strip() or "unknown"),
        ("odds_flag", str(row.get("OddsFlag") or "").strip() or "unknown"),
        ("one_xbet_status", str(row.get("OneXBetStatus") or "").strip() or "unknown"),
        ("event_timing", str(row.get("EventTimingStatus") or "").strip() or "unknown"),
        ("odds_source", str(row.get("OddsSourceUsed") or "").strip() or "unknown"),
        ("freshness", str(row.get("OneXBetOddsFreshness") or "").strip() or "unknown"),
        ("all", "all"),
    ]
    memory_index = {
        (str(mem.get("SegmentType") or ""), str(mem.get("Segment") or "")): mem
        for mem in memory_rows
    }
    priority = {
        "WEAK_SEGMENT_REVIEW": 0,
        "WATCH_SEGMENT": 1,
        "STRONG_SEGMENT": 2,
        "INSUFFICIENT_SAMPLE": 3,
        "NO_MEMORY_SIGNAL": 4,
        "": 5,
    }
    matches: List[Tuple[int, str, str, str, str]] = []
    for seg_type, segment in checks:
        mem = memory_index.get((seg_type, segment))
        if not mem:
            continue
        signal = str(mem.get("Signal") or "")
        matches.append((priority.get(signal, 5), signal, str(mem.get("Accuracy") or ""), seg_type, segment))
    if not matches:
        return "NO_MEMORY_SIGNAL", ""
    matches.sort(key=lambda item: item[0])
    _, signal, accuracy, seg_type, segment = matches[0]
    return signal, f"{accuracy} ({seg_type}:{segment})"


def _evaluate(
    row: Dict[str, Any],
    coverage: Dict[str, Any],
    movement: Dict[str, Any],
    result: Dict[str, Any],
    baseball_context: Dict[str, Any],
    tennis_context: Dict[str, Any],
    sport_context: Dict[str, Any],
    memory_rows: List[Dict[str, Any]],
    min_edge_pct: float,
    min_review_odds: float,
) -> Dict[str, Any]:
    hard: List[str] = []
    soft: List[str] = []
    score = 100

    prob = _as_float(row.get("Prob"))
    odds = _as_float(row.get("PickOdds"))
    target = _as_float(row.get("MinEntryOdds"))
    ev_pct = _as_float(row.get("EVPercent"))
    price_gap_pct = _as_float(row.get("PriceGapPct"))
    status = str(row.get("OneXBetStatus") or "")
    freshness = str(row.get("OneXBetOddsFreshness") or "")
    timing = str(row.get("EventTimingStatus") or "")
    value_verdict = str(row.get("ValueVerdict") or "")
    readiness = str(row.get("EntryReadiness") or "")
    blockers = set(_split_items(row.get("GateBlockers")))
    strategy_gate = str(row.get("StrategyGate") or "")
    coverage_grade = str(coverage.get("CoverageGrade") or "MISSING_COVERAGE")
    coverage_score = _as_int(coverage.get("CoverageScore"))
    result_status = str(result.get("ResultStatus") or "")
    recheck_action = str(result.get("RecheckAction") or "")
    trend = str(movement.get("Trend") or "")
    sport = str(row.get("Sport") or "").strip().lower()
    league = str(row.get("League") or "").strip().lower()
    snapshots = _as_int(movement.get("Snapshots"))
    distance_to_target = _as_float(movement.get("DistanceToTargetPct"))
    memory_signal, memory_accuracy = _memory_signal(row, memory_rows)
    baseball_context_gate = str(baseball_context.get("ContextGate") or "")
    baseball_context_score = str(baseball_context.get("ContextScore") or "")
    tennis_context_gate = str(tennis_context.get("ContextGate") or "")
    tennis_context_score = str(tennis_context.get("ContextScore") or "")
    sport_context_gate = str(sport_context.get("ContextGate") or "")
    sport_context_score = str(sport_context.get("ContextScore") or "")
    odds_floor_candidate = odds is not None and odds >= min_review_odds
    strategy_watch_only = strategy_gate.upper().startswith("WATCH_ONLY")
    name_flags = participant_quality_flags(row.get("Home"), row.get("Away"))

    if name_flags:
        _add_unique(hard, "bad_match_name")
        for flag in name_flags:
            _add_unique(soft, flag)
        score -= 80
    if prob is None or odds is None:
        _add_unique(hard, "missing_price_or_probability")
        score -= 35
    if target is None or ev_pct is None:
        _add_unique(hard, "missing_value_calculation")
        score -= 25
    if strategy_watch_only:
        _add_unique(hard, "strategy_watch_only_not_validated")
        score -= 35
        if prob is not None and prob < 0.70:
            _add_unique(hard, "watch_only_prob_below_70")
            score -= 10
        if prob is not None and odds is not None and prob < 0.70 and 1.30 <= odds <= 1.60:
            _add_unique(hard, "favorite_upset_odds_band_requires_context")
            score -= 8
    if sport == "baseball":
        _add_unique(hard, "baseball_pitcher_lineup_weather_context_required")
        score -= 12
        if not baseball_context_gate:
            baseball_context_gate = "BLOCKED_CONTEXT_MISSING"
            baseball_context_score = "0"
        if baseball_context_gate == "BLOCKED_CONTEXT_MISSING":
            _add_unique(hard, "baseball_context_missing")
            score -= 10
        elif baseball_context_gate == "BLOCKED_CONTEXT_INCOMPLETE":
            _add_unique(hard, "baseball_context_incomplete")
            score -= 8
        elif baseball_context_gate == "BLOCKED_ROTATION_RISK":
            _add_unique(hard, "baseball_rotation_risk_not_cleared")
            score -= 10
        elif baseball_context_gate == "BLOCKED_CONTEXT_WEAK":
            _add_unique(hard, "baseball_context_weak")
            score -= 6
        elif baseball_context_gate == "CONTEXT_READY_BACKTEST_REQUIRED":
            _add_unique(soft, "baseball_context_ready_backtest_required")
        if "ncaa" in league or "college" in league:
            _add_unique(hard, "college_baseball_rotation_volatility")
            score -= 10
        if prob is not None and prob < 0.70:
            _add_unique(hard, "baseball_prob_below_repaired_floor")
            score -= 8
        if odds is not None and odds > 1.70:
            _add_unique(hard, "baseball_odds_volatility_band")
            score -= 6
    elif sport == "tennis" and strategy_watch_only:
        _add_unique(hard, "tennis_player_surface_withdrawal_context_required")
        score -= 8
        if not tennis_context_gate:
            tennis_context_gate = "BLOCKED_CONTEXT_MISSING"
            tennis_context_score = "0"
        if tennis_context_gate == "BLOCKED_CONTEXT_MISSING":
            _add_unique(hard, "tennis_context_missing")
            score -= 8
        elif tennis_context_gate == "BLOCKED_CONTEXT_INCOMPLETE":
            _add_unique(hard, "tennis_context_incomplete")
            score -= 6
        elif tennis_context_gate == "BLOCKED_WITHDRAWAL_RISK":
            _add_unique(hard, "tennis_withdrawal_risk_not_cleared")
            score -= 10
        elif tennis_context_gate == "BLOCKED_INJURY_RISK":
            _add_unique(hard, "tennis_injury_risk_not_cleared")
            score -= 10
        elif tennis_context_gate == "BLOCKED_CONTEXT_WEAK":
            _add_unique(hard, "tennis_context_weak")
            score -= 4
        elif tennis_context_gate == "CONTEXT_READY_BACKTEST_REQUIRED":
            _add_unique(soft, "tennis_context_ready_backtest_required")
    elif sport in REMAINING_CONTEXT_SPORTS and strategy_watch_only:
        _add_unique(hard, f"{sport}_context_required")
        score -= 8
        if not sport_context_gate:
            sport_context_gate = "BLOCKED_CONTEXT_MISSING"
            sport_context_score = "0"
        if sport_context_gate == "BLOCKED_CONTEXT_MISSING":
            _add_unique(hard, f"{sport}_context_missing")
            score -= 8
        elif sport_context_gate == "BLOCKED_CONTEXT_INCOMPLETE":
            _add_unique(hard, f"{sport}_context_incomplete")
            score -= 6
        elif sport_context_gate == "BLOCKED_CONTEXT_WEAK":
            _add_unique(hard, f"{sport}_context_weak")
            score -= 4
        elif sport_context_gate == "CONTEXT_READY_BACKTEST_REQUIRED":
            _add_unique(soft, f"{sport}_context_ready_backtest_required")

    confirmed_1xbet = is_confirmed_1xbet_status(status)
    if not confirmed_1xbet:
        _add_unique(hard, "unconfirmed_1xbet_event")
        score -= 25
    if confirmed_1xbet and freshness != "FRESH":
        _add_unique(hard, "stale_or_unknown_1xbet_price")
        score -= 20

    if coverage_grade != "A_DATA_COMPLETE":
        _add_unique(hard, "source_coverage_not_complete")
        score -= _coverage_penalty(coverage_grade)
    elif coverage_score < 95:
        _add_unique(soft, "coverage_complete_but_not_max_score")
        score -= 3

    if timing == "STARTED_OR_EXPIRED":
        _add_unique(hard, "event_started_or_expired")
        score -= 80
    elif timing == "CLOSE_TO_START":
        _add_unique(hard, "event_too_close_to_start")
        score -= 35
    elif timing in {"", "UNKNOWN_START"}:
        _add_unique(hard, "unknown_start_time")
        score -= 15

    if recheck_action == "RECHECK_NOW":
        _add_unique(hard, "result_recheck_due_now")
        score -= 15
    elif result_status and result_status not in {"NOT_STARTED", "NO_EVENT_ID"}:
        _add_unique(hard, f"result_status_{result_status.lower()}")
        score -= 40

    if ev_pct is not None and ev_pct < min_edge_pct:
        _add_unique(hard, "ev_below_minimum")
        score -= 20
    if odds is not None and target is not None and odds < target:
        _add_unique(hard, "price_below_target")
        if price_gap_pct is not None:
            score -= min(35, max(5, int(round(price_gap_pct))))
        else:
            score -= 15

    for blocker in blockers:
        if blocker in {"stale_1xbet_price", "unknown_1xbet_price_age"}:
            _add_unique(hard, "stale_or_unknown_1xbet_price")
        elif blocker in {"unconfirmed_1xbet_event", "missing_price_or_probability"}:
            _add_unique(hard, blocker)
        elif blocker in PRICE_ONLY_VETOES:
            _add_unique(hard, blocker)
        elif blocker:
            _add_unique(soft, f"model_blocker_{blocker}")

    if snapshots <= 0:
        _add_unique(soft, "no_odds_movement_history")
        score -= 5
    elif snapshots == 1:
        _add_unique(soft, "single_odds_snapshot")
        score -= 3
    elif trend == "FALLING" and (distance_to_target is None or distance_to_target > 0):
        _add_unique(soft, "odds_moving_away_from_target")
        score -= 8
    elif trend == "RISING" and distance_to_target is not None and distance_to_target > 0:
        _add_unique(soft, "odds_moving_toward_target")

    if memory_signal == "WEAK_SEGMENT_REVIEW":
        _add_unique(hard, "weak_memory_segment")
        score -= 20
    elif memory_signal in {"INSUFFICIENT_SAMPLE", "NO_MEMORY_SIGNAL"}:
        _add_unique(soft, "memory_sample_not_ready")
        score -= 5
    elif memory_signal == "WATCH_SEGMENT":
        _add_unique(soft, "memory_segment_watch")
        score -= 3
    elif memory_signal == "STRONG_SEGMENT":
        _add_unique(soft, "memory_segment_strong")
    if odds_floor_candidate:
        _add_unique(soft, f"odds_floor_candidate_{min_review_odds:.2f}_plus")

    score = max(0, min(100, score))
    non_price_hard = [item for item in hard if item not in PRICE_ONLY_VETOES]
    has_only_price_vetoes = bool(hard) and not non_price_hard

    if value_verdict.startswith("ENTER") and not hard:
        decision = "APPROVED_FOR_HUMAN_REVIEW"
        decision_class = "ENTER_REVIEW"
        next_action = "FINAL_MANUAL_SOURCE_AND_PRICE_CHECK"
    elif odds_floor_candidate and not hard:
        decision = "APPROVED_FOR_HUMAN_REVIEW"
        decision_class = "ODDS_FLOOR_REVIEW"
        next_action = "FINAL_MANUAL_SOURCE_AND_PRICE_CHECK"
    elif "bad_match_name" in hard:
        decision = "NO_ENTRY"
        decision_class = "DATA_QUALITY_BLOCKED"
        next_action = "FIX_OR_DROP_PLACEHOLDER_MATCH_NAME"
    elif "strategy_watch_only_not_validated" in hard:
        decision = "STRATEGY_LAB_ONLY"
        decision_class = "SPORT_STRATEGY_NOT_VALIDATED"
        next_action = "BUILD_LOCAL_BACKTEST_AND_CONTEXT_GATES_BEFORE_REVIEW"
    elif "result_recheck_due_now" in hard:
        decision = "RECHECK_RESULT_STATUS"
        decision_class = "RESULT_RECHECK"
        next_action = "RUN_RESULT_CHECKER"
    elif "event_started_or_expired" in hard or "event_too_close_to_start" in hard:
        decision = "NO_ENTRY"
        decision_class = "TIME_BLOCKED"
        next_action = "DO_NOT_USE_FOR_TODAY"
    elif odds_floor_candidate and has_only_price_vetoes:
        decision = "APPROVED_FOR_HUMAN_REVIEW"
        decision_class = "ODDS_FLOOR_REVIEW"
        next_action = "FINAL_MANUAL_SOURCE_AND_PRICE_CHECK"
    elif odds_floor_candidate and (
        "source_coverage_not_complete" in hard
        or "unconfirmed_1xbet_event" in hard
        or "unknown_start_time" in hard
    ):
        decision = "ODDS_FLOOR_MANUAL_CHECK"
        decision_class = "ODDS_FLOOR_SOURCE_REVIEW"
        next_action = "CONFIRM_1XBET_EVENT_LIVE_PRICE_AND_START_TIME"
    elif "source_coverage_not_complete" in hard or "unconfirmed_1xbet_event" in hard or "missing_price_or_probability" in hard:
        decision = "IMPROVE_SOURCE_COVERAGE"
        decision_class = "SOURCE_REVIEW"
        next_action = "CONFIRM_EVENT_PRICE_AND_RESULT_SOURCE"
    elif "unknown_start_time" in hard:
        decision = "IMPROVE_SOURCE_COVERAGE"
        decision_class = "SOURCE_REVIEW"
        next_action = "CONFIRM_START_TIME_EVENT_AND_PRICE"
    elif hard and not non_price_hard and readiness == "PRICE_TARGET_ONLY":
        decision = "WATCH_PRICE_ONLY"
        decision_class = "PRICE_WATCH"
        next_action = "MONITOR_UNTIL_CURRENT_ODDS_REACH_TARGET"
    elif "stale_or_unknown_1xbet_price" in hard:
        decision = "RECHECK_1XBET_PRICE"
        decision_class = "PRICE_RECHECK"
        next_action = "REFRESH_PUBLIC_OR_MANUAL_1XBET_PRICE"
    elif "weak_memory_segment" in hard:
        decision = "NO_ENTRY"
        decision_class = "MEMORY_BLOCKED"
        next_action = "TIGHTEN_THRESHOLDS_OR_WAIT_FOR_NEW_EVIDENCE"
    elif hard:
        decision = "NO_ENTRY"
        decision_class = "HARD_VETO"
        next_action = "DO_NOT_USE_UNTIL_VETOES_CLEAR"
    elif readiness == "PRICE_TARGET_ONLY":
        decision = "WATCH_PRICE_ONLY"
        decision_class = "PRICE_WATCH"
        next_action = "MONITOR_UNTIL_CURRENT_ODDS_REACH_TARGET"
    else:
        decision = "NO_ENTRY"
        decision_class = "NO_VALUE"
        next_action = "IGNORE_UNLESS_NEW_PRICE_OR_SOURCE_DATA_APPEARS"

    reason_parts = []
    if hard:
        reason_parts.append("hard=" + ";".join(hard))
    if soft:
        reason_parts.append("soft=" + ";".join(soft))
    if not reason_parts:
        reason_parts.append("all_guard_checks_clear")

    guarded_stake = row.get("StakeAmount") if decision == "APPROVED_FOR_HUMAN_REVIEW" else "0"
    stake_lock = "REVIEW_ONLY" if decision == "APPROVED_FOR_HUMAN_REVIEW" else "FINAL_GUARD_BLOCKED"

    return {
        "Rank": row.get("Rank"),
        "Sport": row.get("Sport"),
        "Date": row.get("Date"),
        "League": row.get("League"),
        "Home": row.get("Home"),
        "Away": row.get("Away"),
        "Pick": row.get("Pick"),
        "Prob": row.get("Prob"),
        "CurrentOdds": row.get("PickOdds"),
        "TargetOdds": row.get("MinEntryOdds"),
        "EVPercent": row.get("EVPercent"),
        "PriceGapPct": row.get("PriceGapPct"),
        "StakeAmount": row.get("StakeAmount"),
        "GuardedStakeAmount": guarded_stake,
        "StakeLock": stake_lock,
        "ValueVerdict": value_verdict,
        "EntryReadiness": readiness,
        "OneXBetStatus": status,
        "OneXBetFreshness": freshness,
        "EventTimingStatus": timing,
        "CoverageScore": coverage_score,
        "CoverageGrade": coverage_grade,
        "ResultStatus": result_status,
        "RecheckAction": recheck_action,
        "StartTimeLocal": result.get("StartTimeLocal") or "",
        "MinutesToStart": row.get("MinutesToStart") or "",
        "OddsSnapshots": snapshots,
        "OddsTrend": trend,
        "DistanceToTargetPct": distance_to_target if distance_to_target is not None else "",
        "MinReviewOdds": min_review_odds,
        "OddsFloorCandidate": "yes" if odds_floor_candidate else "no",
        "MemorySignal": memory_signal,
        "MemoryAccuracy": memory_accuracy,
        "BaseballContextGate": baseball_context_gate,
        "BaseballContextScore": baseball_context_score,
        "TennisContextGate": tennis_context_gate,
        "TennisContextScore": tennis_context_score,
        "SportContextGate": sport_context_gate,
        "SportContextScore": sport_context_score,
        "GuardScore": score,
        "GuardLevel": _guard_level(score, decision),
        "DecisionClass": decision_class,
        "FinalDecision": decision,
        "HardVetoes": ";".join(hard) if hard else "none",
        "SoftWarnings": ";".join(soft) if soft else "none",
        "NextAction": next_action,
        "AuditReason": " | ".join(reason_parts),
        "StrategyGate": strategy_gate,
    }


def _build(
    target: date,
    min_edge_pct: float,
    min_review_odds: float,
    *,
    advisor_csv: Path | None = None,
    coverage_csv: Path | None = None,
    movement_csv: Path | None = None,
    results_csv: Path | None = None,
    baseball_context_csv: Path | None = None,
    tennis_context_csv: Path | None = None,
    sport_context_csv: Path | None = None,
    memory_csv: Path | None = None,
) -> List[Dict[str, Any]]:
    day = target.isoformat()
    advisor = _read_csv(advisor_csv or REPORTS_DIR / f"daily_1xbet_value_advisor_{day}.csv")
    coverage = _map_rows(_read_csv(coverage_csv or REPORTS_DIR / f"source_coverage_{day}.csv"))
    movement = _map_rows(_read_csv(movement_csv or REPORTS_DIR / f"odds_movement_{day}.csv"))
    results = _map_rows(_read_csv(results_csv or REPORTS_DIR / f"result_recheck_schedule_{day}.csv"))
    baseball_context = _map_rows(_read_csv(baseball_context_csv or REPORTS_DIR / f"baseball_context_gate_{day}.csv"))
    tennis_context = _map_rows(_read_csv(tennis_context_csv or REPORTS_DIR / f"tennis_context_gate_{day}.csv"))
    sport_context = _map_rows(_read_csv(sport_context_csv or REPORTS_DIR / f"remaining_sport_context_gate_{day}.csv"))
    memory = _read_csv(memory_csv or REPORTS_DIR / "prediction_memory_analysis.csv")

    rows = [
        _evaluate(
            row,
            coverage.get(_key(row), {}),
            movement.get(_key(row), {}),
            results.get(_key(row), {}),
            baseball_context.get(_key(row), {}),
            tennis_context.get(_key(row), {}),
            sport_context.get(_key(row), {}),
            memory,
            min_edge_pct,
            min_review_odds,
        )
        for row in advisor
    ]
    rows.sort(key=lambda r: (-_as_int(r.get("GuardScore")), _as_int(r.get("Rank")) or 999))
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
        "CurrentOdds",
        "TargetOdds",
        "EVPercent",
        "PriceGapPct",
        "StakeAmount",
        "GuardedStakeAmount",
        "StakeLock",
        "ValueVerdict",
        "EntryReadiness",
        "OneXBetStatus",
        "OneXBetFreshness",
        "EventTimingStatus",
        "CoverageScore",
        "CoverageGrade",
        "ResultStatus",
        "RecheckAction",
        "StartTimeLocal",
        "MinutesToStart",
        "OddsSnapshots",
        "OddsTrend",
        "DistanceToTargetPct",
        "MinReviewOdds",
        "OddsFloorCandidate",
        "MemorySignal",
        "MemoryAccuracy",
        "BaseballContextGate",
        "BaseballContextScore",
        "TennisContextGate",
        "TennisContextScore",
        "SportContextGate",
        "SportContextScore",
        "GuardScore",
        "GuardLevel",
        "DecisionClass",
        "FinalDecision",
        "HardVetoes",
        "SoftWarnings",
        "NextAction",
        "StrategyGate",
        "AuditReason",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _write_md(rows: List[Dict[str, Any]], target: date, path: Path) -> None:
    decisions = Counter(str(row.get("FinalDecision") or "EMPTY") for row in rows)
    classes = Counter(str(row.get("DecisionClass") or "EMPTY") for row in rows)
    vetoes: Counter[str] = Counter()
    for row in rows:
        for item in _split_items(row.get("HardVetoes")):
            vetoes[item] += 1

    actionable = [
        row
        for row in rows
        if row.get("FinalDecision")
        in {"APPROVED_FOR_HUMAN_REVIEW", "WATCH_PRICE_ONLY", "RECHECK_1XBET_PRICE", "IMPROVE_SOURCE_COVERAGE", "RECHECK_RESULT_STATUS", "STRATEGY_LAB_ONLY"}
    ]

    lines = [
        "# Final decision guard",
        f"- Date: {target.isoformat()}",
        f"- Candidates audited: {len(rows)}",
        "- Rule: the model is not enough. A candidate must pass price, source, timing, event, odds freshness, and memory gates before review.",
        "",
        "## Decision Counts",
        *([f"- {key}: {value}" for key, value in decisions.most_common()] or ["- none: 0"]),
        "",
        "## Decision Classes",
        *([f"- {key}: {value}" for key, value in classes.most_common()] or ["- none: 0"]),
        "",
        "## Main Hard Vetoes",
        *([f"- {key}: {value}" for key, value in vetoes.most_common(12)] or ["- none: 0"]),
        "",
        "## 1.30+ Odds Floor",
        "- Any confirmed, fresh candidate with current odds at or above the review floor is promoted to final human review even if the model target is higher.",
        "- Any unconfirmed candidate at or above the review floor is promoted to manual 1xBet/source check instead of being hidden inside generic source gaps.",
        "",
        "## Top Guarded Actions",
        "| # | Score | Decision | Match | Pick | Odds | Target | EV% | Guard level | Next action | Vetoes |",
        "| ---: | ---: | --- | --- | --- | ---: | ---: | ---: | --- | --- | --- |",
    ]
    for idx, row in enumerate(actionable[:30], start=1):
        lines.append(
            f"| {idx} | {row.get('GuardScore')} | {row.get('FinalDecision')} | "
            f"{row.get('Home')} vs {row.get('Away')} | {row.get('Pick')} | {row.get('CurrentOdds')} | "
            f"{row.get('TargetOdds')} | {row.get('EVPercent')} | {row.get('GuardLevel')} | "
            f"{row.get('NextAction')} | {row.get('HardVetoes')} |"
        )
    if not actionable:
        lines.append("| - | - | - | - | - | - | - | - | - | - | - |")

    lines.extend(
        [
            "",
            "## Entry Rule",
            "- `APPROVED_FOR_HUMAN_REVIEW` is still not automatic. It means the row may be reviewed with a live 1xBet price and independent sources.",
            "- `ODDS_FLOOR_MANUAL_CHECK` means the displayed odds are high enough for review, but the event/price/source still must be confirmed.",
            "- `WATCH_PRICE_ONLY` means the candidate is rejected now and becomes reviewable only if the current price reaches `TargetOdds` while all other gates stay clean.",
            "- Any hard veto means no entry until that exact veto disappears in a fresh run.",
            "- Baseball also requires `baseball_context_gate` to clear pitcher, lineup, bullpen/rest, weather/park, and league-split context before any backtest review.",
            "- Tennis also requires `tennis_context_gate` to clear player form, surface, injury/withdrawal, round, and event id context before any backtest review.",
            "- Existing watch sports also require `sport_context_gate` to clear their sport-specific context before any backtest review.",
            "- `GuardedStakeAmount` is forced to `0` for every row that is not `APPROVED_FOR_HUMAN_REVIEW`.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build final decision guard report.")
    parser.add_argument("--date", default="today")
    parser.add_argument("--min-edge-pct", type=float, default=1.5)
    parser.add_argument("--min-review-odds", type=float, default=1.30)
    parser.add_argument("--advisor-csv", default="")
    parser.add_argument("--coverage-csv", default="")
    parser.add_argument("--movement-csv", default="")
    parser.add_argument("--results-csv", default="")
    parser.add_argument("--baseball-context-csv", default="")
    parser.add_argument("--tennis-context-csv", default="")
    parser.add_argument("--sport-context-csv", default="")
    parser.add_argument("--memory-csv", default="")
    parser.add_argument("--out-csv", default="")
    parser.add_argument("--out-md", default="")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    target = _target_date(args.date)
    min_edge_pct = args.min_edge_pct * 100.0 if args.min_edge_pct <= 1.0 else args.min_edge_pct
    rows = _build(
        target,
        min_edge_pct,
        args.min_review_odds,
        advisor_csv=Path(args.advisor_csv) if args.advisor_csv else None,
        coverage_csv=Path(args.coverage_csv) if args.coverage_csv else None,
        movement_csv=Path(args.movement_csv) if args.movement_csv else None,
        results_csv=Path(args.results_csv) if args.results_csv else None,
        baseball_context_csv=Path(args.baseball_context_csv) if args.baseball_context_csv else None,
        tennis_context_csv=Path(args.tennis_context_csv) if args.tennis_context_csv else None,
        sport_context_csv=Path(args.sport_context_csv) if args.sport_context_csv else None,
        memory_csv=Path(args.memory_csv) if args.memory_csv else None,
    )
    out_csv = Path(args.out_csv) if args.out_csv else REPORTS_DIR / f"final_decision_guard_{target.isoformat()}.csv"
    out_md = Path(args.out_md) if args.out_md else REPORTS_DIR / f"final_decision_guard_{target.isoformat()}.md"
    _write_csv(rows, out_csv)
    _write_md(rows, target, out_md)
    counts = Counter(str(row.get("FinalDecision") or "EMPTY") for row in rows)
    print(f"Wrote {out_csv}")
    print(f"Wrote {out_md}")
    print("guard=" + " ".join(f"{key}={value}" for key, value in counts.most_common()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
