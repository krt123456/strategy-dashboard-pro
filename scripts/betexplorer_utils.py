#!/usr/bin/env python3
"""Utilities for extracting results + 1X2 odds from betexplorer.com."""
from __future__ import annotations

import csv
import re
import time
from dataclasses import dataclass
from datetime import date, timedelta
from html import unescape
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

try:
    import requests  # type: ignore
except Exception:  # pragma: no cover
    raise SystemExit("Missing dependency: requests. Install with: pip install requests")

BASE_URL = "https://www.betexplorer.com"
UA = "Mozilla/5.0 (compatible; BetExplorerScraper/1.0)"


@dataclass
class MatchRow:
    date_iso: str
    home: str
    away: str
    fthg: int
    ftag: int
    odd_h: float
    odd_d: float
    odd_a: float
    season: str


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


def _strip_tags(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text)


def normalize_name(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def parse_country_leagues(html: str, country_slug: str) -> Dict[str, str]:
    pattern = rf'href="(/football/{re.escape(country_slug)}/[^"]+/)"[^>]*>([^<]+)</a>'
    seen_keys: Dict[str, str] = {}
    out: Dict[str, str] = {}
    for rel_url, name in re.findall(pattern, html):
        league_name = unescape(name).strip()
        url = BASE_URL + rel_url
        key = normalize_name(league_name)
        if not key:
            continue
        existing = seen_keys.get(key)
        if existing is None:
            seen_keys[key] = url
            out[league_name] = url
            continue
        # Prefer non-season links when duplicates exist.
        has_year = bool(re.search(r"-\d{4}-\d{4}", url))
        existing_year = bool(re.search(r"-\d{4}-\d{4}", existing))
        if existing_year and not has_year:
            seen_keys[key] = url
            out[league_name] = url
    return out


def parse_season_links(html: str) -> List[Tuple[str, str]]:
    seasons: List[Tuple[str, str]] = []
    for rel_url, label in re.findall(r'<option value="([^"]+)">\\s*([^<]+)</option>', html):
        if not rel_url.startswith("/football/"):
            continue
        seasons.append((label.strip(), BASE_URL + rel_url))
    # Keep unique while preserving order.
    deduped: List[Tuple[str, str]] = []
    seen = set()
    for label, url in seasons:
        key = (label, url)
        if key in seen:
            continue
        seen.add(key)
        deduped.append((label, url))
    return deduped


def _parse_date(text: str, season_label: str | None) -> str | None:
    text = text.strip()
    today = date.today()
    if text.lower() == "today":
        return today.isoformat()
    if text.lower() == "yesterday":
        return (today - timedelta(days=1)).isoformat()
    # Drop time if present.
    text = text.split(" ")[0]
    m = re.match(r"(\d{2})\.(\d{2})\.(\d{4})", text)
    if m:
        d, mn, y = map(int, m.groups())
        return date(y, mn, d).isoformat()
    m = re.match(r"(\d{2})\.(\d{2})\.", text)
    if m:
        d, mn = map(int, m.groups())
        y = None
        if season_label and "/" in season_label:
            try:
                start, end = season_label.split("/")
                start_y = int(start)
                end_y = int(end)
                y = start_y if mn >= 7 else end_y
            except Exception:
                y = None
        if y is None:
            y = today.year
        return date(y, mn, d).isoformat()
    return None


def parse_results_page(html: str, season_label: str) -> Iterable[MatchRow]:
    rows = re.findall(r"<tr>(.*?)</tr>", html, re.DOTALL)
    for row in rows:
        if "data-odd" not in row or "in-match" not in row:
            continue
        link_match = re.search(r'class="in-match"[^>]*>(.*?)</a>', row, re.DOTALL)
        if not link_match:
            continue
        link_html = link_match.group(1)
        spans = re.findall(r"<span[^>]*>(.*?)</span>", link_html, re.DOTALL)
        teams = [_strip_tags(s).strip() for s in spans if _strip_tags(s).strip()]
        if len(teams) < 2:
            continue
        home, away = teams[0], teams[1]

        score_match = re.search(r'<td class="h-text-center"[^>]*>\s*<a[^>]*>([^<]+)</a>', row)
        if not score_match:
            continue
        score = score_match.group(1).strip()
        if not re.match(r"^\d+:\d+$", score):
            continue
        fthg, ftag = (int(x) for x in score.split(":"))

        odds = re.findall(r'data-odd="([0-9.]+)"', row)
        if len(odds) < 3:
            continue
        odd_h, odd_d, odd_a = (float(odds[0]), float(odds[1]), float(odds[2]))

        date_match = re.search(r'<td class="h-text-right h-text-no-wrap">([^<]+)</td>', row)
        if not date_match:
            continue
        date_text = unescape(date_match.group(1))
        date_iso = _parse_date(date_text, season_label)
        if not date_iso:
            continue

        yield MatchRow(
            date_iso=date_iso,
            home=home,
            away=away,
            fthg=fthg,
            ftag=ftag,
            odd_h=odd_h,
            odd_d=odd_d,
            odd_a=odd_a,
            season=season_label,
        )


def _results_url(url: str) -> str:
    if not url.endswith("/"):
        url += "/"
    if not url.endswith("/results/"):
        url += "results/"
    return url


def download_league_csv(
    base_url: str, out_path: Path, max_seasons: int = 0, sleep_s: float = 0.2
) -> bool:
    html = _get(base_url)
    if not html:
        return False
    seasons = parse_season_links(html)
    # Always include the current/base URL first to capture ongoing season results.
    seasons = [("current", base_url)] + seasons
    # De-duplicate by URL while preserving order.
    deduped: List[Tuple[str, str]] = []
    seen_urls = set()
    for label, url in seasons:
        if url in seen_urls:
            continue
        seen_urls.add(url)
        deduped.append((label, url))
    seasons = deduped
    if max_seasons and max_seasons > 0:
        seasons = seasons[:max_seasons]

    rows: List[MatchRow] = []
    seen = set()
    for label, season_url in seasons:
        results_url = _results_url(season_url)
        html = _get(results_url)
        if not html:
            continue
        for row in parse_results_page(html, label):
            key = (row.date_iso, row.home, row.away, row.fthg, row.ftag)
            if key in seen:
                continue
            seen.add(key)
            rows.append(row)
        time.sleep(sleep_s)

    if not rows:
        return False

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG", "AvgH", "AvgD", "AvgA", "Season"])
        for row in rows:
            w.writerow(
                [
                    row.date_iso,
                    row.home,
                    row.away,
                    row.fthg,
                    row.ftag,
                    f"{row.odd_h:.2f}",
                    f"{row.odd_d:.2f}",
                    f"{row.odd_a:.2f}",
                    row.season,
                ]
            )
    return True


__all__ = ["download_league_csv", "parse_country_leagues", "normalize_name"]
