#!/usr/bin/env python3
"""Utilities for extracting results + odds from betexplorer.com (basketball)."""
from __future__ import annotations

import csv
import re
import time
from dataclasses import dataclass
from datetime import date, timedelta
from html import unescape
from pathlib import Path
from typing import Iterable, List, Tuple

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
    odd_d: float | None
    odd_a: float
    season: str


@dataclass
class FixtureRow:
    date_iso: str
    home: str
    away: str
    odd_h: float | None
    odd_d: float | None
    odd_a: float | None
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
    return None


def _strip_tags(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text)


def parse_season_links(html: str) -> List[Tuple[str, str]]:
    seasons: List[Tuple[str, str]] = []
    for rel_url, label in re.findall(r'<option value="([^"]+)">\s*([^<]+)</option>', html):
        if not rel_url.startswith("/basketball/"):
            continue
        seasons.append((label.strip(), BASE_URL + rel_url))
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
    lowered = text.lower()
    if lowered.startswith("today"):
        return today.isoformat()
    if lowered.startswith("tomorrow"):
        return (today + timedelta(days=1)).isoformat()
    if lowered.startswith("yesterday"):
        return (today - timedelta(days=1)).isoformat()
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
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.DOTALL)
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
        if len(odds) < 2:
            continue
        odd_h = float(odds[0])
        if len(odds) >= 3:
            odd_d = float(odds[1])
            odd_a = float(odds[2])
        else:
            odd_d = None
            odd_a = float(odds[1])

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


def parse_fixtures_page(html: str, season_label: str) -> Iterable[FixtureRow]:
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.DOTALL)
    for row in rows:
        if "in-match" not in row:
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

        date_match = re.search(r'table-main__datetime">([^<]+)<', row)
        if not date_match:
            continue
        date_text = unescape(date_match.group(1))
        date_iso = _parse_date(date_text, season_label)
        if not date_iso:
            continue

        odds = re.findall(r'data-odd="([0-9.]+)"', row)
        odd_h = float(odds[0]) if len(odds) >= 1 else None
        if len(odds) >= 3:
            odd_d = float(odds[1])
            odd_a = float(odds[2])
        elif len(odds) == 2:
            odd_d = None
            odd_a = float(odds[1])
        else:
            odd_d = None
            odd_a = None

        yield FixtureRow(
            date_iso=date_iso,
            home=home,
            away=away,
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


def _fixtures_url(url: str) -> str:
    if not url.endswith("/"):
        url += "/"
    if not url.endswith("/fixtures/"):
        url += "fixtures/"
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

    rows: List[MatchRow] = []
    seen = set()
    seasons_used = 0
    for label, season_url in seasons:
        results_url = _results_url(season_url)
        html = _get(results_url)
        if not html:
            continue
        before = len(rows)
        for row in parse_results_page(html, label):
            key = (row.date_iso, row.home, row.away, row.fthg, row.ftag)
            if key in seen:
                continue
            seen.add(key)
            rows.append(row)
        if len(rows) > before:
            seasons_used += 1
            if max_seasons and max_seasons > 0 and seasons_used >= max_seasons:
                break
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
                    f"{row.odd_d:.2f}" if row.odd_d is not None else "",
                    f"{row.odd_a:.2f}",
                    row.season,
                ]
            )
    return True


def download_league_fixtures_csv(
    base_url: str, out_path: Path, max_seasons: int = 1, sleep_s: float = 0.2
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

    rows: List[FixtureRow] = []
    seen = set()
    tried = [seasons, list(reversed(seasons))]
    for season_list in tried:
        for label, season_url in season_list:
            fixtures_url = _fixtures_url(season_url)
            html = _get(fixtures_url)
            if not html:
                continue
            found = False
            for row in parse_fixtures_page(html, label):
                key = (row.date_iso, row.home, row.away)
                if key in seen:
                    continue
                seen.add(key)
                rows.append(row)
                found = True
            if found:
                break
            time.sleep(sleep_s)
        if rows:
            break

    if not rows:
        return False

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Date", "HomeTeam", "AwayTeam", "OddH", "OddD", "OddA", "Season"])
        for row in rows:
            w.writerow(
                [
                    row.date_iso,
                    row.home,
                    row.away,
                    f"{row.odd_h:.2f}" if row.odd_h is not None else "",
                    f"{row.odd_d:.2f}" if row.odd_d is not None else "",
                    f"{row.odd_a:.2f}" if row.odd_a is not None else "",
                    row.season,
                ]
            )
    return True


__all__ = ["download_league_csv", "download_league_fixtures_csv"]
