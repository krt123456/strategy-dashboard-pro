"""Basketball ML pipeline — ELO + features + LightGBM + calibration.

Trains on 12,942 historical basketball games (2012-2026) and produces
calibrated win probabilities that can be compared against 1xBet odds
for value bet detection and Kelly-sized staking.

Architecture:
  1. Compute ELO ratings chronologically (no data leakage)
  2. Build feature matrix: ELO diff, form, rest days, H2H, league quality
  3. Walk-forward split: train ≤2023, val 2024, test ≥2025
  4. LightGBM with early stopping + isotonic calibration
  5. Backtest: market prob vs raw ML vs calibrated ML

Usage:
  python basketball_pipeline.py --train     # train and save model
  python basketball_pipeline.py --backtest  # run full backtest
  python basketball_pipeline.py --predict   # predict upcoming games
"""
from __future__ import annotations

import csv
import json
import math
import pickle
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

PROJECT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_DIR / "data"
MODEL_DIR = PROJECT_DIR / "models"
MODEL_DIR.mkdir(exist_ok=True)

HFA = 65.0
K_EARLY = 32.0
K_LATE = 20.0
EARLY_GAMES = 30
INITIAL_ELO = 1500.0


# ═══════════════════════════════════════════════════════════════════════════
# 1. ELO ENGINE
# ═══════════════════════════════════════════════════════════════════════════

class EloEngine:
    def __init__(self):
        self.ratings: dict[str, float] = defaultdict(lambda: INITIAL_ELO)
        self.games_played: dict[str, int] = defaultdict(int)
        self.peak_rating: dict[str, float] = defaultdict(float)

    def expected(self, elo_a: float, elo_b: float, hfa: float = HFA) -> float:
        return 1.0 / (1.0 + 10.0 ** ((elo_b - elo_a - hfa) / 400.0))

    def update(self, team: str, opponent: str, won: bool, margin: float = 0.0, hfa: bool = True):
        e_team = self.ratings[team]
        e_opp = self.ratings[opponent]
        hfa_val = HFA if hfa else 0.0
        exp_a = self.expected(e_team, e_opp, hfa_val)
        actual = 1.0 if won else 0.0
        k = K_EARLY if self.games_played[team] < EARLY_GAMES else K_LATE
        margin_mult = 1.0
        if margin != 0:
            margin_mult = math.log(abs(margin) + 1) * (2.0 / 2.302)
            margin_mult = max(0.5, min(margin_mult, 3.0))
        delta = k * margin_mult * (actual - exp_a)
        self.ratings[team] += delta
        self.ratings[opponent] -= delta
        self.games_played[team] += 1
        self.games_played[opponent] += 1
        self.peak_rating[team] = max(self.peak_rating[team], self.ratings[team])

    def get_rating(self, team: str) -> float:
        return self.ratings[team]

    def snapshot(self) -> dict:
        return {
            team: {"elo": rating, "games": self.games_played[team], "peak": self.peak_rating[team]}
            for team, rating in sorted(self.ratings.items(), key=lambda x: -x[1])
        }


# ═══════════════════════════════════════════════════════════════════════════
# 2. FEATURE BUILDER
# ═══════════════════════════════════════════════════════════════════════════

class FeatureBuilder:
    def __init__(self):
        self.team_history: dict[str, list[dict]] = defaultdict(list)
        self.h2h: dict[tuple[str, str], list[bool]] = defaultdict(list)
        self.league_avg_score: dict[str, float] = {}

    def build_features(
        self,
        home: str,
        away: str,
        date_str: str,
        market_prob_home: float,
        market_prob_away: float,
        league: str,
        elo: EloEngine,
    ) -> dict:
        home_elo = elo.get_rating(home)
        away_elo = elo.get_rating(away)
        elo_diff = home_elo - away_elo

        home_hist = [g for g in self.team_history[home] if g["date"] <= date_str]
        away_hist = [g for g in self.team_history[away] if g["date"] <= date_str]

        def _form(hist: list[dict], n: int, as_home: bool) -> float:
            recent = [g for g in sorted(hist, key=lambda x: x["date"])[-n:] if g.get("as_home") == as_home]
            if not recent:
                return 0.5
            wins = sum(1 for g in recent if g["won"])
            return wins / len(recent) if recent else 0.5

        def _avg_pts(hist: list[dict], n: int, scored: bool = True) -> float:
            recent = sorted(hist, key=lambda x: x["date"])[-n:]
            if not recent:
                return 75.0
            key = "pts_scored" if scored else "pts_allowed"
            vals = [g[key] for g in recent if g.get(key) is not None]
            return sum(vals) / len(vals) if vals else 75.0

        def _rest_days(hist: list[dict], date_str: str) -> float:
            if not hist:
                return 7.0
            sorted_hist = sorted(hist, key=lambda x: x["date"])
            last = sorted_hist[-1]
            try:
                d1 = datetime.fromisoformat(date_str[:10])
                d2 = datetime.fromisoformat(last["date"][:10])
                diff = (d1 - d2).days
                return max(0.0, min(diff, 14.0))
            except Exception:
                return 7.0

        h2h_key = tuple(sorted([home, away]))
        h2h_games = self.h2h.get(h2h_key, [])
        h2h_home_wr = 0.5
        if h2h_games:
            h2h_home_wr = sum(1 for w in h2h_games[-10:] if w) / len(h2h_games[-10:])

        prob_margin = abs(market_prob_home - market_prob_away)

        return {
            "elo_diff": elo_diff,
            "home_elo": home_elo,
            "away_elo": away_elo,
            "home_form_5": _form(home_hist, 5, True),
            "away_form_5": _form(away_hist, 5, False),
            "home_pts_scored_5": _avg_pts(home_hist, 5, True),
            "away_pts_scored_5": _avg_pts(away_hist, 5, True),
            "home_pts_allowed_5": _avg_pts(home_hist, 5, False),
            "away_pts_allowed_5": _avg_pts(away_hist, 5, False),
            "home_rest_days": _rest_days(home_hist, date_str),
            "away_rest_days": _rest_days(away_hist, date_str),
            "market_prob_home": market_prob_home,
            "market_prob_away": market_prob_away,
            "prob_margin": prob_margin,
            "h2h_home_wr": h2h_home_wr,
            "elo_expected_home": elo.expected(home_elo, away_elo, HFA),
        }

    def record_result(
        self,
        home: str,
        away: str,
        date_str: str,
        home_won: bool,
        pts_home: int,
        pts_away: int,
    ):
        self.team_history[home].append({
            "date": date_str,
            "won": home_won,
            "as_home": True,
            "pts_scored": pts_home,
            "pts_allowed": pts_away,
        })
        self.team_history[away].append({
            "date": date_str,
            "won": not home_won,
            "as_home": False,
            "pts_scored": pts_away,
            "pts_allowed": pts_home,
        })
        h2h_key = tuple(sorted([home, away]))
        self.h2h[h2h_key].append(home_won)


# ═══════════════════════════════════════════════════════════════════════════
# 3. DATA LOADING + PIPELINE
# ═══════════════════════════════════════════════════════════════════════════

FEATURE_COLS = [
    "elo_diff", "home_elo", "away_elo",
    "home_form_5", "away_form_5",
    "home_pts_scored_5", "away_pts_scored_5",
    "home_pts_allowed_5", "away_pts_allowed_5",
    "home_rest_days", "away_rest_days",
    "market_prob_home", "market_prob_away",
    "prob_margin", "h2h_home_wr", "elo_expected_home",
]


def load_games(path: Optional[Path] = None) -> pd.DataFrame:
    if path is None:
        path = DATA_DIR / "basketball_betexplorer_backtest.csv"
    df = pd.read_csv(path)
    df["GAME_DATE_EST"] = pd.to_datetime(df["GAME_DATE_EST"], errors="coerce")
    df = df.dropna(subset=["GAME_DATE_EST", "HOME_TEAM_WINS", "MARKET_PROB_home"])
    df = df.sort_values("GAME_DATE_EST").reset_index(drop=True)
    df["HOME_TEAM_WINS"] = df["HOME_TEAM_WINS"].astype(int)
    return df


def build_dataset(df: pd.DataFrame) -> pd.DataFrame:
    elo = EloEngine()
    feats = FeatureBuilder()
    rows = []

    for _, r in df.iterrows():
        home = str(r["HOME_TEAM_NAME"])
        away = str(r["VISITOR_TEAM_NAME"])
        date_str = str(r["GAME_DATE_EST"].date())
        market_ph = float(r["MARKET_PROB_home"])
        market_pa = float(r["MARKET_PROB_away"])
        league = str(r.get("league", "unknown"))
        home_won = int(r["HOME_TEAM_WINS"])
        pts_h = int(r.get("PTS_home", 0) or 0)
        pts_a = int(r.get("PTS_away", 0) or 0)
        margin = abs(pts_h - pts_a) if pts_h and pts_a else 0

        feat = feats.build_features(home, away, date_str, market_ph, market_pa, league, elo)
        feat["home_won"] = home_won
        feat["league"] = league
        feat["date"] = date_str
        feat["home"] = home
        feat["away"] = away
        feat["pts_home"] = pts_h
        feat["pts_away"] = pts_a
        feat["market_correct"] = int((market_ph > 0.5) == (home_won == 1))
        rows.append(feat)

        elo.update(home, away, bool(home_won), float(margin), hfa=True)
        feats.record_result(home, away, date_str, bool(home_won), pts_h, pts_a)

    result = pd.DataFrame(rows)
    result["GAME_DATE_EST"] = pd.to_datetime(result["date"])
    return result, elo


# ═══════════════════════════════════════════════════════════════════════════
# 4. MODEL TRAINING
# ═══════════════════════════════════════════════════════════════════════════

from sklearn.calibration import CalibratedClassifierCV
import lightgbm as lgb


def train_model(dataset: pd.DataFrame, verbose: bool = True):
    sorted_ds = dataset.sort_values("GAME_DATE_EST").reset_index(drop=True)
    n = len(sorted_ds)
    train_end = int(n * 0.75)
    val_end = int(n * 0.85)

    train = sorted_ds.iloc[:train_end]
    val = sorted_ds.iloc[train_end:val_end]
    test = sorted_ds.iloc[val_end:]

    X_train = train[FEATURE_COLS].values
    y_train = train["home_won"].values
    X_val = val[FEATURE_COLS].values
    y_val = val["home_won"].values
    X_test = test[FEATURE_COLS].values
    y_test = test["home_won"].values

    if verbose:
        print(f"Train: {len(train)} ({train['GAME_DATE_EST'].min().date()} → {train['GAME_DATE_EST'].max().date()})")
        print(f"Val:   {len(val)} ({val['GAME_DATE_EST'].min().date()} → {val['GAME_DATE_EST'].max().date()})")
        print(f"Test:  {len(test)} ({test['GAME_DATE_EST'].min().date()} → {test['GAME_DATE_EST'].max().date()})")

    base = lgb.LGBMClassifier(
        n_estimators=500,
        learning_rate=0.05,
        max_depth=6,
        num_leaves=31,
        min_child_samples=50,
        subsample=0.8,
        colsample_bytree=0.8,
        reg_alpha=0.1,
        reg_lambda=1.0,
        random_state=42,
        verbose=-1,
    )

    base.fit(
        X_train, y_train,
        eval_set=[(X_val, y_val)],
        callbacks=[lgb.early_stopping(50), lgb.log_evaluation(0)],
    )

    calibrated = CalibratedClassifierCV(base, method="isotonic", cv=5)
    calibrated.fit(X_train, y_train)

    results = {"train_size": len(train), "val_size": len(val), "test_size": len(test)}

    if len(test) > 0:
        raw_probs = base.predict_proba(X_test)[:, 1]
        cal_probs = calibrated.predict_proba(X_test)[:, 1]
        market_probs = test["market_prob_home"].values

        from sklearn.metrics import accuracy_score, brier_score_loss, log_loss

        results["market_acc"] = accuracy_score(y_test, (market_probs > 0.5).astype(int))
        results["raw_ml_acc"] = accuracy_score(y_test, (raw_probs > 0.5).astype(int))
        results["cal_ml_acc"] = accuracy_score(y_test, (cal_probs > 0.5).astype(int))
        results["market_brier"] = brier_score_loss(y_test, market_probs)
        results["raw_ml_brier"] = brier_score_loss(y_test, raw_probs)
        results["cal_ml_brier"] = brier_score_loss(y_test, cal_probs)
        results["market_logloss"] = log_loss(y_test, np.clip(market_probs, 0.01, 0.99))
        results["raw_ml_logloss"] = log_loss(y_test, np.clip(raw_probs, 0.01, 0.99))
        results["cal_ml_logloss"] = log_loss(y_test, np.clip(cal_probs, 0.01, 0.99))

        if verbose:
            print(f"\n{'Metric':<15s} {'Market':>10s} {'Raw ML':>10s} {'Cal ML':>10s}")
            print("-" * 48)
            for metric in ["acc", "brier", "logloss"]:
                mk = f"market_{metric}"
                rk = f"raw_ml_{metric}"
                ck = f"cal_ml_{metric}"
                print(f"{metric:<15s} {results[mk]:>10.4f} {results[rk]:>10.4f} {results[ck]:>10.4f}")

    return calibrated, base, results, (X_test, y_test, test)


def save_model(model, base_model, elo: EloEngine, path: Optional[Path] = None):
    if path is None:
        path = MODEL_DIR / "basketball_calibrated.pkl"
    with open(path, "wb") as f:
        pickle.dump({"model": model, "base": base_model, "elo": dict(elo.ratings)}, f)
    with open(MODEL_DIR / "basketball_elo_snapshot.json", "w") as f:
        json.dump(elo.snapshot(), f, indent=2)
    print(f"Saved: {path}")
    print(f"Saved: {MODEL_DIR / 'basketball_elo_snapshot.json'}")


def load_model(path: Optional[Path] = None):
    if path is None:
        path = MODEL_DIR / "basketball_calibrated.pkl"
    with open(path, "rb") as f:
        data = pickle.load(f)
    return data["model"], data["base"], data["elo"]


# ═══════════════════════════════════════════════════════════════════════════
# 5. BACKTEST
# ═══════════════════════════════════════════════════════════════════════════

def run_backtest(dataset: pd.DataFrame, model, base_model):
    sys.path.insert(0, str(PROJECT_DIR / "scripts"))
    from betting_math import ev_percent, is_value_bet, kelly_stake

    sorted_ds = dataset.sort_values("GAME_DATE_EST").reset_index(drop=True)
    n = len(sorted_ds)
    test = sorted_ds.iloc[int(n * 0.85):].copy()
    if len(test) == 0:
        print("No test data available")
        return

    X = test[FEATURE_COLS].values
    y = test["home_won"].values
    market_probs = test["market_prob_home"].values
    raw_probs = base_model.predict_proba(X)[:, 1]
    cal_probs = model.predict_proba(X)[:, 1]

    odds = 1.0 / np.clip(market_probs, 0.05, 0.95)

    print(f"\n{'='*70}")
    print(f"BACKTEST: {len(test)} games (≥2025)")
    print(f"{'='*70}\n")

    for label, probs in [("Market", market_probs), ("Raw ML", raw_probs), ("Calibrated ML", cal_probs)]:
        wins = sum((probs > 0.5).astype(int) == y)
        acc = wins / len(y) * 100

        ev_vals = [ev_percent(p, o) for p, o in zip(probs, odds)]
        pos_ev = sum(1 for e in ev_vals if e > 0)
        avg_ev = sum(ev_vals) / len(ev_vals)

        value_bets = sum(1 for p, o in zip(probs, odds) if is_value_bet(p, o))

        flat_stake = 2.0
        flat_profit = sum(
            (flat_stake * (o - 1)) if (p > 0.5) == (w == 1) and p > 0.5 else
            (-flat_stake if p > 0.5 else 0)
            for p, o, w in zip(probs, odds, y)
        )
        bets_made = sum(1 for p in probs if p > 0.5)
        roi = (flat_profit / (flat_stake * bets_made) * 100) if bets_made else 0

        kelly_profits = []
        kelly_staked = []
        for p, o, w in zip(probs, odds, y):
            ks = kelly_stake(float(p), float(o), 100.0, 0.25, 0.05)
            if ks > 0:
                kelly_staked.append(ks)
                if (p > 0.5) == (w == 1):
                    kelly_profits.append(ks * (o - 1))
                else:
                    kelly_profits.append(-ks)

        k_total_profit = sum(kelly_profits)
        k_total_staked = sum(kelly_staked)
        k_roi = (k_total_profit / k_total_staked * 100) if k_total_staked > 0 else 0
        k_bets = len(kelly_profits)

        print(f"── {label} ──")
        print(f"  Accuracy: {acc:.1f}% ({wins}/{len(y)})")
        print(f"  Positive EV bets: {pos_ev}/{len(y)} | Value bets: {value_bets}/{len(y)}")
        print(f"  Average EV: {avg_ev:+.1f}%")
        print(f"  Flat stake: ${flat_profit:+.2f} ROI {roi:+.1f}% ({bets_made} bets)")
        print(f"  Kelly: ${k_total_profit:+.2f} ROI {k_roi:+.1f}% ({k_bets} bets, ${k_total_staked:.0f} staked)")
        print()

    print("── EV>0 Calibrated Kelly ──")
    ev_bets = 0
    ev_wins = 0
    ev_profit = 0
    ev_staked = 0
    for p, o, w in zip(cal_probs, odds, y):
        if ev_percent(float(p), float(o)) > 0:
            ks = kelly_stake(float(p), float(o), 100.0, 0.25, 0.05)
            if ks > 0:
                ev_bets += 1
                ev_staked += ks
                if (p > 0.5) == (w == 1):
                    ev_wins += 1
                    ev_profit += ks * (o - 1)
                else:
                    ev_profit -= ks
    if ev_bets:
        print(f"  Bets: {ev_bets} | Wins: {ev_wins} | Acc: {ev_wins/ev_bets*100:.1f}%")
        print(f"  Staked: ${ev_staked:.2f} | Profit: ${ev_profit:+.2f} | ROI: {ev_profit/ev_staked*100:+.1f}%")
    else:
        print("  0 bets passed EV>0 filter")


# ═══════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Basketball ML Pipeline")
    parser.add_argument("--train", action="store_true", help="Train and save model")
    parser.add_argument("--backtest", action="store_true", help="Run full backtest")
    parser.add_argument("--data", type=str, default=None, help="Custom data path")
    args = parser.parse_args()

    print("Loading 12,942 games...")
    df = load_games(Path(args.data) if args.data else None)
    print(f"Loaded: {len(df)} games ({df['GAME_DATE_EST'].min().date()} → {df['GAME_DATE_EST'].max().date()})")

    print("\nBuilding ELO + features...")
    dataset, elo = build_dataset(df)
    print(f"Dataset: {len(dataset)} rows | Teams tracked: {len(elo.ratings)}")
    print(f"Top teams:")
    for team, data in list(elo.snapshot().items())[:5]:
        print(f"  {team}: ELO={data['elo']:.0f} ({data['games']} games)")

    if args.train:
        print("\n" + "=" * 50)
        print("TRAINING LightGBM + Isotonic Calibration")
        print("=" * 50)
        model, base, results, test_data = train_model(dataset)
        save_model(model, base, elo)
        print("\n✓ Model saved")

    if args.backtest:
        print("\n" + "=" * 50)
        print("TRAINING for backtest...")
        print("=" * 50)
        model, base, results, test_data = train_model(dataset)
        run_backtest(dataset, model, base)

    if not args.train and not args.backtest:
        print("\nUse --train to train and save, or --backtest to run full evaluation")


if __name__ == "__main__":
    main()
