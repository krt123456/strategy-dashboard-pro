#!/usr/bin/env python3
"""Build a YAML list of BetExplorer table tennis leagues."""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Dict, Any, List

try:
    import yaml  # type: ignore
except Exception as exc:  # pragma: no cover
    raise SystemExit("Missing dependency: PyYAML. Install with: pip install pyyaml") from exc

from betexplorer_tabletennis_utils import _get, parse_country_slugs, parse_country_leagues, normalize_name

BASE_URL = "https://www.betexplorer.com"


def build_code(country_slug: str, league_name: str) -> str:
    country = country_slug.replace("-", "_").upper()
    key = normalize_name(league_name).upper()[:40]
    if not key:
        key = "LEAGUE"
    return f"TT_{country}_{key}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/betexplorer_tabletennis_leagues.yaml")
    ap.add_argument("--countries", nargs="*", default=None)
    args = ap.parse_args()

    main_html = _get(f"{BASE_URL}/table-tennis/")
    if not main_html:
        print("Failed to fetch table-tennis index")
        return 1
    slugs = parse_country_slugs(main_html)
    if args.countries:
        slugs = [s for s in slugs if s in set(args.countries)]

    all_entries: List[Dict[str, Any]] = []
    for slug in slugs:
        html = _get(f"{BASE_URL}/table-tennis/{slug}/")
        if not html:
            continue
        leagues = parse_country_leagues(html, slug)
        for name, url in sorted(leagues.items()):
            code = build_code(slug, name)
            all_entries.append(
                {
                    "code": code,
                    "name": name,
                    "url": url,
                    "region": "international" if slug in {"europe", "world"} else "local",
                    "group": slug,
                }
            )

    payload = {"lists": {"betexplorer_tabletennis": all_entries}}
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(payload, f, sort_keys=False, allow_unicode=True)

    print(f"Saved {len(all_entries)} leagues to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
