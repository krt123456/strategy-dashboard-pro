#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Tuple

import pandas as pd


def _predict_side(row) -> Tuple[str, float]:
    ph = row.get("consensus_home_prob", 0.0)
    pa = row.get("consensus_away_prob", 0.0)
    if ph >= pa:
        return "H", float(ph)
    return "A", float(pa)


def apply_strategy(
    df: pd.DataFrame,
    *,
    min_books: int,
    min_prob: float,
    min_prob_margin: float,
    max_dispersion: float,
    max_overround: float,
    min_payout: float,
    max_move_abs: float,
) -> pd.DataFrame:
    if df.empty:
        return df

    preds = []
    for _, row in df.iterrows():
        pred, prob = _predict_side(row)
        prob_margin = abs((row.get("consensus_home_prob", 0.0)) - (row.get("consensus_away_prob", 0.0)))
        qualifies = True

        if row.get("bookmaker_count", 0) < min_books:
            qualifies = False
        if prob < min_prob:
            qualifies = False
        if prob_margin < min_prob_margin:
            qualifies = False
        if row.get("prob_dispersion", 0.0) > max_dispersion:
            qualifies = False
        if row.get("overround_avg", 0.0) > max_overround:
            qualifies = False
        payout = row.get("avg_payout")
        if payout is not None and payout < min_payout:
            qualifies = False
        move_abs = row.get("avg_move_abs")
        if move_abs is not None and move_abs > max_move_abs:
            qualifies = False

        actual = row.get("actual")
        correct = None
        if actual in ("H", "A"):
            correct = int(pred == actual)

        preds.append(
            {
                "match_id": row.get("match_id"),
                "start_time": row.get("start_time"),
                "league": row.get("league"),
                "home": row.get("home"),
                "away": row.get("away"),
                "pred": pred,
                "prob": round(prob, 4),
                "prob_margin": round(prob_margin, 4),
                "bookmaker_count": row.get("bookmaker_count"),
                "prob_dispersion": row.get("prob_dispersion"),
                "overround_avg": row.get("overround_avg"),
                "avg_payout": row.get("avg_payout"),
                "avg_move_abs": row.get("avg_move_abs"),
                "actual": actual,
                "correct": correct,
                "qualifies": int(qualifies),
            }
        )

    return pd.DataFrame(preds)


def summarize(df: pd.DataFrame) -> pd.DataFrame:
    picks = df[df["qualifies"] == 1].copy()
    if picks.empty:
        return pd.DataFrame()
    finished = picks[picks["correct"].notna()]
    if finished.empty:
        return pd.DataFrame()
    summary = (
        finished.groupby("league")
        .agg(Picks=("qualifies", "size"), Correct=("correct", "sum"))
        .reset_index()
    )
    summary["Wrong"] = summary["Picks"] - summary["Correct"]
    summary["Accuracy"] = (summary["Correct"] / summary["Picks"]).round(4)
    return summary.sort_values("Accuracy", ascending=False)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default="data/processed/sportdevs_tabletennis_dataset.csv")
    ap.add_argument("--out-picks", default="reports/sportdevs_tabletennis_picks.csv")
    ap.add_argument("--out-summary", default="reports/sportdevs_tabletennis_summary.csv")
    ap.add_argument("--min-books", type=int, default=6)
    ap.add_argument("--min-prob", type=float, default=0.65)
    ap.add_argument("--min-prob-margin", type=float, default=0.08)
    ap.add_argument("--max-dispersion", type=float, default=0.08)
    ap.add_argument("--max-overround", type=float, default=1.1)
    ap.add_argument("--min-payout", type=float, default=88.0)
    ap.add_argument("--max-move-abs", type=float, default=0.6)
    args = ap.parse_args()

    df = pd.read_csv(args.dataset)
    picks = apply_strategy(
        df,
        min_books=args.min_books,
        min_prob=args.min_prob,
        min_prob_margin=args.min_prob_margin,
        max_dispersion=args.max_dispersion,
        max_overround=args.max_overround,
        min_payout=args.min_payout,
        max_move_abs=args.max_move_abs,
    )

    out_picks = Path(args.out_picks)
    out_picks.parent.mkdir(parents=True, exist_ok=True)
    picks.to_csv(out_picks, index=False)

    summary = summarize(picks)
    if not summary.empty:
        out_summary = Path(args.out_summary)
        out_summary.parent.mkdir(parents=True, exist_ok=True)
        summary.to_csv(out_summary, index=False)
        print(summary.head(20).to_string(index=False))
    else:
        print("No qualifying picks with current thresholds.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
