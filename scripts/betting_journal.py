#!/usr/bin/env python3
"""Betting Journal — يسجّل كل توقع من كل مصدر ويتابع النتائج.

قاعدة بيانات SQLite تتابع:
- كل توقع من كل مصدر (نموذجنا، APIs، GitHub predictors)
- السعر وقت التوقع
- النتيجة الفعلية
- الأداء per source, per sport, per strategy

بعد شهر: نعرف من الأفضل بالأرقام.
"""
from __future__ import annotations

import sqlite3
import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

PROJECT_DIR = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_DIR / "data" / "betting_journal.db"


def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS predictions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            match_date TEXT NOT NULL,
            sport TEXT NOT NULL,
            league TEXT,
            home TEXT NOT NULL,
            away TEXT NOT NULL,
            pick TEXT NOT NULL,
            source TEXT NOT NULL,
            model_prob REAL,
            odds_at_prediction REAL,
            real_odds REAL,
            stake REAL DEFAULT 0,
            kelly_stake REAL DEFAULT 0,
            ev_pct REAL,
            strategy TEXT,
            confidence TEXT,
            notes TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            prediction_id INTEGER NOT NULL,
            checked_at TEXT NOT NULL,
            home_score INTEGER,
            away_score INTEGER,
            pick_won INTEGER,
            outcome TEXT,
            profit REAL,
            roi_pct REAL,
            result_source TEXT,
            FOREIGN KEY (prediction_id) REFERENCES predictions(id)
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS sources (
            name TEXT PRIMARY KEY,
            type TEXT,
            api_key TEXT,
            url TEXT,
            enabled INTEGER DEFAULT 1,
            created_at TEXT,
            notes TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS strategies (
            name TEXT PRIMARY KEY,
            description TEXT,
            min_prob REAL DEFAULT 0.55,
            min_odds REAL DEFAULT 1.35,
            max_odds REAL DEFAULT 2.50,
            min_ev REAL DEFAULT 0.0,
            kelly_fraction REAL DEFAULT 0.25,
            max_stake_pct REAL DEFAULT 0.05,
            enabled INTEGER DEFAULT 1,
            created_at TEXT,
            total_bets INTEGER DEFAULT 0,
            wins INTEGER DEFAULT 0,
            losses INTEGER DEFAULT 0,
            profit REAL DEFAULT 0.0,
            roi REAL DEFAULT 0.0
        )
    """)

    conn.commit()
    conn.close()
    print(f"✓ Database: {DB_PATH}")


def add_prediction(
    match_date: str,
    sport: str,
    home: str,
    away: str,
    pick: str,
    source: str,
    model_prob: float = None,
    odds_at_prediction: float = None,
    real_odds: float = None,
    stake: float = 0,
    kelly_stake: float = 0,
    ev_pct: float = None,
    strategy: str = None,
    confidence: str = None,
    league: str = None,
    notes: str = None,
) -> int:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        INSERT INTO predictions (
            created_at, match_date, sport, league, home, away, pick, source,
            model_prob, odds_at_prediction, real_odds, stake, kelly_stake,
            ev_pct, strategy, confidence, notes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        datetime.now().isoformat(), match_date, sport, league, home, away, pick, source,
        model_prob, odds_at_prediction, real_odds, stake, kelly_stake,
        ev_pct, strategy, confidence, notes
    ))
    pred_id = c.lastrowid
    conn.commit()
    conn.close()
    return pred_id


def add_result(
    prediction_id: int,
    home_score: int,
    away_score: int,
    pick_won: bool,
    result_source: str = "manual",
):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    c.execute("SELECT pick, real_odds, odds_at_prediction, stake, kelly_stake, home, away FROM predictions WHERE id=?", (prediction_id,))
    row = c.fetchone()
    if not row:
        conn.close()
        return

    pick, real_odds, odds_at_prediction, stake, kelly_stake, home, away = row
    # Prefer the confirmed real odds, fall back to odds captured at prediction time.
    odds = real_odds or odds_at_prediction or 1.0
    actual_stake = kelly_stake if kelly_stake and kelly_stake > 0 else (stake or 0)
    if actual_stake > 0:
        profit = actual_stake * (odds - 1) if pick_won else -actual_stake
        roi = profit / actual_stake * 100
    else:
        # No real money staked: still record a notional unit-stake P&L so the
        # report can rank strategies by simulated return on a 1-unit flat bet.
        profit = (odds - 1) if pick_won else -1.0
        roi = profit * 100

    c.execute("""
        INSERT INTO results (prediction_id, checked_at, home_score, away_score,
                            pick_won, outcome, profit, roi_pct, result_source)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        prediction_id, datetime.now().isoformat(),
        home_score, away_score, int(pick_won),
        "WON" if pick_won else "LOST",
        round(profit, 2), round(roi, 1), result_source
    ))

    if strategy_name := c.execute("SELECT strategy FROM predictions WHERE id=?", (prediction_id,)).fetchone()[0]:
        c.execute("""
            UPDATE strategies SET
                total_bets = total_bets + 1,
                wins = wins + ?,
                losses = losses + ?,
                profit = profit + ?,
                roi = CASE WHEN total_bets > 0 THEN (profit + ?) / (total_bets * 1.0) * 100 ELSE 0 END
            WHERE name = ?
        """, (int(pick_won), int(not pick_won), profit, profit, strategy_name))

    conn.commit()
    conn.close()


def get_unresolved_predictions(target_date: str = None) -> list[dict]:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    if target_date:
        c.execute("""
            SELECT p.id, p.match_date, p.sport, p.league, p.home, p.away,
                   p.pick, p.source, p.model_prob, p.real_odds, p.strategy
            FROM predictions p
            LEFT JOIN results r ON p.id = r.prediction_id
            WHERE r.id IS NULL AND p.match_date <= ?
            ORDER BY p.match_date
        """, (target_date,))
    else:
        c.execute("""
            SELECT p.id, p.match_date, p.sport, p.league, p.home, p.away,
                   p.pick, p.source, p.model_prob, p.real_odds, p.strategy
            FROM predictions p
            LEFT JOIN results r ON p.id = r.prediction_id
            WHERE r.id IS NULL
            ORDER BY p.match_date
        """)
    rows = c.fetchall()
    conn.close()
    return [dict(zip([d[0] for d in c.description], r)) for r in rows] if rows else []


def performance_report(days: int = 30) -> dict:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()

    report = {"period_days": days, "sources": {}, "strategies": {}, "sports": {}}

    # By source
    c.execute("""
        SELECT p.source,
               COUNT(*) as total,
               SUM(CASE WHEN r.pick_won=1 THEN 1 ELSE 0 END) as wins,
               SUM(CASE WHEN r.pick_won=0 THEN 1 ELSE 0 END) as losses,
               SUM(r.profit) as profit,
               AVG(r.roi_pct) as avg_roi
        FROM predictions p
        JOIN results r ON p.id = r.prediction_id
        WHERE p.created_at >= ?
        GROUP BY p.source
        ORDER BY profit DESC
    """, (cutoff,))
    for row in c.fetchall():
        src, total, wins, losses, profit, avg_roi = row
        report["sources"][src] = {
            "total": total, "wins": wins or 0, "losses": losses or 0,
            "accuracy": (wins / total * 100) if total > 0 else 0,
            "profit": round(profit or 0, 2),
            "avg_roi": round(avg_roi or 0, 1),
        }

    # By strategy
    c.execute("""
        SELECT COALESCE(p.strategy, 'default'),
               COUNT(*),
               SUM(CASE WHEN r.pick_won=1 THEN 1 ELSE 0 END),
               SUM(r.profit)
        FROM predictions p
        JOIN results r ON p.id = r.prediction_id
        WHERE p.created_at >= ?
        GROUP BY p.strategy
        ORDER BY SUM(r.profit) DESC
    """, (cutoff,))
    for row in c.fetchall():
        strat, total, wins, profit = row
        report["strategies"][strat] = {
            "total": total, "wins": wins or 0,
            "accuracy": (wins / total * 100) if total > 0 else 0,
            "profit": round(profit or 0, 2),
        }

    # By sport
    c.execute("""
        SELECT p.sport,
               COUNT(*),
               SUM(CASE WHEN r.pick_won=1 THEN 1 ELSE 0 END),
               SUM(r.profit)
        FROM predictions p
        JOIN results r ON p.id = r.prediction_id
        WHERE p.created_at >= ?
        GROUP BY p.sport
        ORDER BY SUM(r.profit) DESC
    """, (cutoff,))
    for row in c.fetchall():
        sport, total, wins, profit = row
        report["sports"][sport] = {
            "total": total, "wins": wins or 0,
            "accuracy": (wins / total * 100) if total > 0 else 0,
            "profit": round(profit or 0, 2),
        }

    conn.close()
    return report


def register_source(name: str, source_type: str, url: str = None, api_key: str = None, notes: str = None):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        INSERT OR REPLACE INTO sources (name, type, url, api_key, enabled, created_at, notes)
        VALUES (?, ?, ?, ?, 1, ?, ?)
    """, (name, source_type, url, api_key, datetime.now().isoformat(), notes))
    conn.commit()
    conn.close()
    print(f"✓ Source registered: {name}")


def register_strategy(name: str, description: str, min_prob=0.55, min_odds=1.35,
                       max_odds=2.50, min_ev=0.0, kelly_fraction=0.25, max_stake_pct=0.05):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        INSERT OR REPLACE INTO strategies
        (name, description, min_prob, min_odds, max_odds, min_ev, kelly_fraction, max_stake_pct, enabled, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
    """, (name, description, min_prob, min_odds, max_odds, min_ev, kelly_fraction, max_stake_pct,
          datetime.now().isoformat()))
    conn.commit()
    conn.close()
    print(f"✓ Strategy registered: {name}")


if __name__ == "__main__":
    init_db()

    # Register our model as a source
    register_source("our_lightgbm", "model", notes="LightGBM + ELO + calibration, trained on 12942 games")

    # Register default strategies
    register_strategy("conservative", "High prob only, safe odds",
                      min_prob=0.70, min_odds=1.35, max_odds=2.20, min_ev=2.0)
    register_strategy("balanced", "Moderate risk, good coverage",
                      min_prob=0.60, min_odds=1.40, max_odds=2.50, min_ev=3.0)
    register_strategy("aggressive", "More bets, higher variance",
                      min_prob=0.55, min_odds=1.50, max_odds=3.00, min_ev=5.0)

    print("\n✓ Betting journal ready")
    print(f"  DB: {DB_PATH}")
    print(f"  Sources: our_lightgbm")
    print(f"  Strategies: conservative, balanced, aggressive")
