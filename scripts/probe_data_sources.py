#!/usr/bin/env python3
"""Probe candidate sports data sources without mutating model datasets.

The goal is to separate source availability from prediction quality.  This
script performs small, bounded requests and writes a compact report that can be
used to decide which sources are safe to integrate into the refresh pipeline.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
from dataclasses import dataclass, asdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

import requests


BASE_DIR = Path(__file__).resolve().parent.parent
UA = "StrategyDashboardSourceProbe/1.0"


def _first_existing(*candidates: Path) -> Path:
    for path in candidates:
        if path.exists():
            return path
    return candidates[0]


@dataclass
class ProbeResult:
    source: str
    sport: str
    endpoint: str
    ok: bool
    status: int | None = None
    records: int | None = None
    latency_ms: int | None = None
    note: str = ""


def _now_ms() -> float:
    return datetime.now().timestamp() * 1000.0


def _get(url: str, *, params: dict[str, Any] | None = None, timeout: int = 20) -> tuple[int | None, str, int, str]:
    start = _now_ms()
    try:
        resp = requests.get(url, params=params, headers={"User-Agent": UA}, timeout=timeout)
        latency = int(_now_ms() - start)
        text = resp.text[:2_000_000]
        return resp.status_code, text, latency, ""
    except Exception as exc:
        latency = int(_now_ms() - start)
        return None, "", latency, type(exc).__name__


def _count_csv_rows(text: str) -> int:
    if not text.strip():
        return 0
    try:
        reader = csv.DictReader(io.StringIO(text))
        return sum(1 for _ in reader)
    except Exception:
        return 0


def _extract_keys(path: Path) -> list[str]:
    if not path.exists():
        return []
    files = [path] if path.is_file() else sorted(p for p in path.iterdir() if p.is_file())
    keys: list[str] = []
    seen: set[str] = set()
    for file in files:
        try:
            text = file.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for token in text.split():
            token = token.strip()
            if len(token) < 16:
                continue
            if not all(ch.isalnum() or ch in "-_" for ch in token):
                continue
            if token not in seen:
                seen.add(token)
                keys.append(token)
    return keys


def probe_football_data(start: date, end: date) -> list[ProbeResult]:
    out: list[ProbeResult] = []
    fixtures_url = "https://www.football-data.co.uk/fixtures.csv"
    status, text, latency, err = _get(fixtures_url, timeout=25)
    records = _count_csv_rows(text) if status == 200 else None
    out.append(
        ProbeResult(
            "football-data.co.uk",
            "football",
            fixtures_url,
            status == 200 and bool(records),
            status,
            records,
            latency,
            err or "fixtures csv",
        )
    )

    epl_url = "https://www.football-data.co.uk/mmz4281/2526/E0.csv"
    status, text, latency, err = _get(epl_url, timeout=25)
    records = _count_csv_rows(text) if status == 200 else None
    out.append(
        ProbeResult(
            "football-data.co.uk",
            "football",
            epl_url,
            status == 200 and bool(records),
            status,
            records,
            latency,
            err or "season csv sample",
        )
    )
    return out


def probe_betexplorer() -> list[ProbeResult]:
    sports = ["football", "basketball", "hockey", "tennis", "handball", "table-tennis"]
    out: list[ProbeResult] = []
    for sport in sports:
        url = f"https://www.betexplorer.com/{sport}/fixtures/"
        status, text, latency, err = _get(url, timeout=25)
        event_blocks = len(re.findall(r'data-event-id=|class="in-match"|table-main__match', text))
        out.append(
            ProbeResult(
                "BetExplorer",
                sport,
                url,
                status == 200 and event_blocks > 0,
                status,
                event_blocks,
                latency,
                err or "fixture page event markers",
            )
        )
    return out


def probe_odds_api_io(start: date, end: date, key_path: Path) -> list[ProbeResult]:
    keys = _extract_keys(key_path)
    sports = ["football", "basketball", "tennis", "ice-hockey", "table-tennis"]
    out: list[ProbeResult] = []
    if not keys:
        return [
            ProbeResult(
                "Odds-API.io",
                sport,
                "https://api.odds-api.io/v3/events",
                False,
                None,
                None,
                None,
                "missing key",
            )
            for sport in sports
        ]
    key = keys[0]
    url = "https://api.odds-api.io/v3/events"
    for sport in sports:
        params = {
            "apiKey": key,
            "sport": sport,
            "from": f"{start.isoformat()}T00:00:00Z",
            "to": f"{(end + timedelta(days=1)).isoformat()}T00:00:00Z",
            "limit": "50",
        }
        status, text, latency, err = _get(url, params=params, timeout=25)
        records = None
        note = err
        if status == 200:
            try:
                payload = json.loads(text)
                records = len(payload) if isinstance(payload, list) else None
                note = "events sample"
            except Exception as exc:
                note = type(exc).__name__
        out.append(ProbeResult("Odds-API.io", sport, url, status == 200 and (records or 0) > 0, status, records, latency, note))
    return out


def probe_oddspapi(key_file: Path) -> list[ProbeResult]:
    keys = _extract_keys(key_file)
    key = keys[0] if keys else ""
    url = "https://api.oddspapi.io/v4/sports"
    if not key:
        return [ProbeResult("OddsPapi", "multi-sport", url, False, None, None, None, "missing key")]
    status, text, latency, err = _get(url, params={"apiKey": key}, timeout=25)
    records = None
    note = err
    if status == 200:
        try:
            payload = json.loads(text)
            records = len(payload) if isinstance(payload, list) else len(payload.get("data", [])) if isinstance(payload, dict) else None
            note = "sports catalogue"
        except Exception as exc:
            note = type(exc).__name__
    return [ProbeResult("OddsPapi", "multi-sport", url, status == 200 and (records or 0) > 0, status, records, latency, note)]


def probe_public_sport_sites(start: date) -> list[ProbeResult]:
    probes: list[tuple[str, str, str, Callable[[str], int]]] = [
        (
            "NHL public API",
            "hockey",
            f"https://api-web.nhle.com/v1/schedule/{start.isoformat()}",
            lambda text: sum(len(day.get("games", [])) for day in json.loads(text).get("gameWeek", [])),
        ),
        (
            "Open-Meteo",
            "football-weather",
            "https://api.open-meteo.com/v1/forecast?latitude=51.5072&longitude=-0.1276&hourly=temperature_2m,precipitation,wind_speed_10m&forecast_days=2",
            lambda text: len(json.loads(text).get("hourly", {}).get("time", [])),
        ),
        (
            "ScoreTennis",
            "table-tennis",
            "https://score-tennis.com",
            lambda text: 1 if "tennis" in text.lower() or len(text) > 1000 else 0,
        ),
        (
            "OddsPortal",
            "multi-sport",
            "https://www.oddsportal.com",
            lambda text: 1 if len(text) > 1000 else 0,
        ),
    ]
    out: list[ProbeResult] = []
    for source, sport, url, counter in probes:
        status, text, latency, err = _get(url, timeout=25)
        records = None
        note = err
        if status == 200:
            try:
                records = counter(text)
                note = "probe ok"
            except Exception as exc:
                note = type(exc).__name__
        out.append(ProbeResult(source, sport, url, status == 200 and (records or 0) > 0, status, records, latency, note))
    return out


def write_reports(results: list[ProbeResult], out_json: Path, out_md: Path) -> None:
    out_json.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "results": [asdict(r) for r in results],
    }
    out_json.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    lines = [
        "# Source Probe Report",
        f"- Generated: {payload['generated_at']}",
        f"- Tested sources: {len(results)}",
        "",
        "| Source | Sport | OK | Status | Records | Latency ms | Note |",
        "| --- | --- | --- | ---: | ---: | ---: | --- |",
    ]
    for r in results:
        lines.append(
            f"| {r.source} | {r.sport} | {'yes' if r.ok else 'no'} | {r.status if r.status is not None else ''} | "
            f"{r.records if r.records is not None else ''} | {r.latency_ms if r.latency_ms is not None else ''} | {r.note} |"
        )
    out_md.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    workspace_dir = BASE_DIR.parent.parent
    ap.add_argument("--start-date", default=date.today().isoformat())
    ap.add_argument("--end-date", default=(date.today() + timedelta(days=1)).isoformat())
    ap.add_argument(
        "--odds-api-path",
        default=str(
            _first_existing(
                Path(os.environ.get("ODDSAPI_KEY_FILE", "")) if os.environ.get("ODDSAPI_KEY_FILE") else BASE_DIR / "odds-api",
                workspace_dir / "odds-api",
                Path.home() / "Desktop" / "odds-api",
            )
        ),
    )
    ap.add_argument(
        "--oddspapi-key-file",
        default=str(
            _first_existing(
                Path(os.environ.get("ODDSPAPI_KEY_FILE", "")) if os.environ.get("ODDSPAPI_KEY_FILE") else BASE_DIR / "api" / "OddsPapi.txt",
                workspace_dir / "api" / "oddspapi-pool",
                workspace_dir / "api" / "OddsPapi.txt",
                Path.home() / "Desktop" / "api" / "OddsPapi.txt",
            )
        ),
    )
    ap.add_argument("--out-json", default=str(BASE_DIR / "reports" / f"source_probe_{date.today().isoformat()}.json"))
    ap.add_argument("--out-md", default=str(BASE_DIR / "reports" / f"source_probe_{date.today().isoformat()}.md"))
    args = ap.parse_args()

    start = date.fromisoformat(args.start_date)
    end = date.fromisoformat(args.end_date)
    results: list[ProbeResult] = []
    results.extend(probe_football_data(start, end))
    results.extend(probe_betexplorer())
    results.extend(probe_odds_api_io(start, end, Path(args.odds_api_path)))
    results.extend(probe_oddspapi(Path(args.oddspapi_key_file)))
    results.extend(probe_public_sport_sites(start))

    write_reports(results, Path(args.out_json), Path(args.out_md))
    ok = sum(1 for r in results if r.ok)
    print(f"Source probes passed: {ok}/{len(results)}")
    print(f"Wrote {args.out_json}")
    print(f"Wrote {args.out_md}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
