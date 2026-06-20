#!/usr/bin/env python3
"""Download basketball results + fixtures from BetExplorer (no API)."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Dict, List, Tuple

from betexplorer_basketball_utils import download_league_csv, download_league_fixtures_csv

try:
    import requests  # type: ignore
except Exception as exc:  # pragma: no cover
    raise SystemExit("Missing dependency: requests. Install with: pip install requests") from exc

BASE_URL = "https://www.betexplorer.com"
UA = "Mozilla/5.0 (compatible; BetExplorerScraper/1.0)"

SKIP_COUNTRIES = {
    "streaks",
    "teams",
    "standings",
    "results",
    "fixtures",
    "odds",
    "live",
}


def _get(url: str) -> str | None:
    try:
        resp = requests.get(url, headers={"User-Agent": UA}, timeout=45)
        if resp.status_code != 200:
            return None
        return resp.text
    except requests.RequestException:
        return None


def _normalize_code(country: str, league: str) -> str:
    code = f"{country}_{league}".lower()
    code = re.sub(r"[^a-z0-9]+", "_", code).strip("_")
    return code


def _titleize(text: str) -> str:
    return " ".join([t.capitalize() for t in text.replace("-", " ").split()])


def collect_league_links(html: str, max_leagues: int = 0) -> List[Tuple[str, str, str]]:
    links = re.findall(r'href="(/basketball/([^"/]+)/([^"/]+)/)"', html)
    seen = set()
    out: List[Tuple[str, str, str]] = []
    for rel_url, country, league in links:
        if country in SKIP_COUNTRIES or league in {"results", "fixtures", "odds"}:
            continue
        key = (country, league)
        if key in seen:
            continue
        seen.add(key)
        out.append((country, league, BASE_URL + rel_url))
        if max_leagues and len(out) >= max_leagues:
            break
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-results-dir", default="data/raw/betexplorer_basketball_results")
    ap.add_argument("--out-fixtures-dir", default="data/raw/betexplorer_basketball_fixtures")
    ap.add_argument("--out-map", default="data/raw/betexplorer_basketball_map.json")
    ap.add_argument("--max-seasons", type=int, default=1)
    ap.add_argument("--max-leagues", type=int, default=0)
    ap.add_argument("--sleep", type=float, default=0.2)
    ap.add_argument("--mode", default="both", choices=["both", "results", "fixtures"])
    args = ap.parse_args()

    html = _get(f"{BASE_URL}/basketball/")
    if not html:
        print("Failed to fetch BetExplorer basketball index.")
        return 1
    leagues = collect_league_links(html, max_leagues=args.max_leagues)
    if not leagues:
        print("No basketball leagues discovered.")
        return 1

    results_dir = Path(args.out_results_dir)
    fixtures_dir = Path(args.out_fixtures_dir)
    results_dir.mkdir(parents=True, exist_ok=True)
    fixtures_dir.mkdir(parents=True, exist_ok=True)

    mapping: Dict[str, Dict[str, str]] = {}
    ok_any = False
    for country, league, url in leagues:
        code = _normalize_code(country, league)
        league_name = f"{_titleize(country)} { _titleize(league) }".strip()
        mapping[code] = {"url": url, "league": league_name, "country": country, "slug": league}

        if args.mode in {"both", "results"}:
            out_path = results_dir / f"{code}.csv"
            ok = download_league_csv(url, out_path, max_seasons=args.max_seasons, sleep_s=args.sleep)
            ok_any = ok_any or ok
            status = "ok" if ok else "failed"
            print(f"[results] {code}: {status} -> {out_path}")

        if args.mode in {"both", "fixtures"}:
            out_path = fixtures_dir / f"{code}.csv"
            ok = download_league_fixtures_csv(url, out_path, max_seasons=1, sleep_s=args.sleep)
            ok_any = ok_any or ok
            status = "ok" if ok else "failed"
            print(f"[fixtures] {code}: {status} -> {out_path}")

    map_path = Path(args.out_map)
    map_path.parent.mkdir(parents=True, exist_ok=True)
    map_path.write_text(json.dumps(mapping, indent=2, ensure_ascii=False), encoding="utf-8")
    return 0 if ok_any else 1


if __name__ == "__main__":
    raise SystemExit(main())
