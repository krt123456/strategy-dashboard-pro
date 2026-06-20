#!/usr/bin/env python3
"""Build a minimal hockey dataset from BetExplorer CSVs for the engine."""
from __future__ import annotations

import argparse
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd


def _slugify(value: str) -> str:
    value = (value or "").strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-") or "unknown"


def _to_float(value: object) -> Optional[float]:
    try:
        if value is None:
            return None
        if isinstance(value, float) and pd.isna(value):
            return None
        return float(value)
    except Exception:
        return None


def _parse_date(value: object) -> Optional[datetime]:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value).strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d.%m.%Y"):
        try:
            return datetime.strptime(text, fmt)
        except Exception:
            continue
    try:
        return pd.to_datetime(text, errors="coerce").to_pydatetime()
    except Exception:
        return None


def _build_rows(csv_path: Path) -> List[Dict[str, object]]:
    league_id = csv_path.stem
    df = pd.read_csv(csv_path)
    rows: List[Dict[str, object]] = []
    for _, record in df.iterrows():
        match_date = _parse_date(record.get("Date"))
        if match_date is None:
            continue
        start_time = match_date.replace(hour=12, minute=0, second=0, microsecond=0)
        home = str(record.get("HomeTeam") or "").strip()
        away = str(record.get("AwayTeam") or "").strip()
        if not home or not away:
            continue
        home_id = _slugify(home)
        away_id = _slugify(away)
        event_id = f"{league_id}_{start_time.date()}_{home_id}_vs_{away_id}"

        odds_home = _to_float(record.get("AvgH"))
        odds_away = _to_float(record.get("AvgA"))
        odds_draw = _to_float(record.get("AvgD"))
        if odds_home is None or odds_away is None:
            continue

        p_home = 1.0 / odds_home if odds_home > 0 else None
        p_away = 1.0 / odds_away if odds_away > 0 else None
        if p_home is None or p_away is None:
            continue
        overround = p_home + p_away
        if overround <= 0:
            continue
        book_prob_home = p_home / overround
        book_prob_away = p_away / overround

        # BetExplorer provides 1X2 averages; we normalize to 2-way probabilities.
        # Fair probabilities are unknown, so we mirror book probabilities.
        fair_prob_home = book_prob_home
        fair_prob_away = book_prob_away

        updated_at = start_time.isoformat()

        rows.append(
            {
                "event_id": event_id,
                "league_id": league_id,
                "start_time": start_time.isoformat(),
                "home_team_id": home_id,
                "away_team_id": away_id,
                "home_team": home,
                "away_team": away,
                "home_score": _to_float(record.get("FTHG")),
                "away_score": _to_float(record.get("FTAG")),
                "reg_home_score": None,
                "reg_away_score": None,
                "book_odds_home": odds_home,
                "book_odds_away": odds_away,
                "book_odds_home_updated_at": updated_at,
                "book_odds_away_updated_at": updated_at,
                "book_odds_home_available": True,
                "book_odds_away_available": True,
                "book_prob_home": book_prob_home,
                "book_prob_away": book_prob_away,
                "book_overround": overround,
                "fair_prob_home": fair_prob_home,
                "fair_prob_away": fair_prob_away,
                "reg_odds_home": odds_home,
                "reg_odds_away": odds_away,
                "reg_odds_home_updated_at": updated_at,
                "reg_odds_away_updated_at": updated_at,
                "reg_odds_home_available": True,
                "reg_odds_away_available": True,
                "reg_prob_home": book_prob_home,
                "reg_prob_away": book_prob_away,
                "reg_overround": overround,
                "reg_fair_prob_home": fair_prob_home,
                "reg_fair_prob_away": fair_prob_away,
                "total_line": None,
                "total_over_odds": None,
                "total_under_odds": None,
                "puck_line_home": None,
                "puck_line_away": None,
                "puck_odds_home": None,
                "puck_odds_away": None,
                "book_odds_1xbet_home": None,
                "book_odds_1xbet_away": None,
                "season": record.get("Season"),
                "avg_draw_odds": odds_draw,
            }
        )
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in-dir", default="data/raw/betexplorer_hockey")
    ap.add_argument(
        "--out",
        default="/home/luna/hockey_sgodds/data/processed/betexplorer_hockey_matches.csv",
    )
    args = ap.parse_args()

    in_dir = Path(args.in_dir)
    out_path = Path(args.out)
    if not in_dir.exists():
        raise SystemExit(f"Missing input directory: {in_dir}")

    all_rows: List[Dict[str, object]] = []
    for csv_path in sorted(in_dir.glob("*.csv")):
        all_rows.extend(_build_rows(csv_path))

    if not all_rows:
        raise SystemExit("No rows produced from BetExplorer files.")

    df = pd.DataFrame(all_rows)
    df = df.sort_values("start_time")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_path, index=False)
    print(f"Wrote {len(df)} rows -> {out_path}")


if __name__ == "__main__":
    main()
