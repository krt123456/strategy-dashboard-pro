#!/usr/bin/env python3
"""Audit daily operational gaps for the 1xBet strategy advisor."""
from __future__ import annotations

import argparse
import csv
import re
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List

BASE_DIR = Path(__file__).resolve().parents[1]
REPORTS_DIR = BASE_DIR / "reports"

SEVERITY_ORDER = {
    "BLOCKER": 0,
    "HIGH": 1,
    "MEDIUM": 2,
    "LOW": 3,
    "INFO": 4,
    "OK": 5,
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


def _count(rows: List[Dict[str, Any]], field: str) -> Counter[str]:
    return Counter(str(row.get(field) or "EMPTY") for row in rows)


def _parse_md_int(path: Path, pattern: str) -> int:
    if not path.exists():
        return 0
    text = path.read_text(encoding="utf-8", errors="ignore")
    match = re.search(pattern, text)
    return int(match.group(1)) if match else 0


def _add(
    rows: List[Dict[str, Any]],
    severity: str,
    area: str,
    gap: str,
    evidence: str,
    best_fix: str,
    source_report: str,
    blocking: bool = False,
) -> None:
    rows.append(
        {
            "Severity": severity,
            "Area": area,
            "Gap": gap,
            "Evidence": evidence,
            "BestFix": best_fix,
            "BlockingDecision": "YES" if blocking else "NO",
            "SourceReport": source_report,
        }
    )


def _build(target: date) -> List[Dict[str, Any]]:
    day = target.isoformat()
    advisor = _read_csv(REPORTS_DIR / f"daily_1xbet_value_advisor_{day}.csv")
    guard = _read_csv(REPORTS_DIR / f"final_decision_guard_{day}.csv")
    coverage = _read_csv(REPORTS_DIR / f"source_coverage_{day}.csv")
    odds_floor = _read_csv(REPORTS_DIR / f"odds_floor_confirmation_audit_{day}.csv")
    results = _read_csv(REPORTS_DIR / f"prediction_results_{day}.csv")
    rechecks = _read_csv(REPORTS_DIR / f"result_recheck_schedule_{day}.csv")
    promotion_lab = _read_csv(REPORTS_DIR / f"sport_strategy_promotion_lab_{day}.csv")
    context_worklist = _read_csv(REPORTS_DIR / f"context_collection_worklist_{day}.csv")
    context_completion = _read_csv(REPORTS_DIR / f"context_override_completion_{day}.csv")
    health = _first(REPORTS_DIR / f"daily_system_health_{day}.csv")

    sync_md = REPORTS_DIR / f"1xbet_public_odds_sync_{day}.md"
    linefeed_md = REPORTS_DIR / f"1xbet_linefeed_snapshot_{day}.md"
    sync_checked = _parse_md_int(sync_md, r"Candidates checked:\s*(\d+)")
    sync_confirmed = _parse_md_int(sync_md, r"Confirmed odds written:\s*(\d+)")
    sync_blocked = _parse_md_int(sync_md, r"Blocked/unconfirmed:\s*(\d+)")
    linefeed_rows = _parse_md_int(linefeed_md, r"Rows:\s*(\d+)")

    guard_counts = _count(guard, "FinalDecision")
    coverage_counts = _count(coverage, "CoverageGrade")
    odds_floor_counts = _count(odds_floor, "Status")
    result_counts = _count(rechecks, "ResultStatus")
    rows: List[Dict[str, Any]] = []

    if not advisor:
        _add(
            rows,
            "BLOCKER",
            "Pipeline",
            "Daily advisor report is missing or empty.",
            f"daily_1xbet_value_advisor_{day}.csv rows=0",
            "Run scripts/run_daily_1xbet_advisor.sh for the target date and do not review matches until it rebuilds.",
            "daily_1xbet_value_advisor",
            True,
        )

    if linefeed_rows <= 0:
        _add(
            rows,
            "HIGH",
            "1xBet Source",
            "Public linefeed snapshot did not capture events.",
            f"linefeed_rows={linefeed_rows}",
            "Keep DAILY_1XBET_LINEFEED_SNAPSHOT=1, retry with a larger timeout/count, and keep previous confirmed rows only as history.",
            "1xbet_linefeed_snapshot",
            True,
        )

    if sync_checked <= 0:
        _add(
            rows,
            "HIGH",
            "1xBet Source",
            "Public odds sync did not check any candidate.",
            f"checked={sync_checked} confirmed={sync_confirmed} blocked={sync_blocked}",
            "Use broad queries, increase DAILY_1XBET_PUBLIC_ODDS_LIMIT, and pass the current linefeed snapshot to the sync script.",
            "1xbet_public_odds_sync",
            True,
        )
    elif sync_confirmed / max(sync_checked, 1) < 0.85:
        _add(
            rows,
            "HIGH",
            "1xBet Source",
            "1xBet confirmation rate is below the safe threshold.",
            f"checked={sync_checked} confirmed={sync_confirmed} blocked={sync_blocked}",
            "Prioritize unmatched rows through linefeed event ids before accepting any 1.30+ exception.",
            "1xbet_public_odds_sync",
            True,
        )

    unresolved = odds_floor_counts.get("UNCONFIRMED_NEEDS_REPAIR", 0)
    if unresolved > 0:
        _add(
            rows,
            "BLOCKER",
            "1.30+ Guard",
            "Some 1.30+ candidates are still unconfirmed.",
            f"UNCONFIRMED_NEEDS_REPAIR={unresolved}",
            "Do not promote those rows. Re-run linefeed snapshot and public odds sync, then rebuild the advisor and final guard.",
            "odds_floor_confirmation_audit",
            True,
        )

    stale_prices = _as_int(health.get("stale_1xbet_prices"))
    if stale_prices > 0:
        stale_review_risk = [
            row
            for row in guard
            if str(row.get("OneXBetFreshness") or "") == "STALE"
            and str(row.get("FinalDecision") or "") in {"APPROVED_FOR_HUMAN_REVIEW", "ODDS_FLOOR_MANUAL_CHECK"}
        ]
        if stale_review_risk:
            _add(
                rows,
                "BLOCKER",
                "Price Freshness",
                "A reviewable 1xBet price is stale.",
                f"stale_reviewable_rows={len(stale_review_risk)}; stale_1xbet_prices={stale_prices}",
                "Refresh public odds immediately and keep stale reviewable rows blocked until OneXBetFreshness becomes FRESH.",
                "daily_system_health",
                True,
            )
        else:
            _add(
                rows,
                "INFO",
                "Price Freshness",
                "Stale 1xBet prices are already blocked by the final guard.",
                f"stale_1xbet_prices={stale_prices}; stale_reviewable_rows=0",
                "No entry action needed for these rows; refresh odds only if a row becomes reviewable.",
                "daily_system_health",
                False,
            )

    missing_price = _as_int(health.get("missing_price_rows"))
    if missing_price > 0:
        _add(
            rows,
            "HIGH",
            "Price Coverage",
            "Some candidates lack price or probability fields.",
            f"missing_price_rows={missing_price}",
            "Exclude rows with missing price/probability from review and fix their upstream source adapter.",
            "daily_system_health",
            True,
        )

    source_gap_rows = coverage_counts.get("C_SOURCE_GAPS", 0) + coverage_counts.get("D_INSUFFICIENT", 0)
    if source_gap_rows > 0:
        _add(
            rows,
            "MEDIUM",
            "Source Coverage",
            "Some candidates still have incomplete source coverage.",
            f"C/D source rows={source_gap_rows}",
            "Start from operational_action_queue rows with IMPROVE_SOURCE_COVERAGE and confirm event id, start time, and result source.",
            "source_coverage",
            False,
        )

    improve_source = guard_counts.get("IMPROVE_SOURCE_COVERAGE", 0)
    if improve_source > 0:
        _add(
            rows,
            "MEDIUM",
            "Final Guard",
            "Final guard is blocking rows because source quality is not complete.",
            f"IMPROVE_SOURCE_COVERAGE={improve_source}",
            "Use event-id based matching first, then name normalization only as fallback.",
            "final_decision_guard",
            False,
        )

    strategy_lab_only = guard_counts.get("STRATEGY_LAB_ONLY", 0)
    strategy_watch_vetoes = sum(
        1
        for row in guard
        if "strategy_watch_only_not_validated" in str(row.get("HardVetoes") or "")
    )
    if strategy_lab_only > 0 or strategy_watch_vetoes > 0:
        top_promotion = max((_as_float(row.get("PromotionPriority")) for row in promotion_lab), default=0.0)
        _add(
            rows,
            "INFO",
            "Sport Expansion",
            "Some promising sports are strategy-lab only.",
            f"STRATEGY_LAB_ONLY={strategy_lab_only}; strategy_watch_only_not_validated={strategy_watch_vetoes}; top_promotion_priority={top_promotion}",
            "Use sport_strategy_promotion_lab to build the next local dataset/backtest/context gate, then keep the final guard veto until precision is proven.",
            "sport_strategy_promotion_lab",
            False,
        )

    if context_worklist:
        sports = Counter(str(row.get("Sport") or "unknown") for row in context_worklist)
        evidence = "; ".join(f"{sport}={count}" for sport, count in sports.most_common())
        _add(
            rows,
            "MEDIUM",
            "Sport Context Gates",
            "Some sport-lab rows are blocked by missing required context.",
            f"context_rows={len(context_worklist)}; {evidence}",
            "Fill the exact missing fields in context_collection_worklist, rebuild sport context gates, then rebuild the final decision guard.",
            "context_collection_worklist",
            False,
        )

    bad_name_rows = [
        row
        for row in guard
        if "bad_match_name" in str(row.get("HardVetoes") or "")
    ] + [
        row
        for row in context_completion
        if str(row.get("Status") or "") == "BAD_MATCH_NAME"
    ]
    if bad_name_rows:
        _add(
            rows,
            "HIGH",
            "Data Quality",
            "Placeholder or invalid participant names reached a downstream gate.",
            f"bad_name_rows={len(bad_name_rows)}",
            "Drop or repair rows with Home/Away/TBD-style names upstream; do not seed context rows for fake matches.",
            "final_decision_guard/context_override_completion",
            True,
        )

    incomplete_overrides = [
        row
        for row in context_completion
        if str(row.get("Status") or "") not in {"", "COMPLETE_READY_FOR_GATE_REBUILD"}
    ]
    if incomplete_overrides:
        sports = Counter(str(row.get("Sport") or "unknown") for row in incomplete_overrides)
        evidence = "; ".join(f"{sport}={count}" for sport, count in sports.most_common())
        _add(
            rows,
            "MEDIUM",
            "Context Overrides",
            "Manual context override rows are seeded but not complete.",
            f"incomplete_overrides={len(incomplete_overrides)}; {evidence}",
            "Use context_override_completion to fill the nearest rows first, then rebuild context gates and the final guard.",
            "context_override_completion",
            False,
        )

    recheck_now = result_counts.get("RESULT_SOURCE_REQUIRED", 0)
    if recheck_now > 0:
        _add(
            rows,
            "MEDIUM",
            "Result Tracking",
            "Some events need result/status source confirmation.",
            f"RESULT_SOURCE_REQUIRED={recheck_now}",
            "Keep those rows out of final review until the result checker has a valid event id and start state.",
            "result_recheck_schedule",
            False,
        )

    if _as_int(health.get("memory_finished_rows")) < 10:
        _add(
            rows,
            "MEDIUM",
            "Learning Memory",
            "Prediction memory is still cold.",
            f"memory_finished_rows={_as_int(health.get('memory_finished_rows'))}",
            "Keep thresholds conservative and record official outcomes before increasing confidence or stake suggestions.",
            "prediction_result_memory",
            False,
        )

    if _as_int(health.get("official_entries")) <= 0:
        _add(
            rows,
            "LOW",
            "Feedback Loop",
            "No official reviewed entries are recorded for this date.",
            "official_entries=0",
            "After human review, mark only real reviewed entries so future memory calibration is based on actual decisions.",
            "prediction_results",
            False,
        )

    stale_history = _as_int(health.get("stale_history_leagues"))
    if stale_history > 0:
        _add(
            rows,
            "MEDIUM",
            "Football Data",
            "Some football leagues were skipped because history is stale.",
            f"stale_history_leagues={stale_history}",
            "Refresh only the affected football-data league codes and keep stale leagues excluded until updated.",
            "daily_picks",
            False,
        )

    approved_negative_ev = [
        row
        for row in guard
        if str(row.get("FinalDecision") or "") == "APPROVED_FOR_HUMAN_REVIEW"
        and _as_float(row.get("EVPercent")) < 0
    ]
    if approved_negative_ev:
        _add(
            rows,
            "INFO",
            "Review Policy",
            "Approved rows are review exceptions, not automatic positive-EV entries.",
            f"approved_negative_ev={len(approved_negative_ev)}",
            "Keep GuardedStakeAmount at zero unless final human review confirms current price, source, and risk are acceptable.",
            "final_decision_guard",
            False,
        )

    watch_price = guard_counts.get("WATCH_PRICE_ONLY", 0)
    if watch_price > 0:
        _add(
            rows,
            "INFO",
            "Price Watch",
            "Some rows are only useful if price improves.",
            f"WATCH_PRICE_ONLY={watch_price}",
            "Monitor them only through the price watchlist; do not treat them as accepted candidates.",
            "price_target_watchlist",
            False,
        )

    if not rows:
        _add(
            rows,
            "OK",
            "System",
            "No blocking operational gaps detected.",
            f"advisor_rows={len(advisor)} guard_rows={len(guard)} linefeed_rows={linefeed_rows}",
            "Continue normal daily refresh and final human review only for approved rows.",
            "daily_project_gaps",
            False,
        )

    rows.sort(key=lambda row: (SEVERITY_ORDER.get(str(row.get("Severity")), 99), str(row.get("Area")), str(row.get("Gap"))))
    return rows


def _write_csv(rows: List[Dict[str, Any]], path: Path) -> None:
    fields = ["Severity", "Area", "Gap", "Evidence", "BestFix", "BlockingDecision", "SourceReport"]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _write_md(rows: List[Dict[str, Any]], target: date, path: Path) -> None:
    counts = Counter(str(row.get("Severity") or "EMPTY") for row in rows)
    blockers = [row for row in rows if row.get("BlockingDecision") == "YES"]
    lines = [
        "# Daily project gaps audit",
        f"- Date: {target.isoformat()}",
        f"- Gaps: {len(rows)}",
        f"- Blocking gaps: {len(blockers)}",
        "",
        "## Severity Counts",
        *[f"- {key}: {value}" for key, value in counts.most_common()],
        "",
        "## Best Fix Order",
        "| # | Severity | Area | Gap | Evidence | Best fix |",
        "| ---: | --- | --- | --- | --- | --- |",
    ]
    for idx, row in enumerate(rows[:40], start=1):
        lines.append(
            f"| {idx} | {row.get('Severity')} | {row.get('Area')} | {row.get('Gap')} | "
            f"{row.get('Evidence')} | {row.get('BestFix')} |"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit daily project gaps.")
    parser.add_argument("--date", default="today")
    parser.add_argument("--out-csv", default="")
    parser.add_argument("--out-md", default="")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    target = _target_date(args.date)
    rows = _build(target)
    out_csv = Path(args.out_csv) if args.out_csv else REPORTS_DIR / f"daily_project_gaps_{target.isoformat()}.csv"
    out_md = Path(args.out_md) if args.out_md else REPORTS_DIR / f"daily_project_gaps_{target.isoformat()}.md"
    _write_csv(rows, out_csv)
    _write_md(rows, target, out_md)
    counts = Counter(str(row.get("Severity") or "EMPTY") for row in rows)
    print(f"Wrote {out_csv}")
    print(f"Wrote {out_md}")
    print("gaps=" + " ".join(f"{key}={value}" for key, value in counts.most_common()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
