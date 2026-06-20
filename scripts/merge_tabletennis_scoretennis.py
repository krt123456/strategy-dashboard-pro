#!/usr/bin/env python3
"""Merge ScoreTennis table tennis batch CSVs into a single deduplicated file."""
from __future__ import annotations

import argparse
import csv
from pathlib import Path
from typing import Iterable, Tuple


def iter_rows(paths: Iterable[Path]):
    for path in paths:
        with path.open("r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                yield row


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in-dir", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    in_dir = Path(args.in_dir)
    out_path = Path(args.out)

    files = sorted(in_dir.glob("*.csv"))
    if not files:
        print("No CSV files found to merge.")
        return 1

    seen: set[Tuple[str, str, str, str, str, str]] = set()
    rows = []
    for row in iter_rows(files):
        key = (
            row.get("Date", ""),
            row.get("League", ""),
            row.get("HomeTeam", ""),
            row.get("AwayTeam", ""),
            row.get("FTHG", ""),
            row.get("FTAG", ""),
        )
        if key in seen:
            continue
        seen.add(key)
        rows.append(row)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG", "AvgH", "AvgA", "Season", "League"],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "Date": row.get("Date", ""),
                    "HomeTeam": row.get("HomeTeam", ""),
                    "AwayTeam": row.get("AwayTeam", ""),
                    "FTHG": row.get("FTHG", ""),
                    "FTAG": row.get("FTAG", ""),
                    "AvgH": row.get("AvgH", "0"),
                    "AvgA": row.get("AvgA", "0"),
                    "Season": row.get("Season", ""),
                    "League": row.get("League", ""),
                }
            )

    print(f"Merged {len(rows)} rows into {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
