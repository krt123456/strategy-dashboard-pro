#!/usr/bin/env python3
"""Build a focused watchlist for candidates that only need a better price."""
from __future__ import annotations

import argparse
import csv
import math
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Tuple

BASE_DIR = Path(__file__).resolve().parents[1]
REPORTS_DIR = BASE_DIR / "reports"

try:
    from one_xbet_status import is_confirmed_1xbet_status
except Exception:  # pragma: no cover
    def is_confirmed_1xbet_status(value: object) -> bool:
        return str(value or "") in {"AUTO_MATCHED", "PUBLIC_ODDS_CONFIRMED"}


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


def _match_key(row: Dict[str, Any]) -> Tuple[str, str, str, str]:
    return (
        str(row.get("Date") or "").strip()[:10],
        str(row.get("Sport") or "").strip().lower(),
        str(row.get("Home") or "").strip().lower(),
        str(row.get("Away") or "").strip().lower(),
    )


def _results_map(rows: List[Dict[str, Any]]) -> Dict[Tuple[str, str, str, str], Dict[str, Any]]:
    return {_match_key(row): row for row in rows}


def _priority(row: Dict[str, Any]) -> str:
    gap = _as_float(row.get("PriceGapPct"))
    status = str(row.get("OneXBetStatus") or "")
    freshness = str(row.get("OneXBetOddsFreshness") or row.get("OneXBetFreshness") or "")
    if not is_confirmed_1xbet_status(status):
        return "LOW_UNCONFIRMED_EVENT"
    if freshness != "FRESH":
        return "RECHECK_PRICE_AGE"
    if gap is None:
        return "LOW_NO_GAP"
    if gap <= 1.0:
        return "HIGH_NEAR_TARGET"
    if gap <= 3.0:
        return "MEDIUM_NEAR_TARGET"
    return "LOW_WAIT_PRICE_MOVE"


def _build_rows(advisor_rows: List[Dict[str, Any]], result_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    result_by_match = _results_map(result_rows)
    out: List[Dict[str, Any]] = []
    for row in advisor_rows:
        readiness = str(row.get("EntryReadiness") or "")
        action = str(row.get("ActionVerdict") or "")
        if readiness != "PRICE_TARGET_ONLY" and action not in {"PRICE_TARGET_NEAR", "PRICE_TARGET_WAIT"}:
            continue
        odds = _as_float(row.get("PickOdds"))
        target = _as_float(row.get("MinEntryOdds"))
        if odds is None or target is None:
            continue
        result = result_by_match.get(_match_key(row), {})
        watch = {
            "Rank": row.get("Rank"),
            "Sport": row.get("Sport"),
            "Date": row.get("Date"),
            "League": row.get("League"),
            "Home": row.get("Home"),
            "Away": row.get("Away"),
            "Pick": row.get("Pick"),
            "Prob": row.get("Prob"),
            "CurrentOdds": odds,
            "TargetOdds": target,
            "NeededOddsIncreasePct": round(((target / odds) - 1.0) * 100.0, 2) if odds > 0 else "",
            "EVPercent": row.get("EVPercent"),
            "PriceGapPct": row.get("PriceGapPct"),
            "OneXBetStatus": row.get("OneXBetStatus"),
            "OneXBetFreshness": row.get("OneXBetOddsFreshness"),
            "OneXBetAgeMin": row.get("OneXBetOddsAgeMin"),
            "StartTimeLocal": result.get("StartTimeLocal") or "",
            "MinutesToStart": row.get("MinutesToStart") or "",
            "EventTimingStatus": row.get("EventTimingStatus") or "",
            "ResultStatus": result.get("ResultStatus") or "",
            "GateBlockers": row.get("GateBlockers"),
            "Priority": "",
        }
        watch["Priority"] = _priority(watch)
        out.append(watch)
    out.sort(
        key=lambda r: (
            {"HIGH_NEAR_TARGET": 0, "MEDIUM_NEAR_TARGET": 1, "LOW_WAIT_PRICE_MOVE": 2}.get(str(r["Priority"]), 3),
            _as_float(r.get("NeededOddsIncreasePct")) if _as_float(r.get("NeededOddsIncreasePct")) is not None else 999,
            -(_as_float(r.get("Prob")) or 0.0),
        )
    )
    return out


def _write_csv(rows: List[Dict[str, Any]], path: Path) -> None:
    fields = [
        "Rank",
        "Priority",
        "Sport",
        "Date",
        "League",
        "Home",
        "Away",
        "Pick",
        "Prob",
        "CurrentOdds",
        "TargetOdds",
        "NeededOddsIncreasePct",
        "EVPercent",
        "OneXBetStatus",
        "OneXBetFreshness",
        "OneXBetAgeMin",
        "StartTimeLocal",
        "MinutesToStart",
        "EventTimingStatus",
        "ResultStatus",
        "GateBlockers",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _write_md(rows: List[Dict[str, Any]], target: date, path: Path) -> None:
    lines = [
        "# Price-target watchlist",
        f"- Date: {target.isoformat()}",
        f"- Candidates: {len(rows)}",
        "- Rule: this is watch-only. It becomes reviewable only if current 1xBet odds reach TargetOdds and freshness stays FRESH.",
        "",
        "| # | Priority | Match | Pick | Current | Target | Need % | EV% | 1xBet | Timing | Start | Blockers |",
        "| ---: | --- | --- | --- | ---: | ---: | ---: | ---: | --- | --- | --- | --- |",
    ]
    for idx, row in enumerate(rows, start=1):
        timing = str(row.get("EventTimingStatus") or "UNKNOWN_START")
        minutes = str(row.get("MinutesToStart") or "")
        timing_label = f"{timing} {minutes}m" if minutes else timing
        lines.append(
            f"| {idx} | {row.get('Priority')} | {row.get('Home')} vs {row.get('Away')} | {row.get('Pick')} | "
            f"{float(row.get('CurrentOdds') or 0):.3f} | {float(row.get('TargetOdds') or 0):.3f} | "
            f"{float(row.get('NeededOddsIncreasePct') or 0):.2f} | {float(row.get('EVPercent') or 0):.2f} | "
            f"{row.get('OneXBetStatus')}/{row.get('OneXBetFreshness')} | {timing_label} | "
            f"{row.get('StartTimeLocal')} | {row.get('GateBlockers')} |"
        )
    if not rows:
        lines.append("| - | - | - | - | - | - | - | - | - | - | - | - |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build price-target watchlist.")
    parser.add_argument("--date", default="today")
    parser.add_argument("--advisor-csv", default="")
    parser.add_argument("--results-csv", default="")
    parser.add_argument("--out-csv", default="")
    parser.add_argument("--out-md", default="")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    target = _target_date(args.date)
    advisor_csv = Path(args.advisor_csv) if args.advisor_csv else REPORTS_DIR / f"daily_1xbet_value_advisor_{target.isoformat()}.csv"
    results_csv = Path(args.results_csv) if args.results_csv else REPORTS_DIR / f"prediction_results_{target.isoformat()}.csv"
    rows = _build_rows(_read_csv(advisor_csv), _read_csv(results_csv))
    out_csv = Path(args.out_csv) if args.out_csv else REPORTS_DIR / f"price_target_watchlist_{target.isoformat()}.csv"
    out_md = Path(args.out_md) if args.out_md else REPORTS_DIR / f"price_target_watchlist_{target.isoformat()}.md"
    _write_csv(rows, out_csv)
    _write_md(rows, target, out_md)
    print(f"Wrote {out_csv}")
    print(f"Wrote {out_md}")
    print(f"watchlist={len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
