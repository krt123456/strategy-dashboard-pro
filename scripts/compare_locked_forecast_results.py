#!/usr/bin/env python3
"""Compare a locked daily forecast with checked prediction results."""
from __future__ import annotations

import argparse
import csv
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

BASE_DIR = Path(__file__).resolve().parents[1]
REPORTS_DIR = BASE_DIR / "reports"
LOCK_DIR = REPORTS_DIR / "locked_forecasts"


def _target_date(value: str) -> date:
    raw = (value or "today").strip().lower()
    today = date.today()
    if raw in {"today", "اليوم"}:
        return today
    if raw in {"yesterday", "امس", "أمس"}:
        return today - timedelta(days=1)
    if raw in {"tomorrow", "غدا", "غداً"}:
        return today + timedelta(days=1)
    return date.fromisoformat(raw)


def _read_csv(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _key(row: Dict[str, Any]) -> Tuple[str, str, str, str, str]:
    date_value = str(row.get("ForecastDate") or row.get("Date") or "").strip()[:10]
    return (
        date_value,
        str(row.get("Sport") or "").strip().lower(),
        str(row.get("Home") or "").strip().lower(),
        str(row.get("Away") or "").strip().lower(),
        str(row.get("Pick") or "").strip().lower(),
    )


def _write_csv(rows: Iterable[Dict[str, Any]], path: Path) -> None:
    rows = list(rows)
    fields = [
        "ForecastDate",
        "Rank",
        "Sport",
        "League",
        "Home",
        "Away",
        "Pick",
        "Prob",
        "CurrentOdds",
        "FinalDecision",
        "LockClass",
        "OfficialEntry",
        "ResultStatus",
        "HomeScore",
        "AwayScore",
        "PickOutcome",
        "ResultSource",
        "OutcomeClass",
        "LearningAction",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _outcome_class(lock: Dict[str, Any], result: Dict[str, Any]) -> Tuple[str, str]:
    outcome = str(result.get("PickOutcome") or "")
    status = str(result.get("ResultStatus") or "")
    lock_class = str(lock.get("LockClass") or "")
    if outcome == "CORRECT":
        if str(lock.get("OfficialEntry") or "") == "yes":
            return "OFFICIAL_CORRECT", "Keep gate; still audit price/value quality."
        if lock_class == "LAB_FORECAST_LOCKED":
            return "LAB_CORRECT", "Keep as lab evidence only; add to sport/context sample before promotion."
        return "RAW_CORRECT", "Use as learning evidence; source/value veto still explains no entry."
    if outcome == "WRONG":
        if str(lock.get("OfficialEntry") or "") == "yes":
            return "OFFICIAL_WRONG", "Tighten entry gate immediately and inspect root cause."
        if lock_class == "LAB_FORECAST_LOCKED":
            return "LAB_WRONG", "Lab veto was justified; keep sport unpromoted and add context requirements."
        return "RAW_WRONG", "Tighten this segment; do not promote until source/value and memory improve."
    if status in {"NO_EVENT_ID", "RESULT_SOURCE_REQUIRED", ""}:
        return "RESULT_SOURCE_MISSING", "Find independent result source or event id before learning from it."
    if status == "STRATEGY_LAB_RESULT_DEFERRED":
        return "LAB_RESULT_DEFERRED", "Result was intentionally deferred; collect only after source is available."
    return "PENDING", "Recheck after finish or when result source becomes available."


def _build(lock_rows: List[Dict[str, Any]], result_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    results = {_key(row): row for row in result_rows}
    out: List[Dict[str, Any]] = []
    for lock in lock_rows:
        result = results.get(_key(lock), {})
        outcome_class, action = _outcome_class(lock, result)
        out.append(
            {
                "ForecastDate": lock.get("ForecastDate") or "",
                "Rank": lock.get("Rank") or "",
                "Sport": lock.get("Sport") or "",
                "League": lock.get("League") or "",
                "Home": lock.get("Home") or "",
                "Away": lock.get("Away") or "",
                "Pick": lock.get("Pick") or "",
                "Prob": lock.get("Prob") or "",
                "CurrentOdds": lock.get("CurrentOdds") or "",
                "FinalDecision": lock.get("FinalDecision") or "",
                "LockClass": lock.get("LockClass") or "",
                "OfficialEntry": lock.get("OfficialEntry") or "",
                "ResultStatus": result.get("ResultStatus") or "RESULT_NOT_CHECKED",
                "HomeScore": result.get("HomeScore") or "",
                "AwayScore": result.get("AwayScore") or "",
                "PickOutcome": result.get("PickOutcome") or "PENDING",
                "ResultSource": result.get("ResultSource") or "",
                "OutcomeClass": outcome_class,
                "LearningAction": action,
            }
        )
    return out


def _sport_summary(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    stats: Dict[str, Dict[str, Counter[str]]] = defaultdict(lambda: {"outcomes": Counter(), "classes": Counter()})
    for row in rows:
        sport = str(row.get("Sport") or "unknown")
        stats[sport]["outcomes"][str(row.get("PickOutcome") or "PENDING")] += 1
        stats[sport]["classes"][str(row.get("OutcomeClass") or "UNKNOWN")] += 1
    out = []
    for sport, buckets in stats.items():
        outcomes = buckets["outcomes"]
        classes = buckets["classes"]
        finished = outcomes.get("CORRECT", 0) + outcomes.get("WRONG", 0)
        acc = (outcomes.get("CORRECT", 0) / finished) if finished else None
        out.append(
            {
                "Sport": sport,
                "Finished": finished,
                "Correct": outcomes.get("CORRECT", 0),
                "Wrong": outcomes.get("WRONG", 0),
                "Pending": outcomes.get("PENDING", 0),
                "Accuracy": acc,
                "MissingSource": classes.get("RESULT_SOURCE_MISSING", 0),
                "LabDeferred": classes.get("LAB_RESULT_DEFERRED", 0),
            }
        )
    out.sort(key=lambda r: (-int(r["Finished"]), str(r["Sport"])))
    return out


def _write_md(rows: List[Dict[str, Any]], target: date, lock_csv: Path, results_csv: Path, path: Path) -> None:
    outcomes = Counter(str(row.get("PickOutcome") or "PENDING") for row in rows)
    classes = Counter(str(row.get("OutcomeClass") or "UNKNOWN") for row in rows)
    decisions = Counter(str(row.get("FinalDecision") or "UNKNOWN") for row in rows)
    sport_summary = _sport_summary(rows)
    finished = outcomes.get("CORRECT", 0) + outcomes.get("WRONG", 0)
    accuracy = (outcomes.get("CORRECT", 0) / finished) if finished else None

    lines = [
        "# Locked forecast result comparison",
        f"- Forecast date: {target.isoformat()}",
        f"- Lock CSV: `{lock_csv.relative_to(BASE_DIR) if lock_csv.is_relative_to(BASE_DIR) else lock_csv}`",
        f"- Results CSV: `{results_csv.relative_to(BASE_DIR) if results_csv.is_relative_to(BASE_DIR) else results_csv}`",
        f"- Locked rows compared: {len(rows)}",
        f"- Finished: {finished}",
        f"- Correct: {outcomes.get('CORRECT', 0)}",
        f"- Wrong: {outcomes.get('WRONG', 0)}",
        f"- Pending: {outcomes.get('PENDING', 0)}",
        f"- Accuracy: {'n/a' if accuracy is None else f'{accuracy:.2%}'}",
        "",
        "## Outcome Classes",
        *([f"- {key}: {value}" for key, value in classes.most_common()] or ["- none: 0"]),
        "",
        "## Original Decisions",
        *([f"- {key}: {value}" for key, value in decisions.most_common()] or ["- none: 0"]),
        "",
        "## Sport Summary",
        "| Sport | Finished | Correct | Wrong | Pending | Accuracy | Missing source | Lab deferred |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in sport_summary:
        acc = row["Accuracy"]
        lines.append(
            f"| {row['Sport']} | {row['Finished']} | {row['Correct']} | {row['Wrong']} | {row['Pending']} | "
            f"{'n/a' if acc is None else f'{acc:.2%}'} | {row['MissingSource']} | {row['LabDeferred']} |"
        )
    if not sport_summary:
        lines.append("| - | - | - | - | - | - | - | - |")

    lines.extend(
        [
            "",
            "## Finished Rows",
            "| # | Class | Sport | Match | Pick | Score | Action |",
            "| ---: | --- | --- | --- | --- | --- | --- |",
        ]
    )
    finished_rows = [r for r in rows if r.get("PickOutcome") in {"CORRECT", "WRONG"}]
    for row in finished_rows[:60]:
        score = f"{row.get('HomeScore')}-{row.get('AwayScore')}" if row.get("HomeScore") or row.get("AwayScore") else ""
        lines.append(
            f"| {row.get('Rank')} | {row.get('OutcomeClass')} | {row.get('Sport')} | {row.get('Home')} vs {row.get('Away')} | "
            f"{row.get('Pick')} | {score} | {row.get('LearningAction')} |"
        )
    if not finished_rows:
        lines.append("| - | - | - | - | - | - | - |")

    lines.extend(
        [
            "",
            "## Missing Results To Fix First",
            "| # | Status | Sport | Match | Pick | Source |",
            "| ---: | --- | --- | --- | --- | --- |",
        ]
    )
    missing = [r for r in rows if r.get("OutcomeClass") in {"RESULT_SOURCE_MISSING", "LAB_RESULT_DEFERRED", "PENDING"}]
    for row in missing[:80]:
        lines.append(
            f"| {row.get('Rank')} | {row.get('ResultStatus')} | {row.get('Sport')} | {row.get('Home')} vs {row.get('Away')} | "
            f"{row.get('Pick')} | {row.get('ResultSource')} |"
        )
    if not missing:
        lines.append("| - | - | - | - | - | - |")

    lines.extend(
        [
            "",
            "## Rule",
            "- Do not update strategy from pending rows.",
            "- Wrong lab rows strengthen the lab-only veto.",
            "- Correct lab rows only become promotion evidence after enough finished locked samples exist.",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare locked forecast against checked results.")
    parser.add_argument("--date", default="yesterday")
    parser.add_argument("--lock-csv", default="")
    parser.add_argument("--results-csv", default="")
    parser.add_argument("--out-csv", default="")
    parser.add_argument("--out-md", default="")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    target = _target_date(args.date)
    day = target.isoformat()
    lock_csv = Path(args.lock_csv) if args.lock_csv else LOCK_DIR / f"forecast_lock_{day}.csv"
    results_csv = Path(args.results_csv) if args.results_csv else REPORTS_DIR / f"prediction_results_{day}.csv"
    out_csv = Path(args.out_csv) if args.out_csv else REPORTS_DIR / f"locked_forecast_result_comparison_{day}.csv"
    out_md = Path(args.out_md) if args.out_md else REPORTS_DIR / f"locked_forecast_result_comparison_{day}.md"
    rows = _build(_read_csv(lock_csv), _read_csv(results_csv))
    _write_csv(rows, out_csv)
    _write_md(rows, target, lock_csv, results_csv, out_md)
    outcomes = Counter(str(row.get("PickOutcome") or "PENDING") for row in rows)
    print(f"Wrote {out_csv}")
    print(f"Wrote {out_md}")
    print("outcomes=" + " ".join(f"{key}={value}" for key, value in outcomes.most_common()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
