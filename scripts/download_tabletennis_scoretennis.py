#!/usr/bin/env python3
"""Download table tennis results from score-tennis.com for selected leagues."""
from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

try:
    import yaml  # type: ignore
except Exception as exc:  # pragma: no cover
    raise SystemExit("Missing dependency: PyYAML. Install with: pip install pyyaml") from exc

from scoretennis_tabletennis_utils import download_range, write_csv, normalize_league


def load_yaml(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", default="data/scoretennis_tabletennis_selected.yaml")
    ap.add_argument("--out", default="data/raw/tabletennis_scoretennis/tabletennis_scoretennis.csv")
    ap.add_argument("--start-date", required=True)
    ap.add_argument("--end-date", required=True)
    ap.add_argument("--sleep", type=float, default=0.2)
    args = ap.parse_args()

    cfg = load_yaml(Path(args.list))
    leagues = cfg.get("lists", {}).get("scoretennis_tabletennis", [])
    target_leagues = {normalize_league(str(item.get("name", "")).strip()) for item in leagues if item.get("name")}
    if not target_leagues:
        print("No ScoreTennis leagues configured.")
        return 1

    start = date.fromisoformat(args.start_date)
    end = date.fromisoformat(args.end_date)
    rows = download_range(start, end, target_leagues, sleep_s=args.sleep)

    out_path = Path(args.out)
    write_csv(rows, out_path)
    print(f"Saved {len(rows)} rows to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
