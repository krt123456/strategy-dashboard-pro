#!/usr/bin/env python3
"""Summarize typical season start/end timing per league from local match data."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

from backtest_primary_strict_range import load_df


def parse_dates(df: pd.DataFrame) -> List[pd.Timestamp]:
    if "Date" not in df.columns:
        return []
    raw = df["Date"].dropna().astype(str)
    if raw.empty:
        return []
    iso_ratio = raw.str.match(r"\\d{4}-\\d{2}-\\d{2}").mean()
    dayfirst = False if iso_ratio >= 0.8 else True
    dates = pd.to_datetime(raw, dayfirst=dayfirst, errors="coerce")
    dates = dates.dropna().sort_values()
    return list(dates)


def segment_seasons(
    dates: List[pd.Timestamp],
    gap_days: int,
    min_matches: int,
) -> List[Tuple[pd.Timestamp, pd.Timestamp, int]]:
    if not dates:
        return []
    segments: List[Tuple[pd.Timestamp, pd.Timestamp, int]] = []
    start = dates[0]
    prev = dates[0]
    count = 1
    for d in dates[1:]:
        if (d - prev).days > gap_days:
            if count >= min_matches:
                segments.append((start, prev, count))
            start = d
            count = 1
        else:
            count += 1
        prev = d
    if count >= min_matches:
        segments.append((start, prev, count))
    return segments


def most_common_month(months: List[int]) -> Optional[int]:
    if not months:
        return None
    return Counter(months).most_common(1)[0][0]


def summarize_segments(
    segments: List[Tuple[pd.Timestamp, pd.Timestamp, int]],
    completed: List[Tuple[pd.Timestamp, pd.Timestamp, int]],
) -> Dict[str, object]:
    if not segments:
        return {
            "Seasons": 0,
            "StartMonthMode": None,
            "StartMonthMedian": None,
            "EndMonthMode": None,
            "EndMonthMedian": None,
            "SplitSeasons": 0.0,
            "MedianSeasonDays": None,
            "MedianBreakDays": None,
        }
    start_months = [s.month for s, _, _ in segments]
    use_for_end = completed if completed else segments
    end_months = [e.month for _, e, _ in use_for_end]
    start_month_mode = most_common_month(start_months)
    end_month_mode = most_common_month(end_months)
    start_month_med = int(pd.Series(start_months).median())
    end_month_med = int(pd.Series(end_months).median())
    split_ratio = sum(1 for s, e, _ in use_for_end if s.year != e.year) / len(use_for_end)
    season_days = [(e - s).days for s, e, _ in use_for_end]
    breaks = []
    for i in range(1, len(use_for_end)):
        prev_end = use_for_end[i - 1][1]
        curr_start = use_for_end[i][0]
        breaks.append((curr_start - prev_end).days)
    median_break = int(pd.Series(breaks).median()) if breaks else None
    return {
        "Seasons": len(segments),
        "StartMonthMode": start_month_mode,
        "StartMonthMedian": start_month_med,
        "EndMonthMode": end_month_mode,
        "EndMonthMedian": end_month_med,
        "SplitSeasons": round(split_ratio, 2),
        "MedianSeasonDays": int(pd.Series(season_days).median()) if season_days else None,
        "MedianBreakDays": median_break,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--summary", default="reports/primary_strategy_all_competitions.csv")
    ap.add_argument("--gap-days", type=int, default=45)
    ap.add_argument("--min-matches", type=int, default=60)
    ap.add_argument("--active-cutoff-days", type=int, default=60)
    ap.add_argument("--out-csv", default="reports/season_calendar_summary.csv")
    ap.add_argument("--out-md", default="reports/season_calendar_summary.md")
    args = ap.parse_args()

    summary_path = Path(args.summary)
    if not summary_path.exists():
        print(f"Missing summary file: {summary_path}")
        return 1

    summary_df = pd.read_csv(summary_path)
    summary_df = summary_df[summary_df["Status"] == "ok"].copy()

    raw_dir = Path("data/raw/football_data")
    bet_dir = Path("data/raw/betexplorer")

    rows: List[Dict[str, object]] = []
    for _, row in summary_df.iterrows():
        code = str(row.get("Code"))
        league = str(row.get("League"))
        source = str(row.get("Source"))
        df = load_df(code, source, raw_dir, bet_dir)
        if df is None or df.empty:
            continue

        dates = parse_dates(df)
        segments = segment_seasons(dates, args.gap_days, args.min_matches)
        max_date = max(dates).date() if dates else None
        ongoing = False
        completed = segments
        if max_date and segments:
            if (date.today() - max_date).days <= args.active_cutoff_days:
                ongoing = True
                completed = segments[:-1] if len(segments) > 1 else []
        summary = summarize_segments(segments, completed)
        rows.append(
            {
                "Code": code,
                "League": league,
                "Source": source,
                "OngoingSeason": ongoing,
                "Seasons": summary["Seasons"],
                "StartMonthMode": summary["StartMonthMode"],
                "StartMonthMedian": summary["StartMonthMedian"],
                "EndMonthMode": summary["EndMonthMode"],
                "EndMonthMedian": summary["EndMonthMedian"],
                "SplitSeasons": summary["SplitSeasons"],
                "MedianSeasonDays": summary["MedianSeasonDays"],
                "MedianBreakDays": summary["MedianBreakDays"],
            }
        )

    if not rows:
        print("No leagues found to summarize.")
        return 1

    out_df = pd.DataFrame(rows).sort_values(["StartMonthMedian", "League"])
    out_csv = Path(args.out_csv)
    out_md = Path(args.out_md)
    out_df.to_csv(out_csv, index=False)

    lines = [
        "# Season calendar summary",
        f"- Generated: {date.today().isoformat()}",
        f"- Gap threshold (days): {args.gap_days}",
        f"- Minimum matches per season segment: {args.min_matches}",
        "",
        "| Code | League | Source | OngoingSeason | Seasons | StartMonthMode | StartMonthMedian | EndMonthMode | EndMonthMedian | SplitSeasons | MedianSeasonDays | MedianBreakDays |",
        "| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for _, r in out_df.iterrows():
        lines.append(
            "| {Code} | {League} | {Source} | {OngoingSeason} | {Seasons} | {StartMonthMode} | {StartMonthMedian} | {EndMonthMode} | {EndMonthMedian} | {SplitSeasons} | {MedianSeasonDays} | {MedianBreakDays} |".format(
                **r.to_dict()
            )
        )
    out_md.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {out_csv} and {out_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
