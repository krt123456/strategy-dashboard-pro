#!/usr/bin/env python3
"""Utilities for extracting table tennis results from score-tennis.com."""
from __future__ import annotations

import csv
import re
import time
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from html import unescape
from pathlib import Path
from typing import Iterable, List, Dict

try:
    import requests  # type: ignore
except Exception:  # pragma: no cover
    raise SystemExit("Missing dependency: requests. Install with: pip install requests")

try:
    from bs4 import BeautifulSoup  # type: ignore
except Exception:  # pragma: no cover
    raise SystemExit("Missing dependency: beautifulsoup4. Install with: pip install beautifulsoup4")

BASE_URL = "https://score-tennis.com"
UA = "Mozilla/5.0 (compatible; ScoreTennisScraper/1.0)"


@dataclass
class MatchRow:
    date_iso: str
    home: str
    away: str
    fthg: int
    ftag: int
    odd_h: float
    odd_a: float
    season: str
    league: str


def _get(url: str, timeout_s: int = 30, retries: int = 2, sleep_s: float = 0.6) -> str | None:
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


def _parse_score(score_text: str) -> tuple[int, int] | None:
    score_text = score_text.strip()
    if not score_text:
        return None
    # match like "3:0 (11:9,11:6,12:10)" or "3:2"
    m = re.match(r"^(\d+)\s*:\s*(\d+)", score_text)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def _split_players(players: str) -> tuple[str, str] | None:
    players = players.strip()
    if not players:
        return None
    if " – " in players:
        home, away = players.split(" – ", 1)
    elif " - " in players:
        home, away = players.split(" - ", 1)
    else:
        return None
    return home.strip(), away.strip()


def normalize_league(name: str) -> str:
    """Normalize league names for robust matching (case/accents/spacing)."""
    name = name.strip()
    if not name:
        return ""
    name = unicodedata.normalize("NFKD", name)
    name = "".join(ch for ch in name if not unicodedata.combining(ch))
    name = name.lower()
    name = re.sub(r"\s+", " ", name).strip()
    return name


def extract_matches(html_text: str, target_leagues: set[str], match_date: date) -> List[MatchRow]:
    soup = BeautifulSoup(html_text, "html.parser")
    rows: List[MatchRow] = []
    for block in soup.select("div.games-list"):
        h2 = block.find("h2")
        if not h2:
            continue
        league = unescape(h2.get_text(strip=True))
        league_key = normalize_league(league)
        if league_key not in target_leagues:
            continue
        tbody = block.find("tbody")
        if not tbody:
            continue
        for tr in tbody.find_all("tr"):
            tds = tr.find_all("td")
            if len(tds) < 3:
                continue
            players_tag = tr.find("a")
            if not players_tag:
                continue
            players = unescape(players_tag.get_text(strip=True))
            split = _split_players(players)
            if not split:
                continue
            home, away = split
            score_text = unescape(tds[2].get_text(strip=True))
            score = _parse_score(score_text)
            if not score:
                continue
            fthg, ftag = score
            rows.append(
                MatchRow(
                    date_iso=match_date.isoformat(),
                    home=home,
                    away=away,
                    fthg=fthg,
                    ftag=ftag,
                    odd_h=0.0,
                    odd_a=0.0,
                    season=str(match_date.year),
                    league=league,
                )
            )
    return rows


def download_date(
    match_date: date,
    target_leagues: set[str],
    *,
    sleep_s: float = 0.2,
) -> List[MatchRow]:
    url = f"{BASE_URL}/games/?date={match_date.isoformat()}"
    html_text = _get(url)
    if not html_text:
        return []
    rows = extract_matches(html_text, target_leagues, match_date)
    time.sleep(sleep_s)
    return rows


def download_range(
    start_date: date,
    end_date: date,
    target_leagues: set[str],
    *,
    sleep_s: float = 0.2,
) -> List[MatchRow]:
    rows: List[MatchRow] = []
    current = start_date
    while current <= end_date:
        rows.extend(download_date(current, target_leagues, sleep_s=sleep_s))
        current += timedelta(days=1)
    return rows


def write_csv(rows: Iterable[MatchRow], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG", "AvgH", "AvgA", "Season", "League"])
        for row in rows:
            writer.writerow(
                [
                    row.date_iso,
                    row.home,
                    row.away,
                    row.fthg,
                    row.ftag,
                    row.odd_h,
                    row.odd_a,
                    row.season,
                    row.league,
                ]
            )
