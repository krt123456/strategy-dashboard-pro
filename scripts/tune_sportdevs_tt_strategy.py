#!/usr/bin/env python3
from __future__ import annotations

import argparse
from itertools import product
from pathlib import Path

import pandas as pd

from sportdevs_tt_strategy import apply_strategy


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--min-picks", type=int, default=100)
    ap.add_argument("--start-date", default=None)
    ap.add_argument("--end-date", default=None)
    args = ap.parse_args()

    df = pd.read_csv(args.dataset)
    if args.start_date:
        df = df[df["start_time"] >= args.start_date]
    if args.end_date:
        df = df[df["start_time"] <= args.end_date]

    grid_books = [4, 6, 8, 10]
    grid_prob = [0.62, 0.65, 0.68, 0.7, 0.72]
    grid_margin = [0.06, 0.08, 0.1, 0.12]
    grid_disp = [0.05, 0.07, 0.09, 0.12]
    grid_overround = [1.05, 1.08, 1.1, 1.12]
    grid_payout = [86.0, 88.0, 90.0]
    grid_move = [0.4, 0.6, 0.8]

    best = None
    results = []
    for min_books, min_prob, min_margin, max_disp, max_over, min_pay, max_move in product(
        grid_books,
        grid_prob,
        grid_margin,
        grid_disp,
        grid_overround,
        grid_payout,
        grid_move,
    ):
        picks = apply_strategy(
            df,
            min_books=min_books,
            min_prob=min_prob,
            min_prob_margin=min_margin,
            max_dispersion=max_disp,
            max_overround=max_over,
            min_payout=min_pay,
            max_move_abs=max_move,
        )
        picks = picks[picks["qualifies"] == 1]
        finished = picks[picks["correct"].notna()]
        if len(finished) < args.min_picks:
            continue
        acc = finished["correct"].mean()
        results.append((acc, len(finished), min_books, min_prob, min_margin, max_disp, max_over, min_pay, max_move))
        if best is None or acc > best[0]:
            best = (acc, len(finished), min_books, min_prob, min_margin, max_disp, max_over, min_pay, max_move)

    if not results:
        print("No viable configs met minimum picks.")
        return 1

    results.sort(reverse=True)
    print("Top configs (acc, picks, min_books, min_prob, min_margin, max_disp, max_over, min_pay, max_move):")
    for row in results[:12]:
        print(row)
    print("\nBest:", best)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
