#!/usr/bin/env python3
"""Analyze stored 1xBet odds snapshots and report movement toward target odds."""
from __future__ import annotations

import argparse
import csv
import math
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Tuple

BASE_DIR = Path(__file__).resolve().parents[1]
REPORTS_DIR = BASE_DIR / "reports"
DEFAULT_HISTORY = BASE_DIR / "data" / "one_xbet_odds_history.csv"


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


def _parse_dt(value: Any) -> datetime:
    raw = str(value or "")
    try:
        return datetime.fromisoformat(raw)
    except Exception:
        return datetime.min


def _key(row: Dict[str, Any]) -> Tuple[str, str, str, str, str]:
    return (
        str(row.get("Date") or "")[:10],
        str(row.get("Sport") or "").lower(),
        str(row.get("Home") or "").lower(),
        str(row.get("Away") or "").lower(),
        str(row.get("Pick") or "").lower(),
    )


def _trend(first: float, last: float) -> str:
    if last > first:
        return "RISING"
    if last < first:
        return "FALLING"
    return "FLAT"


def _build(rows: List[Dict[str, Any]], target: date) -> List[Dict[str, Any]]:
    grouped: Dict[Tuple[str, str, str, str, str], List[Dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if str(row.get("Date") or "")[:10] != target.isoformat():
            continue
        if _as_float(row.get("OneXBetOdds")) is None:
            continue
        grouped[_key(row)].append(row)

    out: List[Dict[str, Any]] = []
    for _, items in grouped.items():
        items.sort(key=lambda r: _parse_dt(r.get("SnapshotAt")))
        odds = [_as_float(r.get("OneXBetOdds")) for r in items]
        odds = [v for v in odds if v is not None]
        if not odds:
            continue
        first = odds[0]
        last = odds[-1]
        target_odds = _as_float(items[-1].get("TargetOdds"))
        distance = ""
        if target_odds is not None and last > 0:
            distance = round(((target_odds / last) - 1.0) * 100.0, 2)
        out.append(
            {
                "Date": items[-1].get("Date"),
                "Sport": items[-1].get("Sport"),
                "Home": items[-1].get("Home"),
                "Away": items[-1].get("Away"),
                "Pick": items[-1].get("Pick"),
                "Snapshots": len(items),
                "FirstSnapshot": items[0].get("SnapshotAt"),
                "LastSnapshot": items[-1].get("SnapshotAt"),
                "FirstOdds": first,
                "LastOdds": last,
                "MinOdds": min(odds),
                "MaxOdds": max(odds),
                "DeltaOdds": round(last - first, 4),
                "DeltaPct": round(((last / first) - 1.0) * 100.0, 2) if first > 0 else "",
                "Trend": "SINGLE_SNAPSHOT" if len(items) == 1 else _trend(first, last),
                "TargetOdds": target_odds if target_odds is not None else "",
                "DistanceToTargetPct": distance,
                "EventId": items[-1].get("EventId"),
                "StartUtc": items[-1].get("StartUtc"),
            }
        )
    out.sort(key=lambda r: (float(r["DistanceToTargetPct"]) if r["DistanceToTargetPct"] != "" else 999, -int(r["Snapshots"])))
    return out


def _write_csv(rows: List[Dict[str, Any]], path: Path) -> None:
    fields = [
        "Date",
        "Sport",
        "Home",
        "Away",
        "Pick",
        "Snapshots",
        "FirstSnapshot",
        "LastSnapshot",
        "FirstOdds",
        "LastOdds",
        "MinOdds",
        "MaxOdds",
        "DeltaOdds",
        "DeltaPct",
        "Trend",
        "TargetOdds",
        "DistanceToTargetPct",
        "EventId",
        "StartUtc",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _write_md(rows: List[Dict[str, Any]], target: date, path: Path) -> None:
    lines = [
        "# 1xBet odds movement",
        f"- Date: {target.isoformat()}",
        f"- Matches tracked: {len(rows)}",
        "- Rule: movement toward TargetOdds is only a monitoring signal, not an entry by itself.",
        "",
        "| # | Match | Pick | Snapshots | First | Last | Trend | Target | Need % | Start UTC |",
        "| ---: | --- | --- | ---: | ---: | ---: | --- | ---: | ---: | --- |",
    ]
    for idx, row in enumerate(rows, start=1):
        need = row.get("DistanceToTargetPct")
        need_s = "" if need == "" else f"{float(need):.2f}"
        target_odds = row.get("TargetOdds")
        target_s = "" if target_odds == "" else f"{float(target_odds):.3f}"
        lines.append(
            f"| {idx} | {row.get('Home')} vs {row.get('Away')} | {row.get('Pick')} | {row.get('Snapshots')} | "
            f"{float(row.get('FirstOdds') or 0):.3f} | {float(row.get('LastOdds') or 0):.3f} | {row.get('Trend')} | "
            f"{target_s} | {need_s} | {row.get('StartUtc')} |"
        )
    if not rows:
        lines.append("| - | - | - | - | - | - | - | - | - | - |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build odds movement report.")
    parser.add_argument("--date", default="today")
    parser.add_argument("--history-csv", default=str(DEFAULT_HISTORY))
    parser.add_argument("--out-csv", default="")
    parser.add_argument("--out-md", default="")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    target = _target_date(args.date)
    rows = _build(_read_csv(Path(args.history_csv)), target)
    out_csv = Path(args.out_csv) if args.out_csv else REPORTS_DIR / f"odds_movement_{target.isoformat()}.csv"
    out_md = Path(args.out_md) if args.out_md else REPORTS_DIR / f"odds_movement_{target.isoformat()}.md"
    _write_csv(rows, out_csv)
    _write_md(rows, target, out_md)
    print(f"Wrote {out_csv}")
    print(f"Wrote {out_md}")
    print(f"movement_rows={len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
