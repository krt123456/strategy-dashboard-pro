#!/usr/bin/env python3
"""Basketball daily predictor — uses trained ML model to predict today's games.

Flow:
  1. Load upcoming basketball fixtures (betexplorer)
  2. Compute ELO + features for each game
  3. Run trained calibrated LightGBM model
  4. Compare against 1xBet odds
  5. Detect value bets + Kelly staking
  6. Output daily picks

Usage:
  python basketball_predict_daily.py                    # today
  python basketball_predict_daily.py --date 2026-06-19  # specific date
"""
from __future__ import annotations

import csv
import json
import pickle
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR / "scripts"))

from basketball_pipeline import (
    EloEngine, FeatureBuilder, FEATURE_COLS,
    load_games, build_dataset, load_model
)
from betting_math import (
    ev_percent, is_value_bet, kelly_stake,
    simultaneous_kelly, implied_prob, remove_vig, edge
)


def load_upcoming_fixtures(target_date: date) -> pd.DataFrame:
    fixtures_dir = PROJECT_DIR / "data" / "raw" / "betexplorer_basketball_fixtures"
    rows = []
    if not fixtures_dir.exists():
        return pd.DataFrame()
    for csv_path in fixtures_dir.glob("*.csv"):
        try:
            df = pd.read_csv(csv_path, nrows=500)
            for _, r in df.iterrows():
                d = r.get("Date", "")
                if not d:
                    continue
                try:
                    parsed = pd.to_datetime(d, errors="coerce")
                    if pd.isna(parsed):
                        continue
                    if parsed.date() == target_date:
                        home = str(r.get("Home", r.get("HOME_TEAM_NAME", "")))
                        away = str(r.get("Away", r.get("VISITOR_TEAM_NAME", "")))
                        odds_home = r.get("Odds_Home", r.get("MARKET_PROB_home"))
                        odds_away = r.get("Odds_Away", r.get("MARKET_PROB_away"))
                        if home and away:
                            rows.append({
                                "league": csv_path.stem,
                                "home": home,
                                "away": away,
                                "odds_home_raw": odds_home,
                                "odds_away_raw": odds_away,
                            })
                except Exception:
                    continue
        except Exception:
            continue
    return pd.DataFrame(rows)


def load_current_dataset():
    """Load historical data and rebuild ELO + features up to today."""
    df = load_games()
    dataset, elo = build_dataset(df)
    return dataset, elo, df


def predict_games(
    upcoming: pd.DataFrame,
    model,
    base_model,
    elo_snapshot: dict,
    feature_builder: FeatureBuilder,
    bankroll: float = 100.0,
) -> list[dict]:
    picks = []
    for _, r in upcoming.iterrows():
        home = r["home"]
        away = r["away"]
        league = r.get("league", "unknown")

        odds_h_raw = r.get("odds_home_raw")
        odds_a_raw = r.get("odds_away_raw")

        market_ph = 0.5
        market_pa = 0.5
        if odds_h_raw and odds_a_raw:
            try:
                oh = float(odds_h_raw)
                oa = float(odds_a_raw)
                if oh > 1.0 and oa > 1.0:
                    probs = remove_vig([oh, oa])
                    market_ph = probs[0]
                    market_pa = probs[1]
            except (ValueError, TypeError):
                pass

        home_elo = elo_snapshot.get(home, 1500.0)
        away_elo = elo_snapshot.get(away, 1500.0)

        feat = feature_builder.build_features(
            home, away, str(date.today()),
            market_ph, market_pa, league,
            _DummyElo(elo_snapshot)
        )

        X = np.array([[feat[col] for col in FEATURE_COLS]])
        cal_prob = float(model.predict_proba(X)[0, 1])
        raw_prob = float(base_model.predict_proba(X)[0, 1])

        dec_odds = 1.0 / max(cal_prob, 0.01) if cal_prob > 0 else 0

        ev = ev_percent(cal_prob, dec_odds)
        mkt_ev = ev_percent(cal_prob, 1.0 / max(market_ph, 0.01)) if market_ph > 0 else -100

        stake = kelly_stake(cal_prob, 1.0 / max(market_ph, 0.01), bankroll, 0.25, 0.05) if market_ph > 0 else 0

        picks.append({
            "league": league,
            "home": home,
            "away": away,
            "pick": home if cal_prob >= 0.5 else away,
            "model_prob": round(cal_prob, 4),
            "raw_prob": round(raw_prob, 4),
            "market_prob": round(market_ph if cal_prob >= 0.5 else market_pa, 4),
            "home_elo": round(home_elo, 0),
            "away_elo": round(away_elo, 0),
            "elo_diff": round(home_elo - away_elo, 0),
            "model_odds": round(dec_odds, 3),
            "market_odds": round(1.0 / max(market_ph if cal_prob >= 0.5 else market_pa, 0.01), 3),
            "ev_pct": round(ev, 1),
            "market_ev_pct": round(mkt_ev, 1),
            "kelly_stake": round(stake, 2),
            "is_value": is_value_bet(cal_prob, 1.0 / max(market_ph if cal_prob >= 0.5 else market_pa, 0.01)) if market_ph > 0 else False,
            "confidence": "A" if cal_prob >= 0.75 else "B" if cal_prob >= 0.65 else "C",
        })

    picks.sort(key=lambda x: x["ev_pct"], reverse=True)
    return picks


class _DummyElo:
    def __init__(self, snapshot: dict):
        self.ratings = snapshot
    def get_rating(self, team: str) -> float:
        return self.ratings.get(team, 1500.0)
    def expected(self, a, b, hfa=65.0):
        from basketball_pipeline import EloEngine
        return EloEngine.expected(self, a, b, hfa)


def print_daily_report(picks: list[dict], target_date: date):
    value_picks = [p for p in picks if p["is_value"]]
    kelly_picks = [p for p in picks if p["kelly_stake"] > 0]

    print(f"\n{'='*70}")
    print(f"  BASKETBALL ML PREDICTIONS — {target_date}")
    print(f"{'='*70}")
    print(f"  Total games: {len(picks)} | Value bets: {len(value_picks)} | Kelly bets: {len(kelly_picks)}")
    print()

    if not picks:
        print("  No games found for this date.")
        return

    print(f"{'#':<3s} {'League':<18s} {'Match':<35s} {'Pick':<15s} {'Prob':>6s} {'Mkt':>6s} {'EV%':>6s} {'Kelly':>7s} {'Conf':>5s}")
    print("-" * 105)

    for i, p in enumerate(picks[:30]):
        match = f"{p['home'][:15]} vs {p['away'][:15]}"
        print(
            f"{i+1:<3d} {p['league'][:17]:<18s} {match:<35s} "
            f"{p['pick'][:13]:<15s} {p['model_prob']:>5.1%} {p['market_prob']:>5.1%} "
            f"{p['market_ev_pct']:>+5.1f}% ${p['kelly_stake']:>5.2f} {p['confidence']:>5s}"
        )

    if value_picks:
        print(f"\n  ⚡ VALUE BETS ({len(value_picks)}):")
        for p in value_picks[:10]:
            print(
                f"    {p['pick']:15s} | {p['home'][:15]} vs {p['away'][:15]} | "
                f"Prob: {p['model_prob']:.1%} vs Market: {p['market_prob']:.1%} | "
                f"EV: {p['market_ev_pct']:+.1f}% | Stake: ${p['kelly_stake']:.2f}"
            )

    total_stake = sum(p["kelly_stake"] for p in kelly_picks)
    total_potential = sum(p["kelly_stake"] * (p["market_odds"] - 1) for p in kelly_picks)
    print(f"\n  📊 Kelly summary: {len(kelly_picks)} bets | ${total_stake:.2f} staked | ${total_potential:.2f} potential")


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", type=str, default=None)
    parser.add_argument("--bankroll", type=float, default=100.0)
    args = parser.parse_args()

    target = date.today()
    if args.date:
        try:
            target = datetime.strptime(args.date, "%Y-%m-%d").date()
        except ValueError:
            pass

    print(f"Loading ML model...")
    model, base_model, elo_snapshot = load_model()
    print(f"  Model loaded | Teams: {len(elo_snapshot)}")

    print(f"Building feature history from 12,942 games...")
    dataset, elo_engine, raw_df = load_current_dataset()
    feat_builder = FeatureBuilder()
    for _, r in raw_df.iterrows():
        home = str(r["HOME_TEAM_NAME"])
        away = str(r["VISITOR_TEAM_NAME"])
        ds = str(r["GAME_DATE_EST"].date()) if pd.notna(r["GAME_DATE_EST"]) else ""
        home_won = int(r["HOME_TEAM_WINS"])
        pts_h = int(r.get("PTS_home", 0) or 0)
        pts_a = int(r.get("PTS_away", 0) or 0)
        if ds:
            feat_builder.record_result(home, away, ds, bool(home_won), pts_h, pts_a)

    print(f"\nLoading fixtures for {target}...")
    upcoming = load_upcoming_fixtures(target)
    print(f"  Found {len(upcoming)} games")

    if len(upcoming) == 0:
        tomorrow = target + timedelta(days=1)
        print(f"  Checking tomorrow ({tomorrow})...")
        upcoming = load_upcoming_fixtures(tomorrow)
        print(f"  Found {len(upcoming)} games for {tomorrow}")
        if not upcoming.empty:
            target = tomorrow

    if upcoming.empty:
        print("\n  No upcoming fixtures found. Checking current basketball data...")
        curr_path = PROJECT_DIR / "data" / "basketball_betexplorer_current.csv"
        if curr_path.exists():
            df_curr = pd.read_csv(curr_path)
            df_curr["GAME_DATE_EST"] = pd.to_datetime(df_curr["GAME_DATE_EST"], errors="coerce")
            target_ts = pd.Timestamp(target)
            for _, r in df_curr.iterrows():
                if pd.notna(r["GAME_DATE_EST"]) and r["GAME_DATE_EST"].date() >= target:
                    market_ph = float(r.get("MARKET_PROB_home", 0.5) or 0.5)
                    market_pa = float(r.get("MARKET_PROB_away", 0.5) or 0.5)
                    upcoming = pd.concat([upcoming, pd.DataFrame([{
                        "league": str(r.get("league", "unknown")),
                        "home": str(r["HOME_TEAM_NAME"]),
                        "away": str(r["VISITOR_TEAM_NAME"]),
                        "odds_home_raw": 1.0/max(market_ph, 0.01) if market_ph > 0 else None,
                        "odds_away_raw": 1.0/max(market_pa, 0.01) if market_pa > 0 else None,
                    }])], ignore_index=True)
            print(f"  Added {len(upcoming)} from current data")

    if len(upcoming) > 0:
        picks = predict_games(upcoming, model, base_model, elo_snapshot, feat_builder, args.bankroll)
        print_daily_report(picks, target)

        out_path = PROJECT_DIR / "reports" / f"basketball_ml_picks_{target}.csv"
        if picks:
            pd.DataFrame(picks).to_csv(out_path, index=False)
            print(f"\n  Saved: {out_path}")
    else:
        print("\n  No games found for prediction.")


if __name__ == "__main__":
    main()
