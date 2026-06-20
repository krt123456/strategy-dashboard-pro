#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from datetime import date, datetime, timedelta
from html import unescape
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import yaml

try:
    import requests  # type: ignore
except Exception as exc:  # pragma: no cover
    raise SystemExit("Missing dependency: requests. Install with: pip install requests") from exc


UA = "Mozilla/5.0 (compatible; XSportsBetScraper/1.0)"
BASE_URL = "https://www.xsportsbet.com/en/betting/table-tennis/"


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


def _strip_html(html_text: str) -> List[str]:
    text = re.sub(r"<script[^>]*>.*?</script>", " ", html_text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<style[^>]*>.*?</style>", " ", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "\n", text)
    text = unescape(text)
    lines = []
    for raw in text.splitlines():
        cleaned = re.sub(r"\s+", " ", raw).strip()
        if cleaned:
            lines.append(cleaned)
    return lines


def _load_patterns(path: Optional[str]) -> Tuple[List[str], List[str]]:
    if not path:
        return [], []
    p = Path(path)
    if not p.exists():
        return [], []
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    include = data.get("include") or data.get("competitions") or []
    exclude = data.get("exclude") or []
    return list(include), list(exclude)


def _match_any(patterns: List[str], text: str) -> bool:
    for pattern in patterns:
        try:
            if re.search(pattern, text, flags=re.IGNORECASE):
                return True
        except re.error:
            if pattern.lower() in text.lower():
                return True
    return False


def _parse_date_label(label: str, base: date) -> Optional[date]:
    lowered = label.strip().lower()
    if lowered.startswith("today"):
        return base
    if lowered.startswith("tomorrow"):
        return base + timedelta(days=1)
    match = re.match(r"(\d{1,2})[./-](\d{1,2})(?:[./-](\d{2,4}))?", label)
    if match:
        day, month, year = match.groups()
        year_val = base.year
        if year:
            year_val = int(year) + (2000 if len(year) == 2 else 0)
        return date(year_val, int(month), int(day))
    return None


def _is_time(text: str) -> bool:
    return bool(re.match(r"^\d{1,2}:\d{2}$", text))


def _is_decimal(text: str) -> bool:
    return bool(re.match(r"^\d+(?:\.\d+)?$", text))


def _norm_key(text: str) -> str:
    text = text.strip().lower()
    text = re.sub(r"[^a-z0-9]+", "", text)
    return text


def _make_event_id(parts: Iterable[str]) -> int:
    base = "|".join(parts).encode("utf-8")
    digest = hashlib.md5(base).hexdigest()[:12]
    return int(digest, 16)


def _load_existing_event_keys(events_dir: Path) -> set[tuple[str, str, str]]:
    keys: set[tuple[str, str, str]] = set()
    if not events_dir.exists():
        return keys
    for path in events_dir.glob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(payload, list):
            continue
        for ev in payload:
            date_val = str(ev.get("date") or ev.get("Date") or "")
            home = _norm_key(str(ev.get("home") or ev.get("Home") or ""))
            away = _norm_key(str(ev.get("away") or ev.get("Away") or ""))
            if date_val and home and away:
                keys.add((date_val, home, away))
    return keys


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start-date", required=True, help="YYYY-MM-DD")
    ap.add_argument("--end-date", required=True, help="YYYY-MM-DD (inclusive)")
    ap.add_argument("--out-events-dir", default="data/raw/oddsapi_tabletennis_future/events")
    ap.add_argument("--out-odds-dir", default="data/raw/oddsapi_tabletennis_future/odds")
    ap.add_argument("--filter-file", default="data/oddspapi_tabletennis_targets.yaml")
    ap.add_argument("--reset", action="store_true")
    ap.add_argument("--sleep", type=float, default=0.4)
    args = ap.parse_args()

    start_date = datetime.strptime(args.start_date, "%Y-%m-%d").date()
    end_date = datetime.strptime(args.end_date, "%Y-%m-%d").date()

    events_dir = Path(args.out_events_dir)
    odds_dir = Path(args.out_odds_dir)
    events_dir.mkdir(parents=True, exist_ok=True)
    odds_dir.mkdir(parents=True, exist_ok=True)

    if args.reset:
        for path in events_dir.glob("*.json"):
            path.unlink(missing_ok=True)
        for path in odds_dir.glob("*.json"):
            path.unlink(missing_ok=True)

    include_patterns, exclude_patterns = _load_patterns(args.filter_file)

    html = _get(BASE_URL)
    if not html:
        return 1
    lines = _strip_html(html)
    base_today = date.today()

    existing_keys = _load_existing_event_keys(events_dir)
    events: List[Dict[str, Any]] = []
    odds_payloads: List[Tuple[int, Dict[str, Any]]] = []
    current_league: Optional[str] = None

    for idx, line in enumerate(lines):
        if line and include_patterns and _match_any(include_patterns, line):
            if exclude_patterns and _match_any(exclude_patterns, line):
                continue
            current_league = line
            continue
        if not _is_time(line):
            continue
        if idx + 3 >= len(lines):
            continue
        date_label = lines[idx + 1]
        match_date = _parse_date_label(date_label, base_today)
        if not match_date:
            continue
        if not (start_date <= match_date <= end_date):
            continue
        home = lines[idx + 2]
        away = lines[idx + 3]
        if not home or not away or _is_time(home) or _is_time(away):
            continue
        if current_league is None:
            continue
        if include_patterns and not _match_any(include_patterns, current_league):
            continue
        if exclude_patterns and _match_any(exclude_patterns, current_league):
            continue

        odds: List[float] = []
        j = idx + 4
        while j < len(lines) and len(odds) < 2:
            token = lines[j]
            if _is_decimal(token):
                odds.append(float(token))
            j += 1
        if len(odds) < 2:
            continue

        key = (match_date.isoformat(), _norm_key(home), _norm_key(away))
        if key in existing_keys:
            continue
        existing_keys.add(key)

        event_id = _make_event_id([key[0], current_league, home, away])
        events.append(
            {
                "id": event_id,
                "home": home,
                "away": away,
                "league": current_league,
                "date": match_date.isoformat(),
                "scheduled": f"{match_date.isoformat()}T{line}:00Z",
                "source": "xsportsbet",
            }
        )
        odds_payloads.append(
            (
                event_id,
                {
                    "id": event_id,
                    "bookmakers": {
                        "xsportsbet": [
                            {
                                "name": "ML",
                                "odds": [{"home": odds[0], "away": odds[1]}],
                            }
                        ]
                    },
                },
            )
        )

    if not events:
        return 0

    out_events = events_dir / f"events_xsportsbet_{start_date}_{end_date}.json"
    out_events.write_text(json.dumps(events, ensure_ascii=False), encoding="utf-8")
    for event_id, payload in odds_payloads:
        (odds_dir / f"odds_xsportsbet_{event_id}.json").write_text(
            json.dumps(payload, ensure_ascii=False),
            encoding="utf-8",
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
