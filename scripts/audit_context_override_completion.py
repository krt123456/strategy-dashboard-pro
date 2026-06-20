#!/usr/bin/env python3
"""Audit completion of manual sport-context override rows."""
from __future__ import annotations

import argparse
import csv
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List

from build_remaining_sport_context_gates import REQUIREMENTS as REMAINING_REQUIREMENTS
from sport_name_quality import participant_quality_flags

BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / "data"
REPORTS_DIR = BASE_DIR / "reports"

GOOD_STATUS = {"confirmed", "known", "ok", "pass", "ready", "low_risk", "normal", "clear", "verified"}

BASEBALL_FIELDS = [
    "HomeProbablePitcher",
    "AwayProbablePitcher",
    "LineupStatus",
    "BullpenRestStatus",
    "WeatherStatus",
    "ParkFactorStatus",
]
TENNIS_FIELDS = [
    "PickRecentFormStatus",
    "OpponentRecentFormStatus",
    "SurfaceFitStatus",
    "InjuryStatus",
    "WithdrawalStatus",
    "RoundStatus",
    "EventIdStatus",
]


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


def _status_good(value: Any) -> bool:
    raw = str(value or "").strip().lower().replace(" ", "_")
    return bool(raw) and raw in GOOD_STATUS


def _league_type(value: Any) -> str:
    league = str(value or "").lower()
    if "ncaa" in league or "college" in league:
        return "NCAA"
    if "mlb" in league or "major league" in league:
        return "MLB"
    return "OTHER"


def _sport(value: Any) -> str:
    return str(value or "").strip().lower().replace(" ", "")


def _required_fields(source_file: str, row: Dict[str, Any]) -> List[str]:
    if source_file == "baseball_context_overrides.csv":
        fields = BASEBALL_FIELDS.copy()
        if _league_type(row.get("League")) == "NCAA":
            fields.append("RotationRisk")
        return fields
    if source_file == "tennis_context_overrides.csv":
        return TENNIS_FIELDS.copy()
    sport = _sport(row.get("Sport"))
    return list(REMAINING_REQUIREMENTS.get(sport, []))


def _field_ready(field: str, row: Dict[str, Any]) -> bool:
    value = str(row.get(field) or "").strip()
    if field in {"HomeProbablePitcher", "AwayProbablePitcher"}:
        return bool(value)
    if field == "RotationRisk":
        return value.lower().replace(" ", "_") in {"low", "low_risk", "normal", "confirmed"}
    return _status_good(value)


def _audit_file(path: Path, target: date) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    source_file = path.name
    for row in _read_csv(path):
        if str(row.get("Date") or "").strip()[:10] != target.isoformat():
            continue
        sport = _sport(row.get("Sport")) or ("baseball" if source_file.startswith("baseball") else "tennis" if source_file.startswith("tennis") else "unknown")
        required = _required_fields(source_file, row)
        name_flags = participant_quality_flags(row.get("Home"), row.get("Away"))
        missing = [field for field in required if not _field_ready(field, row)]
        if name_flags:
            missing = ["ValidHomeAwayNames"] + missing
        total = len(required) + (1 if name_flags else 0)
        done = max(0, total - len(missing))
        completion = round((done / total) * 100.0, 2) if total else 0.0
        if name_flags:
            status = "BAD_MATCH_NAME"
            action = "FIX_OR_DROP_PLACEHOLDER_MATCH_NAME"
        elif missing:
            status = "INCOMPLETE"
            action = "FILL_MISSING_CONTEXT_FIELDS"
        elif not row.get("ContextSource"):
            status = "SOURCE_MISSING"
            action = "ADD_CONTEXT_SOURCE_BEFORE_TRUSTING_ROW"
        else:
            status = "COMPLETE_READY_FOR_GATE_REBUILD"
            action = "REBUILD_CONTEXT_GATE_AND_FINAL_GUARD"
        rows.append(
            {
                "TargetOverrideFile": str(path.relative_to(BASE_DIR)),
                "Sport": sport,
                "Date": row.get("Date") or "",
                "League": row.get("League") or "",
                "Home": row.get("Home") or "",
                "Away": row.get("Away") or "",
                "Pick": row.get("Pick") or "",
                "Status": status,
                "CompletionPct": completion,
                "RequiredFields": ";".join(required) if required else "none",
                "MissingFields": ";".join(missing) if missing else "none",
                "ContextSource": row.get("ContextSource") or "",
                "NextAction": action,
            }
        )
    return rows


def _build(target: date) -> List[Dict[str, Any]]:
    files = [
        DATA_DIR / "baseball_context_overrides.csv",
        DATA_DIR / "tennis_context_overrides.csv",
        DATA_DIR / "sport_context_overrides.csv",
    ]
    rows: List[Dict[str, Any]] = []
    for path in files:
        rows.extend(_audit_file(path, target))
    rows.sort(key=lambda row: (row["Status"], float(row["CompletionPct"]), row["Sport"], row["Home"]))
    return rows


def _write_csv(rows: List[Dict[str, Any]], path: Path) -> None:
    fields = [
        "TargetOverrideFile",
        "Sport",
        "Date",
        "League",
        "Home",
        "Away",
        "Pick",
        "Status",
        "CompletionPct",
        "RequiredFields",
        "MissingFields",
        "ContextSource",
        "NextAction",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _write_md(rows: List[Dict[str, Any]], target: date, path: Path) -> None:
    status_counts = Counter(str(row.get("Status") or "EMPTY") for row in rows)
    sport_counts = Counter(str(row.get("Sport") or "unknown") for row in rows)
    incomplete = [row for row in rows if str(row.get("Status") or "") != "COMPLETE_READY_FOR_GATE_REBUILD"]
    lines = [
        "# Context override completion audit",
        f"- Date: {target.isoformat()}",
        f"- Override rows audited: {len(rows)}",
        f"- Rows still needing work: {len(incomplete)}",
        "- Rule: a complete override row only unlocks rebuild/backtest review; it does not approve an entry.",
        "",
        "## Status Counts",
        *([f"- {status}: {count}" for status, count in status_counts.most_common()] or ["- none: 0"]),
        "",
        "## Sport Counts",
        *([f"- {sport}: {count}" for sport, count in sport_counts.most_common()] or ["- none: 0"]),
        "",
        "## Next Rows To Fix",
        "| Sport | Match | Pick | Status | Completion | Missing | Next action |",
        "| --- | --- | --- | --- | ---: | --- | --- |",
    ]
    for row in incomplete[:80]:
        lines.append(
            f"| {row.get('Sport')} | {row.get('Home')} vs {row.get('Away')} | {row.get('Pick')} | "
            f"{row.get('Status')} | {row.get('CompletionPct')} | {row.get('MissingFields')} | {row.get('NextAction')} |"
        )
    if not incomplete:
        lines.append("| - | - | - | - | - | - | - |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit sport context override completion.")
    parser.add_argument("--date", default="today")
    parser.add_argument("--out-csv", default="")
    parser.add_argument("--out-md", default="")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    target = _target_date(args.date)
    rows = _build(target)
    out_csv = Path(args.out_csv) if args.out_csv else REPORTS_DIR / f"context_override_completion_{target.isoformat()}.csv"
    out_md = Path(args.out_md) if args.out_md else REPORTS_DIR / f"context_override_completion_{target.isoformat()}.md"
    _write_csv(rows, out_csv)
    _write_md(rows, target, out_md)
    print(f"Wrote {out_csv}")
    print(f"Wrote {out_md}")
    print(f"context_override_rows={len(rows)} incomplete={sum(1 for row in rows if row['Status'] != 'COMPLETE_READY_FOR_GATE_REBUILD')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
