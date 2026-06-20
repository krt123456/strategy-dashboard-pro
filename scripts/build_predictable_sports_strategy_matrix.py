#!/usr/bin/env python3
"""Rank predictable sports and write their strategy gates."""
from __future__ import annotations

import argparse
import csv
import math
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List

from sports_strategy_profiles import SPORT_PROFILES, get_profile
from watch_sport_lab import watch_memory_by_sport

BASE_DIR = Path(__file__).resolve().parents[1]
REPORTS_DIR = BASE_DIR / "reports"


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


def _as_int(value: Any) -> int:
    try:
        return int(float(value))
    except Exception:
        return 0


def _scan_by_profile(target: date) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for row in _read_csv(REPORTS_DIR / f"1xbet_sports_strategy_scan_{target.isoformat()}.csv"):
        profile = get_profile(row.get("Sport"))
        if not profile:
            continue
        key = str(profile["display"]).lower().replace(" ", "")
        out[key] = row
    return out


def _advisor_counts(target: date) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for row in _read_csv(REPORTS_DIR / f"daily_1xbet_value_advisor_{target.isoformat()}.csv"):
        sport = str(row.get("Sport") or "").strip().lower().replace(" ", "")
        if sport:
            counts[sport] = counts.get(sport, 0) + 1
    return counts


def _strategy_mode(status: str) -> str:
    if status == "ACTIVE":
        return "ENTRY_GATE_ENABLED"
    if status == "ACTIVE_SECONDARY":
        return "SECONDARY_REVIEW_ENABLED"
    if status in {"PARTIAL", "PARTIAL_BLOCKED"}:
        return "WATCH_ONLY_UNTIL_LOCAL_FIX"
    return "STRATEGY_LAB_WATCH_ONLY"


def _next_action(status: str, profile: Dict[str, Any], key: str, memory_row: Dict[str, float]) -> str:
    sample = int(memory_row.get("sample", 0.0))
    accuracy = float(memory_row.get("accuracy", 0.0))
    if key == "tennis" and sample >= 10 and accuracy >= 0.75:
        return "Split tennis into prime path (main draw/doubles) vs qualification/UTR; promote prime path first with player/surface/injury context."
    if key == "baseball" and sample >= 6 and accuracy < 0.60:
        return "Keep baseball in deep lab only; do not surface until pitcher/lineup/weather + MLB/NCAA split is repaired."
    if status == "ACTIVE":
        return "Keep strict EV/source/timing gates; expand only with finished-result memory."
    if status == "ACTIVE_SECONDARY":
        return "Allow only secondary review with exact event id and fresh price; keep stake locked at review stage."
    if status == "PARTIAL":
        return "Refresh current fixtures and complete context gates before promotion."
    if status == "PARTIAL_BLOCKED":
        return "Retune weak leagues and require league-level precision before review."
    return f"Build local dataset/backtest first; required context: {profile.get('required_context')}"


def _rank_score(profile: Dict[str, Any], scan: Dict[str, Any], daily_rows: int, memory_row: Dict[str, float]) -> float:
    events = _as_int(scan.get("OneXBetEvents"))
    base = float(profile.get("predictability_score") or 0)
    event_bonus = min(12.0, math.log10(max(events, 1)) * 4.0)
    local_bonus = 8.0 if daily_rows > 0 else 0.0
    status = str(profile.get("model_status") or "")
    status_bonus = 8.0 if status == "ACTIVE" else 5.0 if status == "ACTIVE_SECONDARY" else 1.0 if status == "PARTIAL" else 0.0
    lab_penalty = 8.0 if str(profile.get("model_status") or "").endswith("LAB") or status == "STRATEGY_LAB" else 0.0
    blocked_penalty = 6.0 if status == "PARTIAL_BLOCKED" else 0.0
    sample = int(memory_row.get("sample", 0.0))
    accuracy = float(memory_row.get("accuracy", 0.0))
    if sample >= 6 and accuracy >= 0.75:
        memory_bonus = 6.0
    elif sample >= 6 and accuracy >= 0.68:
        memory_bonus = 3.0
    elif sample >= 6 and accuracy < 0.60:
        memory_bonus = -10.0
    elif sample > 0:
        memory_bonus = -1.0
    else:
        memory_bonus = 0.0
    return round(max(0.0, min(100.0, base + event_bonus + local_bonus + status_bonus - lab_penalty - blocked_penalty + memory_bonus)), 2)


def _build(target: date) -> List[Dict[str, Any]]:
    scan = _scan_by_profile(target)
    daily = _advisor_counts(target)
    memory = watch_memory_by_sport()
    rows: List[Dict[str, Any]] = []
    for key, profile in SPORT_PROFILES.items():
        scan_key = str(profile["display"]).lower().replace(" ", "")
        scan_row = scan.get(scan_key, {})
        daily_rows = daily.get(key, 0)
        status = str(profile.get("model_status") or "")
        memory_row = memory.get(key, {})
        score = _rank_score(profile, scan_row, daily_rows, memory_row)
        rows.append(
            {
                "Rank": 0,
                "Sport": profile.get("display"),
                "SportKey": key,
                "SportId": profile.get("sport_id"),
                "PredictabilityScore": score,
                "PredictabilityTier": profile.get("predictability_tier"),
                "ModelStatus": status,
                "StrategyMode": _strategy_mode(status),
                "OneXBetEvents": _as_int(scan_row.get("OneXBetEvents")),
                "DailyRowsInAdvisor": daily_rows,
                "WatchMemorySample": int(memory_row.get("sample", 0.0)),
                "WatchMemoryAccuracy": round(float(memory_row.get("accuracy", 0.0)) * 100.0, 2) if memory_row else "",
                "StrategyGate": profile.get("strategy_gate"),
                "RequiredContext": profile.get("required_context"),
                "ModelNote": profile.get("model_note"),
                "NextDevelopmentAction": _next_action(status, profile, key, memory_row),
            }
        )
    rows.sort(key=lambda row: (float(row["PredictabilityScore"]), _as_int(row["OneXBetEvents"])), reverse=True)
    for idx, row in enumerate(rows, start=1):
        row["Rank"] = idx
    return rows


def _write_csv(rows: List[Dict[str, Any]], path: Path) -> None:
    fields = [
        "Rank",
        "Sport",
        "SportKey",
        "SportId",
        "PredictabilityScore",
        "PredictabilityTier",
        "ModelStatus",
        "StrategyMode",
        "OneXBetEvents",
        "DailyRowsInAdvisor",
        "WatchMemorySample",
        "WatchMemoryAccuracy",
        "StrategyGate",
        "RequiredContext",
        "ModelNote",
        "NextDevelopmentAction",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _write_md(rows: List[Dict[str, Any]], target: date, path: Path) -> None:
    active = [r for r in rows if str(r.get("StrategyMode")) in {"ENTRY_GATE_ENABLED", "SECONDARY_REVIEW_ENABLED"}]
    lab = [r for r in rows if str(r.get("StrategyMode")) == "STRATEGY_LAB_WATCH_ONLY"]
    lines = [
        "# Predictable sports strategy matrix",
        f"- Date: {target.isoformat()}",
        f"- Sports ranked: {len(rows)}",
        f"- Entry/secondary enabled: {len(active)}",
        f"- Strategy-lab watch-only: {len(lab)}",
        "- Rule: a sport in `STRATEGY_LAB_WATCH_ONLY` may produce watch rows, but the final guard must not promote it to review until a local backtest/context gate exists.",
        "- Public-watch rank now also reflects realized watch-memory accuracy when a sport already has enough finished rows.",
        "",
        "| Rank | Sport | Score | Mode | 1xBet events | Advisor rows | Watch memory | Gate |",
        "| ---: | --- | ---: | --- | ---: | ---: | --- | --- |",
    ]
    for row in rows:
        lines.append(
            f"| {row.get('Rank')} | {row.get('Sport')} | {row.get('PredictabilityScore')} | "
            f"{row.get('StrategyMode')} | {row.get('OneXBetEvents')} | {row.get('DailyRowsInAdvisor')} | "
            f"{row.get('WatchMemorySample')}/{row.get('WatchMemoryAccuracy')}% | {row.get('StrategyGate')} |"
        )
    lines.extend(
        [
            "",
            "## Development Order",
            "- Keep football and basketball as core decision sports.",
            "- Keep table tennis as secondary review only with exact event and fresh price.",
            "- Build next local models in this order: tennis prime path, volleyball selected leagues, hockey, handball selected leagues, baseball deep-lab repair.",
            "- Tennis qualification/UTR and baseball stay behind stricter lab walls until their specialized context gaps are repaired.",
            "- Cricket, American football, futsal, darts, and snooker remain watch-only until context datasets exist.",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build predictable sports strategy matrix.")
    parser.add_argument("--date", default="today")
    parser.add_argument("--out-csv", default="")
    parser.add_argument("--out-md", default="")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    target = _target_date(args.date)
    rows = _build(target)
    out_csv = Path(args.out_csv) if args.out_csv else REPORTS_DIR / f"predictable_sports_strategy_matrix_{target.isoformat()}.csv"
    out_md = Path(args.out_md) if args.out_md else REPORTS_DIR / f"predictable_sports_strategy_matrix_{target.isoformat()}.md"
    _write_csv(rows, out_csv)
    _write_md(rows, target, out_md)
    print(f"Wrote {out_csv}")
    print(f"Wrote {out_md}")
    print(f"strategy_sports={len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
