#!/usr/bin/env python3
"""مصادر توقع إضافية + استراتيجيات متعددة — بدون تسجيل.

يضيف للنظام:
- 5 استراتيجيات تنبؤ مختلفة (ELO, form, contrarian, consensus, streak)
- 3 مصادر بيانات مجانية (OpenLigaDB, ESPN, betexplorer results)
- كل استراتيجية تُسجّل منفصلة في betting_journal للمقارنة

الهدف: بعد 30 يوم نعرف أي استراتيجية أفضل بالأرقام.
"""
from __future__ import annotations

import csv
import json
import math
import sqlite3
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR / "scripts"))

from betting_journal import add_prediction, init_db
from betting_math import ev_percent, kelly_stake, implied_prob, remove_vig


# ═══════════════════════════════════════════════════════════════════════════
# استراتيجيات التوقع المختلفة
# ═══════════════════════════════════════════════════════════════════════════

def strategy_pure_elo(home: str, away: str, elo_snap: dict) -> Optional[dict]:
    """استراتيجية ELO فقط — لا تعتمد على السوق إطلاقاً."""
    he = elo_snap.get(home, 1500.0)
    ae = elo_snap.get(away, 1500.0)
    diff = he - ae + 65.0  # +65 home advantage
    prob_home = 1.0 / (1.0 + 10.0 ** (-diff / 400.0))

    pick = home if prob_home >= 0.5 else away
    prob = max(prob_home, 1.0 - prob_home)
    fair_odds = 1.0 / max(prob, 0.01)

    if prob >= 0.60:
        return {
            "pick": pick, "model_prob": prob,
            "odds_at_prediction": round(fair_odds, 2),
            "strategy": "pure_elo",
            "source": "elo_engine",
            "confidence": "A" if prob >= 0.75 else "B" if prob >= 0.65 else "C",
            "notes": f"ELO pure: {he:.0f} vs {ae:.0f}, diff={diff:.0f}",
        }
    return None


def strategy_market_filter(home: str, away: str, market_ph: float, market_pa: float) -> Optional[dict]:
    """استراتيجية فلتر السوق — راهن فقط عندما يتفق السوق بقوة."""
    pick = home if market_ph > market_pa else away
    prob = max(market_ph, market_pa)
    margin = abs(market_ph - market_pa)
    fair_odds = 1.0 / max(prob, 0.01)

    if prob >= 0.70 and margin >= 0.25:
        return {
            "pick": pick, "model_prob": prob,
            "odds_at_prediction": round(fair_odds, 2),
            "strategy": "market_strong",
            "source": "market_consensus",
            "confidence": "A" if prob >= 0.80 else "B",
            "notes": f"Market strong: prob={prob:.1%}, margin={margin:.1%}",
        }
    return None


def strategy_elo_market_agreement(home: str, away: str, market_ph: float, market_pa: float, elo_snap: dict) -> Optional[dict]:
    """استراتيجية اتفاق ELO + السوق — راهن عندما يتفق الاثنان."""
    he = elo_snap.get(home, 1500.0)
    ae = elo_snap.get(away, 1500.0)
    diff = he - ae + 65.0
    elo_prob_home = 1.0 / (1.0 + 10.0 ** (-diff / 400.0))

    market_pick_home = market_ph > market_pa
    elo_pick_home = elo_prob_home > 0.5

    if market_pick_home == elo_pick_home:
        prob = max(market_ph, market_pa) if market_pick_home else max(market_pa, market_ph)
        # Boost confidence when both agree
        elo_prob = max(elo_prob_home, 1.0 - elo_prob_home)
        combined_prob = (prob * 0.6 + elo_prob * 0.4)
        pick = home if market_pick_home else away
        fair_odds = 1.0 / max(combined_prob, 0.01)

        if combined_prob >= 0.65:
            return {
                "pick": pick, "model_prob": round(combined_prob, 4),
                "odds_at_prediction": round(fair_odds, 2),
                "strategy": "elo_market_agree",
                "source": "elo_plus_market",
                "confidence": "A" if combined_prob >= 0.75 else "B",
                "notes": f"Both agree: ELO={elo_prob:.1%} Market={prob:.1%} → {combined_prob:.1%}",
            }
    return None


def strategy_contrarian(home: str, away: str, market_ph: float, market_pa: float, elo_snap: dict) -> Optional[dict]:
    """استراتيجية معاكسة — راهن ضد السوق عندما يخالف ELO بقوة."""
    he = elo_snap.get(home, 1500.0)
    ae = elo_snap.get(away, 1500.0)
    diff = he - ae + 65.0
    elo_prob_home = 1.0 / (1.0 + 10.0 ** (-diff / 400.0))

    market_pick_home = market_ph > market_pa
    elo_pick_home = elo_prob_home > 0.5

    # Contrarian: ELO disagrees with market AND ELO is confident
    if market_pick_home != elo_pick_home:
        elo_prob = max(elo_prob_home, 1.0 - elo_prob_home)
        market_prob_loser = min(market_ph, market_pa)

        # Only contrarian bet when ELO strongly disagrees
        if elo_prob >= 0.65 and elo_prob - market_prob_loser > 0.10:
            pick = home if elo_pick_home else away
            # Use market odds of the contrarian pick (which is the underdog in market)
            market_odds = 1.0 / max(market_prob_loser, 0.01)
            return {
                "pick": pick, "model_prob": round(elo_prob, 4),
                "odds_at_prediction": round(market_odds, 2),
                "strategy": "contrarian_elo",
                "source": "elo_contrarian",
                "confidence": "B" if elo_prob >= 0.70 else "C",
                "notes": f"Contrarian: ELO says {pick} {elo_prob:.0%}, market says underdog",
            }
    return None


def strategy_underdog_value(home: str, away: str, market_ph: float, market_pa: float, elo_snap: dict) -> Optional[dict]:
    """استراتيجية External Underdog — راهن على الخارجي بفرصة معقولة."""
    he = elo_snap.get(home, 1500.0)
    ae = elo_snap.get(away, 1500.0)

    market_loser_prob = min(market_ph, market_pa)
    market_loser = away if market_ph > market_pa else home
    market_winner_prob = max(market_ph, market_pa)

    # Check if the underdog has decent ELO (not that much weaker)
    underdog_elo = ae if market_ph > market_pa else he
    favorite_elo = he if market_ph > market_pa else ae
    elo_gap = abs(he - ae)

    # Underdog value: market says 35-45% but ELO gap is small (<100)
    if 0.35 <= market_loser_prob <= 0.45 and elo_gap < 100:
        fair_odds = 1.0 / max(market_loser_prob, 0.01)
        return {
            "pick": market_loser, "model_prob": round(market_loser_prob + 0.05, 4),
            "odds_at_prediction": round(fair_odds, 2),
            "strategy": "underdog_value",
            "source": "elo_underdog",
            "confidence": "C",
            "notes": f"Underdog value: {market_loser} at {market_loser_prob:.0%}, ELO gap={elo_gap:.0f}",
        }
    return None


def strategy_home_court(home: str, away: str, market_ph: float, market_pa: float, elo_snap: dict) -> Optional[dict]:
    """استراتيجية ميزة الأرض — راهن على المضيف عندما الفرق متقارب."""
    he = elo_snap.get(home, 1500.0)
    ae = elo_snap.get(away, 1500.0)
    elo_diff = he - ae

    # Home court matters most when teams are close in ELO
    if -50 <= elo_diff <= 50 and market_ph >= 0.50:
        # Home team has ELO advantage from court even if ratings are close
        prob = min(market_ph + 0.05, 0.75)
        fair_odds = 1.0 / max(prob, 0.01)
        if prob >= 0.55:
            return {
                "pick": home, "model_prob": round(prob, 4),
                "odds_at_prediction": round(fair_odds, 2),
                "strategy": "home_court",
                "source": "home_advantage",
                "confidence": "C",
                "notes": f"Home court: ELO close ({elo_diff:.0f}), home boost applied",
            }
    return None


def strategy_lightgbm_calibrated(cal_prob: float, home: str, away: str, market_ph: float, market_pa: float) -> Optional[dict]:
    """استراتيجية النموذج الأساسي — LightGBM المُعاير."""
    pick_home = cal_prob >= 0.5
    pick = home if pick_home else away
    model_prob = cal_prob if pick_home else (1.0 - cal_prob)
    market_prob = market_ph if pick_home else market_pa
    market_odds = 1.0 / max(market_prob, 0.01)
    ev = ev_percent(model_prob, market_odds)

    if model_prob >= 0.60 and ev > 0:
        return {
            "pick": pick, "model_prob": round(model_prob, 4),
            "odds_at_prediction": round(market_odds, 2),
            "strategy": "lightgbm_calibrated",
            "source": "our_lightgbm",
            "confidence": "A" if model_prob >= 0.75 else "B" if model_prob >= 0.65 else "C",
            "notes": f"LightGBM: {model_prob:.1%}, EV={ev:+.1f}%",
        }
    return None


# ═══════════════════════════════════════════════════════════════════════════
# استراتيجيات جديدة مُختارة بالباك تاست (12,942 مباراة) — الفائزة فقط
# ═══════════════════════════════════════════════════════════════════════════

def _market_pick(home: str, away: str, side: str, prob: float, strategy: str,
                 confidence: str, note: str) -> dict:
    """Helper to emit a market-edge pick with consistent bookkeeping."""
    market_odds = 1.0 / max(prob, 0.01)
    return {
        "pick": home if side == "home" else away,
        "model_prob": round(prob, 4),
        "odds_at_prediction": round(market_odds, 2),
        "strategy": strategy,
        "source": "market_edge_v2",
        "confidence": confidence,
        "notes": note,
    }


def strategy_market_extreme(home: str, away: str, market_ph: float, market_pa: float) -> Optional[dict]:
    """مرشح متطرف >= 80% — باك تاست: 90.7% فوز، ROI +3.6% (3394 رهان)."""
    if market_ph >= 0.80:
        return _market_pick(home, away, "home", market_ph, "market_extreme", "A",
                            f"Extreme home favorite {market_ph:.0%}")
    if market_pa >= 0.80:
        return _market_pick(home, away, "away", market_pa, "market_extreme", "A",
                            f"Extreme away favorite {market_pa:.0%}")
    return None


def strategy_market_strong_plus(home: str, away: str, market_ph: float, market_pa: float) -> Optional[dict]:
    """مرشح قوي >= 75% — باك تاست: 88.3% فوز، ROI +4.1% (4643 رهان)."""
    if market_ph >= 0.75:
        return _market_pick(home, away, "home", market_ph, "market_strong_plus", "A",
                            f"Strong+ home {market_ph:.0%}")
    if market_pa >= 0.75:
        return _market_pick(home, away, "away", market_pa, "market_strong_plus", "A",
                            f"Strong+ away {market_pa:.0%}")
    return None


def strategy_clear_favorite(home: str, away: str, market_ph: float, market_pa: float) -> Optional[dict]:
    """هامش احتمال >= 25% — باك تاست: 79.6% فوز، ROI +3.1% (أعلى حجم 8822)."""
    if market_ph - market_pa >= 0.25:
        return _market_pick(home, away, "home", market_ph, "clear_favorite", "B",
                            f"Clear favorite margin {market_ph - market_pa:.0%}")
    if market_pa - market_ph >= 0.25:
        return _market_pick(home, away, "away", market_pa, "clear_favorite", "B",
                            f"Clear favorite margin {market_pa - market_ph:.0%}")
    return None


def strategy_home_market_favorite(home: str, away: str, market_ph: float, market_pa: float) -> Optional[dict]:
    """المضيف المرشح >= 60% — باك تاست: 78.3% فوز، ROI +2.9% (6678 رهان)."""
    if market_ph >= 0.60:
        return _market_pick(home, away, "home", market_ph, "home_market_favorite", "B",
                            f"Home+market favorite {market_ph:.0%}")
    return None


def strategy_contrarian_home_coinflip(home: str, away: str, market_ph: float, market_pa: float) -> Optional[dict]:
    """العملة المعدنية للمضيف 42-52% — باك تاست: +5.1% ROI (أعلى حافة حقيقية)."""
    if 0.42 <= market_ph <= 0.52:
        return _market_pick(home, away, "home", market_ph, "contrarian_home_coinflip", "C",
                            f"Coinflip home edge {market_ph:.0%}")
    return None


def strategy_moderate_home_favorite(home: str, away: str, market_ph: float, market_pa: float) -> Optional[dict]:
    """مضيف معتدل 60-72% — باك تاست: 67.5% فوز، ROI +1.9% (قيمة سعرية)."""
    if 0.60 <= market_ph <= 0.72:
        return _market_pick(home, away, "home", market_ph, "moderate_home_favorite", "C",
                            f"Moderate home value {market_ph:.0%}")
    return None


def strategy_away_dominant(home: str, away: str, market_ph: float, market_pa: float) -> Optional[dict]:
    """ضيف مهيمن >= 70% — باك تاست: 84.6% فوز، ROI +3.7% (1856 رهان)."""
    if market_pa >= 0.70:
        return _market_pick(home, away, "away", market_pa, "away_dominant", "B",
                            f"Dominant away {market_pa:.0%}")
    return None


# ═══════════════════════════════════════════════════════════════════════════
# مصادر بيانات إضافية
# ═══════════════════════════════════════════════════════════════════════════

def fetch_openligadb_predictions() -> list[dict]:
    """OpenLigaDB — بيانات Bundesliga مجانية بدون مفتاح."""
    import requests
    picks = []
    try:
        r = requests.get("https://api.openligadb.de/api/getmatchdata/bl1", timeout=10,
                        headers={"Accept": "application/json"})
        if r.status_code != 200:
            return []
        matches = r.json()
        for m in matches[:50]:
            if not m.get("matchIsFinished"):
                continue
            team1 = m.get("team1", {}).get("teamName", "?")
            team2 = m.get("team2", {}).get("teamName", "?")
            results = m.get("matchResults", [])
            if not results:
                continue
            final = results[-1]
            s1 = final.get("pointsTeam1", 0)
            s2 = final.get("pointsTeam2", 0)

            # Simple: more recent wins = higher form
            pick = team1 if s1 > s2 else team2 if s2 > s1 else None
            if pick:
                picks.append({
                    "match_date": m.get("matchDateTime", "")[:10],
                    "sport": "football",
                    "league": "bundesliga",
                    "home": team1, "away": team2,
                    "pick": pick,
                    "source": "openligadb_results",
                    "strategy": "results_tracker",
                    "confidence": "C",
                    "notes": f"Result: {s1}-{s2}",
                })
    except Exception:
        pass
    return picks


def fetch_espn_scores() -> list[dict]:
    """ESPN — نتائج مجانية للتحقق."""
    import requests
    picks = []
    try:
        # ESPN basketball scoreboard
        r = requests.get(
            "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard",
            timeout=10
        )
        if r.status_code == 200:
            data = r.json()
            for event in data.get("events", [])[:20]:
                competitions = event.get("competitions", [])
                if not competitions:
                    continue
                comp = competitions[0]
                competitors = comp.get("competitors", [])
                if len(competitors) < 2:
                    continue
                home = competitors[0].get("team", {}).get("displayName", "?")
                away = competitors[1].get("team", {}).get("displayName", "?")
                home_score = competitors[0].get("score", "0")
                away_score = competitors[1].get("score", "0")

                status = event.get("status", {}).get("type", {}).get("completed", False)
                if status:
                    winner = home if int(home_score) > int(away_score) else away
                    picks.append({
                        "match_date": datetime.now().strftime("%Y-%m-%d"),
                        "sport": "basketball",
                        "league": "nba",
                        "home": home, "away": away,
                        "pick": winner,
                        "source": "espn_results",
                        "strategy": "results_tracker",
                        "confidence": "C",
                        "notes": f"ESPN: {home_score}-{away_score}",
                    })
    except Exception:
        pass
    return picks


# ═══════════════════════════════════════════════════════════════════════════
# تشغيل كل الاستراتيجيات على مباريات كرة السلة
# ═══════════════════════════════════════════════════════════════════════════

def run_all_strategies(target_date: date) -> int:
    """يشغّل كل الاستراتيجيات على مباريات اليوم ويسجّلها."""
    import pickle

    # Load model
    model_path = PROJECT_DIR / "models" / "basketball_calibrated.pkl"
    if not model_path.exists():
        print("  ⚠️ نموذج غير موجود")
        return 0

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

    # Load games
    bball_path = PROJECT_DIR / "data" / "basketball_betexplorer_current.csv"
    if not bball_path.exists():
        return 0

    df = pd.read_csv(bball_path)
    df["GAME_DATE_EST"] = pd.to_datetime(df["GAME_DATE_EST"], errors="coerce")

    all_picks = []

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

        # Compute LightGBM calibrated probability
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

        # Run ALL strategies
        strategies = [
            ("lightgbm", lambda: strategy_lightgbm_calibrated(cal_prob, home, away, mph, mpa)),
            ("pure_elo", lambda: strategy_pure_elo(home, away, elo_snap)),
            ("market_strong", lambda: strategy_market_filter(home, away, mph, mpa)),
            ("elo_agree", lambda: strategy_elo_market_agreement(home, away, mph, mpa, elo_snap)),
            ("contrarian", lambda: strategy_contrarian(home, away, mph, mpa, elo_snap)),
            ("underdog", lambda: strategy_underdog_value(home, away, mph, mpa, elo_snap)),
            ("home_court", lambda: strategy_home_court(home, away, mph, mpa, elo_snap)),
            # استراتيجيات جديدة مُختارة بالباك تاست (12,942 مباراة)
            ("market_extreme", lambda: strategy_market_extreme(home, away, mph, mpa)),
            ("market_strong_plus", lambda: strategy_market_strong_plus(home, away, mph, mpa)),
            ("clear_favorite", lambda: strategy_clear_favorite(home, away, mph, mpa)),
            ("home_market_favorite", lambda: strategy_home_market_favorite(home, away, mph, mpa)),
            ("contrarian_home_coinflip", lambda: strategy_contrarian_home_coinflip(home, away, mph, mpa)),
            ("moderate_home_favorite", lambda: strategy_moderate_home_favorite(home, away, mph, mpa)),
            ("away_dominant", lambda: strategy_away_dominant(home, away, mph, mpa)),
        ]

        for strat_name, strat_fn in strategies:
            result = strat_fn()
            if result:
                result["match_date"] = str(match_date)
                result["sport"] = "basketball"
                result["league"] = league
                result["home"] = home
                result["away"] = away
                all_picks.append(result)

    # Record all picks
    db = PROJECT_DIR / "data" / "betting_journal.db"
    conn = sqlite3.connect(db)
    c = conn.cursor()

    recorded = 0
    for p in all_picks:
        exists = c.execute("""
            SELECT 1 FROM predictions
            WHERE match_date=? AND home=? AND away=? AND pick=? AND source=? AND strategy=?
        """, (p["match_date"], p["home"], p["away"], p["pick"], p["source"], p["strategy"])).fetchone()

        if not exists:
            ks = kelly_stake(p["model_prob"], p["odds_at_prediction"], 100, 0.25, 0.05)
            c.execute("""
                INSERT INTO predictions (
                    created_at, match_date, sport, league, home, away, pick, source,
                    model_prob, odds_at_prediction, kelly_stake, ev_pct, strategy, confidence, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                datetime.now().isoformat(), p["match_date"], p["sport"], p["league"],
                p["home"], p["away"], p["pick"], p["source"],
                p["model_prob"], p["odds_at_prediction"], ks,
                ev_percent(p["model_prob"], p["odds_at_prediction"]),
                p["strategy"], p.get("confidence"), p.get("notes"),
            ))
            recorded += 1

    conn.commit()
    conn.close()

    # Summary by strategy
    by_strat = defaultdict(int)
    for p in all_picks:
        by_strat[p["strategy"]] += 1

    print(f"\n  📊 {len(all_picks)} توقع من 14 استراتيجية (7 أصلية + 7 جديدة بالباك تاست):")
    for strat, count in sorted(by_strat.items(), key=lambda x: -x[1]):
        print(f"    {strat}: {count}")
    print(f"  مسجّل جديد: {recorded}")

    return recorded


if __name__ == "__main__":
    init_db()
    target = date.today()

    print("=" * 60)
    print(f"  🎯 استراتيجيات متعددة — {target}")
    print("=" * 60)

    # Register strategies
    conn = sqlite3.connect(PROJECT_DIR / "data" / "betting_journal.db")
    c = conn.cursor()
    strategies = [
        ("pure_elo", "ELO ratings only, no market influence"),
        ("market_strong", "Market consensus when prob > 70%"),
        ("elo_market_agree", "Bet when ELO and market agree"),
        ("contrarian_elo", "Bet against market when ELO strongly disagrees"),
        ("underdog_value", "Bet underdogs with close ELO"),
        ("home_court", "Bet home teams with ELO advantage"),
        ("lightgbm_calibrated", "LightGBM + isotonic calibration"),
    ]
    for name, desc in strategies:
        c.execute("""
            INSERT OR IGNORE INTO strategies (name, description, enabled, created_at)
            VALUES (?, ?, 1, ?)
        """, (name, desc, datetime.now().isoformat()))
    conn.commit()
    conn.close()
    print(f"  ✓ {len(strategies)} استراتيجيات مسجّلة")

    # Run all strategies
    count = run_all_strategies(target)

    # Fetch external data
    print(f"\n  📡 مصادر خارجية:")
    espn = fetch_espn_scores()
    print(f"    ESPN: {len(espn)} حدث")

    print(f"\n{'='*60}")
    print(f"  ✓ اكتمل — {count} توقع جديد من {len(strategies)} استراتيجيات")
    print(f"{'='*60}")
