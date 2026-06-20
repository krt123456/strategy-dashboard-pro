#!/usr/bin/env python3
"""Build a baseball context gate for daily watch-only candidates.

The script does not predict baseball by itself. It checks whether a baseball
candidate has the minimum context needed before the sport can ever leave lab
mode: pitchers, lineups, bullpen/rest, weather/park, and league split risk.
"""
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
DEFAULT_CONTEXT = DATA_DIR / "baseball_context_overrides.csv"

CONTEXT_FIELDS = [
    "HomeProbablePitcher",
    "AwayProbablePitcher",
    "LineupStatus",
    "BullpenRestStatus",
    "WeatherStatus",
    "ParkFactorStatus",
]
GOOD_STATUS = {"confirmed", "known", "ok", "pass", "ready", "low_risk", "normal"}
BAD_STATUS = {"bad", "fail", "high_risk", "risk", "unknown", "missing", "out"}


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


def _key(row: Dict[str, Any]) -> Tuple[str, str, str, str]:
    return (
        str(row.get("Date") or "").strip()[:10],
        _norm(row.get("League")),
        _norm(row.get("Home")),
        _norm(row.get("Away")),
    )


def _context_index(rows: List[Dict[str, Any]]) -> Dict[Tuple[str, str, str, str], Dict[str, Any]]:
    return {_key(row): row for row in rows}


def _league_type(value: Any) -> str:
    league = str(value or "").lower()
    if "ncaa" in league or "college" in league:
        return "NCAA"
    if "mlb" in league or "major league" in league:
        return "MLB"
    return "OTHER"


def _status_good(value: Any) -> bool:
    raw = str(value or "").strip().lower().replace(" ", "_")
    return bool(raw) and raw in GOOD_STATUS


def _status_bad(value: Any) -> bool:
    raw = str(value or "").strip().lower().replace(" ", "_")
    return (not raw) or raw in BAD_STATUS


def _evaluate(candidate: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    league_type = _league_type(candidate.get("League"))
    prob = _as_float(candidate.get("Prob"))
    odds = _as_float(candidate.get("PickOdds"))
    missing: List[str] = []
    weak: List[str] = []

    if not context:
        missing = CONTEXT_FIELDS.copy()
        if league_type == "NCAA":
            missing.append("RotationRisk")
        gate = "BLOCKED_CONTEXT_MISSING"
        score = 0
    else:
        for field in CONTEXT_FIELDS:
            value = context.get(field)
            if field.endswith("Pitcher"):
                if not str(value or "").strip():
                    missing.append(field)
            elif _status_good(value):
                continue
            elif _status_bad(value):
                missing.append(field)
            else:
                weak.append(field)
        rotation = str(context.get("RotationRisk") or "").strip().lower().replace(" ", "_")
        if league_type == "NCAA" and rotation not in {"low", "low_risk", "normal", "confirmed"}:
            missing.append("RotationRisk")
        if _status_bad(context.get("WeatherStatus")):
            weak.append("WeatherStatus")
        complete = max(0, len(CONTEXT_FIELDS) - len(set(missing)))
        score = int(round((complete / len(CONTEXT_FIELDS)) * 100))
        if "RotationRisk" in missing:
            gate = "BLOCKED_ROTATION_RISK"
        elif missing:
            gate = "BLOCKED_CONTEXT_INCOMPLETE"
        elif weak:
            gate = "BLOCKED_CONTEXT_WEAK"
        else:
            gate = "CONTEXT_READY_BACKTEST_REQUIRED"

    threshold_notes: List[str] = []
    if prob is None or prob < 0.66:
        threshold_notes.append("prob_below_66")
    if odds is not None and odds > 1.70:
        threshold_notes.append("odds_above_1.70")
    if league_type == "NCAA":
        threshold_notes.append("ncaa_split_required")

    action = "KEEP_LAB_ONLY"
    if gate == "CONTEXT_READY_BACKTEST_REQUIRED" and not threshold_notes:
        action = "READY_FOR_BASEBALL_BACKTEST_ONLY"
    elif context:
        action = "COMPLETE_CONTEXT_BEFORE_BACKTEST"

    return {
        "ContextGate": gate,
        "ContextScore": score,
        "LeagueType": league_type,
        "MissingContext": ";".join(sorted(set(missing))) if missing else "none",
        "WeakContext": ";".join(sorted(set(weak))) if weak else "none",
        "ThresholdNotes": ";".join(threshold_notes) if threshold_notes else "thresholds_clear",
        "RecommendedAction": action,
    }


def _build(target: date, advisor_csv: Path, context_csv: Path) -> List[Dict[str, Any]]:
    context_by_key = _context_index(_read_csv(context_csv))
    rows: List[Dict[str, Any]] = []
    for candidate in _read_csv(advisor_csv):
        if str(candidate.get("Sport") or "").strip().lower() != "baseball":
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
                    "LeagueType": _league_type(candidate.get("League")),
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
        "# Baseball pitcher/context gate",
        f"- Date: {target.isoformat()}",
        f"- Advisor source: `{advisor_label}`",
        f"- Manual context source: `{context_label}`",
        f"- Baseball candidates checked: {len(rows)}",
        "- Rule: baseball remains lab-only until this gate has complete context and a separate MLB/NCAA backtest passes.",
        "",
        "## Gate Counts",
        *([f"- {gate}: {count}" for gate, count in sorted(gate_counts.items())] or ["- none: 0"]),
        "",
        "| Match | Pick | Prob | Odds | League type | Gate | Missing | Threshold notes | Action |",
        "| --- | --- | ---: | ---: | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        lines.append(
            f"| {row.get('Home')} vs {row.get('Away')} | {row.get('Pick')} | {row.get('Prob')} | {row.get('PickOdds')} | "
            f"{row.get('LeagueType')} | {row.get('ContextGate')} | {row.get('MissingContext')} | "
            f"{row.get('ThresholdNotes')} | {row.get('RecommendedAction')} |"
        )
    if not rows:
        lines.append("| - | - | - | - | - | - | - | - | - |")
    lines.extend(
        [
            "",
            "## Manual Context Columns",
            "- Fill `data/baseball_context_overrides.csv` only from verified sources.",
            "- Required fields: home/away probable pitchers, lineup status, bullpen rest, weather, park factor, and NCAA rotation risk where applicable.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build baseball pitcher/context gate.")
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
    out_csv = Path(args.out_csv) if args.out_csv else REPORTS_DIR / f"baseball_context_gate_{target.isoformat()}.csv"
    out_md = Path(args.out_md) if args.out_md else REPORTS_DIR / f"baseball_context_gate_{target.isoformat()}.md"
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
        "LeagueType",
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
    print(f"baseball_context_rows={len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
