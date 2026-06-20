#!/usr/bin/env python3
from __future__ import annotations

import argparse
from itertools import product

import pandas as pd

from oddspapi_tt_strategy import apply_strategy


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default="data/processed/oddspapi_tabletennis_dataset.csv")
    ap.add_argument("--min-picks", type=int, default=50)
    ap.add_argument("--start-date", default=None)
    ap.add_argument("--end-date", default=None)
    args = ap.parse_args()

    df = pd.read_csv(args.dataset)
    if args.start_date:
        df = df[df["start_time"] >= args.start_date]
    if args.end_date:
        df = df[df["start_time"] <= args.end_date]

    grid_min_prob = [0.75, 0.78, 0.8, 0.82, 0.85, 0.88, 0.9]
    grid_margin = [0.12, 0.15, 0.18, 0.2, 0.22, 0.25]
    grid_max_odds = [1.25, 1.3, 1.35, 1.4]
    grid_overround = [1.05, 1.06, 1.08, 1.1]

    best = None
    results = []
    for min_prob, min_margin, max_odds, max_overround in product(
        grid_min_prob, grid_margin, grid_max_odds, grid_overround
    ):
        picks = apply_strategy(
            df,
            min_prob=min_prob,
            min_margin=min_margin,
            max_odds=max_odds,
            max_overround=max_overround,
        )
        picks = picks[picks["qualifies"] == 1]
        if picks.empty:
            continue
        finished = picks[picks["correct"].notna()]
        if finished.empty:
            continue
        total = len(finished)
        if total < args.min_picks:
            continue
        correct = finished["correct"].sum()
        acc = correct / total
        results.append((acc, total, min_prob, min_margin, max_odds, max_overround))
        if best is None or acc > best[0]:
            best = (acc, total, min_prob, min_margin, max_odds, max_overround)

    if not results:
        print("No viable configurations met the minimum picks.")
        return 1

    results.sort(reverse=True)
    print("Top configs (acc, picks, min_prob, min_margin, max_odds, max_overround):")
    for row in results[:15]:
        print(row)

    acc, total, min_prob, min_margin, max_odds, max_overround = best
    print(
        f"\nBest: acc={acc:.4f}, picks={total}, min_prob={min_prob}, min_margin={min_margin}, max_odds={max_odds}, max_overround={max_overround}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
