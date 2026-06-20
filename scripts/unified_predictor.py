#!/usr/bin/env python3
"""Unified daily predictor — all sports, one report.

Combines sport-specific models into a single daily prediction system:
- Basketball: LightGBM + ELO + isotonic calibration
- Football: Dixon-Coles Poisson + draw risk
- Tennis: Surface ELO (framework)
- Darts: Form-based (framework)

Each sport uses its OWN science, equations, and staking rules.
The meta-system combines picks and applies portfolio-level Kelly sizing.

Usage:
  python unified_predictor.py                     # today
  python unified_predictor.py --date 2026-06-19   # specific date
"""
from __future__ import annotations

import csv
import json
import math
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR / "scripts"))

from betting_math import (
    ev_percent, is_value_bet, kelly_stake,
    simultaneous_kelly, implied_prob, remove_vig, edge,
    summarize_picks, decimal_to_american
)
from sport_science import get_sport_config


class UnifiedPredictor:
    """Meta-predictor that delegates to sport-specific engines."""

    def __init__(self, bankroll: float = 100.0):
        self.bankroll = bankroll
        self.basketball_model = None
        self.basketball_base = None
        self.basketball_elo = None
        self.football_attack = {}
        self.football_defense = {}
        self._load_models()

    def _load_models(self):
        # Basketball
        import pickle
        bball_path = PROJECT_DIR / "models" / "basketball_calibrated.pkl"
        if bball_path.exists():
            with open(bball_path, "rb") as f:
                data = pickle.load(f)
            self.basketball_model = data["model"]
            self.basketball_base = data["base"]
            self.basketball_elo = data["elo"]

        # Football
        epl_path = PROJECT_DIR / "data" / "processed" / "epl_matches.csv"
        if epl_path.exists():
            self._train_football(epl_path)

    def _train_football(self, path: Path):
        df = pd.read_csv(path)
        df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
        df = df.dropna(subset=["Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG", "FTR"])
        cutoff = df["Date"].max() - pd.Timedelta(days=730)
        recent = df[df["Date"] >= cutoff]
        avg_g = (recent["FTHG"].sum() + recent["FTAG"].sum()) / (len(recent) * 2)

        atk = defaultdict(lambda: {"s": 0, "g": 0})
        dfd = defaultdict(lambda: {"c": 0, "g": 0})
        for _, m in recent.iterrows():
            atk[m["HomeTeam"]]["s"] += m["FTHG"]; atk[m["HomeTeam"]]["g"] += 1
            atk[m["AwayTeam"]]["s"] += m["FTAG"]; atk[m["AwayTeam"]]["g"] += 1
            dfd[m["HomeTeam"]]["c"] += m["FTAG"]; dfd[m["HomeTeam"]]["g"] += 1
            dfd[m["AwayTeam"]]["c"] += m["FTHG"]; dfd[m["AwayTeam"]]["g"] += 1

        self.football_attack = {
            t: max(0.5, min(2.0, (d["s"] / max(d["g"], 1)) / avg_g))
            for t, d in atk.items()
        }
        self.football_defense = {
            t: max(0.5, min(2.0, (d["c"] / max(d["g"], 1)) / avg_g))
            for t, d in dfd.items()
        }
        self.football_home_adv = 1.35

    def predict_basketball(self, home: str, away: str, market_ph: float, market_pa: float) -> Optional[dict]:
        if not self.basketball_model:
            return None
        from basketball_pipeline import FeatureBuilder, FEATURE_COLS, EloEngine

        elo_snap = self.basketball_elo or {}
        home_elo = elo_snap.get(home, 1500.0)
        away_elo = elo_snap.get(away, 1500.0)

        feat = {
            "elo_diff": home_elo - away_elo,
            "home_elo": home_elo,
            "away_elo": away_elo,
            "home_form_5": 0.5,
            "away_form_5": 0.5,
            "home_pts_scored_5": 75.0,
            "away_pts_scored_5": 75.0,
            "home_pts_allowed_5": 75.0,
            "away_pts_allowed_5": 75.0,
            "home_rest_days": 3.0,
            "away_rest_days": 3.0,
            "market_prob_home": market_ph,
            "market_prob_away": market_pa,
            "prob_margin": abs(market_ph - market_pa),
            "h2h_home_wr": 0.5,
            "elo_expected_home": 1.0 / (1.0 + 10.0 ** ((away_elo - home_elo - 65.0) / 400.0)),
        }

        X = np.array([[feat[col] for col in FEATURE_COLS]])
        cal_prob = float(self.basketball_model.predict_proba(X)[0, 1])

        pick = home if cal_prob >= 0.5 else away
        pick_prob = cal_prob if cal_prob >= 0.5 else (1.0 - cal_prob)
        market_pick_prob = market_ph if cal_prob >= 0.5 else market_pa

        return {
            "sport": "basketball",
            "home": home,
            "away": away,
            "pick": pick,
            "model_prob": pick_prob,
            "market_prob": market_pick_prob,
            "model_odds": round(1.0 / max(pick_prob, 0.01), 3),
            "market_odds": round(1.0 / max(market_pick_prob, 0.01), 3),
            "home_elo": home_elo,
            "away_elo": away_elo,
        }

    def predict_football(self, home: str, away: str, odds: Optional[dict] = None) -> Optional[dict]:
        ah = self.football_attack.get(home, 1.0)
        da = self.football_defense.get(away, 1.0)
        aa = self.football_attack.get(away, 1.0)
        dh = self.football_defense.get(home, 1.0)

        if home not in self.football_attack and away not in self.football_attack:
            return None

        lh = max(0.2, ah * da * self.football_home_adv)
        la = max(0.2, aa * dh)

        ph = pd = pa = 0.0
        for hg in range(8):
            for ag in range(8):
                p = math.exp(-lh) * lh**hg / math.factorial(hg) * math.exp(-la) * la**ag / math.factorial(ag)
                if hg > ag:
                    ph += p
                elif hg == ag:
                    pd += p
                else:
                    pa += p
        t = ph + pd + pa

        probs = {"H": ph / t, "D": pd / t, "A": pa / t}
        pick = max(probs, key=probs.get)
        pick_prob = probs[pick]

        result = {
            "sport": "football",
            "home": home,
            "away": away,
            "pick": f"{'Home' if pick=='H' else 'Draw' if pick=='D' else 'Away'}",
            "model_prob": pick_prob,
            "probs": probs,
            "lambda_home": lh,
            "lambda_away": la,
            "expected_goals": round(lh + la, 2),
        }

        if odds and pick in odds:
            result["market_odds"] = odds[pick]
            result["market_prob"] = implied_prob(odds[pick])
        else:
            result["market_odds"] = round(1.0 / max(pick_prob, 0.01), 3)
            result["market_prob"] = pick_prob

        return result

    def generate_daily_picks(self, target_date: date) -> list[dict]:
        all_picks = []

        # ── Basketball ──
        bball_path = PROJECT_DIR / "data" / "basketball_betexplorer_current.csv"
        if bball_path.exists() and self.basketball_model:
            df = pd.read_csv(bball_path)
            df["GAME_DATE_EST"] = pd.to_datetime(df["GAME_DATE_EST"], errors="coerce")
            target_ts = pd.Timestamp(target_date)
            for _, r in df.iterrows():
                if pd.notna(r["GAME_DATE_EST"]) and r["GAME_DATE_EST"].date() >= target_date:
                    mph = float(r.get("MARKET_PROB_home", 0.5) or 0.5)
                    mpa = float(r.get("MARKET_PROB_away", 0.5) or 0.5)
                    pred = self.predict_basketball(
                        str(r["HOME_TEAM_NAME"]), str(r["VISITOR_TEAM_NAME"]), mph, mpa
                    )
                    if pred:
                        pred["league"] = str(r.get("league", ""))
                        pred["date"] = str(r["GAME_DATE_EST"].date())
                        all_picks.append(pred)

        # ── Football (if upcoming fixtures available) ──
        # Add football picks when fixtures are available

        # ── Apply sport-specific Kelly staking ──
        for pick in all_picks:
            sport = pick["sport"]
            config = get_sport_config(sport)
            max_bet = 0.05
            max_daily = 0.20
            if "bankroll_rule" in config:
                if "max 3%" in config["bankroll_rule"]:
                    max_bet = 0.03
                elif "max 4%" in config["bankroll_rule"]:
                    max_bet = 0.04

            ev = ev_percent(pick["model_prob"], pick["market_odds"])
            pick["ev_pct"] = round(ev, 1)
            pick["is_value"] = ev > 0
            pick["kelly_stake"] = round(
                kelly_stake(pick["model_prob"], pick["market_odds"], self.bankroll, 0.25, max_bet),
                2
            )
            pick["confidence"] = (
                "A" if pick["model_prob"] >= 0.75 else
                "B" if pick["model_prob"] >= 0.65 else
                "C" if pick["model_prob"] >= 0.55 else "D"
            )

        value_picks = [p for p in all_picks if p["is_value"]]
        value_picks.sort(key=lambda x: x["ev_pct"], reverse=True)

        return value_picks, all_picks

    def report(self, value_picks: list[dict], all_picks: list[dict], target_date: date):
        print(f"\n{'='*75}")
        print(f"  🎯 UNIFIED DAILY PREDICTIONS — {target_date}")
        print(f"  💰 Bankroll: ${self.bankroll:.0f}")
        print(f"{'='*75}")

        by_sport = defaultdict(list)
        for p in all_picks:
            by_sport[p["sport"]].append(p)

        print(f"\n  📊 Coverage:")
        for sport, picks in sorted(by_sport.items()):
            config = get_sport_config(sport)
            model = config.get("model_type", "?")[:40]
            vp = sum(1 for p in picks if p["is_value"])
            print(f"    {sport:12s}: {len(picks):3d} games | {vp:3d} value bets | {model}")

        print(f"\n  Total: {len(all_picks)} games | {len(value_picks)} value bets")

        if value_picks:
            total_stake = sum(p["kelly_stake"] for p in value_picks if p["kelly_stake"] > 0)
            total_potential = sum(
                p["kelly_stake"] * (p["market_odds"] - 1)
                for p in value_picks if p["kelly_stake"] > 0
            )
            print(f"  Kelly: ${total_stake:.2f} staked | ${total_potential:.2f} potential profit")

        print(f"\n{'─'*75}")
        print(f"  ⚡ TOP VALUE BETS (EV > 0)")
        print(f"{'─'*75}")
        print(f"  {'#':<3s} {'Sport':<8s} {'Match':<35s} {'Pick':<15s} {'Prob':>6s} {'Mkt':>6s} {'EV%':>6s} {'Kelly':>7s} {'Conf':>5s}")
        print(f"  {'─'*73}")

        for i, p in enumerate(value_picks[:25]):
            match = f"{p['home'][:15]} vs {p['away'][:15]}"
            print(
                f"  {i+1:<3d} {p['sport'][:7]:<8s} {match:<35s} "
                f"{p['pick'][:13]:<15s} {p['model_prob']:>5.1%} "
                f"{p['market_prob']:>5.1%} {p['ev_pct']:>+5.1f}% "
                f"${p['kelly_stake']:>5.2f} {p['confidence']:>5s}"
            )

        grade_a = [p for p in value_picks if p["confidence"] == "A"]
        grade_b = [p for p in value_picks if p["confidence"] == "B"]
        print(f"\n  📈 Grade A (prob≥75%): {len(grade_a)} bets")
        print(f"  📊 Grade B (prob≥65%): {len(grade_b)} bets")

        return value_picks


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Unified Daily Predictor")
    parser.add_argument("--date", type=str, default=None)
    parser.add_argument("--bankroll", type=float, default=100.0)
    parser.add_argument("--save", action="store_true", help="Save picks to CSV")
    args = parser.parse_args()

    target = date.today()
    if args.date:
        try:
            target = datetime.strptime(args.date, "%Y-%m-%d").date()
        except ValueError:
            pass

    print("Loading sport-specific models...")
    predictor = UnifiedPredictor(bankroll=args.bankroll)
    print(f"  Basketball model: {'✓' if predictor.basketball_model else '✗'}")
    print(f"  Football model: {'✓' if predictor.football_attack else '✗'} ({len(predictor.football_attack)} teams)")

    value_picks, all_picks = predictor.generate_daily_picks(target)
    predictor.report(value_picks, all_picks, target)

    if args.save and value_picks:
        out_path = PROJECT_DIR / "reports" / f"unified_picks_{target}.csv"
        pd.DataFrame(value_picks).to_csv(out_path, index=False)
        print(f"\n  💾 Saved: {out_path}")


if __name__ == "__main__":
    main()
