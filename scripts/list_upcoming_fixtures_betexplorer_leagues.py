#!/usr/bin/env python3
"""List upcoming fixtures for a set of BetExplorer leagues (no API)."""
from __future__ import annotations

import argparse
import csv
import re
import time
from datetime import date, datetime, timedelta
from html import unescape
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import yaml

try:
    import requests  # type: ignore
except Exception as exc:  # pragma: no cover
    raise SystemExit("Missing dependency: requests. Install with: pip install requests") from exc

UA = "Mozilla/5.0 (compatible; BetExplorerScraper/1.0)"


def _get(url: str, timeout_s: int = 45, retries: int = 2, sleep_s: float = 0.8) -> str | None:
    for attempt in range(retries + 1):
        try:
            resp = requests.get(url, headers={"User-Agent": UA}, timeout=timeout_s)
            if resp.status_code != 200:
                return None
            return resp.text
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
    lowered = text.lower()
    if lowered.startswith("today"):
        return today.isoformat()
    if lowered.startswith("tomorrow"):
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


def parse_fixtures_page(html: str) -> Iterable[Dict[str, str]]:
    rows = re.findall(r"<tr>(.*?)</tr>", html, re.DOTALL)
    last_date: Optional[str] = None
    for row in rows:
        if "in-match" not in row:
            continue
        date_match = re.search(r'table-main__datetime">([^<]+)<', row)
        date_iso = None
        if date_match:
            date_iso = _parse_date(unescape(date_match.group(1)))
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

        yield {
            "Date": date_iso,
            "Home": teams[0],
            "Away": teams[1],
        }


def within_range(date_iso: str, start: date, end: date) -> bool:
    try:
        d = datetime.fromisoformat(date_iso).date()
    except Exception:
        return False
    return start <= d <= end


def load_yaml(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--league-list", required=True)
    ap.add_argument("--list-key", default="")
    ap.add_argument("--start", default="")
    ap.add_argument("--end", default="")
    ap.add_argument("--days-ahead", type=int, default=3)
    ap.add_argument("--out-csv", default="")
    ap.add_argument("--out-md", default="")
    ap.add_argument("--sleep", type=float, default=0.2)
    args = ap.parse_args()

    if args.start and args.end:
        start_date = datetime.strptime(args.start, "%Y-%m-%d").date()
        end_date = datetime.strptime(args.end, "%Y-%m-%d").date()
    else:
        start_date = date.today()
        end_date = start_date + timedelta(days=args.days_ahead)

    cfg = load_yaml(Path(args.league_list))
    list_key = args.list_key or next(iter(cfg.get("lists", {}).keys()), "")
    leagues = cfg.get("lists", {}).get(list_key, [])
    if not leagues:
        print(f"No leagues found for key: {list_key}")
        return 1

    rows: List[Dict[str, str]] = []
    for entry in leagues:
        code = entry.get("code") or ""
        name = entry.get("name") or ""
        url = entry.get("url") or ""
        if not url:
            continue
        if not url.endswith("/"):
            url += "/"
        if not url.endswith("fixtures/"):
            url += "fixtures/"
        html = _get(url)
        if not html:
            continue
        for fix in parse_fixtures_page(html):
            if not within_range(fix["Date"], start_date, end_date):
                continue
            rows.append(
                {
                    "League": code or name,
                    "Date": fix["Date"],
                    "Home": fix["Home"],
                    "Away": fix["Away"],
                    "Source": "betexplorer",
                }
            )
        time.sleep(args.sleep)

    rows.sort(key=lambda r: (r["League"], r["Date"], r["Home"]))
    out_csv = Path(args.out_csv) if args.out_csv else Path(f"reports/upcoming_fixtures_{list_key}_{start_date}_{end_date}.csv")
    out_md = Path(args.out_md) if args.out_md else Path(f"reports/upcoming_fixtures_{list_key}_{start_date}_{end_date}.md")
    out_csv.parent.mkdir(parents=True, exist_ok=True)

    with out_csv.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["League", "Date", "Home", "Away", "Source"])
        writer.writeheader()
        writer.writerows(rows)

    lines = [
        "# Upcoming fixtures (BetExplorer)",
        f"- List: {list_key}",
        f"- Range: {start_date.isoformat()} to {end_date.isoformat()}",
        f"- Total fixtures: {len(rows)}",
        "",
        "| League | Date | Home | Away | Source |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        lines.append(f"| {row['League']} | {row['Date']} | {row['Home']} | {row['Away']} | {row['Source']} |")

    out_md.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {out_md} and {out_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
