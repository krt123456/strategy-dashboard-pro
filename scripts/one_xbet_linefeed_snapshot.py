#!/usr/bin/env python3
"""Capture a reusable public 1xBet linefeed snapshot for odds confirmation."""
from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlencode


BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / "data"
REPORTS_DIR = BASE_DIR / "reports"
DEFAULT_SNAPSHOT = DATA_DIR / "one_xbet_linefeed_snapshot.csv"
DEFAULT_HISTORY = DATA_DIR / "one_xbet_linefeed_history.csv"

DEFAULT_BASE_URLS = [
    "https://q1ayxwi7tuwrn.bar",
    "https://1xbet.com",
]

try:
    from sports_strategy_profiles import sport_ids, sport_labels
    SPORT_IDS = sport_ids()
    SPORT_LABELS = sport_labels()
except Exception:  # pragma: no cover
    SPORT_IDS = {
        "football": 1,
        "hockey": 2,
        "basketball": 3,
        "tennis": 4,
        "handball": 8,
        "tabletennis": 10,
        "volleyball": 6,
        "baseball": 5,
        "cricket": 66,
        "americanfootball": 13,
        "futsal": 14,
        "darts": 21,
        "snooker": 30,
    }
    SPORT_LABELS = {value: key for key, value in SPORT_IDS.items()}


def _target_date(value: str) -> date:
    raw = (value or "today").strip().lower()
    today = date.today()
    if raw in {"today", "اليوم"}:
        return today
    if raw in {"tomorrow", "غدا", "غداً"}:
        return today + timedelta(days=1)
    return date.fromisoformat(raw)


def _base_urls() -> List[str]:
    configured = os.environ.get("ONE_XBET_PUBLIC_BASE_URLS") or os.environ.get("ONE_XBET_PUBLIC_BASE_URL") or ""
    urls = [item.strip().rstrip("/") for item in configured.split(",") if item.strip()]
    for url in DEFAULT_BASE_URLS:
        if url not in urls:
            urls.append(url)
    return urls


def _curl_json(base_url: str, path: str, params: Dict[str, Any], timeout_s: int) -> Dict[str, Any]:
    full_url = f"{base_url}{path}?{urlencode(params)}"
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
        full_url,
    ]
    proc = subprocess.run(cmd, check=False, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or f"curl rc={proc.returncode}").strip())
    return json.loads(proc.stdout)


def _fetch_events(sport_id: int, count: int, timeout_s: int) -> Tuple[List[Dict[str, Any]], str]:
    last_error: Optional[Exception] = None
    for base in _base_urls():
        try:
            payload = _curl_json(
                base,
                "/service-api/LineFeed/Get1x2_VZip",
                {"sports": sport_id, "count": count, "lng": "en", "mode": 1},
                timeout_s,
            )
            if payload.get("Success") is True:
                return [item for item in payload.get("Value") or [] if isinstance(item, dict)], base
            last_error = RuntimeError(str(payload.get("Error") or payload.get("ErrorCode") or "api error"))
        except Exception as exc:
            last_error = exc
            continue
    raise RuntimeError(str(last_error or "1xBet public linefeed failed"))


def _event_start_utc(value: Any) -> str:
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc).isoformat(timespec="seconds")
    except Exception:
        return ""


def _event_date(value: Any) -> str:
    start = _event_start_utc(value)
    return start[:10] if start else ""


def _main_prices(event: Dict[str, Any]) -> Dict[int, float]:
    prices: Dict[int, float] = {}
    for odd in event.get("E") or []:
        if not isinstance(odd, dict):
            continue
        try:
            group = int(odd.get("G"))
            typ = int(odd.get("T"))
            price = float(odd.get("C"))
        except Exception:
            continue
        if group == 1 and typ in {1, 2, 3} and price > 1.0:
            prices[typ] = price
    return prices


def _sport_filter(value: str) -> List[int]:
    raw = (value or "football,basketball,tennis,handball,hockey,tabletennis,volleyball,baseball,cricket,americanfootball,futsal,darts,snooker").strip()
    out: List[int] = []
    for item in raw.split(","):
        key = item.strip().lower().replace("_", "").replace(" ", "")
        if not key:
            continue
        if key.isdigit():
            out.append(int(key))
        elif key in SPORT_IDS:
            out.append(SPORT_IDS[key])
        elif key == "tabletennis":
            out.append(SPORT_IDS["tabletennis"])
    return list(dict.fromkeys(out))


def _build_rows(target: date, sport_ids: Iterable[int], count: int, timeout_s: int) -> Tuple[List[Dict[str, Any]], List[str]]:
    rows: List[Dict[str, Any]] = []
    notes: List[str] = []
    snapshot_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    for sport_id in sport_ids:
        sport = SPORT_LABELS.get(sport_id, str(sport_id))
        try:
            events, base_url = _fetch_events(sport_id, count=count, timeout_s=timeout_s)
        except Exception as exc:
            notes.append(f"- {sport}: unavailable ({str(exc)[:160]}).")
            continue
        added = 0
        for event in events:
            event_day = _event_date(event.get("S"))
            if event_day != target.isoformat():
                continue
            prices = _main_prices(event)
            if not prices:
                continue
            home = str(event.get("O1E") or event.get("O1") or "").strip()
            away = str(event.get("O2E") or event.get("O2") or "").strip()
            if not home or not away:
                continue
            rows.append(
                {
                    "SnapshotAt": snapshot_at,
                    "Date": event_day,
                    "Sport": sport,
                    "SportId": sport_id,
                    "League": event.get("LE") or event.get("L") or "",
                    "Home": home,
                    "Away": away,
                    "EventId": event.get("I") or "",
                    "CanonicalId": event.get("CI") or "",
                    "StartUtc": _event_start_utc(event.get("S")),
                    "HomeOdds": prices.get(1, ""),
                    "DrawOdds": prices.get(2, ""),
                    "AwayOdds": prices.get(3, ""),
                    "Source": "1XBET_PUBLIC_LINEFEED",
                    "PublicBase": base_url,
                }
            )
            added += 1
        notes.append(f"- {sport}: fetched={len(events)} target_rows={added}.")
    return rows, notes


def _write_csv(path: Path, rows: List[Dict[str, Any]], append: bool = False) -> None:
    fields = [
        "SnapshotAt",
        "Date",
        "Sport",
        "SportId",
        "League",
        "Home",
        "Away",
        "EventId",
        "CanonicalId",
        "StartUtc",
        "HomeOdds",
        "DrawOdds",
        "AwayOdds",
        "Source",
        "PublicBase",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = "a" if append else "w"
    write_header = not append or not path.exists() or path.stat().st_size == 0
    with path.open(mode, encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        if write_header:
            writer.writeheader()
        writer.writerows(rows)


def _write_md(path: Path, target: date, rows: List[Dict[str, Any]], notes: List[str]) -> None:
    counts = Counter(str(row.get("Sport") or "") for row in rows)
    lines = [
        "# 1xBet linefeed snapshot",
        f"- Date: {target.isoformat()}",
        f"- Rows: {len(rows)}",
        "",
        "## Sports",
        *([f"- {sport}: {count}" for sport, count in counts.most_common()] or ["- none: 0"]),
        "",
        "## Fetch Notes",
        *notes,
        "",
        "| Sport | League | Match | Start UTC | Home | Draw | Away | EventId |",
        "| --- | --- | --- | --- | ---: | ---: | ---: | --- |",
    ]
    for row in rows[:120]:
        lines.append(
            f"| {row.get('Sport')} | {row.get('League')} | {row.get('Home')} vs {row.get('Away')} | "
            f"{row.get('StartUtc')} | {row.get('HomeOdds')} | {row.get('DrawOdds')} | "
            f"{row.get('AwayOdds')} | {row.get('EventId')} |"
        )
    if not rows:
        lines.append("| - | - | - | - | - | - | - | - |")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Capture public 1xBet linefeed events for target date.")
    parser.add_argument("--date", default="today")
    parser.add_argument("--sports", default="football,basketball,tennis,handball,hockey,tabletennis,volleyball,baseball,cricket,americanfootball,futsal,darts,snooker")
    parser.add_argument("--count", type=int, default=500)
    parser.add_argument("--timeout", type=int, default=15)
    parser.add_argument("--out-csv", default=str(DEFAULT_SNAPSHOT))
    parser.add_argument("--history-out", default=str(DEFAULT_HISTORY))
    parser.add_argument("--out-md", default="")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    target = _target_date(args.date)
    rows, notes = _build_rows(target, _sport_filter(args.sports), args.count, args.timeout)
    out_csv = Path(args.out_csv)
    history = Path(args.history_out)
    out_md = Path(args.out_md) if args.out_md else REPORTS_DIR / f"1xbet_linefeed_snapshot_{target.isoformat()}.md"
    _write_csv(out_csv, rows, append=False)
    _write_csv(history, rows, append=True)
    _write_md(out_md, target, rows, notes)
    print(f"Wrote {out_csv}")
    print(f"Wrote {history}")
    print(f"Wrote {out_md}")
    print(f"linefeed_snapshot_rows={len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
