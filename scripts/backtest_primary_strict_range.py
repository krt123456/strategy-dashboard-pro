#!/usr/bin/env python3
"""Backtest primary strict strategy over a date range."""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

from apply_primary_strategy_all import apply_strategy, parse_params
from daily_select import match_qualifies
from run_all_european_enhanced import (
    build_match_features,
    evaluate_strategies,
    normalize_main,
    pick_odds_cols,
)


def normalize_any(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    if "HG" in df.columns and "AG" in df.columns:
        df = df.rename(columns={"HG": "FTHG", "AG": "FTAG"})
    if "Home" in df.columns and "Away" in df.columns:
        df = df.rename(columns={"Home": "HomeTeam", "Away": "AwayTeam"})
    return df


def load_df(code: str, source: str, raw_dir: Path, bet_dir: Path) -> Optional[pd.DataFrame]:
    if source == "betexplorer":
        path = bet_dir / f"{code}_current.csv"
        if not path.exists():
            return None
        df = pd.read_csv(path, encoding="utf-8-sig")
        return normalize_any(df)

    # football-data sources
    all_path = raw_dir / f"{code}_all.csv"
    if all_path.exists():
        df = pd.read_csv(all_path, encoding="utf-8-sig")
        return normalize_any(df)

    files = sorted(raw_dir.glob(f"{code}_*.csv"))
    if not files:
        return None
    dfs = []
    for path in files:
        df = pd.read_csv(path, encoding="utf-8-sig")
        df = normalize_main(df) if source == "main" else normalize_any(df)
        dfs.append(df)
    if not dfs:
        return None
    return pd.concat(dfs, ignore_index=True, sort=False)


def resolve_params(feat: pd.DataFrame) -> Optional[Dict[str, float]]:
    thresholds = [0.60, 0.65, 0.70, 0.75]
    thresholds_ext = [0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70, 0.75]
    res = evaluate_strategies(feat, thresholds, target_acc=0.90, extended=False, allow_draws=False)
    best = res.get("best")
    if (best is None) or (best["coverage"] < 0.06):
        res_ext = evaluate_strategies(feat, thresholds_ext, target_acc=0.90, extended=True, allow_draws=True)
        best_ext = res_ext.get("best")
        if best_ext and (
            best is None
            or best_ext["coverage"] > best["coverage"]
            or (best_ext["coverage"] == best["coverage"] and best_ext["accuracy"] > best["accuracy"])
        ):
            best = best_ext
    if not best:
        return None
    return best.get("params")


def parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="")
    ap.add_argument("--end", default="")
    ap.add_argument("--days-back", type=int, default=365)
    ap.add_argument("--summary", default="reports/primary_strategy_all_competitions.csv")
    ap.add_argument("--out", default="reports/primary_strategy_strict_1y_backtest.md")
    args = ap.parse_args()

    today = date.today()
    if args.start and args.end:
        start_date = parse_date(args.start)
        end_date = parse_date(args.end)
    else:
        end_date = today
        start_date = today - timedelta(days=args.days_back)

    summary_path = Path(args.summary)
    if not summary_path.exists():
        print(f"Missing summary file: {summary_path}")
        return 1

    summary_df = pd.read_csv(summary_path)
    summary_df = summary_df[summary_df["Status"] == "ok"].copy()

    raw_dir = Path("data/raw/football_data")
    bet_dir = Path("data/raw/betexplorer")

    total_matches = 0
    total_primary = 0
    total_correct = 0
    total_wrong = 0
    phase_stats = {
        "early_season": {"picks": 0, "correct": 0, "wrong": 0},
        "mid_season": {"picks": 0, "correct": 0, "wrong": 0},
        "late_season": {"picks": 0, "correct": 0, "wrong": 0},
        "transfer_window": {"picks": 0, "correct": 0, "wrong": 0},
        "post_summer_window": {"picks": 0, "correct": 0, "wrong": 0},
        "post_winter_window": {"picks": 0, "correct": 0, "wrong": 0},
    }

    rows: List[Dict[str, object]] = []

    for _, row in summary_df.iterrows():
        code = str(row.get("Code"))
        league = str(row.get("League"))
        source = str(row.get("Source"))
        params = parse_params(row.get("Params"))

        df = load_df(code, source, raw_dir, bet_dir)
        if df is None or df.empty:
            continue

        odds_cols = pick_odds_cols(df)
        if not odds_cols:
            continue
        for col in odds_cols:
            df[col] = pd.to_numeric(df[col], errors="coerce")

        feat = build_match_features(df, odds_cols, window=5, team_geo=None, external_features=None)
        if feat.empty:
            continue

        if not params:
            params = resolve_params(feat)
        if not params:
            continue

        feat["DateOnly"] = pd.to_datetime(feat["Date"], errors="coerce").dt.date
        in_range = feat[(feat["DateOnly"] >= start_date) & (feat["DateOnly"] <= end_date)].copy()
        if in_range.empty:
            continue

        total_matches += len(in_range)
        qualifying = in_range[in_range.apply(lambda r: match_qualifies(r, params), axis=1)].copy()
        if qualifying.empty:
            rows.append(
                {
                    "Code": code,
                    "League": league,
                    "Matches": len(in_range),
                    "Primary": 0,
                    "Correct": 0,
                    "Wrong": 0,
                    "Accuracy": 0.0,
                }
            )
            continue

        _, primary = apply_strategy(qualifying, code)
        if primary.empty:
            rows.append(
                {
                    "Code": code,
                    "League": league,
                    "Matches": len(in_range),
                    "Primary": 0,
                    "Correct": 0,
                    "Wrong": 0,
                    "Accuracy": 0.0,
                }
            )
            continue

        correct = (primary["Pred"] == primary["Actual"]).sum()
        wrong = len(primary) - correct
        acc = correct / len(primary) if len(primary) else 0.0

        total_primary += len(primary)
        total_correct += int(correct)
        total_wrong += int(wrong)

        if "IsEarlySeason" in primary.columns and "IsLateSeason" in primary.columns:
            early = primary[primary["IsEarlySeason"] == 1]
            late = primary[primary["IsLateSeason"] == 1]
            mid = primary[(primary["IsEarlySeason"] != 1) & (primary["IsLateSeason"] != 1)]
            for label, subset in (("early_season", early), ("mid_season", mid), ("late_season", late)):
                if subset.empty:
                    continue
                c = (subset["Pred"] == subset["Actual"]).sum()
                phase_stats[label]["picks"] += len(subset)
                phase_stats[label]["correct"] += int(c)
                phase_stats[label]["wrong"] += int(len(subset) - c)

        if "IsTransferWindow" in primary.columns:
            subset = primary[primary["IsTransferWindow"] == 1]
            if not subset.empty:
                c = (subset["Pred"] == subset["Actual"]).sum()
                phase_stats["transfer_window"]["picks"] += len(subset)
                phase_stats["transfer_window"]["correct"] += int(c)
                phase_stats["transfer_window"]["wrong"] += int(len(subset) - c)

        if "IsPostSummerWindow" in primary.columns:
            subset = primary[primary["IsPostSummerWindow"] == 1]
            if not subset.empty:
                c = (subset["Pred"] == subset["Actual"]).sum()
                phase_stats["post_summer_window"]["picks"] += len(subset)
                phase_stats["post_summer_window"]["correct"] += int(c)
                phase_stats["post_summer_window"]["wrong"] += int(len(subset) - c)

        if "IsPostWinterWindow" in primary.columns:
            subset = primary[primary["IsPostWinterWindow"] == 1]
            if not subset.empty:
                c = (subset["Pred"] == subset["Actual"]).sum()
                phase_stats["post_winter_window"]["picks"] += len(subset)
                phase_stats["post_winter_window"]["correct"] += int(c)
                phase_stats["post_winter_window"]["wrong"] += int(len(subset) - c)

        rows.append(
            {
                "Code": code,
                "League": league,
                "Matches": len(in_range),
                "Primary": len(primary),
                "Correct": int(correct),
                "Wrong": int(wrong),
                "Accuracy": acc,
            }
        )

    rows.sort(key=lambda r: (r["Accuracy"], r["Primary"]), reverse=True)

    out_path = Path(args.out)
    lines = [
        "# Primary strategy strict backtest",
        f"- Range: {start_date.isoformat()} to {end_date.isoformat()}",
        f"- Matches in range (all leagues): {total_matches}",
        f"- Primary picks: {total_primary}",
        f"- Correct: {total_correct}",
        f"- Wrong: {total_wrong}",
        f"- Accuracy: {(total_correct / total_primary) if total_primary else 0:.2%}",
        "",
        "## Phase breakdown",
        "| Phase | Picks | Correct | Wrong | Accuracy |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for label, stats in phase_stats.items():
        picks = stats["picks"]
        correct = stats["correct"]
        wrong = stats["wrong"]
        acc = (correct / picks) if picks else 0.0
        lines.append(f"| {label} | {picks} | {correct} | {wrong} | {acc*100:.2f}% |")
    lines += [
        "",
        "| Code | League | Matches | Primary | Correct | Wrong | Accuracy |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for r in rows:
        lines.append(
            f"| {r['Code']} | {r['League']} | {r['Matches']} | {r['Primary']} | {r['Correct']} | {r['Wrong']} | {r['Accuracy']*100:.2f}% |"
        )
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"saved: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
