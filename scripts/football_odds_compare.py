#!/usr/bin/env python3
from __future__ import annotations

import argparse
import math
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd


def _norm_team(text: str) -> str:
    if not text:
        return ""
    return "".join(ch for ch in text.lower().strip() if ch.isalnum())


def _implied_probs(odd_h: Optional[float], odd_d: Optional[float], odd_a: Optional[float]) -> Tuple[Optional[float], Optional[float], Optional[float], Optional[float]]:
    if odd_h is None or odd_a is None:
        return None, None, None, None
    if odd_h <= 0 or odd_a <= 0:
        return None, None, None, None
    p_h = 1.0 / odd_h
    p_a = 1.0 / odd_a
    p_d = 1.0 / odd_d if odd_d and odd_d > 0 else 0.0
    s = p_h + p_a + p_d
    if s <= 0:
        return None, None, None, None
    return p_h / s, p_d / s if p_d else None, p_a / s, s


def _load_fixtures(fixtures_csv: Path) -> pd.DataFrame:
    df = pd.read_csv(fixtures_csv, low_memory=False)
    for col in ("AvgH", "AvgD", "AvgA"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    if "Date" in df.columns:
        df["Date"] = pd.to_datetime(df["Date"], errors="coerce").dt.date
    return df


def _ensure_fixtures_csv(start: date, end: date, out_csv: Path) -> None:
    cmd = [
        sys.executable,
        str(Path(__file__).resolve().parent / "list_upcoming_fixtures.py"),
        "--start",
        start.isoformat(),
        "--end",
        end.isoformat(),
        "--out-csv",
        str(out_csv),
        "--out-md",
        "",
    ]
    subprocess.run(cmd, check=False, cwd=Path(__file__).resolve().parent.parent)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="")
    ap.add_argument("--end", default="")
    ap.add_argument("--days-ahead", type=int, default=3)
    ap.add_argument("--fixtures-csv", default="")
    ap.add_argument("--out", default="reports/football_odds_compare.csv")
    ap.add_argument("--out-summary", default="reports/football_odds_compare_summary.csv")
    args = ap.parse_args()

    if args.start and args.end:
        start = datetime.strptime(args.start, "%Y-%m-%d").date()
        end = datetime.strptime(args.end, "%Y-%m-%d").date()
    else:
        start = date.today()
        end = start + timedelta(days=args.days_ahead)

    fixtures_csv = Path(args.fixtures_csv) if args.fixtures_csv else Path(args.out).with_name(
        f"fixtures_compare_{start.isoformat()}_{end.isoformat()}.csv"
    )
    if not fixtures_csv.exists():
        _ensure_fixtures_csv(start, end, fixtures_csv)

    if not fixtures_csv.exists():
        print("No fixtures CSV found.")
        return 1

    df = _load_fixtures(fixtures_csv)
    if df.empty:
        print("No fixtures found.")
        return 1

    rows: List[Dict[str, object]] = []
    df["HomeNorm"] = df["HomeTeam"].astype(str).map(_norm_team)
    df["AwayNorm"] = df["AwayTeam"].astype(str).map(_norm_team)
    df["Code"] = df["Code"].astype(str) if "Code" in df.columns else ""

    grouped = df.groupby(["Date", "Code", "HomeNorm", "AwayNorm"])
    for (date_val, code, home_norm, away_norm), g in grouped:
        if not date_val or not home_norm or not away_norm:
            continue
        sources = {}
        for _, r in g.iterrows():
            src = str(r.get("Source") or "").strip().lower() or "unknown"
            ph, pd_, pa, over = _implied_probs(r.get("AvgH"), r.get("AvgD"), r.get("AvgA"))
            if ph is None or pa is None:
                continue
            sources[src] = {
                "AvgH": r.get("AvgH"),
                "AvgD": r.get("AvgD"),
                "AvgA": r.get("AvgA"),
                "ProbH": ph,
                "ProbD": pd_,
                "ProbA": pa,
                "Overround": over,
                "Home": r.get("HomeTeam"),
                "Away": r.get("AwayTeam"),
                "League": r.get("League"),
            }

        if not sources:
            continue

        # best source = lowest overround
        best_src = min(sources.items(), key=lambda kv: kv[1]["Overround"] or 99)
        best_name, best = best_src

        # consensus (average probabilities)
        prob_h = sum(v["ProbH"] for v in sources.values()) / len(sources)
        prob_a = sum(v["ProbA"] for v in sources.values()) / len(sources)
        prob_d = None
        has_d = any(v["ProbD"] is not None for v in sources.values())
        if has_d:
            prob_d = sum((v["ProbD"] or 0.0) for v in sources.values()) / len(sources)

        diff_prob_h = None
        if len(sources) > 1:
            probs = [v["ProbH"] for v in sources.values()]
            diff_prob_h = max(probs) - min(probs)

        rows.append(
            {
                "Date": date_val,
                "Code": code,
                "League": best.get("League"),
                "Home": best.get("Home"),
                "Away": best.get("Away"),
                "Sources": ",".join(sorted(sources.keys())),
                "BestSource": best_name,
                "BestAvgH": best.get("AvgH"),
                "BestAvgD": best.get("AvgD"),
                "BestAvgA": best.get("AvgA"),
                "BestProbH": round(best.get("ProbH", 0.0), 6),
                "BestProbD": round(best.get("ProbD", 0.0), 6) if best.get("ProbD") is not None else None,
                "BestProbA": round(best.get("ProbA", 0.0), 6),
                "BestOverround": round(best.get("Overround", 0.0), 6) if best.get("Overround") is not None else None,
                "ConsensusProbH": round(prob_h, 6),
                "ConsensusProbD": round(prob_d, 6) if prob_d is not None else None,
                "ConsensusProbA": round(prob_a, 6),
                "DiffProbH": round(diff_prob_h, 6) if diff_prob_h is not None else None,
            }
        )

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_df = pd.DataFrame(rows)
    out_df.to_csv(out_path, index=False)

    # summary
    summary_rows = []
    if not out_df.empty:
        out_df["SourceCount"] = out_df["Sources"].str.split(",").map(len)
        summary_rows.append(
            {
                "total_matches": len(out_df),
                "both_sources": int((out_df["SourceCount"] >= 2).sum()),
                "single_source": int((out_df["SourceCount"] == 1).sum()),
                "avg_diff_prob_h": round(out_df["DiffProbH"].dropna().mean(), 6) if out_df["DiffProbH"].notna().any() else None,
            }
        )
    pd.DataFrame(summary_rows).to_csv(Path(args.out_summary), index=False)

    print(f"Wrote {len(out_df)} rows to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
