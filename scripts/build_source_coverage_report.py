#!/usr/bin/env python3
"""Score source/data coverage for each daily advisor candidate."""
from __future__ import annotations

import argparse
import csv
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Tuple

BASE_DIR = Path(__file__).resolve().parents[1]
REPORTS_DIR = BASE_DIR / "reports"

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


def _key(row: Dict[str, Any]) -> Tuple[str, str, str, str]:
    return (
        str(row.get("Date") or "").strip()[:10],
        str(row.get("Sport") or "").strip().lower(),
        str(row.get("Home") or "").strip().lower(),
        str(row.get("Away") or "").strip().lower(),
    )


def _result_map(rows: List[Dict[str, Any]]) -> Dict[Tuple[str, str, str, str], Dict[str, Any]]:
    return {_key(row): row for row in rows}


def _has(row: Dict[str, Any], field: str) -> bool:
    value = row.get(field)
    return value not in (None, "", "nan", "NaN")


def _score(row: Dict[str, Any], result: Dict[str, Any]) -> Tuple[int, List[str]]:
    score = 0
    missing: List[str] = []

    if _has(row, "Prob"):
        score += 15
    else:
        missing.append("probability")
    if _has(row, "PickOdds"):
        score += 15
    else:
        missing.append("price")
    if _has(row, "MinEntryOdds") and _has(row, "EVPercent"):
        score += 10
    else:
        missing.append("value_calc")
    if is_confirmed_1xbet_status(row.get("OneXBetStatus")):
        score += 20
    else:
        missing.append("confirmed_1xbet_event")
    if str(row.get("OneXBetOddsFreshness") or "") == "FRESH":
        score += 15
    elif _has(row, "OneXBetManualOdds"):
        missing.append("fresh_1xbet_price")
    if _has(result, "StartTimeLocal") or str(result.get("ResultStatus") or "") not in {"", "NO_EVENT_ID"}:
        score += 10
    else:
        missing.append("start_or_result_status")
    if _has(row, "Source"):
        score += 5
    else:
        missing.append("local_source")
    if _has(row, "GateBlockers"):
        score += 5
    else:
        missing.append("gate_explanation")
    if str(row.get("EntryReadiness") or ""):
        score += 5
    else:
        missing.append("entry_readiness")

    return min(score, 100), missing


def _grade(score: int) -> str:
    if score >= 85:
        return "A_DATA_COMPLETE"
    if score >= 65:
        return "B_USABLE_WITH_CAUTION"
    if score >= 45:
        return "C_SOURCE_GAPS"
    return "D_INSUFFICIENT"


def _build(advisor_rows: List[Dict[str, Any]], result_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    results = _result_map(result_rows)
    out: List[Dict[str, Any]] = []
    for row in advisor_rows:
        result = results.get(_key(row), {})
        score, missing = _score(row, result)
        out.append(
            {
                "Rank": row.get("Rank"),
                "Sport": row.get("Sport"),
                "Date": row.get("Date"),
                "Home": row.get("Home"),
                "Away": row.get("Away"),
                "Pick": row.get("Pick"),
                "CoverageScore": score,
                "CoverageGrade": _grade(score),
                "MissingCoverage": ";".join(missing) if missing else "none",
                "EntryReadiness": row.get("EntryReadiness"),
                "GateBlockers": row.get("GateBlockers"),
                "OneXBetStatus": row.get("OneXBetStatus"),
                "OneXBetFreshness": row.get("OneXBetOddsFreshness"),
                "ResultStatus": result.get("ResultStatus") or "",
            }
        )
    out.sort(key=lambda r: (-int(r["CoverageScore"]), int(str(r.get("Rank") or "999") or 999)))
    return out


def _write_csv(rows: List[Dict[str, Any]], path: Path) -> None:
    fields = [
        "Rank",
        "Sport",
        "Date",
        "Home",
        "Away",
        "Pick",
        "CoverageScore",
        "CoverageGrade",
        "MissingCoverage",
        "EntryReadiness",
        "GateBlockers",
        "OneXBetStatus",
        "OneXBetFreshness",
        "ResultStatus",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _write_md(rows: List[Dict[str, Any]], target: date, path: Path) -> None:
    grade_counts = Counter(str(row.get("CoverageGrade")) for row in rows)
    missing_counts: Counter[str] = Counter()
    for row in rows:
        for item in str(row.get("MissingCoverage") or "").split(";"):
            if item and item != "none":
                missing_counts[item] += 1
    lines = [
        "# Source coverage report",
        f"- Date: {target.isoformat()}",
        f"- Rows: {len(rows)}",
        "",
        "## Coverage Grades",
        *[f"- {key}: {value}" for key, value in grade_counts.most_common()],
        "",
        "## Most Common Gaps",
        *([f"- {key}: {value}" for key, value in missing_counts.most_common(12)] or ["- none: 0"]),
        "",
        "| Rank | Grade | Score | Match | Pick | Missing | Readiness | 1xBet |",
        "| ---: | --- | ---: | --- | --- | --- | --- | --- |",
    ]
    for row in rows[:80]:
        lines.append(
            f"| {row.get('Rank')} | {row.get('CoverageGrade')} | {row.get('CoverageScore')} | "
            f"{row.get('Home')} vs {row.get('Away')} | {row.get('Pick')} | {row.get('MissingCoverage')} | "
            f"{row.get('EntryReadiness')} | {row.get('OneXBetStatus')}/{row.get('OneXBetFreshness')} |"
        )
    if not rows:
        lines.append("| - | - | - | - | - | - | - | - |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build source coverage report.")
    parser.add_argument("--date", default="today")
    parser.add_argument("--advisor-csv", default="")
    parser.add_argument("--results-csv", default="")
    parser.add_argument("--out-csv", default="")
    parser.add_argument("--out-md", default="")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    target = _target_date(args.date)
    advisor_csv = Path(args.advisor_csv) if args.advisor_csv else REPORTS_DIR / f"daily_1xbet_value_advisor_{target.isoformat()}.csv"
    results_csv = Path(args.results_csv) if args.results_csv else REPORTS_DIR / f"prediction_results_{target.isoformat()}.csv"
    rows = _build(_read_csv(advisor_csv), _read_csv(results_csv))
    out_csv = Path(args.out_csv) if args.out_csv else REPORTS_DIR / f"source_coverage_{target.isoformat()}.csv"
    out_md = Path(args.out_md) if args.out_md else REPORTS_DIR / f"source_coverage_{target.isoformat()}.md"
    _write_csv(rows, out_csv)
    _write_md(rows, target, out_md)
    print(f"Wrote {out_csv}")
    print(f"Wrote {out_md}")
    print(f"coverage_rows={len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
