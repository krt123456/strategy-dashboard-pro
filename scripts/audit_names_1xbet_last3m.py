#!/usr/bin/env python3
import argparse
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
import re
import unicodedata

import pandas as pd
import sys

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from app.engine import (
    compute_range,
    compute_range_basketball,
    compute_range_tennis,
    compute_range_hockey,
)


def _strip_accents(text: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch))


def _normalize_simple(name: str) -> str:
    if not name:
        return ""
    text = _strip_accents(name).lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


@dataclass
class SportResult:
    sport: str
    picks: pd.DataFrame


def _collect_names(df: pd.DataFrame, sport: str) -> pd.DataFrame:
    rows = []
    for row in df.to_dict("records"):
        for role in ("Home", "Away", "Pred"):
            raw = row.get(role)
            display = row.get(f"{role}Display") or raw
            if raw is None or str(raw).strip() == "":
                continue
            rows.append(
                {
                    "sport": sport,
                    "role": role.lower(),
                    "name_raw": str(raw).strip(),
                    "name_display": str(display).strip() if display is not None else "",
                }
            )
    if not rows:
        return pd.DataFrame(columns=["sport", "role", "name_raw", "name_display", "count", "variants", "status"])

    frame = pd.DataFrame(rows)
    grouped = (
        frame.groupby(["sport", "role", "name_raw"])\
        .agg(
            count=("name_raw", "count"),
            variants=("name_display", lambda s: len(set(s))),
            displays=("name_display", lambda s: " | ".join(sorted(set(s)))),
        )
        .reset_index()
    )
    grouped["status"] = grouped.apply(
        lambda r: "match" if r["variants"] == 1 and r["displays"] == r["name_raw"] else "diff",
        axis=1,
    )
    grouped = grouped.rename(columns={"displays": "name_display"})
    return grouped


def _collect_duplicates(df: pd.DataFrame, sport: str) -> pd.DataFrame:
    names = set()
    for col in ("Home", "Away", "Pred"):
        if col not in df.columns:
            continue
        for val in df[col].dropna().astype(str):
            val = val.strip()
            if val:
                names.add(val)
    buckets = defaultdict(list)
    for name in sorted(names):
        buckets[_normalize_simple(name)].append(name)
    rows = []
    for norm, items in buckets.items():
        if len(items) > 1:
            rows.append({"sport": sport, "normalized": norm, "variants": " | ".join(items)})
    return pd.DataFrame(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare last-3-months names with 1XBet display versions.")
    parser.add_argument("--start", help="Start date YYYY-MM-DD")
    parser.add_argument("--end", help="End date YYYY-MM-DD")
    parser.add_argument("--season", default="2526", help="Football season code")
    args = parser.parse_args()

    end = date.fromisoformat(args.end) if args.end else date.today()
    start = date.fromisoformat(args.start) if args.start else end - timedelta(days=90)
    if start > end:
        start, end = end, start

    results = []
    results.append(SportResult("football", compute_range(start, end, auto_update_future=False, season_code=args.season).picks))
    results.append(SportResult("basketball", compute_range_basketball(start, end).picks))
    results.append(SportResult("tennis", compute_range_tennis(start, end).picks))
    results.append(SportResult("hockey", compute_range_hockey(start, end).picks))

    all_rows = []
    dup_rows = []
    for res in results:
        if res.picks is None or res.picks.empty:
            continue
        all_rows.append(_collect_names(res.picks, res.sport))
        dup_rows.append(_collect_duplicates(res.picks, res.sport))

    out_dir = (Path(__file__).resolve().parents[1] / "reports")
    out_dir.mkdir(parents=True, exist_ok=True)
    name_report = out_dir / f"name_compare_1xbet_{start}_{end}.csv"
    dup_report = out_dir / f"name_duplicates_{start}_{end}.csv"
    if all_rows:
        pd.concat(all_rows, ignore_index=True).to_csv(name_report, index=False)
        print(f"saved {name_report}")
    if dup_rows:
        pd.concat(dup_rows, ignore_index=True).to_csv(dup_report, index=False)
        print(f"saved {dup_report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
