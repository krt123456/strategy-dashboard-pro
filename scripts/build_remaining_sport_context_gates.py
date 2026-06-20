#!/usr/bin/env python3
"""Build context gates for existing non-core watch sports.

This covers only sports that already exist in the project strategy profiles.
It does not add sports or odds providers.
"""
from __future__ import annotations

import argparse
import csv
import math
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Tuple

from sports_strategy_profiles import get_profile, normalize_sport_key
from sport_name_quality import has_bad_participant_pair

BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / "data"
REPORTS_DIR = BASE_DIR / "reports"
DEFAULT_CONTEXT = DATA_DIR / "sport_context_overrides.csv"

REQUIREMENTS: Dict[str, List[str]] = {
    "hockey": ["GoalieStatus", "RestStatus", "OvertimeMarketStatus", "EventIdStatus"],
    "handball": ["LeagueQualityStatus", "TeamStrengthStatus", "WeakLeagueStatus", "FixtureFreshnessStatus", "EventIdStatus"],
    "volleyball": ["RotationStatus", "InjuryStatus", "SetVolatilityStatus", "LeagueQualityStatus", "EventIdStatus"],
    "cricket": ["TossStatus", "PitchStatus", "LineupStatus", "FormatStatus", "WeatherStatus", "EventIdStatus"],
    "americanfootball": ["QBStatus", "InjuryStatus", "RestStatus", "WeatherStatus", "SpreadMovementStatus", "EventIdStatus"],
    "futsal": ["VolatilityStatus", "LineupStatus", "GoalProfileStatus", "LeagueQualityStatus", "EventIdStatus"],
    "darts": ["RecentFormStatus", "LegSetFormatStatus", "StageStatus", "EventIdStatus"],
    "snooker": ["FrameFormatStatus", "RecentFormStatus", "StageStatus", "EventIdStatus"],
}

GOOD_STATUS = {"confirmed", "known", "ok", "pass", "ready", "low_risk", "normal", "clear", "verified"}
BAD_STATUS = {"bad", "fail", "high_risk", "risk", "unknown", "missing", "out", "stale", "weak"}


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
        normalize_sport_key(row.get("Sport")),
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


def _evaluate(candidate: Dict[str, Any], context: Dict[str, Any], requirements: List[str]) -> Dict[str, Any]:
    sport = normalize_sport_key(candidate.get("Sport"))
    profile = get_profile(sport) or {}
    prob = _as_float(candidate.get("Prob"))
    odds = _as_float(candidate.get("PickOdds"))
    missing: List[str] = []
    weak: List[str] = []

    if not context:
        missing = requirements.copy()
        gate = "BLOCKED_CONTEXT_MISSING"
        score = 0
    else:
        for field in requirements:
            value = context.get(field)
            if _status_good(value):
                continue
            if _status_bad(value):
                missing.append(field)
            else:
                weak.append(field)
        complete = max(0, len(requirements) - len(set(missing)))
        score = int(round((complete / max(len(requirements), 1)) * 100))
        if missing:
            gate = "BLOCKED_CONTEXT_INCOMPLETE"
        elif weak:
            gate = "BLOCKED_CONTEXT_WEAK"
        else:
            gate = "CONTEXT_READY_BACKTEST_REQUIRED"

    threshold_notes: List[str] = []
    min_prob = _as_float(profile.get("min_prob"))
    max_odds = _as_float(profile.get("max_odds"))
    if min_prob is not None and (prob is None or prob < min_prob):
        threshold_notes.append(f"prob_below_{min_prob:.2f}")
    if max_odds is not None and odds is not None and odds > max_odds:
        threshold_notes.append(f"odds_above_{max_odds:.2f}")

    action = "KEEP_LAB_ONLY"
    if gate == "CONTEXT_READY_BACKTEST_REQUIRED" and not threshold_notes:
        action = "READY_FOR_SPORT_BACKTEST_ONLY"
    elif context:
        action = "COMPLETE_CONTEXT_BEFORE_BACKTEST"

    return {
        "ContextGate": gate,
        "ContextScore": score,
        "RequiredFields": ";".join(requirements),
        "MissingContext": ";".join(sorted(set(missing))) if missing else "none",
        "WeakContext": ";".join(sorted(set(weak))) if weak else "none",
        "ThresholdNotes": ";".join(threshold_notes) if threshold_notes else "thresholds_clear",
        "RecommendedAction": action,
    }


def _build(target: date, advisor_csv: Path, context_csv: Path) -> List[Dict[str, Any]]:
    context_by_key = _context_index(_read_csv(context_csv))
    rows: List[Dict[str, Any]] = []
    for candidate in _read_csv(advisor_csv):
        sport = normalize_sport_key(candidate.get("Sport"))
        requirements = REQUIREMENTS.get(sport)
        if not requirements:
            continue
        if str(candidate.get("Date") or "").strip()[:10] != target.isoformat():
            continue
        if has_bad_participant_pair(candidate.get("Home"), candidate.get("Away")):
            rows.append(
                {
                    "Date": candidate.get("Date") or "",
                    "Sport": sport,
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
                    "RequiredFields": "ValidHomeAwayNames",
                    "MissingContext": "ValidHomeAwayNames",
                    "WeakContext": "none",
                    "ThresholdNotes": "bad_or_placeholder_participant_name",
                    "RecommendedAction": "FIX_OR_DROP_SOURCE_MATCH_NAME",
                }
            )
            continue
        context = context_by_key.get(_key(candidate), {})
        gate = _evaluate(candidate, context, requirements)
        rows.append(
            {
                "Date": candidate.get("Date") or "",
                "Sport": sport,
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
    rows.sort(key=lambda row: (row["ContextGate"], row["Sport"], row["League"], row["Home"]))
    return rows


def _write_md(rows: List[Dict[str, Any]], target: date, advisor_csv: Path, context_csv: Path, path: Path) -> None:
    gate_counts: Dict[str, int] = {}
    sport_counts: Dict[str, int] = {}
    for row in rows:
        gate = str(row.get("ContextGate") or "EMPTY")
        sport = str(row.get("Sport") or "unknown")
        gate_counts[gate] = gate_counts.get(gate, 0) + 1
        sport_counts[sport] = sport_counts.get(sport, 0) + 1
    try:
        advisor_label = str(advisor_csv.resolve().relative_to(BASE_DIR))
    except Exception:
        advisor_label = str(advisor_csv)
    try:
        context_label = str(context_csv.resolve().relative_to(BASE_DIR))
    except Exception:
        context_label = str(context_csv)
    lines = [
        "# Remaining sport context gates",
        f"- Date: {target.isoformat()}",
        f"- Advisor source: `{advisor_label}`",
        f"- Manual context source: `{context_label}`",
        f"- Sport candidates checked: {len(rows)}",
        "- Rule: these are existing watch/partial sports only. No new sports or odds sources are introduced.",
        "",
        "## Gate Counts",
        *([f"- {gate}: {count}" for gate, count in sorted(gate_counts.items())] or ["- none: 0"]),
        "",
        "## Sport Counts",
        *([f"- {sport}: {count}" for sport, count in sorted(sport_counts.items())] or ["- none: 0"]),
        "",
        "| Sport | Match | Pick | Prob | Odds | Gate | Missing | Threshold notes | Action |",
        "| --- | --- | --- | ---: | ---: | --- | --- | --- | --- |",
    ]
    for row in rows:
        lines.append(
            f"| {row.get('Sport')} | {row.get('Home')} vs {row.get('Away')} | {row.get('Pick')} | "
            f"{row.get('Prob')} | {row.get('PickOdds')} | {row.get('ContextGate')} | "
            f"{row.get('MissingContext')} | {row.get('ThresholdNotes')} | {row.get('RecommendedAction')} |"
        )
    if not rows:
        lines.append("| - | - | - | - | - | - | - | - | - |")
    lines.extend(
        [
            "",
            "## Manual Context Columns",
            "- Fill `data/sport_context_overrides.csv` only from verified sources.",
            "- Required fields vary by sport and are listed in `RequiredFields` in the CSV output.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build context gates for remaining existing watch sports.")
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
    out_csv = Path(args.out_csv) if args.out_csv else REPORTS_DIR / f"remaining_sport_context_gate_{target.isoformat()}.csv"
    out_md = Path(args.out_md) if args.out_md else REPORTS_DIR / f"remaining_sport_context_gate_{target.isoformat()}.md"
    fields = [
        "Date",
        "Sport",
        "League",
        "Home",
        "Away",
        "Pick",
        "Prob",
        "PickOdds",
        "OneXBetStatus",
        "OneXBetEventId",
        "ContextGate",
        "ContextScore",
        "RequiredFields",
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
    print(f"remaining_context_rows={len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
