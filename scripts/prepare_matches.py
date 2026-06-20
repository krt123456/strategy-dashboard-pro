#!/usr/bin/env python3
"""Prepare EPL match dataset from football-data.co.uk CSVs."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    import yaml  # type: ignore
except Exception:
    print("Missing dependency: PyYAML. Install with: pip install pyyaml", file=sys.stderr)
    raise

try:
    import pandas as pd  # type: ignore
except Exception:
    print("Missing dependency: pandas. Install with: pip install pandas", file=sys.stderr)
    raise


DATE_FORMATS = ["%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d"]


def load_config(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def parse_date(series: pd.Series) -> pd.Series:
    for fmt in DATE_FORMATS:
        try:
            return pd.to_datetime(series, format=fmt, errors="raise")
        except Exception:
            continue
    # fallback
    return pd.to_datetime(series, errors="coerce", dayfirst=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    args = ap.parse_args()

    cfg = load_config(Path(args.config))
    league_code = cfg["sources"]["football_data"]["league_code"]
    seasons = cfg["seasons"]["football_data_codes"]
    raw_dir = Path(cfg["paths"]["raw"]) / "football_data"
    out_dir = Path(cfg["paths"]["processed"])
    out_dir.mkdir(parents=True, exist_ok=True)

    frames = []
    for code in seasons:
        code_str = str(code)
        path = raw_dir / f"{league_code}_{code_str}.csv"
        if not path.exists():
            print(f"missing: {path}", file=sys.stderr)
            continue
        df = pd.read_csv(path)
        if "Date" not in df.columns:
            print(f"missing Date column in {path}", file=sys.stderr)
            continue
        df["Date"] = parse_date(df["Date"])
        df["SeasonCode"] = code_str
        frames.append(df)

    if not frames:
        print("No data loaded. Run download_football_data.py first.", file=sys.stderr)
        return 1

    data = pd.concat(frames, ignore_index=True)

    # Keep a standard subset + any bookmaker odds columns if present
    keep_cols = [
        "Date",
        "SeasonCode",
        "HomeTeam",
        "AwayTeam",
        "FTHG",
        "FTAG",
        "FTR",
        "HTHG",
        "HTAG",
        "HTR",
        "HS",
        "AS",
        "HST",
        "AST",
        "HC",
        "AC",
        "HY",
        "AY",
        "HR",
        "AR",
        "B365H",
        "B365D",
        "B365A",
    ]
    keep_cols = [c for c in keep_cols if c in data.columns]
    data = data[keep_cols].copy()

    data = data.dropna(subset=["Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG"])
    data = data.sort_values("Date").reset_index(drop=True)

    out_path = out_dir / "epl_matches.csv"
    data.to_csv(out_path, index=False)
    print(f"saved: {out_path} ({len(data)} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
