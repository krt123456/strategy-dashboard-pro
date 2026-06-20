#!/usr/bin/env python3
"""List upcoming fixtures for supported leagues without predictions."""
from __future__ import annotations

import argparse
import re
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
import yaml

from apply_primary_strategy_all import EXCLUDED_CODES

try:
    import requests  # type: ignore
except Exception as exc:  # pragma: no cover
    raise SystemExit("Missing dependency: requests. Install with: pip install requests") from exc


UA = "Mozilla/5.0 (compatible; FixturesFetcher/1.0)"
FIXTURES_URL = "https://www.football-data.co.uk/fixtures.csv"


def _get(url: str, timeout_s: int = 45, retries: int = 2, sleep_s: float = 0.8) -> bytes | None:
    for attempt in range(retries + 1):
        try:
            resp = requests.get(url, headers={"User-Agent": UA}, timeout=timeout_s)
            if resp.status_code != 200:
                return None
            return resp.content
        except requests.RequestException:
            if attempt >= retries:
                return None
            time.sleep(sleep_s)
    return None


def _parse_date(text: str) -> str | None:
    text = text.strip()
    today = date.today()
    if not text or text == "&nbsp;":
        return None
    if text.lower().startswith("today"):
        return today.isoformat()
    if text.lower().startswith("tomorrow"):
        return (today + timedelta(days=1)).isoformat()
    text = text.split(" ")[0]
    m = re.match(r"(\d{2})\.(\d{2})\.(\d{4})", text)
    if m:
        d, mn, y = map(int, m.groups())
        return date(y, mn, d).isoformat()
    m = re.match(r"(\d{2})\.(\d{2})\.", text)
    if m:
        d, mn = map(int, m.groups())
        return date(today.year, mn, d).isoformat()
    return None


def parse_betexplorer_fixtures(html: str) -> List[Dict[str, str]]:
    rows = re.findall(r"<tr>(.*?)</tr>", html, re.DOTALL)
    fixtures: List[Dict[str, str]] = []
    last_date: Optional[str] = None
    for row in rows:
        if "in-match" not in row:
            continue
        odds = re.findall(r'data-odd="([0-9.]+)"', row)
        if len(odds) < 3:
            continue
        date_match = re.search(r'table-main__datetime\">([^<]+)<', row)
        date_iso = None
        if date_match:
            date_iso = _parse_date(date_match.group(1))
        if not date_iso:
            date_iso = last_date
        if not date_iso:
            continue
        last_date = date_iso

        link_match = re.search(r'class="in-match"[^>]*>(.*?)</a>', row, re.DOTALL)
        if not link_match:
            continue
        link_html = link_match.group(1)
        spans = re.findall(r"<span[^>]*>(.*?)</span>", link_html, re.DOTALL)
        teams = [re.sub(r"<[^>]+>", "", s).strip() for s in spans if re.sub(r"<[^>]+>", "", s).strip()]
        if len(teams) < 2:
            continue

        fixtures.append(
            {
                "Date": date_iso,
                "HomeTeam": teams[0],
                "AwayTeam": teams[1],
                "AvgH": float(odds[0]),
                "AvgD": float(odds[1]),
                "AvgA": float(odds[2]),
            }
        )
    return fixtures


def fetch_betexplorer_fixtures(url: str) -> List[Dict[str, str]]:
    if not url.endswith("/"):
        url += "/"
    if not url.endswith("fixtures/"):
        url += "fixtures/"
    html = _get(url)
    if not html:
        return []
    return parse_betexplorer_fixtures(html.decode("utf-8", errors="ignore"))


def within_range(date_iso: str, start: date, end: date) -> bool:
    try:
        d = datetime.fromisoformat(date_iso).date()
    except Exception:
        return False
    return start <= d <= end


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="")
    ap.add_argument("--end", default="")
    ap.add_argument("--days-ahead", type=int, default=7)
    ap.add_argument("--summary", default="reports/primary_strategy_all_competitions.csv")
    ap.add_argument("--betexplorer-list", default="data/betexplorer_leagues.yaml")
    ap.add_argument("--out-md", default="")
    ap.add_argument("--out-csv", default="")
    args = ap.parse_args()

    if args.start and args.end:
        start_date = datetime.strptime(args.start, "%Y-%m-%d").date()
        end_date = datetime.strptime(args.end, "%Y-%m-%d").date()
    else:
        start_date = date.today()
        end_date = start_date + timedelta(days=args.days_ahead)

    summary_path = Path(args.summary)
    if not summary_path.exists():
        print(f"Missing summary file: {summary_path}")
        return 1

    summary = pd.read_csv(summary_path)
    summary = summary[summary["Status"] == "ok"].copy()
    supported = {str(c) for c in summary["Code"].tolist() if str(c) not in EXCLUDED_CODES}
    sources = dict(zip(summary["Code"].astype(str), summary["Source"].astype(str)))
    names = dict(zip(summary["Code"].astype(str), summary["League"].astype(str)))

    # Update fixtures.csv from football-data (drop cache to avoid stale future data)
    fixtures_path = Path("data/raw/football_data/fixtures.csv")
    if fixtures_path.exists():
        try:
            fixtures_path.unlink()
        except OSError:
            pass
    payload = _get(FIXTURES_URL)
    if payload:
        fixtures_path.parent.mkdir(parents=True, exist_ok=True)
        fixtures_path.write_bytes(payload)

    fixtures_rows: List[Dict[str, str]] = []
    if fixtures_path.exists():
        fixtures_df = pd.read_csv(fixtures_path, encoding="utf-8-sig")
        if "Div" in fixtures_df.columns:
            fixtures_df["Div"] = fixtures_df["Div"].astype(str)
            fixtures_df["Date"] = pd.to_datetime(fixtures_df["Date"], errors="coerce", dayfirst=True)
            fixtures_df = fixtures_df.dropna(subset=["Date"])
            fixtures_df["DateOnly"] = fixtures_df["Date"].dt.date
            fixtures_df = fixtures_df[(fixtures_df["DateOnly"] >= start_date) & (fixtures_df["DateOnly"] <= end_date)]
            for _, row in fixtures_df.iterrows():
                code = str(row.get("Div"))
                if code not in supported:
                    continue
                fixtures_rows.append(
                    {
                        "Date": row["DateOnly"].isoformat(),
                        "League": names.get(code, code),
                        "Code": code,
                        "HomeTeam": str(row.get("HomeTeam", "")),
                        "AwayTeam": str(row.get("AwayTeam", "")),
                        "AvgH": row.get("AvgH"),
                        "AvgD": row.get("AvgD"),
                        "AvgA": row.get("AvgA"),
                        "Source": "football-data",
                    }
                )

    # BetExplorer fixtures
    bet_cfg = {}
    bet_path = Path(args.betexplorer_list)
    if bet_path.exists():
        with bet_path.open("r", encoding="utf-8") as f:
            bet_cfg = yaml.safe_load(f) or {}
    for entry in bet_cfg.get("lists", {}).get("betexplorer", []):
        code = str(entry.get("code", "")).strip()
        url = str(entry.get("url", "")).strip()
        if not code or not url or code not in supported:
            continue
        fixtures = fetch_betexplorer_fixtures(url)
        for fix in fixtures:
            if not fix.get("Date") or not within_range(fix["Date"], start_date, end_date):
                continue
            fixtures_rows.append(
                {
                    "Date": fix["Date"],
                    "League": names.get(code, code),
                    "Code": code,
                    "HomeTeam": fix["HomeTeam"],
                    "AwayTeam": fix["AwayTeam"],
                    "AvgH": fix.get("AvgH"),
                    "AvgD": fix.get("AvgD"),
                    "AvgA": fix.get("AvgA"),
                    "Source": "betexplorer",
                }
            )

    fixtures_rows.sort(key=lambda r: (r["Date"], r["League"], r["HomeTeam"]))

    out_md = Path(args.out_md) if args.out_md else Path(f"reports/upcoming_fixtures_{start_date}_{end_date}.md")
    out_csv = Path(args.out_csv) if args.out_csv else Path(f"reports/upcoming_fixtures_{start_date}_{end_date}.csv")
    pd.DataFrame(fixtures_rows).to_csv(out_csv, index=False)

    lines = [
        "# Upcoming fixtures (supported leagues)",
        f"- Range: {start_date.isoformat()} to {end_date.isoformat()}",
        f"- Total fixtures: {len(fixtures_rows)}",
        "",
        "| Date | League | Code | Home | Away | Source |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for row in fixtures_rows:
        lines.append(
            f"| {row['Date']} | {row['League']} | {row['Code']} | {row['HomeTeam']} | {row['AwayTeam']} | {row['Source']} |"
        )

    out_md.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {out_md} and {out_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
