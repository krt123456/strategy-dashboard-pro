#!/usr/bin/env python3
"""الوكيل اليومي المستقل — يجمع، يسجّل، يراقب، يقارن.

يعمل كل يوم تلقائياً:
1. يحمّل توقعات نموذجنا (LightGBM)
2. يحاول جلب توقعات من مصادر خارجية (APIs)
3. يسجّل كل توقع في betting_journal.db
4. يفحص نتائج الأمس
5. يحدّث الأداء لكل مصدر واستراتيجية
6. ينتج تقرير يومي

بعد 30 يوم: نفحص الأداء ونقرر الأفضل.
"""
from __future__ import annotations

import csv
import json
import sqlite3
import sys
import math
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR / "scripts"))

from betting_journal import (
    init_db, add_prediction, add_result,
    get_unresolved_predictions, performance_report,
    register_source, register_strategy
)
from betting_math import ev_percent, kelly_stake, implied_prob


# ═══════════════════════════════════════════════════════════════════════════
# SOURCE 1: Our LightGBM Model
# ═══════════════════════════════════════════════════════════════════════════

def fetch_our_model_predictions(target_date: date) -> list[dict]:
    import pickle
    model_path = PROJECT_DIR / "models" / "basketball_calibrated.pkl"
    if not model_path.exists():
        return []

    with open(model_path, "rb") as f:
        data = pickle.load(f)
    model = data["model"]
    elo_snap = data["elo"]

    FEATURE_COLS = [
        "elo_diff", "home_elo", "away_elo",
        "home_form_5", "away_form_5",
        "home_pts_scored_5", "away_pts_scored_5",
        "home_pts_allowed_5", "away_pts_allowed_5",
        "home_rest_days", "away_rest_days",
        "market_prob_home", "market_prob_away",
        "prob_margin", "h2h_home_wr", "elo_expected_home",
    ]

    bball_path = PROJECT_DIR / "data" / "basketball_betexplorer_current.csv"
    if not bball_path.exists():
        return []

    df = pd.read_csv(bball_path)
    df["GAME_DATE_EST"] = pd.to_datetime(df["GAME_DATE_EST"], errors="coerce")

    picks = []
    for _, r in df.iterrows():
        if pd.isna(r["GAME_DATE_EST"]):
            continue
        match_date = r["GAME_DATE_EST"].date()
        if match_date < target_date:
            continue

        home = str(r["HOME_TEAM_NAME"])
        away = str(r["VISITOR_TEAM_NAME"])
        mph = float(r.get("MARKET_PROB_home", 0.5) or 0.5)
        mpa = float(r.get("MARKET_PROB_away", 0.5) or 0.5)
        league = str(r.get("league", ""))

        he = elo_snap.get(home, 1500.0)
        ae = elo_snap.get(away, 1500.0)

        feat = {
            "elo_diff": he - ae, "home_elo": he, "away_elo": ae,
            "home_form_5": 0.5, "away_form_5": 0.5,
            "home_pts_scored_5": 75.0, "away_pts_scored_5": 75.0,
            "home_pts_allowed_5": 75.0, "away_pts_allowed_5": 75.0,
            "home_rest_days": 3.0, "away_rest_days": 3.0,
            "market_prob_home": mph, "market_prob_away": mpa,
            "prob_margin": abs(mph - mpa), "h2h_home_wr": 0.5,
            "elo_expected_home": 1.0 / (1.0 + 10.0 ** ((ae - he - 65.0) / 400.0)),
        }

        X = np.array([[feat[c] for c in FEATURE_COLS]])
        cal_prob = float(model.predict_proba(X)[0, 1])

        pick_home = cal_prob >= 0.5
        pick = home if pick_home else away
        model_prob = cal_prob if pick_home else (1.0 - cal_prob)
        be_odds = 1.0 / max(mph if pick_home else mpa, 0.01)

        confidence = "A" if model_prob >= 0.75 else "B" if model_prob >= 0.65 else "C" if model_prob >= 0.55 else "D"

        for strat_name, min_p, min_o, max_o, min_ev in [
            ("conservative", 0.70, 1.35, 2.20, 2.0),
            ("balanced", 0.60, 1.40, 2.50, 3.0),
            ("aggressive", 0.55, 1.50, 3.00, 5.0),
        ]:
            ev = ev_percent(model_prob, be_odds)
            if model_prob >= min_p and be_odds >= min_o and be_odds <= max_o:
                picks.append({
                    "match_date": str(match_date),
                    "sport": "basketball",
                    "league": league,
                    "home": home,
                    "away": away,
                    "pick": pick,
                    "source": "our_lightgbm",
                    "model_prob": round(model_prob, 4),
                    "odds_at_prediction": round(be_odds, 2),
                    "ev_pct": round(ev, 1),
                    "strategy": strat_name,
                    "confidence": confidence,
                    "notes": f"ELO: {he:.0f} vs {ae:.0f}",
                })

    return picks


# ═══════════════════════════════════════════════════════════════════════════
# SOURCE 2: The Odds API (if key available)
# ═══════════════════════════════════════════════════════════════════════════

def fetch_odds_api_predictions(target_date: date, api_key: str = None) -> list[dict]:
    import requests

    if not api_key:
        env_key = PROJECT_DIR / ".env"
        if env_key.exists():
            for line in env_key.read_text().splitlines():
                if line.startswith("ODDS_API_KEY="):
                    api_key = line.split("=", 1)[1].strip()
                    break

    if not api_key:
        return []

    picks = []
    sports_to_check = [
        ("basketball_nba", "basketball"),
        ("basketball_wnba", "basketball"),
        ("basketball_euroleague", "basketball"),
        ("soccer_epl", "football"),
        ("tennis_atp", "tennis"),
        ("tennis_wta", "tennis"),
    ]

    for sport_key, sport_name in sports_to_check:
        try:
            url = f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds"
            params = {
                "apiKey": api_key,
                "regions": "eu",
                "markets": "h2h",
                "oddsFormat": "decimal",
            }
            r = requests.get(url, params=params, timeout=10)
            if r.status_code != 200:
                continue

            events = r.json()
            for event in events[:50]:
                home = event.get("home_team", "")
                away = event.get("away_team", "")
                commence = event.get("commence_time", "")[:10]

                best_odds = {}
                for bookmaker in event.get("bookmakers", []):
                    for market in bookmaker.get("markets", []):
                        for outcome in market.get("outcomes", []):
                            name = outcome["name"]
                            price = outcome["price"]
                            if name not in best_odds or price > best_odds[name]:
                                best_odds[name] = price

                if home in best_odds and away in best_odds:
                    h_odds = best_odds[home]
                    a_odds = best_odds[away]
                    h_prob = 1.0 / h_odds
                    a_prob = 1.0 / a_odds
                    total = h_prob + a_prob
                    h_prob_norm = h_prob / total
                    a_prob_norm = a_prob / total

                    pick = home if h_prob_norm > a_prob_norm else away
                    pick_prob = max(h_prob_norm, a_prob_norm)
                    pick_odds = max(h_odds, a_odds)

                    if pick_prob >= 0.55:
                        picks.append({
                            "match_date": commence,
                            "sport": sport_name,
                            "league": sport_key,
                            "home": home,
                            "away": away,
                            "pick": pick,
                            "source": "the_odds_api",
                            "model_prob": round(pick_prob, 4),
                            "odds_at_prediction": round(pick_odds, 2),
                            "ev_pct": 0.0,
                            "strategy": "market_consensus",
                            "confidence": "A" if pick_prob >= 0.75 else "B" if pick_prob >= 0.65 else "C",
                            "notes": f"Best odds from {len(event.get('bookmakers',[]))} books",
                        })
        except Exception as e:
            continue

    return picks


# ═══════════════════════════════════════════════════════════════════════════
# SOURCE 3: Market consensus from linefeed (real 1xBet events)
# ═══════════════════════════════════════════════════════════════════════════

def fetch_linefeed_predictions(target_date: date) -> list[dict]:
    lf_path = PROJECT_DIR / "data" / "one_xbet_linefeed_snapshot.csv"
    if not lf_path.exists():
        return []

    df = pd.read_csv(lf_path)
    picks = []

    for _, r in df.iterrows():
        home = str(r.get("Home", ""))
        away = str(r.get("Away", ""))
        sport = str(r.get("Sport", "")).lower()
        league = str(r.get("League", ""))

        h_odds = r.get("HomeOdds")
        a_odds = r.get("AwayOdds")

        if not h_odds or not a_odds or pd.isna(h_odds) or pd.isna(a_odds):
            continue

        try:
            h_odds = float(h_odds)
            a_odds = float(a_odds)
        except (ValueError, TypeError):
            continue

        if h_odds <= 1.0 or a_odds <= 1.0:
            continue

        h_prob = 1.0 / h_odds
        a_prob = 1.0 / a_odds

        d_odds = r.get("DrawOdds")
        if d_odds and not pd.isna(d_odds):
            try:
                d_odds = float(d_odds)
                if d_odds > 1.0:
                    d_prob = 1.0 / d_odds
                    total = h_prob + a_prob + d_prob
                else:
                    total = h_prob + a_prob
            except (ValueError, TypeError):
                total = h_prob + a_prob
        else:
            total = h_prob + a_prob

        h_prob_norm = h_prob / total
        a_prob_norm = a_prob / total

        pick = home if h_prob_norm > a_prob_norm else away
        pick_prob = max(h_prob_norm, a_prob_norm)
        pick_odds = h_odds if h_prob_norm > a_prob_norm else a_odds

        if pick_prob >= 0.55 and 1.35 <= pick_odds <= 3.00:
            ev = ev_percent(pick_prob, pick_odds)
            picks.append({
                "match_date": str(r.get("Date", target_date)),
                "sport": sport,
                "league": league,
                "home": home,
                "away": away,
                "pick": pick,
                "source": "xbet_linefeed",
                "model_prob": round(pick_prob, 4),
                "odds_at_prediction": round(pick_odds, 2),
                "ev_pct": round(ev, 1),
                "strategy": "market_consensus",
                "confidence": "A" if pick_prob >= 0.75 else "B" if pick_prob >= 0.65 else "C",
                "notes": f"Real 1xBet odds: {h_odds:.2f} / {a_odds:.2f}",
            })

    return picks


# ═══════════════════════════════════════════════════════════════════════════
# DAILY AGENT
# ═══════════════════════════════════════════════════════════════════════════

def run_daily_agent(target_date: date = None, bankroll: float = 100.0):
    if target_date is None:
        target_date = date.today()

    init_db()

    log_lines = []
    def log(msg):
        print(msg)
        log_lines.append(msg)

    log(f"\n{'='*70}")
    log(f"  🤖 الوكيل اليومي — {target_date}")
    log(f"{'='*70}")

    # ── 1. Collect predictions from all sources ──
    log(f"\n📖 جمع التوقعات من كل المصادر...")

    all_preds = []

    # Source 1: Our model
    our_preds = fetch_our_model_predictions(target_date)
    log(f"  • نموذجنا (LightGBM): {len(our_preds)} توقع")
    all_preds.extend(our_preds)

    # Source 2: The Odds API
    api_preds = fetch_odds_api_predictions(target_date)
    log(f"  • The Odds API: {len(api_preds)} توقع")
    all_preds.extend(api_preds)

    # Source 3: 1xBet linefeed
    lf_preds = fetch_linefeed_predictions(target_date)
    log(f"  • 1xBet linefeed: {len(lf_preds)} توقع")
    all_preds.extend(lf_preds)

    log(f"  الإجمالي: {len(all_preds)} توقع من {len(set(p['source'] for p in all_preds))} مصدر")

    # ── 2. Record in journal ──
    log(f"\n📝 تسجيل في betting_journal...")
    db = PROJECT_DIR / "data" / "betting_journal.db"
    conn = sqlite3.connect(db)
    c = conn.cursor()

    recorded = 0
    for p in all_preds:
        exists = c.execute("""
            SELECT 1 FROM predictions
            WHERE match_date=? AND home=? AND away=? AND pick=? AND source=? AND strategy=?
        """, (p["match_date"], p["home"], p["away"], p["pick"], p["source"], p.get("strategy"))).fetchone()

        if not exists:
            ks = kelly_stake(p["model_prob"], p["odds_at_prediction"], bankroll, 0.25, 0.05)
            add_prediction(
                match_date=p["match_date"],
                sport=p["sport"],
                home=p["home"],
                away=p["away"],
                pick=p["pick"],
                source=p["source"],
                model_prob=p["model_prob"],
                odds_at_prediction=p["odds_at_prediction"],
                kelly_stake=ks,
                ev_pct=p.get("ev_pct", 0),
                strategy=p.get("strategy"),
                confidence=p.get("confidence"),
                league=p.get("league"),
                notes=p.get("notes"),
            )
            recorded += 1

    conn.close()
    log(f"  مسجّل جديد: {recorded}")
    log(f"  مكرر (متجاهل): {len(all_preds) - recorded}")

    # ── 3. Check yesterday's results ──
    yesterday = str(target_date - timedelta(days=1))
    log(f"\n🔍 فحص نتائج {yesterday}...")
    unresolved = get_unresolved_predictions(yesterday)
    log(f"  توقعات غير محلولة حتى {yesterday}: {len(unresolved)}")

    # ── 4. Performance report ──
    log(f"\n📊 تقرير الأداء (آخر 30 يوم)...")
    report = performance_report(days=30)

    if report["sources"]:
        log(f"\n  حسب المصدر:")
        log(f"  {'المصدر':<20s} {'الرهانات':>8s} {'فوز':>5s} {'دقة':>7s} {'ربح':>10s} {'ROI':>8s}")
        log(f"  {'─'*60}")
        for src, stats in sorted(report["sources"].items(), key=lambda x: -x[1].get("profit", 0)):
            log(f"  {src:<20s} {stats['total']:>8d} {stats['wins']:>5d} {stats['accuracy']:>6.1f}% ${stats['profit']:>+8.2f} {stats['avg_roi']:>+7.1f}%")

    if report["strategies"]:
        log(f"\n  حسب الاستراتيجية:")
        log(f"  {'الاستراتيجية':<20s} {'الرهانات':>8s} {'فوز':>5s} {'دقة':>7s} {'ربح':>10s}")
        log(f"  {'─'*55}")
        for strat, stats in sorted(report["strategies"].items(), key=lambda x: -x[1].get("profit", 0)):
            log(f"  {strat:<20s} {stats['total']:>8d} {stats['wins']:>5d} {stats['accuracy']:>6.1f}% ${stats['profit']:>+8.2f}")

    if report["sports"]:
        log(f"\n  حسب الرياضة:")
        log(f"  {'الرياضة':<15s} {'الرهانات':>8s} {'فوز':>5s} {'دقة':>7s} {'ربح':>10s}")
        log(f"  {'─'*50}")
        for sport, stats in sorted(report["sports"].items(), key=lambda x: -x[1].get("profit", 0)):
            log(f"  {sport:<15s} {stats['total']:>8d} {stats['wins']:>5d} {stats['accuracy']:>6.1f}% ${stats['profit']:>+8.2f}")

    # ── 5. Save daily log ──
    log_path = PROJECT_DIR / "reports" / f"agent_daily_{target_date}.log"
    log_path.write_text("\n".join(log_lines))
    log(f"\n💾 سجل اليوم: {log_path}")

    log(f"\n{'='*70}")
    log(f"  ✓ الوكيل اكتمل — {target_date}")
    log(f"{'='*70}")

    return {"predictions_recorded": recorded, "unresolved": len(unresolved), "report": report}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="الوكيل اليومي المستقل")
    parser.add_argument("--date", type=str, default=None)
    parser.add_argument("--bankroll", type=float, default=100.0)
    args = parser.parse_args()

    target = date.today()
    if args.date:
        try:
            target = datetime.strptime(args.date, "%Y-%m-%d").date()
        except ValueError:
            pass

    run_daily_agent(target, args.bankroll)
