#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd


def apply_strategy(
    df: pd.DataFrame,
    *,
    min_prob: float,
    min_margin: float,
    max_odds: float,
    max_overround: float,
) -> pd.DataFrame:
    rows = []
    for _, row in df.iterrows():
        home_prob = row.get("home_prob", 0.0)
        away_prob = row.get("away_prob", 0.0)
        pred = "H" if home_prob >= away_prob else "A"
        prob = home_prob if pred == "H" else away_prob
        odds = row.get("home_odds") if pred == "H" else row.get("away_odds")
        prob_margin = abs(home_prob - away_prob)
        qualifies = True
        if prob < min_prob:
            qualifies = False
        if prob_margin < min_margin:
            qualifies = False
        if odds is None or odds > max_odds:
            qualifies = False
        if row.get("overround", 0.0) > max_overround:
            qualifies = False
        actual = row.get("actual")
        correct = None
        if actual in ("H", "A"):
            correct = int(pred == actual)
        rows.append(
            {
                "event_id": row.get("event_id"),
                "date": row.get("date"),
                "league": row.get("league"),
                "home": row.get("home"),
                "away": row.get("away"),
                "pred": pred,
                "prob": round(prob, 4),
                "prob_margin": round(prob_margin, 4),
                "odds": odds,
                "overround": row.get("overround"),
                "actual": actual,
                "correct": correct,
                "qualifies": int(qualifies),
            }
        )
    return pd.DataFrame(rows)


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
    ap.add_argument("--dataset", default="data/processed/oddsapi_tabletennis_dataset.csv")
    ap.add_argument("--out-picks", default="reports/oddsapi_tabletennis_picks.csv")
    ap.add_argument("--out-summary", default="reports/oddsapi_tabletennis_summary.csv")
    ap.add_argument("--min-prob", type=float, default=0.8)
    ap.add_argument("--min-margin", type=float, default=0.2)
    ap.add_argument("--max-odds", type=float, default=1.35)
    ap.add_argument("--max-overround", type=float, default=1.08)
    args = ap.parse_args()

    df = pd.read_csv(args.dataset)
    picks = apply_strategy(
        df,
        min_prob=args.min_prob,
        min_margin=args.min_margin,
        max_odds=args.max_odds,
        max_overround=args.max_overround,
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
