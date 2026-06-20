#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd


def apply_strategy(
    df: pd.DataFrame,
    min_prob: float,
    min_margin: float,
    comp_acc_min: float | None = None,
    comp_min_picks: int = 5,
    start_date: str | None = None,
    end_date: str | None = None,
    adaptive: bool = False,
    relax_prob: float | None = None,
    relax_margin: float | None = None,
    relax_acc_min: float = 0.98,
    relax_min_picks: int = 10,
    newcomp_min_prob: float | None = None,
    newcomp_min_margin: float | None = None,
    strict_prob_boost: float = 0.04,
    strict_margin_boost: float = 0.03,
    allow_strict_override: bool = True,
    women_min_prob: float | None = None,
    women_min_margin: float | None = None,
) -> pd.DataFrame:
    if "start_time" in df.columns:
        df = df.copy()
        df["start_time"] = pd.to_datetime(df["start_time"], errors="coerce", utc=True)
        if start_date:
            df = df[df["start_time"] >= pd.to_datetime(start_date, utc=True)]
        if end_date:
            df = df[df["start_time"] <= pd.to_datetime(end_date, utc=True)]
        df = df.sort_values("start_time")

    rows = []
    # rolling competition stats to avoid leakage
    comp_stats: dict[str, dict[str, float]] = {}
    for _, row in df.iterrows():
        home_prob = float(row.get("home_prob", 0.0) or 0.0)
        away_prob = float(row.get("away_prob", 0.0) or 0.0)
        pred = "H" if home_prob >= away_prob else "A"
        prob = home_prob if pred == "H" else away_prob
        margin = abs(home_prob - away_prob)
        comp = str(row.get("competition_name") or "")
        comp_lower = comp.lower()
        # choose thresholds (adaptive for high-performing competitions)
        eff_min_prob = min_prob
        eff_min_margin = min_margin
        stats = comp_stats.get(comp)
        if adaptive and stats and stats.get("picks", 0) >= relax_min_picks:
            acc = stats.get("correct", 0) / max(1, stats.get("picks", 0))
            if acc >= relax_acc_min:
                if relax_prob is not None:
                    eff_min_prob = min(eff_min_prob, relax_prob)
                if relax_margin is not None:
                    eff_min_margin = min(eff_min_margin, relax_margin)

        # extra strictness for women's competitions if configured
        if women_min_prob is not None and ("women" in comp_lower or ", ws" in comp_lower):
            eff_min_prob = max(eff_min_prob, women_min_prob)
        if women_min_margin is not None and ("women" in comp_lower or ", ws" in comp_lower):
            eff_min_margin = max(eff_min_margin, women_min_margin)

        qualifies = int(prob >= eff_min_prob and margin >= eff_min_margin)
        actual = row.get("actual")
        correct = None
        if actual in ("H", "A"):
            correct = int(pred == actual)
        rows.append(
            {
                "event_id": row.get("event_id"),
                "start_time": row.get("start_time"),
                "competition": row.get("competition_name"),
                "home": row.get("home_name"),
                "away": row.get("away_name"),
                "pred": pred,
                "prob": round(prob, 4),
                "margin": round(margin, 4),
                "actual": actual,
                "correct": correct,
                "qualifies": qualifies,
                "eff_min_prob": round(eff_min_prob, 4),
                "eff_min_margin": round(eff_min_margin, 4),
            }
        )
        # update rolling stats using finished matches only
        if correct is not None:
            stats = comp_stats.setdefault(comp, {"picks": 0.0, "correct": 0.0})
            stats["picks"] += 1.0
            stats["correct"] += float(correct)

    out = pd.DataFrame(rows)
    if comp_acc_min is None:
        return out

    # apply competition-level filter using rolling stats (no future leakage)
    comp_allowed = []
    comp_stats = {}
    for _, row in out.iterrows():
        comp = str(row.get("competition") or "")
        stats = comp_stats.get(comp)
        allowed = True
        acc = None
        if stats and stats.get("picks", 0) > 0:
            acc = stats.get("correct", 0) / max(1, stats.get("picks", 0))

        if stats and stats.get("picks", 0) >= comp_min_picks:
            allowed = acc is not None and acc >= comp_acc_min
        else:
            # for new/low-sample competitions, require stricter thresholds
            if newcomp_min_prob is not None and row.get("prob", 0) < newcomp_min_prob:
                allowed = False
            if newcomp_min_margin is not None and row.get("margin", 0) < newcomp_min_margin:
                allowed = False

        # strict override: allow only if very strong signal
        if not allowed and allow_strict_override:
            if (row.get("prob", 0) >= min_prob + strict_prob_boost) and (
                row.get("margin", 0) >= min_margin + strict_margin_boost
            ):
                allowed = True

        comp_allowed.append(1 if allowed else 0)

        # update rolling stats after processing this row
        if row.get("correct") in (0, 1):
            stats = comp_stats.setdefault(comp, {"picks": 0.0, "correct": 0.0})
            stats["picks"] += 1.0
            stats["correct"] += float(row.get("correct"))

    out["comp_allowed"] = comp_allowed
    out.loc[out["comp_allowed"] == 0, "qualifies"] = 0
    return out


def summarize(df: pd.DataFrame) -> pd.DataFrame:
    picks = df[df["qualifies"] == 1].copy()
    if picks.empty:
        return pd.DataFrame()
    finished = picks[picks["correct"].notna()]
    if finished.empty:
        return pd.DataFrame()
    summary = (
        finished.groupby("competition")
        .agg(Picks=("qualifies", "size"), Correct=("correct", "sum"))
        .reset_index()
    )
    summary["Wrong"] = summary["Picks"] - summary["Correct"]
    summary["Accuracy"] = (summary["Correct"] / summary["Picks"]).round(4)
    return summary.sort_values("Accuracy", ascending=False)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default="data/processed/sportradar_tabletennis_prob_dataset.csv")
    ap.add_argument("--out-picks", default="reports/sportradar_tabletennis_prob_picks.csv")
    ap.add_argument("--out-summary", default="reports/sportradar_tabletennis_prob_summary.csv")
    ap.add_argument("--min-prob", type=float, default=0.75)
    ap.add_argument("--min-margin", type=float, default=0.15)
    ap.add_argument("--comp-acc-min", type=float, default=None)
    ap.add_argument("--comp-min-picks", type=int, default=5)
    ap.add_argument("--start-date", default=None)
    ap.add_argument("--end-date", default=None)
    ap.add_argument("--adaptive", action="store_true")
    ap.add_argument("--relax-prob", type=float, default=None)
    ap.add_argument("--relax-margin", type=float, default=None)
    ap.add_argument("--relax-acc-min", type=float, default=0.98)
    ap.add_argument("--relax-min-picks", type=int, default=10)
    ap.add_argument("--newcomp-min-prob", type=float, default=None)
    ap.add_argument("--newcomp-min-margin", type=float, default=None)
    ap.add_argument("--strict-prob-boost", type=float, default=0.04)
    ap.add_argument("--strict-margin-boost", type=float, default=0.03)
    ap.add_argument("--no-strict-override", action="store_true")
    ap.add_argument("--women-min-prob", type=float, default=None)
    ap.add_argument("--women-min-margin", type=float, default=None)
    args = ap.parse_args()

    df = pd.read_csv(args.dataset)
    picks = apply_strategy(
        df,
        min_prob=args.min_prob,
        min_margin=args.min_margin,
        comp_acc_min=args.comp_acc_min,
        comp_min_picks=args.comp_min_picks,
        start_date=args.start_date,
        end_date=args.end_date,
        adaptive=args.adaptive,
        relax_prob=args.relax_prob,
        relax_margin=args.relax_margin,
        relax_acc_min=args.relax_acc_min,
        relax_min_picks=args.relax_min_picks,
        newcomp_min_prob=args.newcomp_min_prob,
        newcomp_min_margin=args.newcomp_min_margin,
        strict_prob_boost=args.strict_prob_boost,
        strict_margin_boost=args.strict_margin_boost,
        allow_strict_override=not args.no_strict_override,
        women_min_prob=args.women_min_prob,
        women_min_margin=args.women_min_margin,
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
        print("No qualifying picks.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
