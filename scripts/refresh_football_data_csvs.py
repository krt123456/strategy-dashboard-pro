#!/usr/bin/env python3
"""Fast football-data CSV refresher for daily prediction runs.

This updates raw match-result CSVs only. It intentionally does not rebuild the
strategy grid; daily_select.py can reuse the existing strategy summary while
reading fresher team form/history from these raw files.
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path
from typing import Iterable

import requests


MAIN_CODES = {
    "E0",
    "E1",
    "E2",
    "E3",
    "EC",
    "SC0",
    "SC1",
    "SC2",
    "SC3",
    "D1",
    "D2",
    "I1",
    "I2",
    "SP1",
    "SP2",
    "F1",
    "F2",
    "N1",
    "B1",
    "P1",
    "T1",
    "G1",
}


def split_codes(raw: str) -> list[str]:
    if not raw.strip():
        return sorted(MAIN_CODES)
    return [c.strip().upper() for c in raw.split(",") if c.strip()]


def fetch(url: str, dest: Path, retries: int = 3) -> bool:
    headers = {"User-Agent": "Mozilla/5.0 (compatible; FootballDataDailyRefresh/1.0)"}
    dest.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(retries):
        try:
            resp = requests.get(url, timeout=30, headers=headers)
            if resp.status_code == 200 and resp.content.strip():
                tmp = dest.with_suffix(dest.suffix + ".tmp")
                tmp.write_bytes(resp.content)
                tmp.replace(dest)
                return True
        except requests.RequestException:
            pass
        if attempt < retries - 1:
            time.sleep(1.5 * (attempt + 1))
    return False


def refresh_codes(codes: Iterable[str], season: str, raw_dir: Path) -> tuple[list[str], list[str], list[str]]:
    ok: list[str] = []
    failed: list[str] = []
    skipped: list[str] = []
    base = "https://www.football-data.co.uk/mmz4281"
    for code in codes:
        if code not in MAIN_CODES:
            skipped.append(code)
            continue
        url = f"{base}/{season}/{code}.csv"
        dest = raw_dir / f"{code}_{season}.csv"
        if fetch(url, dest):
            ok.append(code)
        else:
            failed.append(code)
    return ok, failed, skipped


def main() -> int:
    ap = argparse.ArgumentParser(description="Refresh raw football-data CSVs quickly.")
    ap.add_argument("--season", default="2526")
    ap.add_argument("--codes", default="", help="Comma-separated football-data league codes. Empty means all main codes.")
    ap.add_argument("--raw-dir", default="data/raw/football_data")
    args = ap.parse_args()

    ok, failed, skipped = refresh_codes(split_codes(args.codes), str(args.season), Path(args.raw_dir))
    print(f"football-data refresh ok={len(ok)} failed={len(failed)} skipped={len(skipped)}")
    if ok:
        print("ok_codes=" + ",".join(ok))
    if failed:
        print("failed_codes=" + ",".join(failed))
    if skipped:
        print("skipped_codes=" + ",".join(skipped))
    return 0 if ok or skipped else 1


if __name__ == "__main__":
    raise SystemExit(main())
