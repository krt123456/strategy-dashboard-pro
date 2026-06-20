#!/usr/bin/env python3
"""Build a schedule for when prediction results should be checked again."""
from __future__ import annotations

import argparse
import csv
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List
from zoneinfo import ZoneInfo

BASE_DIR = Path(__file__).resolve().parents[1]
REPORTS_DIR = BASE_DIR / "reports"
LOCAL_TZ = ZoneInfo("Africa/Algiers")

SPORT_BUFFER_MIN = {
    "football": 130,
    "basketball": 150,
    "tennis": 180,
    "tabletennis": 90,
    "handball": 120,
    "hockey": 150,
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
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _parse_dt(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw)
        return dt if dt.tzinfo else dt.replace(tzinfo=LOCAL_TZ)
    except Exception:
        return None


def _recheck_action(row: Dict[str, Any], now: datetime) -> Dict[str, Any]:
    sport = str(row.get("Sport") or "").lower()
    status = str(row.get("ResultStatus") or "")
    start = _parse_dt(row.get("StartTimeLocal"))
    buffer_min = SPORT_BUFFER_MIN.get(sport, 150)
    if status in {"FINISHED", "FINISHED_OR_LIVE_SCORE"} or str(row.get("PickOutcome") or "") in {"CORRECT", "WRONG"}:
        action = "DONE"
        due = ""
    elif status == "NOT_STARTED" and start is not None:
        due_dt = start + timedelta(minutes=buffer_min)
        due = due_dt.isoformat(timespec="minutes")
        action = "RECHECK_AFTER_FINISH" if due_dt > now else "RECHECK_NOW"
    elif status == "NOT_STARTED":
        due = ""
        action = "SOURCE_OR_EVENT_ID_REQUIRED"
    elif status in {"STARTED_OR_RESULT_PENDING", "RESULT_SOURCE_REQUIRED"}:
        due = now.isoformat(timespec="minutes")
        action = "RECHECK_NOW"
    elif status == "STRATEGY_LAB_RESULT_DEFERRED":
        due = ""
        action = "LAB_RESULT_DEFERRED"
    elif status == "NO_EVENT_ID":
        due = ""
        action = "SOURCE_OR_EVENT_ID_REQUIRED"
    else:
        due = now.isoformat(timespec="minutes")
        action = "RECHECK_NOW"
    return {
        "Rank": row.get("Rank"),
        "Sport": row.get("Sport"),
        "Date": row.get("Date"),
        "Home": row.get("Home"),
        "Away": row.get("Away"),
        "Pick": row.get("Pick"),
        "ResultStatus": status,
        "StartTimeLocal": row.get("StartTimeLocal") or "",
        "RecheckAfterLocal": due,
        "RecheckAction": action,
        "OfficialEntry": row.get("OfficialEntry"),
        "ResultSource": row.get("ResultSource"),
    }


def _build(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    now = datetime.now(LOCAL_TZ)
    out = [_recheck_action(row, now) for row in rows]
    order = {"RECHECK_NOW": 0, "RECHECK_AFTER_FINISH": 1, "SOURCE_OR_EVENT_ID_REQUIRED": 2, "LAB_RESULT_DEFERRED": 3, "DONE": 4}
    out.sort(key=lambda r: (order.get(str(r.get("RecheckAction")), 9), str(r.get("RecheckAfterLocal") or "9999"), int(str(r.get("Rank") or "999") or 999)))
    return out


def _write_csv(rows: List[Dict[str, Any]], path: Path) -> None:
    fields = [
        "Rank",
        "Sport",
        "Date",
        "Home",
        "Away",
        "Pick",
        "ResultStatus",
        "StartTimeLocal",
        "RecheckAfterLocal",
        "RecheckAction",
        "OfficialEntry",
        "ResultSource",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _write_md(rows: List[Dict[str, Any]], target: date, path: Path) -> None:
    counts: Dict[str, int] = {}
    for row in rows:
        counts[str(row.get("RecheckAction"))] = counts.get(str(row.get("RecheckAction")), 0) + 1
    lines = [
        "# Result recheck schedule",
        f"- Date: {target.isoformat()}",
        f"- Rows: {len(rows)}",
        "",
        "## Counts",
        *[f"- {key}: {value}" for key, value in sorted(counts.items())],
        "",
        "| # | Action | Match | Pick | Status | Start | Recheck after | Source |",
        "| ---: | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows[:80]:
        lines.append(
            f"| {row.get('Rank')} | {row.get('RecheckAction')} | {row.get('Home')} vs {row.get('Away')} | "
            f"{row.get('Pick')} | {row.get('ResultStatus')} | {row.get('StartTimeLocal')} | "
            f"{row.get('RecheckAfterLocal')} | {row.get('ResultSource')} |"
        )
    if not rows:
        lines.append("| - | - | - | - | - | - | - | - |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build result recheck schedule.")
    parser.add_argument("--date", default="today")
    parser.add_argument("--results-csv", default="")
    parser.add_argument("--out-csv", default="")
    parser.add_argument("--out-md", default="")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    target = _target_date(args.date)
    results_csv = Path(args.results_csv) if args.results_csv else REPORTS_DIR / f"prediction_results_{target.isoformat()}.csv"
    rows = _build(_read_csv(results_csv))
    out_csv = Path(args.out_csv) if args.out_csv else REPORTS_DIR / f"result_recheck_schedule_{target.isoformat()}.csv"
    out_md = Path(args.out_md) if args.out_md else REPORTS_DIR / f"result_recheck_schedule_{target.isoformat()}.md"
    _write_csv(rows, out_csv)
    _write_md(rows, target, out_md)
    print(f"Wrote {out_csv}")
    print(f"Wrote {out_md}")
    print(f"schedule_rows={len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
