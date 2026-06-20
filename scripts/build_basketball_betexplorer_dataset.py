#!/usr/bin/env python3
"""Build minimal basketball dataset from BetExplorer CSVs for the app."""
from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from typing import Dict, Iterable, Tuple


def _safe_float(value: str) -> float | None:
    try:
        return float(value)
    except Exception:
        return None


def _implied_probs(odd_h: float | None, odd_a: float | None, odd_d: float | None) -> Tuple[float | None, float | None]:
    if odd_h is None or odd_a is None:
        return None, None
    if odd_h <= 0 or odd_a <= 0:
        return None, None
    p_h = 1.0 / odd_h
    p_a = 1.0 / odd_a
    if odd_d is not None and odd_d > 0:
        p_d = 1.0 / odd_d
        s = p_h + p_a + p_d
    else:
        s = p_h + p_a
    if s <= 0:
        return None, None
    return p_h / s, p_a / s


def _load_map(path: Path) -> Dict[str, Dict[str, str]]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _iter_csv_rows(path: Path) -> Iterable[dict]:
    with path.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            yield row


def build_backtest(
    results_dir: Path, mapping: Dict[str, Dict[str, str]], out_path: Path
) -> int:
    rows = []
    for csv_path in results_dir.glob("*.csv"):
        code = csv_path.stem
        league = mapping.get(code, {}).get("league", code)
        for row in _iter_csv_rows(csv_path):
            date_iso = (row.get("Date") or "").strip()
            if not date_iso:
                continue
            home = (row.get("HomeTeam") or "").strip()
            away = (row.get("AwayTeam") or "").strip()
            if not home or not away:
                continue
            try:
                fthg = int(float(row.get("FTHG") or 0))
                ftag = int(float(row.get("FTAG") or 0))
            except Exception:
                continue
            odd_h = _safe_float(row.get("AvgH") or "")
            odd_d = _safe_float(row.get("AvgD") or "")
            odd_a = _safe_float(row.get("AvgA") or "")
            prob_h, prob_a = _implied_probs(odd_h, odd_a, odd_d)
            if prob_h is None or prob_a is None:
                continue
            home_win = 1 if fthg > ftag else 0
            pred_home = prob_h >= 0.5
            correct = int((pred_home and home_win == 1) or ((not pred_home) and home_win == 0))
            prob_margin = abs(prob_h - 0.5)
            rows.append(
                {
                    "GAME_DATE_EST": date_iso,
                    "HOME_TEAM_NAME": home,
                    "VISITOR_TEAM_NAME": away,
                    "PTS_home": fthg,
                    "PTS_away": ftag,
                    "HOME_TEAM_WINS": home_win,
                    "MARKET_PROB_home": round(prob_h, 6),
                    "MARKET_PROB_away": round(prob_a, 6),
                    "prob_margin": round(prob_margin, 6),
                    "correct": correct,
                    "league": league,
                    "accepted": 1,
                    "STATUS": "finished",
                }
            )

    if not rows:
        return 0
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0].keys())
    with out_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def build_current(
    fixtures_dir: Path, mapping: Dict[str, Dict[str, str]], out_path: Path
) -> int:
    rows = []
    for csv_path in fixtures_dir.glob("*.csv"):
        code = csv_path.stem
        league = mapping.get(code, {}).get("league", code)
        for row in _iter_csv_rows(csv_path):
            date_iso = (row.get("Date") or "").strip()
            if not date_iso:
                continue
            home = (row.get("HomeTeam") or "").strip()
            away = (row.get("AwayTeam") or "").strip()
            if not home or not away:
                continue
            odd_h = _safe_float(row.get("OddH") or "")
            odd_d = _safe_float(row.get("OddD") or "")
            odd_a = _safe_float(row.get("OddA") or "")
            prob_h, prob_a = _implied_probs(odd_h, odd_a, odd_d)
            if prob_h is None or prob_a is None:
                continue
            prob_margin = abs(prob_h - 0.5)
            rows.append(
                {
                    "GAME_DATE_EST": date_iso,
                    "HOME_TEAM_NAME": home,
                    "VISITOR_TEAM_NAME": away,
                    "PTS_home": "",
                    "PTS_away": "",
                    "HOME_TEAM_WINS": "",
                    "MARKET_PROB_home": round(prob_h, 6),
                    "MARKET_PROB_away": round(prob_a, 6),
                    "prob_margin": round(prob_margin, 6),
                    "correct": "",
                    "league": league,
                    "accepted": 1,
                    "STATUS": "not_started",
                }
            )

    if not rows:
        return 0
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0].keys())
    with out_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results-dir", default="data/raw/betexplorer_basketball_results")
    ap.add_argument("--fixtures-dir", default="data/raw/betexplorer_basketball_fixtures")
    ap.add_argument("--map", default="data/raw/betexplorer_basketball_map.json")
    ap.add_argument("--out-backtest", default="data/basketball_betexplorer_backtest.csv")
    ap.add_argument("--out-current", default="data/basketball_betexplorer_current.csv")
    args = ap.parse_args()

    mapping = _load_map(Path(args.map))
    results_dir = Path(args.results_dir)
    fixtures_dir = Path(args.fixtures_dir)

    backtest_rows = build_backtest(results_dir, mapping, Path(args.out_backtest))
    current_rows = build_current(fixtures_dir, mapping, Path(args.out_current))
    print(f"Backtest rows: {backtest_rows}")
    print(f"Current rows: {current_rows}")
    return 0 if (backtest_rows or current_rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
