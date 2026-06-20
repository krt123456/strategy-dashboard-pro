#!/usr/bin/env python3
"""Download hockey results + 1X2 odds from BetExplorer."""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import List, Dict

import yaml

from betexplorer_utils import download_league_csv


def load_yaml(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", default="data/betexplorer_hockey_leagues.yaml")
    ap.add_argument("--out-dir", default="data/raw/betexplorer_hockey")
    ap.add_argument("--max-seasons", type=int, default=1)
    ap.add_argument("--sleep", type=float, default=0.2)
    args = ap.parse_args()

    cfg = load_yaml(Path(args.list))
    leagues: List[Dict[str, str]] = cfg.get("lists", {}).get("betexplorer_hockey", [])
    if not leagues:
        print("No hockey leagues configured in BetExplorer list.")
        return 1

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    ok_any = False
    for entry in leagues:
        code = entry.get("code")
        url = entry.get("url")
        if not code or not url:
            continue
        out_path = out_dir / f"{code}.csv"
        ok = download_league_csv(url, out_path, max_seasons=args.max_seasons, sleep_s=args.sleep)
        ok_any = ok_any or ok
        status = "ok" if ok else "failed"
        print(f"{code}: {status} -> {out_path}")

    return 0 if ok_any else 1


if __name__ == "__main__":
    raise SystemExit(main())
