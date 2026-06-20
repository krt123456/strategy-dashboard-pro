#!/usr/bin/env python3
"""Build a tennis player/context gate for daily watch-only candidates."""
from __future__ import annotations

import argparse
import csv
import math
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Tuple

from sport_name_quality import has_bad_participant_pair

BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / "data"
REPORTS_DIR = BASE_DIR / "reports"
DEFAULT_CONTEXT = DATA_DIR / "tennis_context_overrides.csv"

CONTEXT_FIELDS = [
    "PickRecentFormStatus",
    "OpponentRecentFormStatus",
    "SurfaceFitStatus",
    "InjuryStatus",
    "WithdrawalStatus",
    "RoundStatus",
    "EventIdStatus",
]
GOOD_STATUS = {"confirmed", "known", "ok", "pass", "ready", "low_risk", "normal", "clear"}
BAD_STATUS = {"bad", "fail", "high_risk", "risk", "unknown", "missing", "out", "withdrawal_risk", "injury_risk"}


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


def _write_csv(rows: List[Dict[str, Any]], path: Path, fields: List[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


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


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _key(row: Dict[str, Any]) -> Tuple[str, str, str, str, str]:
    return (
        str(row.get("Date") or "").strip()[:10],
        _norm(row.get("League")),
        _norm(row.get("Home")),
        _norm(row.get("Away")),
        _norm(row.get("Pick")),
    )


def _context_index(rows: List[Dict[str, Any]]) -> Dict[Tuple[str, str, str, str, str], Dict[str, Any]]:
    return {_key(row): row for row in rows}


def _status_good(value: Any) -> bool:
    raw = str(value or "").strip().lower().replace(" ", "_")
    return bool(raw) and raw in GOOD_STATUS


def _status_bad(value: Any) -> bool:
    raw = str(value or "").strip().lower().replace(" ", "_")
    return (not raw) or raw in BAD_STATUS


def _surface_from_league(value: Any) -> str:
    league = str(value or "").lower()
    if "madrid" in league:
        return "clay"
    if "grass" in league:
        return "grass"
    if "hard" in league or "itf" in league or "challenger" in league:
        return "hard_or_event_specific"
    return "unknown"


def _evaluate(candidate: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    prob = _as_float(candidate.get("Prob"))
    odds = _as_float(candidate.get("PickOdds"))
    missing: List[str] = []
    weak: List[str] = []

    if not context:
        missing = CONTEXT_FIELDS.copy()
        gate = "BLOCKED_CONTEXT_MISSING"
        score = 0
    else:
        for field in CONTEXT_FIELDS:
            value = context.get(field)
            if _status_good(value):
                continue
            if _status_bad(value):
                missing.append(field)
            else:
                weak.append(field)
        complete = max(0, len(CONTEXT_FIELDS) - len(set(missing)))
        score = int(round((complete / len(CONTEXT_FIELDS)) * 100))
        if "WithdrawalStatus" in missing:
            gate = "BLOCKED_WITHDRAWAL_RISK"
        elif "InjuryStatus" in missing:
            gate = "BLOCKED_INJURY_RISK"
        elif missing:
            gate = "BLOCKED_CONTEXT_INCOMPLETE"
        elif weak:
            gate = "BLOCKED_CONTEXT_WEAK"
        else:
            gate = "CONTEXT_READY_BACKTEST_REQUIRED"

    threshold_notes: List[str] = []
    if prob is None or prob < 0.70:
        threshold_notes.append("prob_below_70_learning_floor")
    if odds is not None and odds > 1.65:
        threshold_notes.append("odds_above_tennis_watch_band")
    surface = context.get("Surface") or _surface_from_league(candidate.get("League"))

    action = "KEEP_LAB_ONLY"
    if gate == "CONTEXT_READY_BACKTEST_REQUIRED" and not threshold_notes:
        action = "READY_FOR_TENNIS_BACKTEST_ONLY"
    elif context:
        action = "COMPLETE_PLAYER_CONTEXT_BEFORE_BACKTEST"

    return {
        "ContextGate": gate,
        "ContextScore": score,
        "Surface": surface,
        "MissingContext": ";".join(sorted(set(missing))) if missing else "none",
        "WeakContext": ";".join(sorted(set(weak))) if weak else "none",
        "ThresholdNotes": ";".join(threshold_notes) if threshold_notes else "thresholds_clear",
        "RecommendedAction": action,
    }


def _build(target: date, advisor_csv: Path, context_csv: Path) -> List[Dict[str, Any]]:
    context_by_key = _context_index(_read_csv(context_csv))
    rows: List[Dict[str, Any]] = []
    for candidate in _read_csv(advisor_csv):
        if str(candidate.get("Sport") or "").strip().lower() != "tennis":
            continue
        if str(candidate.get("Date") or "").strip()[:10] != target.isoformat():
            continue
        if has_bad_participant_pair(candidate.get("Home"), candidate.get("Away")):
            rows.append(
                {
                    "Date": candidate.get("Date") or "",
                    "League": candidate.get("League") or "",
                    "Home": candidate.get("Home") or "",
                    "Away": candidate.get("Away") or "",
                    "Pick": candidate.get("Pick") or "",
                    "Prob": candidate.get("Prob") or "",
                    "PickOdds": candidate.get("PickOdds") or "",
                    "OneXBetStatus": candidate.get("OneXBetStatus") or "",
                    "OneXBetEventId": candidate.get("OneXBetEventId") or candidate.get("OneXBetManualEventId") or "",
                    "ContextSource": "",
                    "Notes": "",
                    "ContextGate": "BLOCKED_BAD_MATCH_NAME",
                    "ContextScore": 0,
                    "Surface": _surface_from_league(candidate.get("League")),
                    "MissingContext": "ValidHomeAwayNames",
                    "WeakContext": "none",
                    "ThresholdNotes": "bad_or_placeholder_participant_name",
                    "RecommendedAction": "FIX_OR_DROP_SOURCE_MATCH_NAME",
                }
            )
            continue
        context = context_by_key.get(_key(candidate), {})
        gate = _evaluate(candidate, context)
        rows.append(
            {
                "Date": candidate.get("Date") or "",
                "League": candidate.get("League") or "",
                "Home": candidate.get("Home") or "",
                "Away": candidate.get("Away") or "",
                "Pick": candidate.get("Pick") or "",
                "Prob": candidate.get("Prob") or "",
                "PickOdds": candidate.get("PickOdds") or "",
                "OneXBetStatus": candidate.get("OneXBetStatus") or "",
                "OneXBetEventId": candidate.get("OneXBetEventId") or candidate.get("OneXBetManualEventId") or "",
                "ContextSource": context.get("ContextSource") or "",
                "Notes": context.get("Notes") or "",
                **gate,
            }
        )
    rows.sort(key=lambda row: (row["ContextGate"], row["League"], row["Home"]))
    return rows


def _write_md(rows: List[Dict[str, Any]], target: date, advisor_csv: Path, context_csv: Path, path: Path) -> None:
    gate_counts: Dict[str, int] = {}
    for row in rows:
        gate = str(row.get("ContextGate") or "EMPTY")
        gate_counts[gate] = gate_counts.get(gate, 0) + 1
    try:
        advisor_label = str(advisor_csv.resolve().relative_to(BASE_DIR))
    except Exception:
        advisor_label = str(advisor_csv)
    try:
        context_label = str(context_csv.resolve().relative_to(BASE_DIR))
    except Exception:
        context_label = str(context_csv)
    lines = [
        "# Tennis player/context gate",
        f"- Date: {target.isoformat()}",
        f"- Advisor source: `{advisor_label}`",
        f"- Manual context source: `{context_label}`",
        f"- Tennis candidates checked: {len(rows)}",
        "- Rule: tennis remains watch-only until player form, surface fit, injury/withdrawal, round, and event id are verified.",
        "",
        "## Gate Counts",
        *([f"- {gate}: {count}" for gate, count in sorted(gate_counts.items())] or ["- none: 0"]),
        "",
        "| Match | Pick | Prob | Odds | Surface | Gate | Missing | Threshold notes | Action |",
        "| --- | --- | ---: | ---: | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        lines.append(
            f"| {row.get('Home')} vs {row.get('Away')} | {row.get('Pick')} | {row.get('Prob')} | {row.get('PickOdds')} | "
            f"{row.get('Surface')} | {row.get('ContextGate')} | {row.get('MissingContext')} | "
            f"{row.get('ThresholdNotes')} | {row.get('RecommendedAction')} |"
        )
    if not rows:
        lines.append("| - | - | - | - | - | - | - | - | - |")
    lines.extend(
        [
            "",
            "## Manual Context Columns",
            "- Fill `data/tennis_context_overrides.csv` only from verified sources.",
            "- Required fields: pick/opponent recent form, surface fit, injury, withdrawal, round, and event id status.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build tennis player/context gate.")
    parser.add_argument("--date", default="today")
    parser.add_argument("--advisor-csv", default="")
    parser.add_argument("--context-csv", default=str(DEFAULT_CONTEXT))
    parser.add_argument("--out-csv", default="")
    parser.add_argument("--out-md", default="")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    target = _target_date(args.date)
    advisor_csv = Path(args.advisor_csv) if args.advisor_csv else REPORTS_DIR / f"daily_1xbet_value_advisor_{target.isoformat()}.csv"
    context_csv = Path(args.context_csv)
    rows = _build(target, advisor_csv, context_csv)
    out_csv = Path(args.out_csv) if args.out_csv else REPORTS_DIR / f"tennis_context_gate_{target.isoformat()}.csv"
    out_md = Path(args.out_md) if args.out_md else REPORTS_DIR / f"tennis_context_gate_{target.isoformat()}.md"
    fields = [
        "Date",
        "League",
        "Home",
        "Away",
        "Pick",
        "Prob",
        "PickOdds",
        "OneXBetStatus",
        "OneXBetEventId",
        "Surface",
        "ContextGate",
        "ContextScore",
        "MissingContext",
        "WeakContext",
        "ThresholdNotes",
        "RecommendedAction",
        "ContextSource",
        "Notes",
    ]
    _write_csv(rows, out_csv, fields)
    _write_md(rows, target, advisor_csv, context_csv, out_md)
    print(f"Wrote {out_csv}")
    print(f"Wrote {out_md}")
    print(f"tennis_context_rows={len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
