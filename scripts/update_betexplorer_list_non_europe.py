#!/usr/bin/env python3
"""Map missing non-European competitions to BetExplorer URLs and write a list."""
from __future__ import annotations

import argparse
import csv
import re
from collections import defaultdict
from html import unescape
from pathlib import Path
from typing import Dict, List, Tuple

try:
    import requests  # type: ignore
except Exception:  # pragma: no cover
    raise SystemExit("Missing dependency: requests. Install with: pip install requests")

from betexplorer_utils import normalize_name, parse_country_leagues

BASE_URL = "https://www.betexplorer.com"
UA = "Mozilla/5.0 (compatible; BetExplorerMapper/1.0)"


COUNTRY_SLUGS: Dict[str, List[str]] = {
    "Argentina": ["argentina"],
    "Brazil": ["brazil"],
    "Chile": ["chile"],
    "Colombia": ["colombia"],
    "Paraguay": ["paraguay"],
    "Uruguay": ["uruguay"],
    "Peru": ["peru"],
    "Suriname": ["suriname"],
    "Mexico": ["mexico"],
    "Belize": ["belize"],
    "Costa Rica": ["costa-rica"],
    "El Salvador": ["el-salvador"],
    "Jamaica": ["jamaica"],
    "Trinidad and Tobago": ["trinidad-and-tobago"],
    "Dominican Republic": ["dominican-republic"],
    "Saint Kitts and Nevis": ["saint-kitts-and-nevis", "st-kitts-and-nevis"],
    "Nicaragua": ["nicaragua"],
    "Barbados": ["barbados"],
    "Curacao": ["curacao"],
    "Saudi Arabia": ["saudi-arabia"],
    "United Arab Emirates": ["uae", "united-arab-emirates"],
    "Kuwait": ["kuwait"],
    "Iraq": ["iraq"],
    "Indonesia": ["indonesia"],
    "Thailand": ["thailand"],
    "Vietnam": ["vietnam"],
    "Myanmar": ["myanmar"],
    "Australia": ["australia"],
    "Guatemala": ["guatemala"],
    "Panama": ["panama"],
    "India": ["india"],
}

COMPETITION_OVERRIDES: Dict[str, List[str]] = {
    "Copa Libertadores": ["south-america"],
    "Copa Sudamericana": ["south-america"],
    "Recopa Sudamericana": ["south-america"],
    "Continental Champions Match": ["world", "south-america"],
    "OFC Pro League": ["oceania"],
}

COUNTRY_PREFIXES = sorted(COUNTRY_SLUGS.keys(), key=len, reverse=True)


def fetch_country_html(slug: str) -> str | None:
    url = f"{BASE_URL}/football/{slug}/"
    resp = requests.get(url, headers={"User-Agent": UA}, timeout=30)
    if resp.status_code != 200:
        return None
    return resp.text


STOP_TOKENS = {
    "campeonato",
    "liga",
    "league",
    "division",
    "div",
    "super",
    "cup",
    "premier",
    "championship",
    "serie",
    "primera",
    "segunda",
    "tercera",
}


def _tokens(text: str) -> List[str]:
    return [t for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in STOP_TOKENS]


def strip_country_prefix(name: str, country: str | None) -> str:
    if not country:
        return name
    pattern = re.compile(rf"^{re.escape(country)}\\s+", re.IGNORECASE)
    return pattern.sub("", name).strip()


def find_best_match(comp: str, leagues: Dict[str, str]) -> Tuple[str, str] | None:
    comp_tokens = set(_tokens(comp))
    if not comp_tokens:
        return None
    best = None
    best_score = 0.0
    for league_name, url in leagues.items():
        league_tokens = set(_tokens(league_name))
        if not league_tokens:
            continue
        common = comp_tokens & league_tokens
        if not common:
            continue
        score = len(common) / max(min(len(comp_tokens), len(league_tokens)), 1)
        if score > best_score:
            best_score = score
            best = (league_name, url)
    if best and best_score >= 0.7:
        return best
    return None


def make_code(url: str, seen: Dict[str, int]) -> str:
    parts = [p for p in url.strip("/").split("/") if p]
    if len(parts) < 2:
        base = "BE_MISC"
    else:
        base = f"BE_{parts[-2].upper()}_{parts[-1].upper()}".replace("-", "_")
    count = seen.get(base, 0)
    seen[base] = count + 1
    return base if count == 0 else f"{base}_{count+1}"


def load_missing(path: Path) -> List[Dict[str, str]]:
    rows = []
    with path.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("Region") != "non_europe":
                continue
            if row.get("Status") != "no_data_source":
                continue
            rows.append(row)
    return rows


def guess_country_slug(comp: str) -> Tuple[str | None, List[str]]:
    if comp in COMPETITION_OVERRIDES:
        return None, COMPETITION_OVERRIDES[comp]
    for prefix in COUNTRY_PREFIXES:
        if comp.lower().startswith(prefix.lower() + " "):
            if prefix == "UAE":
                country = "United Arab Emirates"
            elif prefix == "Saudi":
                country = "Saudi Arabia"
            else:
                country = prefix
            return country, COUNTRY_SLUGS.get(country, [])
    return None, []


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--coverage", default="reports/primary_strategy_platform_coverage.csv")
    ap.add_argument("--out", default="reports/betexplorer_candidates_non_europe.csv")
    ap.add_argument("--unmatched", default="reports/betexplorer_unmatched_non_europe.csv")
    args = ap.parse_args()

    coverage_path = Path(args.coverage)
    if not coverage_path.exists():
        raise SystemExit(f"Missing coverage file: {coverage_path}")

    missing = load_missing(coverage_path)
    if not missing:
        print("No missing non-European competitions found.")
        return 0

    leagues_cache: Dict[str, Dict[str, str]] = {}
    matches: List[Dict[str, str]] = []
    unmatched: List[Dict[str, str]] = []
    code_seen: Dict[str, int] = defaultdict(int)

    for row in missing:
        comp = row.get("Competition", "")
        country, slug_candidates = guess_country_slug(comp)
        if not slug_candidates:
            unmatched.append(row)
            continue
        comp_clean = strip_country_prefix(comp, country)

        match = None
        for slug in slug_candidates:
            if slug not in leagues_cache:
                html = fetch_country_html(slug)
                if not html:
                    leagues_cache[slug] = {}
                else:
                    leagues_cache[slug] = parse_country_leagues(html, slug)
            leagues = leagues_cache.get(slug, {})
            match = find_best_match(comp_clean, leagues)
            if match:
                break

        if not match:
            unmatched.append(row)
            continue

        league_name, url = match
        code = make_code(url, code_seen)
        matches.append(
            {
                "Region": row.get("Region", ""),
                "Group": row.get("Group", ""),
                "Competition": comp,
                "MatchedName": unescape(league_name),
                "URL": url,
                "Code": code,
            }
        )

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["Region", "Group", "Competition", "MatchedName", "URL", "Code"])
        w.writeheader()
        for row in matches:
            w.writerow(row)

    unmatched_path = Path(args.unmatched)
    with unmatched_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["Region", "Group", "Competition"])
        w.writeheader()
        for row in unmatched:
            w.writerow({k: row.get(k, "") for k in ["Region", "Group", "Competition"]})

    print(f"saved: {out_path}")
    print(f"saved: {unmatched_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
