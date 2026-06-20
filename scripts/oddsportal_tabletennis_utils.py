#!/usr/bin/env python3
"""Utilities for extracting table tennis results + odds from OddsPortal."""
from __future__ import annotations

import base64
import csv
import json
import re
import time
from dataclasses import dataclass
from datetime import datetime
from html import unescape
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

try:
    import requests  # type: ignore
except Exception:  # pragma: no cover
    raise SystemExit("Missing dependency: requests. Install with: pip install requests")

try:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes  # type: ignore
    from cryptography.hazmat.backends import default_backend  # type: ignore
    from cryptography.hazmat.primitives import hashes  # type: ignore
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC  # type: ignore
except Exception:  # pragma: no cover
    raise SystemExit("Missing dependency: cryptography. Install with: pip install cryptography")

BASE_URL = "https://www.oddsportal.com"
UA = "Mozilla/5.0 (compatible; OddsPortalScraper/1.0)"

# OddsPortal archive responses are encrypted. These constants are derived from their client bundle.
OP_PASSWORD = "J*8sQ!p$7aD_fR2yW@gHn*3bVp#sAdLd_k"
OP_SALT = "5b9a8f2c3e6d1a4b7c8e9d0f1a2b3c4d"


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


def _extract_sport_data(html_text: str) -> dict | None:
    m = re.search(r"star-component[^>]+", html_text)
    if not m:
        return None
    attr = m.group(0)
    md = re.search(r':sport-data="([^"]+)"', attr)
    if not md:
        return None
    raw = unescape(md.group(1))
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def _season_entries(sport_data: dict) -> List[Tuple[str, str, int]]:
    entries: List[Tuple[str, str, int]] = []
    tabs = sport_data.get("tabsSeasons")
    if not isinstance(tabs, dict):
        return entries
    for key, value in tabs.items():
        if not isinstance(key, str) or not key.isdigit():
            continue
        if not isinstance(value, dict):
            continue
        url = value.get("url")
        name = value.get("name")
        season_id = value.get("id")
        if isinstance(url, str) and isinstance(season_id, int):
            entries.append((str(name), BASE_URL + url, season_id))
    entries.sort(key=lambda x: int(re.sub(r"\\D", "", x[0]) or 0), reverse=True)
    return entries


def _default_season_url(sport_data: dict) -> Tuple[str, str] | None:
    tabs = sport_data.get("tabsSeasons")
    if not isinstance(tabs, dict):
        return None
    default_id = tabs.get("default")
    for value in tabs.values():
        if not isinstance(value, dict):
            continue
        if value.get("id") == default_id and value.get("url"):
            return (str(value.get("name")), BASE_URL + value["url"])
    return None


def _fetch_bookiehash(tournament_id: int) -> str | None:
    ajax_url = f"{BASE_URL}/ajax-user-data/t/{tournament_id}/?_={int(time.time()*1000)}"
    text = _get(ajax_url)
    if not text:
        return None
    m = re.search(r'bookiehash":"([^"]+)"', text)
    return m.group(1) if m else None


def _decrypt_payload(enc_text: str) -> dict | None:
    try:
        raw = base64.b64decode(enc_text.strip())
        raw_str = raw.decode("latin1")
    except Exception:
        return None
    if ":" not in raw_str:
        return None
    cipher_b64, iv_hex = raw_str.split(":", 1)
    try:
        ciphertext = base64.b64decode(cipher_b64)
        iv = bytes.fromhex(iv_hex)
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=OP_SALT.encode("utf-8"),
            iterations=1000,
            backend=default_backend(),
        )
        key = kdf.derive(OP_PASSWORD.encode("utf-8"))
        cipher = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend())
        decryptor = cipher.decryptor()
        padded = decryptor.update(ciphertext) + decryptor.finalize()
        pad_len = padded[-1]
        plain = padded[:-pad_len]
        return json.loads(plain.decode("utf-8"))
    except Exception:
        return None


def _fetch_archive_page(
    sport_id: int,
    encoded_tournament_id: str,
    bookiehash: str,
    page: int,
) -> dict | None:
    ts = int(time.time() * 1000)
    url = (
        f"{BASE_URL}/ajax-sport-country-tournament-archive_/"
        f"{sport_id}/{encoded_tournament_id}/{bookiehash}/1/0/page/{page}/?_={ts}"
    )
    text = _get(url)
    if not text:
        return None
    return _decrypt_payload(text)


def _parse_match_rows(rows: Iterable[dict], season_label: str) -> Iterable[MatchRow]:
    for row in rows:
        result = row.get("result")
        if not isinstance(result, str) or not re.match(r"^\d+:\d+$", result):
            continue
        try:
            fthg, ftag = (int(x) for x in result.split(":"))
        except Exception:
            continue
        home = str(row.get("home-name") or row.get("home") or "").strip()
        away = str(row.get("away-name") or row.get("away") or "").strip()
        if not home or not away:
            continue
        ts = row.get("date-start-base") or row.get("date-start-timestamp")
        try:
            date_iso = datetime.utcfromtimestamp(int(ts)).date().isoformat()
        except Exception:
            continue

        odd_h = None
        odd_a = None
        for odd in row.get("odds", []) or []:
            if odd.get("bettingTypeId") != 3 or odd.get("scopeId") != 2:
                continue
            outcome = odd.get("outcomeResultId")
            price = odd.get("avgOdds") or odd.get("maxOdds")
            if not price:
                continue
            if outcome == 1:
                odd_h = float(price)
            elif outcome == 2:
                odd_a = float(price)
        if odd_h is None or odd_a is None:
            continue

        yield MatchRow(
            date_iso=date_iso,
            home=home,
            away=away,
            fthg=fthg,
            ftag=ftag,
            odd_h=odd_h,
            odd_a=odd_a,
            season=season_label,
        )


def download_league_csv(
    url: str,
    out_path: Path,
    *,
    max_seasons: int = 1,
    max_pages: int | None = None,
    sleep_s: float = 0.6,
) -> bool:
    html_text = _get(url)
    if not html_text:
        return False
    sport_data = _extract_sport_data(html_text)
    if not sport_data:
        return False

    season_urls: List[Tuple[str, str]]
    if max_seasons <= 1:
        default = _default_season_url(sport_data)
        season_urls = [default] if default else [(str(sport_data.get("pageH1") or "current"), url)]
    else:
        season_urls = [(name, surl) for name, surl, _ in _season_entries(sport_data)[:max_seasons]]

    rows: List[MatchRow] = []
    for season_label, season_url in season_urls:
        season_html = html_text if season_url == url else _get(season_url)
        if not season_html:
            continue
        season_data = _extract_sport_data(season_html)
        if not season_data:
            continue

        sport_id = season_data.get("sport-id")
        encoded = season_data.get("encodedTurnamentId")
        tournament_id = season_data.get("tournamentId")
        if not isinstance(sport_id, int) or not isinstance(encoded, str) or not isinstance(tournament_id, int):
            continue
        bookiehash = _fetch_bookiehash(tournament_id)
        if not bookiehash:
            continue

        page = 1
        total_pages = None
        while True:
            payload = _fetch_archive_page(sport_id, encoded, bookiehash, page)
            if not payload or payload.get("s") != 1:
                break
            data = payload.get("d") or {}
            rows.extend(_parse_match_rows(data.get("rows", []), season_label))
            if total_pages is None:
                total = data.get("total")
                per_page = data.get("onePage") or 50
                if isinstance(total, int) and per_page:
                    total_pages = max(1, int((total + per_page - 1) / per_page))
            if max_pages is not None and page >= max_pages:
                break
            if total_pages is not None and page >= total_pages:
                break
            page += 1
            time.sleep(sleep_s)

    if not rows:
        return False

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG", "AvgH", "AvgA", "Season"])
        for row in rows:
            writer.writerow([row.date_iso, row.home, row.away, row.fthg, row.ftag, row.odd_h, row.odd_a, row.season])
    return True
