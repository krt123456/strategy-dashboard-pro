#!/usr/bin/env python3
"""Seed blank override rows from the context collection worklist.

This script never overwrites existing context. It only appends missing match
rows so the next manual/automated context pass has exact rows to fill.
"""
from __future__ import annotations

import argparse
import csv
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

from sport_name_quality import has_bad_participant_pair

BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / "data"
REPORTS_DIR = BASE_DIR / "reports"


FIELDSETS: Dict[str, List[str]] = {
    "data/baseball_context_overrides.csv": [
        "Date",
        "League",
        "Home",
        "Away",
        "HomeProbablePitcher",
        "AwayProbablePitcher",
        "LineupStatus",
        "BullpenRestStatus",
        "WeatherStatus",
        "ParkFactorStatus",
        "RotationRisk",
        "ContextSource",
        "Notes",
    ],
    "data/tennis_context_overrides.csv": [
        "Date",
        "League",
        "Home",
        "Away",
        "Pick",
        "PickRecentFormStatus",
        "OpponentRecentFormStatus",
        "SurfaceFitStatus",
        "InjuryStatus",
        "WithdrawalStatus",
        "RoundStatus",
        "EventIdStatus",
        "Surface",
        "ContextSource",
        "Notes",
    ],
    "data/sport_context_overrides.csv": [
        "Date",
        "Sport",
        "League",
        "Home",
        "Away",
        "Pick",
        "GoalieStatus",
        "RestStatus",
        "OvertimeMarketStatus",
        "EventIdStatus",
        "LeagueQualityStatus",
        "TeamStrengthStatus",
        "WeakLeagueStatus",
        "FixtureFreshnessStatus",
        "RotationStatus",
        "InjuryStatus",
        "SetVolatilityStatus",
        "TossStatus",
        "PitchStatus",
        "LineupStatus",
        "FormatStatus",
        "WeatherStatus",
        "QBStatus",
        "SpreadMovementStatus",
        "VolatilityStatus",
        "GoalProfileStatus",
        "RecentFormStatus",
        "LegSetFormatStatus",
        "StageStatus",
        "FrameFormatStatus",
        "ContextSource",
        "Notes",
    ],
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


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _split_match(value: Any) -> Tuple[str, str]:
    raw = str(value or "")
    if " vs " not in raw:
        return raw.strip(), ""
    home, away = raw.split(" vs ", 1)
    return home.strip(), away.strip()


def _key(row: Dict[str, Any], *, include_sport: bool, include_pick: bool) -> Tuple[str, ...]:
    parts: List[str] = [str(row.get("Date") or "").strip()[:10]]
    if include_sport:
        parts.append(_norm(row.get("Sport")))
    parts.extend([_norm(row.get("League")), _norm(row.get("Home")), _norm(row.get("Away"))])
    if include_pick:
        parts.append(_norm(row.get("Pick")))
    return tuple(parts)


def _ensure_file(path: Path, fields: List[str]) -> None:
    if path.exists() and path.stat().st_size > 0:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()


def _load_existing_keys(path: Path, fields: List[str], *, include_sport: bool, include_pick: bool) -> set[Tuple[str, ...]]:
    _ensure_file(path, fields)
    return {_key(row, include_sport=include_sport, include_pick=include_pick) for row in _read_csv(path)}


def _base_row(work: Dict[str, Any], fields: List[str], target_file: str) -> Dict[str, str]:
    home = str(work.get("Home") or "").strip()
    away = str(work.get("Away") or "").strip()
    if not home or not away:
        home, away = _split_match(work.get("Match"))
    row = {field: "" for field in fields}
    row["Date"] = str(work.get("Date") or "").strip()[:10]
    row["League"] = str(work.get("League") or "").strip()
    row["Home"] = home
    row["Away"] = away
    if "Sport" in row:
        row["Sport"] = str(work.get("Sport") or "").strip()
    if "Pick" in row:
        row["Pick"] = str(work.get("Pick") or "").strip()
    row["Notes"] = f"seeded_from_context_worklist; missing={work.get('MissingContext') or ''}"
    return row


def _append_rows(path: Path, fields: List[str], rows: Iterable[Dict[str, str]]) -> int:
    rows = list(rows)
    if not rows:
        return 0
    _ensure_file(path, fields)
    with path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writerows(rows)
    return len(rows)


def _build(target: date, worklist_csv: Path) -> Dict[str, int]:
    grouped: Dict[str, List[Dict[str, str]]] = {key: [] for key in FIELDSETS}
    existing: Dict[str, set[Tuple[str, ...]]] = {}
    for rel, fields in FIELDSETS.items():
        include_sport = rel.endswith("sport_context_overrides.csv")
        include_pick = not rel.endswith("baseball_context_overrides.csv")
        existing[rel] = _load_existing_keys(BASE_DIR / rel, fields, include_sport=include_sport, include_pick=include_pick)

    for work in _read_csv(worklist_csv):
        if str(work.get("ContextGate") or "") == "BLOCKED_BAD_MATCH_NAME":
            continue
        rel = str(work.get("TargetOverrideFile") or "").strip()
        if rel not in FIELDSETS:
            continue
        fields = FIELDSETS[rel]
        include_sport = rel.endswith("sport_context_overrides.csv")
        include_pick = not rel.endswith("baseball_context_overrides.csv")
        row = _base_row(work, fields, rel)
        if has_bad_participant_pair(row.get("Home"), row.get("Away")):
            continue
        key = _key(row, include_sport=include_sport, include_pick=include_pick)
        if key in existing[rel]:
            continue
        existing[rel].add(key)
        grouped[rel].append(row)

    counts: Dict[str, int] = {}
    for rel, rows in grouped.items():
        counts[rel] = _append_rows(BASE_DIR / rel, FIELDSETS[rel], rows)
    return counts


def _write_report(counts: Dict[str, int], target: date, worklist_csv: Path, out_md: Path) -> None:
    try:
        worklist_label = str(worklist_csv.resolve().relative_to(BASE_DIR))
    except Exception:
        worklist_label = str(worklist_csv)
    lines = [
        "# Context override seed report",
        f"- Date: {target.isoformat()}",
        f"- Worklist source: `{worklist_label}`",
        "- Rule: existing override rows are never overwritten; only missing blank rows are appended.",
        "",
        "| Override file | Rows appended |",
        "| --- | ---: |",
    ]
    for rel, count in counts.items():
        lines.append(f"| {rel} | {count} |")
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Seed blank context override rows from a context worklist.")
    parser.add_argument("--date", default="today")
    parser.add_argument("--worklist-csv", default="")
    parser.add_argument("--out-md", default="")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    target = _target_date(args.date)
    worklist_csv = Path(args.worklist_csv) if args.worklist_csv else REPORTS_DIR / f"context_collection_worklist_{target.isoformat()}.csv"
    out_md = Path(args.out_md) if args.out_md else REPORTS_DIR / f"context_override_seed_{target.isoformat()}.md"
    counts = _build(target, worklist_csv)
    _write_report(counts, target, worklist_csv, out_md)
    print(f"Wrote {out_md}")
    print("seeded=" + " ".join(f"{Path(key).name}:{value}" for key, value in counts.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
