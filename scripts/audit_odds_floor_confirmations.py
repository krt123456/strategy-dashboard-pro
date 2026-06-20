#!/usr/bin/env python3
"""Audit 1.30+ candidates so unconfirmed odds cannot silently persist."""
from __future__ import annotations

import argparse
import csv
import math
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List


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


def _row_date(value: Any) -> date | None:
    raw = str(value or "").strip()[:10]
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except Exception:
        return None


def _classify(row: Dict[str, Any], min_review_odds: float, today: date) -> str:
    odds = _as_float(row.get("CurrentOdds"))
    status = str(row.get("OneXBetStatus") or "")
    freshness = str(row.get("OneXBetFreshness") or "")
    decision = str(row.get("FinalDecision") or "")
    timing = str(row.get("EventTimingStatus") or "")
    result_status = str(row.get("ResultStatus") or "")
    event_date = _row_date(row.get("Date"))
    is_floor = str(row.get("OddsFloorCandidate") or "").lower() == "yes" or (
        odds is not None and odds >= min_review_odds
    )
    if not is_floor:
        return "BELOW_FLOOR"
    if (
        timing in {"STARTED_OR_EXPIRED", "CLOSE_TO_START"}
        or result_status in {"FINISHED", "FINISHED_OR_LIVE_SCORE"}
        or (event_date is not None and event_date < today)
    ):
        return "TIME_BLOCKED_NO_REPAIR"
    if not is_confirmed_1xbet_status(status) or decision == "ODDS_FLOOR_MANUAL_CHECK":
        return "UNCONFIRMED_NEEDS_REPAIR"
    if freshness != "FRESH":
        if decision == "APPROVED_FOR_HUMAN_REVIEW":
            return "STALE_PRICE_RECHECK_REQUIRED"
        return "CONFIRMED_BLOCKED_BY_GUARD"
    if decision == "APPROVED_FOR_HUMAN_REVIEW":
        return "APPROVED_REVIEW"
    return "CONFIRMED_BLOCKED_BY_GUARD"


def _build(rows: List[Dict[str, Any]], min_review_odds: float) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    today = date.today()
    for row in rows:
        status = _classify(row, min_review_odds, today)
        if status == "BELOW_FLOOR":
            continue
        out.append(
            {
                "Rank": row.get("Rank"),
                "Status": status,
                "Sport": row.get("Sport"),
                "Date": row.get("Date"),
                "League": row.get("League"),
                "Home": row.get("Home"),
                "Away": row.get("Away"),
                "Pick": row.get("Pick"),
                "CurrentOdds": row.get("CurrentOdds"),
                "OneXBetStatus": row.get("OneXBetStatus"),
                "OneXBetFreshness": row.get("OneXBetFreshness"),
                "EventTimingStatus": row.get("EventTimingStatus"),
                "ResultStatus": row.get("ResultStatus"),
                "FinalDecision": row.get("FinalDecision"),
                "HardVetoes": row.get("HardVetoes"),
                "NextAction": (
                    "RUN_PUBLIC_SYNC_OR_LINEFEED_REFRESH"
                    if status == "UNCONFIRMED_NEEDS_REPAIR"
                    else row.get("NextAction")
                ),
            }
        )
    out.sort(key=lambda r: (r["Status"] != "UNCONFIRMED_NEEDS_REPAIR", int(float(r.get("Rank") or 999))))
    return out


def _write_csv(rows: List[Dict[str, Any]], path: Path) -> None:
    fields = [
        "Rank",
        "Status",
        "Sport",
        "Date",
        "League",
        "Home",
        "Away",
        "Pick",
        "CurrentOdds",
        "OneXBetStatus",
        "OneXBetFreshness",
        "EventTimingStatus",
        "ResultStatus",
        "FinalDecision",
        "HardVetoes",
        "NextAction",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _write_md(rows: List[Dict[str, Any]], target: date, path: Path) -> None:
    counts = Counter(str(row.get("Status")) for row in rows)
    unresolved = counts.get("UNCONFIRMED_NEEDS_REPAIR", 0)
    lines = [
        "# 1xBet odds-floor confirmation audit",
        f"- Date: {target.isoformat()}",
        f"- 1.30+ rows audited: {len(rows)}",
        f"- Unconfirmed 1.30+ rows: {unresolved}",
        f"- Approved for final human review: {counts.get('APPROVED_REVIEW', 0)}",
        f"- Confirmed but blocked by guard: {counts.get('CONFIRMED_BLOCKED_BY_GUARD', 0)}",
        f"- Stale price recheck required: {counts.get('STALE_PRICE_RECHECK_REQUIRED', 0)}",
        f"- Time blocked/no repair needed: {counts.get('TIME_BLOCKED_NO_REPAIR', 0)}",
        "",
        "## Status Counts",
        *([f"- {key}: {value}" for key, value in counts.most_common()] or ["- none: 0"]),
        "",
        "| Status | Match | Pick | Odds | 1xBet | Result | Guard | Next Action |",
        "| --- | --- | --- | ---: | --- | --- | --- | --- |",
    ]
    for row in rows[:80]:
        lines.append(
            f"| {row.get('Status')} | {row.get('Home')} vs {row.get('Away')} | {row.get('Pick')} | "
            f"{row.get('CurrentOdds')} | {row.get('OneXBetStatus')}/{row.get('OneXBetFreshness')} | "
            f"{row.get('ResultStatus')} | {row.get('FinalDecision')} | {row.get('NextAction')} |"
        )
    if not rows:
        lines.append("| - | - | - | - | - | - | - | - |")
    lines.extend(
        [
            "",
            "## Permanent Rule",
            "- Any reviewable row at or above the odds floor must be confirmed/fresh from 1xBet; unconfirmed rows are listed as `UNCONFIRMED_NEEDS_REPAIR`.",
            "- `CONFIRMED_BLOCKED_BY_GUARD` means the event/price was confirmed, but another safety gate still blocks it from review.",
            "- `STALE_PRICE_RECHECK_REQUIRED` means a row would otherwise be reviewable but needs a fresh live price first.",
            "- `TIME_BLOCKED_NO_REPAIR` means the event is already started/too close, so price repair is no longer useful for entry.",
            "- Past-date rows with unknown start time are also time-blocked; they should move to result-source repair, not live-price repair.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit 1.30+ 1xBet confirmation state.")
    parser.add_argument("--date", default="today")
    parser.add_argument("--min-review-odds", type=float, default=1.30)
    parser.add_argument("--guard-csv", default="")
    parser.add_argument("--out-csv", default="")
    parser.add_argument("--out-md", default="")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    target = _target_date(args.date)
    day = target.isoformat()
    guard_csv = Path(args.guard_csv) if args.guard_csv else REPORTS_DIR / f"final_decision_guard_{day}.csv"
    rows = _build(_read_csv(guard_csv), args.min_review_odds)
    out_csv = Path(args.out_csv) if args.out_csv else REPORTS_DIR / f"odds_floor_confirmation_audit_{day}.csv"
    out_md = Path(args.out_md) if args.out_md else REPORTS_DIR / f"odds_floor_confirmation_audit_{day}.md"
    _write_csv(rows, out_csv)
    _write_md(rows, target, out_md)
    unresolved = sum(1 for row in rows if row.get("Status") == "UNCONFIRMED_NEEDS_REPAIR")
    print(f"Wrote {out_csv}")
    print(f"Wrote {out_md}")
    print(f"odds_floor_rows={len(rows)} unresolved={unresolved}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
