#!/usr/bin/env python3
"""Build the daily promotion plan for watch-only sports.

This report does not create entry picks. It ranks which sports should receive
model/data work next so a watch-only sport can eventually pass a local gate.
"""
from __future__ import annotations

import argparse
import csv
import math
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path
from statistics import mean
from typing import Any, Dict, Iterable, List

from sports_strategy_profiles import SPORT_PROFILES, get_profile, normalize_sport_key

BASE_DIR = Path(__file__).resolve().parents[1]
REPORTS_DIR = BASE_DIR / "reports"
DATA_DIR = BASE_DIR / "data"


PROMOTION_RULES: Dict[str, Dict[str, Any]] = {
    "tennis": {
        "minimum_sample_target": 900,
        "missing_dataset": "Player match history with surface, round, retirement/withdrawal flags, closing odds, recent form, and a split between prime main-draw/doubles rows vs qualification/UTR rows.",
        "promotion_gate": "Prime tennis path only: backtest >= 68% on odds 1.25-1.45, no withdrawal/injury conflict, exact 1xBet event id, and positive EV after 5% haircut; qualification/UTR stay lab-only until separately repaired.",
        "next_script": "scripts/build_tennis_player_context_dataset.py",
        "risk_note": "High withdrawal/surface risk, and qualification/UTR rows now sit behind a stricter variance wall.",
    },
    "volleyball": {
        "minimum_sample_target": 700,
        "missing_dataset": "Team match history with set scores, league strength, roster/news, and market odds.",
        "promotion_gate": "League-filtered backtest >= 70%, stable match-winner mapping, set-volatility penalty, and no roster conflict.",
        "next_script": "scripts/build_volleyball_set_volatility_dataset.py",
        "risk_note": "Set-level volatility can hide weak favourites.",
    },
    "hockey": {
        "minimum_sample_target": 800,
        "missing_dataset": "Current fixtures joined with goalie status, rest/back-to-back, overtime market rule, and closing odds.",
        "promotion_gate": "Goalie/rest gate complete, market rule verified, current fixture refresh healthy, and backtest >= 67%.",
        "next_script": "scripts/build_hockey_context_gate.py",
        "risk_note": "Goalie changes and overtime rule confusion are the main blockers.",
    },
    "baseball": {
        "minimum_sample_target": 1400,
        "missing_dataset": "Confirmed starting pitchers, bullpen rest, confirmed lineup, park/weather, MLB/NCAA split labels, NCAA midweek rotation flags, and closing moneyline odds.",
        "promotion_gate": "Keep baseball in deep lab until MLB and NCAA split backtests pass separately: MLB >= 70%, NCAA >= 72%, pitcher/lineup/weather gate complete, EV survives 7.5% haircut, and no unresolved college rotation gap.",
        "next_script": "scripts/build_baseball_pitcher_context_dataset.py",
        "risk_note": "2026-04-28 and 2026-04-29 feedback plus watch-memory replay: baseball public-market-only signal remains too noisy without pitcher/lineup/weather context.",
    },
    "handball": {
        "minimum_sample_target": 650,
        "missing_dataset": "Retuned league history, weak-league blacklist, current fixtures, and team strength gap.",
        "promotion_gate": "Selected leagues >= 72% precision, weak leagues blocked, current fixture source healthy, and odds mapping verified.",
        "next_script": "scripts/retune_handball_league_precision.py",
        "risk_note": "Existing partial model is blocked by weak-league precision.",
    },
    "cricket": {
        "minimum_sample_target": 550,
        "missing_dataset": "Format, toss, pitch, lineup, venue/weather, recent form, and market odds.",
        "promotion_gate": "No pre-toss entry, lineup/toss gate complete, format-specific backtest >= 66%, and weather not hostile.",
        "next_script": "scripts/build_cricket_toss_lineup_gate.py",
        "risk_note": "Toss and lineup can invalidate early prices.",
    },
    "americanfootball": {
        "minimum_sample_target": 450,
        "missing_dataset": "QB/injury status, rest, weather, spread movement, and market odds.",
        "promotion_gate": "QB status confirmed, injury/weather gate complete, spread movement not adverse, and backtest >= 65%.",
        "next_script": "scripts/build_americanfootball_injury_weather_gate.py",
        "risk_note": "Context-heavy and schedule density is lower.",
    },
    "futsal": {
        "minimum_sample_target": 550,
        "missing_dataset": "League volatility, recent goals profile, lineup/context, and market odds.",
        "promotion_gate": "League-specific volatility model complete, weak leagues blocked, and backtest >= 68%.",
        "next_script": "scripts/build_futsal_volatility_dataset.py",
        "risk_note": "High-scoring volatility can break simple favourite logic.",
    },
    "darts": {
        "minimum_sample_target": 750,
        "missing_dataset": "Player form, leg/set format, stage, closing odds, and recent result state.",
        "promotion_gate": "Format-aware player model >= 69%, recent-form gate complete, and short-format variance penalty applied.",
        "next_script": "scripts/build_darts_form_format_dataset.py",
        "risk_note": "Short-format variance is high.",
    },
    "snooker": {
        "minimum_sample_target": 600,
        "missing_dataset": "Best-of format, player form, tournament stage, closing odds, and result history.",
        "promotion_gate": "Format-length model >= 69%, recent-form gate complete, stage/format verified on 1xBet, and no sub-70% favorite/upset band.",
        "next_script": "scripts/build_snooker_format_form_dataset.py",
        "risk_note": "2026-04-29 miss confirmed that frame count, stage, and recent form can flip public favourites.",
    },
}


def _target_date(value: str) -> date:
    raw = (value or "today").strip().lower()
    today = date.today()
    if raw in {"today", "اليوم"}:
        return today
    if raw in {"tomorrow", "غدا", "غداً"}:
        return today + timedelta(days=1)
    return date.fromisoformat(raw)


def _read_csv(path: Path) -> List[Dict[str, Any]]:
    if not path.exists() or path.stat().st_size <= 0:
        return []
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _as_int(value: Any) -> int:
    try:
        return int(float(value))
    except Exception:
        return 0


def _as_float(value: Any) -> float:
    try:
        return float(value)
    except Exception:
        return 0.0


def _mean_float(rows: Iterable[Dict[str, Any]], column: str) -> float:
    values = [_as_float(row.get(column)) for row in rows if str(row.get(column) or "").strip()]
    return round(mean(values), 3) if values else 0.0


def _canonical_sport(value: object) -> str:
    profile = get_profile(value)
    if profile:
        for key, candidate in SPORT_PROFILES.items():
            if candidate is profile:
                return key
    return normalize_sport_key(value)


def _strategy_mode(status: str) -> str:
    if status == "ACTIVE":
        return "ENTRY_GATE_ENABLED"
    if status == "ACTIVE_SECONDARY":
        return "SECONDARY_REVIEW_ENABLED"
    if status in {"PARTIAL", "PARTIAL_BLOCKED"}:
        return "WATCH_ONLY_UNTIL_LOCAL_FIX"
    return "STRATEGY_LAB_WATCH_ONLY"


def _sport_rows(rows: Iterable[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in rows:
        key = _canonical_sport(row.get("Sport"))
        if key:
            grouped[key].append(row)
    return grouped


def _scan_counts(target: date) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for row in _read_csv(REPORTS_DIR / f"1xbet_sports_strategy_scan_{target.isoformat()}.csv"):
        key = _canonical_sport(row.get("Sport"))
        if key:
            out[key] = row
    return out


def _linefeed_counts(target: date) -> Counter[str]:
    counts: Counter[str] = Counter()
    path = DATA_DIR / "one_xbet_linefeed_snapshot.csv"
    for row in _read_csv(path):
        row_date = str(row.get("Date") or "").strip()
        if row_date and row_date != target.isoformat():
            continue
        key = _canonical_sport(row.get("Sport"))
        if key:
            counts[key] += 1
    return counts


def _memory_by_sport() -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for row in _read_csv(REPORTS_DIR / "prediction_memory_analysis.csv"):
        if str(row.get("SegmentType") or "") != "sport":
            continue
        key = _canonical_sport(row.get("Segment"))
        if key:
            out[key] = row
    return out


def _priority_score(
    profile: Dict[str, Any],
    matrix_row: Dict[str, Any],
    scan_row: Dict[str, Any],
    watch_rows: List[Dict[str, Any]],
    guard_rows: List[Dict[str, Any]],
    queue_rows: List[Dict[str, Any]],
    linefeed_count: int,
    memory_row: Dict[str, Any],
) -> float:
    status = str(profile.get("model_status") or "")
    predictability = _as_float(matrix_row.get("PredictabilityScore")) or float(profile.get("predictability_score") or 0)
    events = _as_int(matrix_row.get("OneXBetEvents")) or _as_int(scan_row.get("OneXBetEvents")) or linefeed_count
    watch_count = len(watch_rows)
    guard_lab_count = len([row for row in guard_rows if str(row.get("FinalDecision") or "") == "STRATEGY_LAB_ONLY"])
    queue_count = len(queue_rows)
    avg_prob = _mean_float(watch_rows, "Prob")
    event_bonus = min(10.0, math.log10(max(events, 1)) * 3.0)
    watch_bonus = min(18.0, watch_count * 1.7)
    guard_bonus = min(14.0, guard_lab_count * 2.2)
    queue_bonus = min(8.0, queue_count * 0.8)
    prob_bonus = max(0.0, min(8.0, (avg_prob - 0.58) * 50.0)) if avg_prob else 0.0
    status_bonus = 8.0 if status == "PARTIAL" else 5.0 if status == "PARTIAL_BLOCKED" else 0.0
    context_penalty = 0.0
    context_text = str(profile.get("required_context") or "").lower()
    for marker in ("toss", "pitcher", "qb", "weather", "withdrawal"):
        if marker in context_text:
            context_penalty += 1.5
    if status == "PARTIAL_BLOCKED":
        context_penalty += 3.0
    memory_signal = str(memory_row.get("Signal") or "")
    memory_penalty = {
        "WEAK_SEGMENT_REVIEW": 18.0,
        "WATCH_SEGMENT": 8.0,
        "INSUFFICIENT_SAMPLE": 3.0,
    }.get(memory_signal, 0.0)
    return round(max(0.0, min(100.0, predictability + event_bonus + watch_bonus + guard_bonus + queue_bonus + prob_bonus + status_bonus - context_penalty - memory_penalty)), 2)


def _build(target: date) -> List[Dict[str, Any]]:
    matrix_rows = {_canonical_sport(row.get("Sport")): row for row in _read_csv(REPORTS_DIR / f"predictable_sports_strategy_matrix_{target.isoformat()}.csv")}
    guard_by_sport = _sport_rows(_read_csv(REPORTS_DIR / f"final_decision_guard_{target.isoformat()}.csv"))
    queue_by_sport = _sport_rows(_read_csv(REPORTS_DIR / f"operational_action_queue_{target.isoformat()}.csv"))
    watch_by_sport = _sport_rows(_read_csv(REPORTS_DIR / f"other_sports_1xbet_candidates_{target.isoformat()}.csv"))
    scan_by_sport = _scan_counts(target)
    linefeed_by_sport = _linefeed_counts(target)
    memory_by_sport = _memory_by_sport()
    rows: List[Dict[str, Any]] = []

    for key, profile in SPORT_PROFILES.items():
        status = str(profile.get("model_status") or "")
        if status in {"ACTIVE", "ACTIVE_SECONDARY"}:
            continue
        rules = PROMOTION_RULES.get(key, {})
        matrix_row = matrix_rows.get(key, {})
        scan_row = scan_by_sport.get(key, {})
        watch_rows = watch_by_sport.get(key, [])
        guard_rows = guard_by_sport.get(key, [])
        queue_rows = queue_by_sport.get(key, [])
        linefeed_count = linefeed_by_sport.get(key, 0)
        memory_row = memory_by_sport.get(key, {})
        memory_signal = str(memory_row.get("Signal") or "") or "NO_MEMORY_SIGNAL"
        mode = _strategy_mode(status)
        priority = _priority_score(profile, matrix_row, scan_row, watch_rows, guard_rows, queue_rows, linefeed_count, memory_row)
        one_x_events = _as_int(matrix_row.get("OneXBetEvents")) or _as_int(scan_row.get("OneXBetEvents")) or linefeed_count
        guard_lab = [row for row in guard_rows if str(row.get("FinalDecision") or "") == "STRATEGY_LAB_ONLY"]
        odds_floor = [row for row in guard_rows if str(row.get("OddsFloorCandidate") or "").lower() in {"yes", "true", "1"}]
        quality_warnings = []
        if len(watch_rows) == 0:
            quality_warnings.append("no_watch_rows_today")
        if one_x_events == 0:
            quality_warnings.append("no_1xbet_events_seen")
        if status == "PARTIAL_BLOCKED":
            quality_warnings.append("existing_model_blocked")
        if memory_signal == "WEAK_SEGMENT_REVIEW":
            quality_warnings.append("weak_memory_segment")
        elif memory_signal == "WATCH_SEGMENT":
            quality_warnings.append("memory_watch_segment")
        elif memory_signal in {"INSUFFICIENT_SAMPLE", "NO_MEMORY_SIGNAL"}:
            quality_warnings.append("memory_not_ready")
        if any(token in str(profile.get("required_context") or "").lower() for token in ("toss", "pitcher", "qb", "withdrawal")):
            quality_warnings.append("context_mandatory")
        rows.append(
            {
                "Rank": 0,
                "Sport": profile.get("display"),
                "SportKey": key,
                "CurrentMode": mode,
                "ModelStatus": status,
                "PromotionPriority": priority,
                "PredictabilityScore": _as_float(matrix_row.get("PredictabilityScore")) or profile.get("predictability_score"),
                "OneXBetEvents": one_x_events,
                "LinefeedRows": linefeed_count,
                "WatchRows": len(watch_rows),
                "GuardLabRows": len(guard_lab),
                "ActionQueueRows": len(queue_rows),
                "OddsFloorCandidates": len(odds_floor),
                "MemorySignal": memory_signal,
                "MemoryAccuracy": memory_row.get("Accuracy") or "",
                "AvgWatchProb": _mean_float(watch_rows, "Prob"),
                "AvgWatchOdds": _mean_float(watch_rows, "PickOdds") or _mean_float(watch_rows, "CurrentOdds"),
                "MissingDataset": rules.get("missing_dataset") or "Historical results, market odds, context fields, and 1xBet event mapping.",
                "RequiredContext": profile.get("required_context"),
                "PromotionGate": rules.get("promotion_gate") or "Build local dataset, run backtest, verify event id, and keep final guard veto until precision is proven.",
                "MinimumSampleTarget": rules.get("minimum_sample_target") or 500,
                "NextScriptToBuild": rules.get("next_script") or f"scripts/build_{key}_strategy_dataset.py",
                "QualityWarnings": ";".join(quality_warnings) if quality_warnings else "none",
                "RiskNote": rules.get("risk_note") or profile.get("model_note"),
                "Why": (
                    f"score={priority}; watch_rows={len(watch_rows)}; lab_guard_rows={len(guard_lab)}; "
                    f"1xbet_events={one_x_events}; status={status}; memory={memory_signal}"
                ),
            }
        )

    rows.sort(
        key=lambda row: (
            _as_float(row.get("PromotionPriority")),
            _as_int(row.get("WatchRows")),
            _as_int(row.get("OneXBetEvents")),
        ),
        reverse=True,
    )
    for idx, row in enumerate(rows, start=1):
        row["Rank"] = idx
    return rows


def _write_csv(rows: List[Dict[str, Any]], path: Path) -> None:
    fields = [
        "Rank",
        "Sport",
        "SportKey",
        "CurrentMode",
        "ModelStatus",
        "PromotionPriority",
        "PredictabilityScore",
        "OneXBetEvents",
        "LinefeedRows",
        "WatchRows",
        "GuardLabRows",
        "ActionQueueRows",
        "OddsFloorCandidates",
        "MemorySignal",
        "MemoryAccuracy",
        "AvgWatchProb",
        "AvgWatchOdds",
        "MissingDataset",
        "RequiredContext",
        "PromotionGate",
        "MinimumSampleTarget",
        "NextScriptToBuild",
        "QualityWarnings",
        "RiskNote",
        "Why",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _write_md(rows: List[Dict[str, Any]], target: date, path: Path) -> None:
    actionable = [row for row in rows if _as_float(row.get("PromotionPriority")) >= 60]
    lines = [
        "# Sport strategy promotion lab",
        f"- Date: {target.isoformat()}",
        f"- Watch/partial sports ranked: {len(rows)}",
        f"- High-priority model builds: {len(actionable)}",
        "- Rule: this report is for development order only; it must not create entry picks or override the final decision guard.",
        "- Promotion requirement: a sport remains watch-only until its local dataset, backtest, context gate, and 1xBet event mapping are complete.",
        "",
        "| Rank | Sport | Priority | Memory | Mode | 1xBet events | Watch rows | Lab guard rows | Next script | Promotion gate |",
        "| ---: | --- | ---: | --- | --- | ---: | ---: | ---: | --- | --- |",
    ]
    for row in rows:
        lines.append(
            f"| {row.get('Rank')} | {row.get('Sport')} | {row.get('PromotionPriority')} | "
            f"{row.get('MemorySignal')} {row.get('MemoryAccuracy')} | {row.get('CurrentMode')} | "
            f"{row.get('OneXBetEvents')} | {row.get('WatchRows')} | {row.get('GuardLabRows')} | "
            f"{row.get('NextScriptToBuild')} | {row.get('PromotionGate')} |"
        )
    lines.extend(
        [
            "",
            "## Immediate Build Order",
        ]
    )
    if actionable:
        for row in actionable[:6]:
            lines.append(
                f"- {row.get('Sport')}: build `{row.get('NextScriptToBuild')}`; "
                f"sample target {row.get('MinimumSampleTarget')}; missing: {row.get('MissingDataset')}"
            )
    else:
        lines.append("- No sport has enough signal today for immediate promotion work; keep collecting watch rows and 1xBet snapshots.")
    lines.extend(
        [
            "",
            "## Hard No-Promotion Conditions",
            "- No local backtest sample -> keep STRATEGY_LAB_ONLY.",
            "- Missing event id or stale 1xBet price -> keep source repair/action queue only.",
            "- Mandatory context missing (injury, pitcher, toss, QB, goalie, format) -> keep watch-only.",
            "- Positive-looking public market probability alone is not enough for review.",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build sport strategy promotion lab report.")
    parser.add_argument("--date", default="today")
    parser.add_argument("--out-csv", default="")
    parser.add_argument("--out-md", default="")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    target = _target_date(args.date)
    rows = _build(target)
    out_csv = Path(args.out_csv) if args.out_csv else REPORTS_DIR / f"sport_strategy_promotion_lab_{target.isoformat()}.csv"
    out_md = Path(args.out_md) if args.out_md else REPORTS_DIR / f"sport_strategy_promotion_lab_{target.isoformat()}.md"
    _write_csv(rows, out_csv)
    _write_md(rows, target, out_md)
    print(f"Wrote {out_csv}")
    print(f"Wrote {out_md}")
    print(f"promotion_sports={len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
