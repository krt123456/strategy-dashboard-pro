#!/usr/bin/env python3
"""Grid search for table tennis strategy parameters."""
from __future__ import annotations

import argparse
from itertools import product
from pathlib import Path

from tabletennis_strategy import build_predictions, _load_matches


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--league-list", default="data/scoretennis_tabletennis_selected.yaml")
    ap.add_argument("--start-date", default=None)
    ap.add_argument("--end-date", default=None)
    ap.add_argument("--min-picks", type=int, default=200)
    args = ap.parse_args()

    df = _load_matches(Path(args.data_dir), {})
    if df.empty:
        print("No data found.")
        return 1
    if args.start_date:
        df = df[df["Date"] >= args.start_date]
    if args.end_date:
        df = df[df["Date"] <= args.end_date]

    grid_min_prob = [0.58, 0.6, 0.62, 0.64, 0.66, 0.68, 0.7]
    grid_margin = [0.05, 0.08, 0.1, 0.12, 0.15, 0.18, 0.2]
    grid_min_games = [3, 4, 5, 6, 7, 8]
    grid_rest = [1, 2, 3]

    best = None
    results = []
    for min_prob, min_margin, min_games, max_rest in product(
        grid_min_prob, grid_margin, grid_min_games, grid_rest
    ):
        picks = build_predictions(
            df,
            min_games=min_games,
            min_prob=min_prob,
            min_prob_margin=min_margin,
            min_edge=0.0,
            max_rest_disadv=max_rest,
            weight_market=0.0,
        )
        picks = picks[picks["Qualifies"] == 1]
        if picks.empty:
            continue
        finished = picks[picks["Correct"].notna()]
        if finished.empty:
            continue
        total = len(finished)
        if total < args.min_picks:
            continue
        correct = finished["Correct"].sum()
        acc = correct / total
        results.append((acc, total, min_prob, min_margin, min_games, max_rest))
        if best is None or acc > best[0]:
            best = (acc, total, min_prob, min_margin, min_games, max_rest)

    if not results:
        print("No viable configurations met the minimum picks.")
        return 1

    results.sort(reverse=True)
    top = results[:15]
    print("Top configs (acc, picks, min_prob, min_margin, min_games, max_rest):")
    for row in top:
        print(row)

    acc, total, min_prob, min_margin, min_games, max_rest = best
    print(
        f"\nBest: acc={acc:.4f}, picks={total}, min_prob={min_prob}, min_margin={min_margin}, min_games={min_games}, max_rest={max_rest}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
