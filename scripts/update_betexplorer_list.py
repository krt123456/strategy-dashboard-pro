#!/usr/bin/env python3
"""Map missing European competitions to BetExplorer URLs and write a list."""
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

GROUP_TO_SLUG = {
    "england": "england",
    "scotland": "scotland",
    "france": "france",
    "germany": "germany",
    "italy": "italy",
    "spain": "spain",
    "portugal": "portugal",
    "netherlands": "netherlands",
    "russia": "russia",
    "belgium": "belgium",
    "greece": "greece",
    "switzerland": "switzerland",
    "austria": "austria",
    "turkey": "turkey",
    "denmark": "denmark",
    "romania": "romania",
    "slovakia": "slovakia",
    "malta": "malta",
    "cyprus": "cyprus",
    "croatia": "croatia",
    "gibraltar": "gibraltar",
    "bulgaria": "bulgaria",
    "albania": "albania",
    "andorra": "andorra",
    "ireland": "ireland",
    "northern_ireland": "northern-ireland",
    "israel": "israel",
    "club_international": "europe",
}


def fetch_country_html(slug: str) -> str | None:
    url = f"{BASE_URL}/football/{slug}/"
    resp = requests.get(url, headers={"User-Agent": UA}, timeout=30)
    if resp.status_code != 200:
        return None
    return resp.text


STOPWORDS = {"uefa"}
ALIASES = {
    "UEFA Europa Conference League": "Conference League",
    "England League Cup Women": "Women's League Cup",
    "England U21 Development League": "Premier League 2",
    "England U18 League": "Premier League U18",
    "England Womens Super League": "WSL",
    "Scotland FA Cup": "Scottish Cup",
    "Scotland Womens Premier League": "SWPL 1 Women",
    "Turkey TFF 1. Lig": "1. Lig",
    "Portugal Segunda Liga": "Liga Portugal 2",
    "Portugal U23 League": "Liga Revelacao U23",
    "Portugal Womens Cup": "Taça de Portugal Women",
    "Netherlands U19 League": "Divisie 1 U19",
    "Russia Cup": "Russian Cup",
    "Greece Cup": "Greek Cup",
    "Greece Women League": "Division A Women",
    "Denmark Cup": "Landspokal Cup",
    "Cyprus First Division": "Cyprus League",
    "Croatia First League": "HNL",
    "Gibraltar Division 1": "National League",
    "Bulgaria First League": "efbet League",
    "Bulgaria Cup": "Bulgarian Cup",
    "Albania Super League": "Abissnet Superiore",
    "Israel Premier League": "Ligat ha'Al",
    "Italy Primavera": "Primavera 1",
    "Italy U19 League": "Primavera 1",
}


def strip_country(name: str, group: str) -> str:
    country_word = group.replace("_", " ").strip()
    pattern = re.compile(rf"\b{re.escape(country_word)}\b", re.IGNORECASE)
    return pattern.sub("", name).strip()


def _tokens(text: str, group: str) -> List[str]:
    tokens = re.findall(r"[a-z0-9]+", text.lower())
    if group == "club_international":
        tokens = [t for t in tokens if t not in STOPWORDS]
    return tokens


def find_best_match(comp: str, leagues: Dict[str, str], group: str) -> Tuple[str, str] | None:
    comp_clean = strip_country(comp, group)
    comp_tokens = set(_tokens(comp_clean, group))
    if not comp_tokens:
        return None

    alias = ALIASES.get(comp)
    if alias:
        alias_key = normalize_name(alias)
        for league_name, url in leagues.items():
            if normalize_name(league_name) == alias_key:
                return league_name, url

    best = None
    best_score = 0.0
    for league_name, url in leagues.items():
        league_tokens = set(_tokens(league_name, group))
        if not league_tokens:
            continue
        if not league_tokens.issubset(comp_tokens):
            continue
        score = len(league_tokens) / max(len(comp_tokens), 1)
        if score > best_score:
            best_score = score
            best = (league_name, url)
    if best and best_score >= 0.8:
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
            if row.get("Region") != "europe":
                continue
            if row.get("Status") != "no_data_source":
                continue
            rows.append(row)
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--coverage", default="reports/primary_strategy_platform_coverage.csv")
    ap.add_argument("--out", default="reports/betexplorer_candidates.csv")
    ap.add_argument("--unmatched", default="reports/betexplorer_unmatched.csv")
    ap.add_argument("--write", default="data/betexplorer_leagues.yaml")
    args = ap.parse_args()

    coverage_path = Path(args.coverage)
    if not coverage_path.exists():
        raise SystemExit(f"Missing coverage file: {coverage_path}")

    missing = load_missing(coverage_path)
    if not missing:
        print("No missing European competitions found.")
        return 0

    leagues_cache: Dict[str, Dict[str, str]] = {}
    matches: List[Dict[str, str]] = []
    unmatched: List[Dict[str, str]] = []
    code_seen: Dict[str, int] = defaultdict(int)

    for row in missing:
        group = row.get("Group", "")
        comp = row.get("Competition", "")
        slug = GROUP_TO_SLUG.get(group)
        if not slug or not comp:
            unmatched.append(row)
            continue
        if slug not in leagues_cache:
            html = fetch_country_html(slug)
            if not html:
                leagues_cache[slug] = {}
            else:
                leagues_cache[slug] = parse_country_leagues(html, slug)
        leagues = leagues_cache.get(slug, {})
        match = find_best_match(comp, leagues, group)
        if not match:
            unmatched.append(row)
            continue
        league_name, url = match
        code = make_code(url, code_seen)
        matches.append(
            {
                "Region": row.get("Region", ""),
                "Group": group,
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

    write_path = Path(args.write)
    try:
        import yaml  # type: ignore
    except Exception:
        yaml = None
    if yaml is not None:
        payload = {"lists": {"betexplorer": []}}
        for row in matches:
            payload["lists"]["betexplorer"].append(
                {
                    "code": row["Code"],
                    "name": row["Competition"],
                    "url": row["URL"],
                    "region": row["Region"],
                    "group": row["Group"],
                }
            )
        write_path.parent.mkdir(parents=True, exist_ok=True)
        write_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    else:
        print("PyYAML not installed; skipping YAML write.")

    print(f"saved: {out_path}")
    print(f"saved: {unmatched_path}")
    if yaml is not None:
        print(f"saved: {write_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
