#!/usr/bin/env python3
"""Create a background 1xBet sport coverage and strategy-gate report."""
from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import urlencode

from sports_strategy_profiles import get_profile


BASE_DIR = Path(__file__).resolve().parents[1]
REPORTS_DIR = BASE_DIR / "reports"
TMP_DIR = BASE_DIR / "data" / "tmp" / "1xbet_public_api"
SPORTS_JSON = TMP_DIR / "sports_short_2026-04-25.json"
DEFAULT_BASE_URL = "https://q1ayxwi7tuwrn.bar"


def _target_date(value: str) -> date:
    value = (value or "today").strip().lower()
    today = date.today()
    if value == "today":
        return today
    if value == "tomorrow":
        return today + timedelta(days=1)
    return date.fromisoformat(value)


def _base_url() -> str:
    return (os.environ.get("ONE_XBET_PUBLIC_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")


def _fetch_sports(timeout_s: int) -> Dict[str, Any]:
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    url = f"{_base_url()}/service-api/LineFeed/GetSportsShortZip?{urlencode({'lng': 'en'})}"
    cmd = [
        "curl",
        "-sS",
        "-L",
        "-A",
        "Mozilla/5.0",
        "--connect-timeout",
        str(max(2, min(timeout_s, 10))),
        "--max-time",
        str(timeout_s),
        url,
    ]
    proc = subprocess.run(cmd, check=False, capture_output=True, text=True)
    if proc.returncode == 0 and proc.stdout.strip().startswith("{"):
        SPORTS_JSON.write_text(proc.stdout, encoding="utf-8")
        return json.loads(proc.stdout)
    if SPORTS_JSON.exists():
        return json.loads(SPORTS_JSON.read_text(encoding="utf-8"))
    raise RuntimeError((proc.stderr or proc.stdout or "failed to fetch 1xBet sports").strip())


def _load_daily_counts(target: date) -> Dict[str, int]:
    path = REPORTS_DIR / f"daily_1xbet_value_advisor_{target.isoformat()}.csv"
    counts: Dict[str, int] = {}
    if not path.exists():
        return counts
    with path.open("r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            sport = str(row.get("Sport") or "").strip()
            if sport:
                counts[sport.lower()] = counts.get(sport.lower(), 0) + 1
    return counts


def _write_reports(target: date, sports: List[Dict[str, Any]]) -> None:
    counts = _load_daily_counts(target)
    out_csv = REPORTS_DIR / f"1xbet_sports_strategy_scan_{target.isoformat()}.csv"
    out_md = REPORTS_DIR / f"1xbet_sports_strategy_scan_{target.isoformat()}.md"
    rows: List[Dict[str, Any]] = []
    for item in sports:
        name = str(item.get("N") or "")
        profile = get_profile(name)
        status = str(profile.get("model_status")) if profile else "NOT_ENABLED"
        note = str(profile.get("model_note")) if profile else "Visible on 1xBet, but no validated local strategy is enabled."
        gate = str(profile.get("strategy_gate")) if profile else "Do not enter. Build historical dataset, backtest, and source checks first."
        rows.append(
            {
                "SportId": item.get("I"),
                "Sport": name,
                "OneXBetEvents": item.get("C"),
                "Countries": item.get("CC"),
                "ModelStatus": status,
                "DailyRowsInAdvisor": counts.get(name.lower().replace(" ", ""), counts.get(name.lower(), 0)),
                "StrategyGate": gate,
                "Note": note,
            }
        )
    rows.sort(key=lambda r: (str(r["ModelStatus"]).startswith("ACTIVE"), int(r.get("OneXBetEvents") or 0)), reverse=True)

    out_csv.parent.mkdir(parents=True, exist_ok=True)
    fields = ["SportId", "Sport", "OneXBetEvents", "Countries", "ModelStatus", "DailyRowsInAdvisor", "StrategyGate", "Note"]
    with out_csv.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    top = rows[:15]
    lines = [
        "# 1xBet sports strategy scan",
        f"- Date: {target.isoformat()}",
        f"- Sports visible from public 1xBet API: {len(rows)}",
        f"- Active/secondary local strategies: {sum(1 for r in rows if str(r['ModelStatus']).startswith('ACTIVE'))}",
        "- Rule: watch-only sports must not become entry candidates until a local backtest and source-review gate exists.",
        "",
        "| Sport | 1xBet events | Status | Gate |",
        "| --- | ---: | --- | --- |",
    ]
    for row in top:
        lines.append(f"| {row['Sport']} | {row['OneXBetEvents']} | {row['ModelStatus']} | {row['StrategyGate']} |")
    lines.extend(
        [
            "",
            "## Next Development Targets",
            "- Ice Hockey: promote from partial to active after current fixture refresh and 1xBet market mapping is verified.",
            "- Handball: retune or disable weak leagues; do not accept until league-level precision improves.",
            "- Tennis/Volleyball/Baseball/Cricket: build historical dataset first, then add source-specific injury/lineup/weather gates.",
        ]
    )
    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {out_csv}")
    print(f"Wrote {out_md}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scan public 1xBet sport coverage and write strategy gates.")
    parser.add_argument("--date", default="today")
    parser.add_argument("--timeout", type=int, default=15)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    target = _target_date(args.date)
    payload = _fetch_sports(args.timeout)
    sports = [item for item in payload.get("Value") or [] if isinstance(item, dict)]
    _write_reports(target, sports)
    print(f"sports={len(sports)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
