#!/usr/bin/env python3
"""Build a focused worklist for missing sport-context fields."""
from __future__ import annotations

import argparse
import csv
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List

BASE_DIR = Path(__file__).resolve().parents[1]
REPORTS_DIR = BASE_DIR / "reports"

DEFAULT_ACCEPTED_STATUSES = "confirmed|known|ok|pass|ready|low_risk|normal|clear|verified"

FIELD_GUIDANCE = {
    "HomeProbablePitcher": "verified non-empty probable pitcher name",
    "AwayProbablePitcher": "verified non-empty probable pitcher name",
    "LineupStatus": DEFAULT_ACCEPTED_STATUSES,
    "BullpenRestStatus": DEFAULT_ACCEPTED_STATUSES,
    "WeatherStatus": DEFAULT_ACCEPTED_STATUSES,
    "ParkFactorStatus": DEFAULT_ACCEPTED_STATUSES,
    "RotationRisk": "low|low_risk|normal|confirmed",
    "PickRecentFormStatus": DEFAULT_ACCEPTED_STATUSES,
    "OpponentRecentFormStatus": DEFAULT_ACCEPTED_STATUSES,
    "SurfaceFitStatus": DEFAULT_ACCEPTED_STATUSES,
    "InjuryStatus": "clear|low_risk|confirmed|ok",
    "WithdrawalStatus": "clear|low_risk|confirmed|ok",
    "RoundStatus": DEFAULT_ACCEPTED_STATUSES,
    "GoalieStatus": DEFAULT_ACCEPTED_STATUSES,
    "RestStatus": DEFAULT_ACCEPTED_STATUSES,
    "OvertimeMarketStatus": DEFAULT_ACCEPTED_STATUSES,
    "LeagueQualityStatus": DEFAULT_ACCEPTED_STATUSES,
    "TeamStrengthStatus": DEFAULT_ACCEPTED_STATUSES,
    "WeakLeagueStatus": "clear|low_risk|normal|confirmed",
    "FixtureFreshnessStatus": DEFAULT_ACCEPTED_STATUSES,
    "RotationStatus": DEFAULT_ACCEPTED_STATUSES,
    "SetVolatilityStatus": "low_risk|normal|confirmed|ok",
    "TossStatus": DEFAULT_ACCEPTED_STATUSES,
    "PitchStatus": DEFAULT_ACCEPTED_STATUSES,
    "FormatStatus": DEFAULT_ACCEPTED_STATUSES,
    "QBStatus": DEFAULT_ACCEPTED_STATUSES,
    "SpreadMovementStatus": DEFAULT_ACCEPTED_STATUSES,
    "VolatilityStatus": "low_risk|normal|confirmed|ok",
    "GoalProfileStatus": DEFAULT_ACCEPTED_STATUSES,
    "RecentFormStatus": DEFAULT_ACCEPTED_STATUSES,
    "LegSetFormatStatus": DEFAULT_ACCEPTED_STATUSES,
    "StageStatus": DEFAULT_ACCEPTED_STATUSES,
    "FrameFormatStatus": DEFAULT_ACCEPTED_STATUSES,
    "EventIdStatus": "confirmed|verified|known|ok",
    "ValidHomeAwayNames": "replace placeholder Home/Away/TBD with real participant names or drop row",
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


def _priority(sport: str, gate: str, threshold_notes: str) -> int:
    if "WITHDRAWAL" in gate or "INJURY" in gate:
        return 34
    if sport == "tennis":
        return 42
    if sport == "baseball":
        return 46
    if sport in {"hockey", "volleyball", "handball"}:
        return 48
    if sport in {"cricket", "snooker", "darts"}:
        return 52
    if "prob_below" in threshold_notes:
        return 55
    return 50


def _target_file(sport: str) -> str:
    if sport == "baseball":
        return "data/baseball_context_overrides.csv"
    if sport == "tennis":
        return "data/tennis_context_overrides.csv"
    if sport in {"hockey", "handball", "volleyball", "cricket", "americanfootball", "futsal", "darts", "snooker"}:
        return "data/sport_context_overrides.csv"
    return "data/context_overrides.csv"


def _missing_fields(value: str) -> List[str]:
    return [part.strip() for part in str(value or "").split(";") if part.strip() and part.strip() != "none"]


def _guidance(missing: str) -> str:
    fields = _missing_fields(missing)
    if not fields:
        return "no missing fields"
    return " | ".join(f"{field}: {FIELD_GUIDANCE.get(field, DEFAULT_ACCEPTED_STATUSES)}" for field in fields)


def _build(target: date) -> List[Dict[str, Any]]:
    day = target.isoformat()
    sources = [
        ("baseball", REPORTS_DIR / f"baseball_context_gate_{day}.csv"),
        ("tennis", REPORTS_DIR / f"tennis_context_gate_{day}.csv"),
        ("remaining", REPORTS_DIR / f"remaining_sport_context_gate_{day}.csv"),
    ]
    out: List[Dict[str, Any]] = []
    for source_sport, path in sources:
        for row in _read_csv(path):
            gate = str(row.get("ContextGate") or "")
            if not gate.startswith("BLOCKED"):
                continue
            sport = str(row.get("Sport") or source_sport)
            if sport == "remaining":
                sport = "unknown"
            threshold_notes = str(row.get("ThresholdNotes") or "")
            missing = str(row.get("MissingContext") or "")
            guidance = _guidance(missing)
            out.append(
                {
                    "PriorityScore": _priority(sport, gate, threshold_notes),
                    "Sport": sport,
                    "Date": row.get("Date") or day,
                    "League": row.get("League") or "",
                    "Match": f"{row.get('Home')} vs {row.get('Away')}",
                    "Home": row.get("Home") or "",
                    "Away": row.get("Away") or "",
                    "Pick": row.get("Pick") or "",
                    "ContextGate": gate,
                    "MissingContext": missing,
                    "ThresholdNotes": threshold_notes,
                    "TargetOverrideFile": _target_file(sport),
                    "AcceptedContextValues": guidance,
                    "RequiredAction": f"Fill verified {sport} context fields: {missing}; accepted: {guidance}",
                    "SourceReport": path.name,
                }
            )
    out.sort(key=lambda row: (int(row["PriorityScore"]), row["Sport"], row["Match"]))
    return out


def _write_csv(rows: List[Dict[str, Any]], path: Path) -> None:
    fields = [
        "PriorityScore",
        "Sport",
        "Date",
        "League",
        "Match",
        "Home",
        "Away",
        "Pick",
        "ContextGate",
        "MissingContext",
        "ThresholdNotes",
        "TargetOverrideFile",
        "AcceptedContextValues",
        "RequiredAction",
        "SourceReport",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _write_md(rows: List[Dict[str, Any]], target: date, path: Path) -> None:
    lines = [
        "# Context collection worklist",
        f"- Date: {target.isoformat()}",
        f"- Blocked context rows: {len(rows)}",
        "- Rule: filling context does not approve an entry. It only allows the sport to enter backtest/review development.",
        "",
        "| # | Priority | Sport | Match | Pick | Gate | Missing context | How to fill | Target file |",
        "| ---: | ---: | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for idx, row in enumerate(rows[:120], start=1):
        lines.append(
            f"| {idx} | {row.get('PriorityScore')} | {row.get('Sport')} | {row.get('Match')} | "
            f"{row.get('Pick')} | {row.get('ContextGate')} | {row.get('MissingContext')} | "
            f"{row.get('AcceptedContextValues')} | "
            f"{row.get('TargetOverrideFile')} |"
        )
    if not rows:
        lines.append("| - | - | - | - | - | - | - | - | - |")
    lines.extend(
        [
            "",
            "## Operating Rule",
            "- Use verified sources only for injury, withdrawal, probable pitchers, lineups, weather, and rotation risk.",
            "- Keep rows blocked if any required field remains unknown.",
            "- Rebuild the sport context gate and final decision guard after filling override rows.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build context collection worklist.")
    parser.add_argument("--date", default="today")
    parser.add_argument("--out-csv", default="")
    parser.add_argument("--out-md", default="")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    target = _target_date(args.date)
    rows = _build(target)
    out_csv = Path(args.out_csv) if args.out_csv else REPORTS_DIR / f"context_collection_worklist_{target.isoformat()}.csv"
    out_md = Path(args.out_md) if args.out_md else REPORTS_DIR / f"context_collection_worklist_{target.isoformat()}.md"
    _write_csv(rows, out_csv)
    _write_md(rows, target, out_md)
    print(f"Wrote {out_csv}")
    print(f"Wrote {out_md}")
    print(f"context_worklist_rows={len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
