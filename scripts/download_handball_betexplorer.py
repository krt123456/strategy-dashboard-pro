#!/usr/bin/env python3
"""Download handball results from BetExplorer based on a YAML league list."""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Dict, Any, List

try:
    import yaml  # type: ignore
except Exception as exc:  # pragma: no cover
    raise SystemExit("Missing dependency: PyYAML. Install with: pip install pyyaml") from exc

from betexplorer_handball_utils import download_league_csv


def load_yaml(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", default="data/betexplorer_handball_leagues.yaml")
    ap.add_argument("--out-dir", default="data/raw/handball_betexplorer")
    ap.add_argument("--max-seasons", type=int, default=1)
    ap.add_argument("--sleep", type=float, default=0.2)
    args = ap.parse_args()

    cfg = load_yaml(Path(args.list))
    leagues = cfg.get("lists", {}).get("betexplorer_handball", [])
    if not leagues:
        print("No handball leagues configured.")
        return 1

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    ok = 0
    fail = 0
    for entry in leagues:
        code = str(entry.get("code") or "").strip()
        url = str(entry.get("url") or "").strip()
        if not code or not url:
            continue
        out_path = out_dir / f"{code}.csv"
        success = download_league_csv(url, out_path, max_seasons=args.max_seasons, sleep_s=args.sleep)
        if success:
            ok += 1
        else:
            fail += 1

    print(f"downloaded: {ok}, failed: {fail}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
