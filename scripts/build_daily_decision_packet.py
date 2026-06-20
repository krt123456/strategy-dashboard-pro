#!/usr/bin/env python3
"""Create a compact daily decision packet from all generated reports."""
from __future__ import annotations

import argparse
import csv
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List

BASE_DIR = Path(__file__).resolve().parents[1]
REPORTS_DIR = BASE_DIR / "reports"


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


def _first(path: Path) -> Dict[str, Any]:
    rows = _read_csv(path)
    return rows[0] if rows else {}


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


def _top_rows(rows: List[Dict[str, Any]], n: int) -> List[Dict[str, Any]]:
    return rows[: max(0, n)]


def _write_packet(target: date, out_md: Path) -> None:
    day = target.isoformat()
    health = _first(REPORTS_DIR / f"daily_system_health_{day}.csv")
    advisor = _read_csv(REPORTS_DIR / f"daily_1xbet_value_advisor_{day}.csv")
    watch = _read_csv(REPORTS_DIR / f"price_target_watchlist_{day}.csv")
    rechecks = _read_csv(REPORTS_DIR / f"result_recheck_schedule_{day}.csv")
    coverage = _read_csv(REPORTS_DIR / f"source_coverage_{day}.csv")
    movement = _read_csv(REPORTS_DIR / f"odds_movement_{day}.csv")
    guard = _read_csv(REPORTS_DIR / f"final_decision_guard_{day}.csv")
    odds_floor_audit = _read_csv(REPORTS_DIR / f"odds_floor_confirmation_audit_{day}.csv")
    queue = _read_csv(REPORTS_DIR / f"operational_action_queue_{day}.csv")
    gaps = _read_csv(REPORTS_DIR / f"daily_project_gaps_{day}.csv")
    sport_matrix = _read_csv(REPORTS_DIR / f"predictable_sports_strategy_matrix_{day}.csv")
    promotion_lab = _read_csv(REPORTS_DIR / f"sport_strategy_promotion_lab_{day}.csv")
    prediction_results = _read_csv(REPORTS_DIR / f"prediction_results_{day}.csv")
    postmatch = _read_csv(REPORTS_DIR / f"postmatch_learning_{day}.csv")
    postmatch_summary = _read_csv(REPORTS_DIR / f"postmatch_learning_summary_{day}.csv")
    baseball_context = _read_csv(REPORTS_DIR / f"baseball_context_gate_{day}.csv")
    tennis_context = _read_csv(REPORTS_DIR / f"tennis_context_gate_{day}.csv")
    remaining_context = _read_csv(REPORTS_DIR / f"remaining_sport_context_gate_{day}.csv")
    context_completion = _read_csv(REPORTS_DIR / f"context_override_completion_{day}.csv")

    enter = [r for r in advisor if str(r.get("ValueVerdict") or "").startswith("ENTER")]
    guard_approved = [r for r in guard if str(r.get("FinalDecision") or "") == "APPROVED_FOR_HUMAN_REVIEW"]
    guard_odds_floor_manual = [r for r in guard if str(r.get("FinalDecision") or "") == "ODDS_FLOOR_MANUAL_CHECK"]
    guard_watch = [r for r in guard if str(r.get("FinalDecision") or "") == "WATCH_PRICE_ONLY"]
    recheck_now = [r for r in rechecks if str(r.get("RecheckAction") or "") == "RECHECK_NOW"]
    after_finish = [r for r in rechecks if str(r.get("RecheckAction") or "") == "RECHECK_AFTER_FINISH"]
    coverage_counts = Counter(str(r.get("CoverageGrade") or "EMPTY") for r in coverage)
    guard_counts = Counter(str(r.get("FinalDecision") or "EMPTY") for r in guard)
    odds_floor_counts = Counter(str(r.get("Status") or "EMPTY") for r in odds_floor_audit)
    gap_counts = Counter(str(r.get("Severity") or "EMPTY") for r in gaps)
    result_counts = Counter(str(r.get("PickOutcome") or "EMPTY") for r in prediction_results)
    baseball_context_counts = Counter(str(r.get("ContextGate") or "EMPTY") for r in baseball_context)
    tennis_context_counts = Counter(str(r.get("ContextGate") or "EMPTY") for r in tennis_context)
    remaining_context_counts = Counter(str(r.get("ContextGate") or "EMPTY") for r in remaining_context)
    remaining_context_sports = Counter(str(r.get("Sport") or "unknown") for r in remaining_context)
    context_completion_counts = Counter(str(r.get("Status") or "EMPTY") for r in context_completion)
    blocking_gaps = [r for r in gaps if str(r.get("BlockingDecision") or "") == "YES"]
    promotion_ready = [r for r in promotion_lab if _as_float(r.get("PromotionPriority")) >= 60]
    state = health.get("operational_state") or "UNKNOWN"
    raw_finished = result_counts.get("CORRECT", 0) + result_counts.get("WRONG", 0)
    raw_accuracy = (result_counts.get("CORRECT", 0) / raw_finished) if raw_finished else None
    postmatch_correct = [r for r in postmatch if str(r.get("PickOutcome") or "") == "CORRECT"]
    postmatch_wrong = [r for r in postmatch if str(r.get("PickOutcome") or "") == "WRONG"]
    context_rows = (
        [{**row, "ContextSport": "baseball"} for row in baseball_context]
        + [{**row, "ContextSport": "tennis"} for row in tennis_context]
        + [{**row, "ContextSport": str(row.get("Sport") or "other")} for row in remaining_context]
    )
    blocked_baseball_context = sum(1 for r in baseball_context if str(r.get("ContextGate") or "").startswith("BLOCKED"))
    blocked_tennis_context = sum(1 for r in tennis_context if str(r.get("ContextGate") or "").startswith("BLOCKED"))
    blocked_remaining_context = sum(1 for r in remaining_context if str(r.get("ContextGate") or "").startswith("BLOCKED"))
    incomplete_context_overrides = sum(
        1 for r in context_completion if str(r.get("Status") or "") != "COMPLETE_READY_FOR_GATE_REBUILD"
    )

    lines = [
        "# Daily decision packet",
        f"- Date: {day}",
        f"- Operational state: `{state}`",
        f"- Enter candidates: {_as_int(health.get('enter_candidates'))}",
        f"- Price watch candidates: {_as_int(health.get('price_target_only'))}",
        f"- Confirmed 1xBet events: {_as_int(health.get('confirmed_1xbet_events'))}",
        f"- Fresh 1xBet prices: {_as_int(health.get('fresh_1xbet_prices'))}",
        f"- Missing price rows: {_as_int(health.get('missing_price_rows'))}",
        f"- Football stale-history leagues: {_as_int(health.get('stale_history_leagues'))}",
        f"- Final guard approved for review: {len(guard_approved)}",
        f"- Final guard 1.30+ manual checks: {len(guard_odds_floor_manual)}",
        f"- Final guard watch-price only: {len(guard_watch)}",
        f"- Odds-floor unresolved 1.30+: {_as_int(health.get('odds_floor_unresolved'))}",
        f"- Project blocking gaps: {len(blocking_gaps)}",
        f"- Sport promotion lab priorities: {len(promotion_ready)}",
        f"- Baseball context blocked: {blocked_baseball_context}/{len(baseball_context)}",
        f"- Tennis context blocked: {blocked_tennis_context}/{len(tennis_context)}",
        f"- Other sport context blocked: {blocked_remaining_context}/{len(remaining_context)}",
        f"- Context override rows needing work: {incomplete_context_overrides}/{len(context_completion)}",
        f"- Finished raw prediction results: {raw_finished} correct={result_counts.get('CORRECT', 0)} wrong={result_counts.get('WRONG', 0)}",
        "",
        "## قرار اليوم",
    ]
    if guard_approved:
        lines.append("- توجد مرشحات اجتازت حارس القرار النهائي، لكنها ما زالت تحتاج فحص سعر حي ومصادر مستقلة قبل القرار.")
    elif _as_int(health.get("odds_floor_unresolved")) > 0:
        lines.append("- توجد مرشحات 1.30+ غير مؤكدة؛ لا تستخدمها حتى تنجح مزامنة 1xBet أو لقطة linefeed.")
    elif guard_odds_floor_manual and odds_floor_counts.get("UNCONFIRMED_NEEDS_REPAIR", 0) > 0:
        lines.append("- توجد مرشحات بسعر 1.30+ لكنها تحتاج تأكيد حدث/سعر 1xBet قبل أي قرار.")
    elif enter:
        lines.append("- نموذج القيمة أظهر مرشحات، لكن حارس القرار النهائي لم يوافق عليها بعد.")
    elif state == "NO_ENTRY_PRICE_TOO_LOW":
        lines.append("- لا دخول الآن: السبب الأساسي أن السعر الحالي أقل من السعر الهدف أو EV غير كافية.")
    elif state == "PIPELINE_INCOMPLETE":
        lines.append("- لا قرار: خط التشغيل ناقص ويجب إعادة بناء التقرير اليومي.")
    else:
        lines.append("- لا دخول مؤكد الآن حسب بوابات التطبيق.")

    lines.extend(
        [
            "",
            "## تعلم النتائج",
            f"- النتائج الخام المنتهية: {raw_finished}; الصحيح={result_counts.get('CORRECT', 0)}؛ الخطأ={result_counts.get('WRONG', 0)}؛ المعلّق={result_counts.get('PENDING', 0)}.",
            f"- دقة التوقع الخام: {'n/a' if raw_accuracy is None else f'{raw_accuracy:.2%}'}؛ لا يوجد دخول رسمي إلا إذا كان `OfficialEntry=yes`.",
            "",
            "| Sport | Finished | Correct | Wrong | Pending | Accuracy | Gate |",
            "| --- | ---: | ---: | ---: | ---: | ---: | --- |",
        ]
    )
    for row in _top_rows(postmatch_summary, 8):
        acc = None if _as_int(row.get("Finished")) == 0 or row.get("Accuracy") in (None, "") else _as_float(row.get("Accuracy"))
        lines.append(
            f"| {row.get('Sport')} | {row.get('Finished')} | {row.get('Correct')} | {row.get('Wrong')} | "
            f"{row.get('Pending')} | {'n/a' if acc is None else f'{acc:.2%}'} | {row.get('RecommendedGate')} |"
        )
    if not postmatch_summary:
        lines.append("| - | - | - | - | - | - | - |")

    lines.extend(
        [
            "",
            "### التوقعات التي نجحت",
            "| # | Sport | Match | Pick | Score | Note |",
            "| ---: | --- | --- | --- | --- | --- |",
        ]
    )
    for row in _top_rows(postmatch_correct, 10):
        lines.append(
            f"| {row.get('Rank')} | {row.get('Sport')} | {row.get('Home')} vs {row.get('Away')} | "
            f"{row.get('Pick')} | {row.get('Score')} | {row.get('LearningNote')} |"
        )
    if not postmatch_correct:
        lines.append("| - | - | - | - | - | - |")

    lines.extend(
        [
            "",
            "### التوقعات التي أخطأت وما يجب تشديده",
            "| # | Sport | Match | Pick | Score | Action |",
            "| ---: | --- | --- | --- | --- | --- |",
        ]
    )
    for row in _top_rows(postmatch_wrong, 10):
        lines.append(
            f"| {row.get('Rank')} | {row.get('Sport')} | {row.get('Home')} vs {row.get('Away')} | "
            f"{row.get('Pick')} | {row.get('Score')} | {row.get('RecommendedAction')} |"
        )
    if not postmatch_wrong:
        lines.append("| - | - | - | - | - | - |")

    lines.extend(
        [
            "",
            "## نقائص اليوم وأفضل إصلاح",
            *([f"- {severity}: {count}" for severity, count in gap_counts.most_common()] or ["- لا يوجد تقرير نقائص بعد."]),
            "",
            "| Severity | Area | Gap | Evidence | Best fix |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    for row in _top_rows(gaps, 8):
        lines.append(
            f"| {row.get('Severity')} | {row.get('Area')} | {row.get('Gap')} | "
            f"{row.get('Evidence')} | {row.get('BestFix')} |"
        )
    if not gaps:
        lines.append("| - | - | - | - | - |")

    lines.extend(
        [
            "",
            "## الرياضات القابلة للتوقع واستراتيجياتها",
            "| Rank | Sport | Score | Mode | 1xBet events | Gate |",
            "| ---: | --- | ---: | --- | ---: | --- |",
        ]
    )
    for row in _top_rows(sport_matrix, 10):
        lines.append(
            f"| {row.get('Rank')} | {row.get('Sport')} | {row.get('PredictabilityScore')} | "
            f"{row.get('StrategyMode')} | {row.get('OneXBetEvents')} | {row.get('StrategyGate')} |"
        )
    if not sport_matrix:
        lines.append("| - | - | - | - | - |")

    lines.extend(
        [
            "",
            "## مختبر ترقية الرياضات",
            "- هذه ليست مباريات دخول؛ هي خطة تطوير للرياضات التي ما زالت مختبرية.",
            "| Rank | Sport | Priority | Mode | Watch rows | Lab guard | Sample target | Next script | Promotion gate |",
            "| ---: | --- | ---: | --- | ---: | ---: | ---: | --- | --- |",
        ]
    )
    for row in _top_rows(promotion_lab, 8):
        lines.append(
            f"| {row.get('Rank')} | {row.get('Sport')} | {row.get('PromotionPriority')} | "
            f"{row.get('CurrentMode')} | {row.get('WatchRows')} | {row.get('GuardLabRows')} | "
            f"{row.get('MinimumSampleTarget')} | {row.get('NextScriptToBuild')} | {row.get('PromotionGate')} |"
        )
    if not promotion_lab:
        lines.append("| - | - | - | - | - | - | - | - | - |")

    lines.extend(
        [
            "",
            "## بوابات سياق الرياضات",
            *([f"- baseball {gate}: {count}" for gate, count in baseball_context_counts.most_common()] or ["- baseball none: 0"]),
            *([f"- tennis {gate}: {count}" for gate, count in tennis_context_counts.most_common()] or ["- tennis none: 0"]),
            *([f"- other {gate}: {count}" for gate, count in remaining_context_counts.most_common()] or ["- other none: 0"]),
            *([f"- other sport {sport}: {count}" for sport, count in remaining_context_sports.most_common()] or ["- other sport none: 0"]),
            *([f"- override {status}: {count}" for status, count in context_completion_counts.most_common()] or ["- override none: 0"]),
            "",
            "| Sport | Match | Pick | Gate | Missing context | Threshold notes | Action |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for row in _top_rows(context_rows, 30):
        lines.append(
            f"| {row.get('ContextSport')} | {row.get('Home')} vs {row.get('Away')} | {row.get('Pick')} | "
            f"{row.get('ContextGate')} | {row.get('MissingContext')} | {row.get('ThresholdNotes')} | "
            f"{row.get('RecommendedAction')} |"
        )
    if not context_rows:
        lines.append("| - | - | - | - | - | - | - |")

    lines.extend(
        [
            "",
            "### جاهزية ملفات السياق",
            "| Sport | Match | Status | Completion | Missing | Next action |",
            "| --- | --- | --- | ---: | --- | --- |",
        ]
    )
    context_completion_focus = [
        row for row in context_completion if str(row.get("Status") or "") != "COMPLETE_READY_FOR_GATE_REBUILD"
    ]
    for row in _top_rows(context_completion_focus, 20):
        lines.append(
            f"| {row.get('Sport')} | {row.get('Home')} vs {row.get('Away')} | {row.get('Status')} | "
            f"{row.get('CompletionPct')} | {row.get('MissingFields')} | {row.get('NextAction')} |"
        )
    if not context_completion_focus:
        lines.append("| - | - | - | - | - | - |")

    lines.extend(
        [
            "",
            "## حارس القرار النهائي",
            *([f"- {decision}: {count}" for decision, count in guard_counts.most_common()] or ["- لا يوجد تقرير حارس القرار النهائي."]),
            "",
            "| Score | Decision | Match | Pick | Odds | Target | EV% | Vetoes | Next action |",
            "| ---: | --- | --- | --- | ---: | ---: | ---: | --- | --- |",
        ]
    )
    guard_focus = [
        row
        for row in guard
        if str(row.get("FinalDecision") or "")
        in {"APPROVED_FOR_HUMAN_REVIEW", "ODDS_FLOOR_MANUAL_CHECK", "WATCH_PRICE_ONLY", "RECHECK_1XBET_PRICE", "IMPROVE_SOURCE_COVERAGE", "STRATEGY_LAB_ONLY"}
    ]
    for row in _top_rows(guard_focus, 10):
        lines.append(
            f"| {row.get('GuardScore')} | {row.get('FinalDecision')} | {row.get('Home')} vs {row.get('Away')} | "
            f"{row.get('Pick')} | {row.get('CurrentOdds')} | {row.get('TargetOdds')} | {row.get('EVPercent')} | "
            f"{row.get('HardVetoes')} | {row.get('NextAction')} |"
        )
    if not guard_focus:
        lines.append("| - | - | - | - | - | - | - | - | - |")

    lines.extend(
        [
            "",
            "## تدقيق 1.30+",
            *([f"- {status}: {count}" for status, count in odds_floor_counts.most_common()] or ["- لا توجد صفوف فوق حد 1.30."]),
            "",
            "| Status | Match | Pick | Odds | 1xBet | Guard |",
            "| --- | --- | --- | ---: | --- | --- |",
        ]
    )
    for row in _top_rows(odds_floor_audit, 8):
        lines.append(
            f"| {row.get('Status')} | {row.get('Home')} vs {row.get('Away')} | {row.get('Pick')} | "
            f"{row.get('CurrentOdds')} | {row.get('OneXBetStatus')}/{row.get('OneXBetFreshness')} | "
            f"{row.get('FinalDecision')} |"
        )
    if not odds_floor_audit:
        lines.append("| - | - | - | - | - | - |")

    lines.extend(
        [
            "",
            "## مباريات المراجعة النهائية",
            "| Match | Pick | Odds | Target | EV% | Guarded stake | Decision | Vetoes |",
            "| --- | --- | ---: | ---: | ---: | ---: | --- | --- |",
        ]
    )
    if guard_approved:
        for row in guard_approved[:10]:
            lines.append(
                f"| {row.get('Home')} vs {row.get('Away')} | {row.get('Pick')} | {row.get('CurrentOdds')} | "
                f"{row.get('TargetOdds')} | {row.get('EVPercent')} | {row.get('GuardedStakeAmount')} | "
                f"{row.get('DecisionClass')} | {row.get('HardVetoes')} |"
            )
    else:
        lines.append("| - | - | - | - | - | - | - | - |")

    lines.extend(
        [
            "",
            "## مراقبة السعر",
            "| Priority | Match | Pick | Current | Target | Need % | Timing |",
            "| --- | --- | --- | ---: | ---: | ---: | --- |",
        ]
    )
    for row in _top_rows(watch, 8):
        lines.append(
            f"| {row.get('Priority')} | {row.get('Home')} vs {row.get('Away')} | {row.get('Pick')} | "
            f"{row.get('CurrentOdds')} | {row.get('TargetOdds')} | {row.get('NeededOddsIncreasePct')} | "
            f"{row.get('EventTimingStatus')} {row.get('MinutesToStart')}m |"
        )
    if not watch:
        lines.append("| - | - | - | - | - | - | - |")

    lines.extend(
        [
            "",
            "## إعادة فحص النتائج",
            "| Action | Match | Pick | Recheck after | Status |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    for row in _top_rows(recheck_now + after_finish, 8):
        lines.append(
            f"| {row.get('RecheckAction')} | {row.get('Home')} vs {row.get('Away')} | {row.get('Pick')} | "
            f"{row.get('RecheckAfterLocal')} | {row.get('ResultStatus')} |"
        )
    if not (recheck_now or after_finish):
        lines.append("| - | - | - | - | - |")

    lines.extend(
        [
            "",
            "## حركة السعر",
            "| Match | Pick | Snapshots | Last | Target | Need % | Trend |",
            "| --- | --- | ---: | ---: | ---: | ---: | --- |",
        ]
    )
    for row in _top_rows(movement, 8):
        lines.append(
            f"| {row.get('Home')} vs {row.get('Away')} | {row.get('Pick')} | {row.get('Snapshots')} | "
            f"{row.get('LastOdds')} | {row.get('TargetOdds')} | {row.get('DistanceToTargetPct')} | {row.get('Trend')} |"
        )
    if not movement:
        lines.append("| - | - | - | - | - | - | - |")

    lines.extend(
        [
            "",
            "## صحة المصادر",
            *[f"- {grade}: {count}" for grade, count in coverage_counts.most_common()],
            "",
            "## أول أعمال مطلوبة",
            "| Priority | Action | Match | Due | Reason |",
            "| ---: | --- | --- | --- | --- |",
        ]
    )
    for row in _top_rows(queue, 12):
        lines.append(
            f"| {row.get('PriorityScore')} | {row.get('Action')} | {row.get('Match')} | {row.get('DueLocal')} | {row.get('Reason')} |"
        )
    if not queue:
        lines.append("| - | - | - | - | - |")

    lines.extend(
        [
            "",
            "## قاعدة القرار",
            "- المسار القياسي يحتاج: سعر 1xBet حديث، حدث مؤكد، وقت المباراة صالح، EV فوق الحد، وتغطية مصادر كافية.",
            "- مسار 1.30+ الاستثنائي يسمح بالمراجعة النهائية إذا كان السعر مؤكدا وحديثا من 1xBet، حتى لو بقي السعر دون هدف النموذج.",
            "- إذا كان سعر 1.30+ غير مؤكد من 1xBet، يظهر كـ `ODDS_FLOOR_MANUAL_CHECK` وليس كمراجعة نهائية.",
            "- تقرير `odds_floor_confirmation_audit` هو قفل دائم: أي `UNCONFIRMED_NEEDS_REPAIR` يعني لا مراجعة ولا دخول.",
            "- Tennis/Baseball يحتاجان بوابة سياق مستقلة قبل أي ترقية من المختبر.",
            "- مراقبة السعر وحركة السعر ليست دخولاً بذاتها.",
        ]
    )
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build unified daily decision packet.")
    parser.add_argument("--date", default="today")
    parser.add_argument("--out-md", default="")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    target = _target_date(args.date)
    out_md = Path(args.out_md) if args.out_md else REPORTS_DIR / f"daily_decision_packet_{target.isoformat()}.md"
    _write_packet(target, out_md)
    print(f"Wrote {out_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
