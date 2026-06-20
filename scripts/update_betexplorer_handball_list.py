#!/usr/bin/env python3
"""Build a BetExplorer handball league list (YAML) by scraping country pages."""
from __future__ import annotations

import argparse
import re
from collections import defaultdict
from html import unescape
from pathlib import Path
from typing import Dict, List

try:
    import requests  # type: ignore
except Exception as exc:  # pragma: no cover
    raise SystemExit("Missing dependency: requests. Install with: pip install requests") from exc

try:
    import yaml  # type: ignore
except Exception as exc:  # pragma: no cover
    raise SystemExit("Missing dependency: PyYAML. Install with: pip install pyyaml") from exc

from betexplorer_handball_utils import normalize_name, parse_country_leagues

BASE_URL = "https://www.betexplorer.com"
UA = "Mozilla/5.0 (compatible; BetExplorerHandballMapper/1.0)"

DEFAULT_SLUGS = [
    "europe",
    "germany",
    "france",
    "spain",
    "denmark",
    "sweden",
    "norway",
    "poland",
    "hungary",
    "romania",
    "portugal",
    "slovenia",
    "croatia",
    "serbia",
    "czech-republic",
    "slovakia",
    "iceland",
    "austria",
    "switzerland",
]


def fetch_country_html(slug: str) -> str | None:
    url = f"{BASE_URL}/handball/{slug}/"
    resp = requests.get(url, headers={"User-Agent": UA}, timeout=30)
    if resp.status_code != 200:
        return None
    return resp.text


def make_code(url: str, seen: Dict[str, int]) -> str:
    parts = [p for p in url.strip("/").split("/") if p]
    if len(parts) < 2:
        base = "HB_MISC"
    else:
        base = f"HB_{parts[-2].upper()}_{parts[-1].upper()}".replace("-", "_")
    count = seen.get(base, 0)
    seen[base] = count + 1
    return base if count == 0 else f"{base}_{count+1}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--slugs", default=",".join(DEFAULT_SLUGS))
    ap.add_argument("--out", default="data/betexplorer_handball_leagues.yaml")
    ap.add_argument("--max-per-country", type=int, default=0, help="Limit leagues per country (0=all)")
    args = ap.parse_args()

    slugs = [s.strip() for s in args.slugs.split(",") if s.strip()]
    if not slugs:
        raise SystemExit("No country slugs provided.")

    entries: List[Dict[str, str]] = []
    seen_codes: Dict[str, int] = defaultdict(int)
    for slug in slugs:
        html = fetch_country_html(slug)
        if not html:
            continue
        leagues = parse_country_leagues(html, slug)
        items = list(leagues.items())
        if args.max_per_country and args.max_per_country > 0:
            items = items[: args.max_per_country]
        for name, url in items:
            code = make_code(url, seen_codes)
            entries.append(
                {
                    "code": code,
                    "name": unescape(name),
                    "url": url,
                    "region": "europe" if slug != "europe" else "international",
                    "group": slug.replace("-", "_"),
                }
            )

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"lists": {"betexplorer_handball": entries}}
    out_path.write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True), encoding="utf-8")
    print(f"saved: {out_path}")
    print(f"count: {len(entries)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
