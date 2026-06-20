#!/usr/bin/env python3
"""Strategy evolution report — the daily tournament dashboard.

Combines two signals for every strategy:
  1. Backtest edge — return on the 12,942-match historical dataset.
  2. Live edge — real graded return from betting_journal.db.

Each daily run appends a timestamped snapshot to reports/strategy_evolution/,
so over weeks/months you can watch which strategies keep their edge in reality
and which decay. The recommendation column proposes keep / watch / cut based on
combined evidence, which is how the strategy pool evolves over time.
"""
from __future__ import annotations

import argparse
import csv
import sqlite3
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Dict, List

PROJECT_DIR = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_DIR / "data" / "betting_journal.db"
EVOLUTION_DIR = PROJECT_DIR / "reports" / "strategy_evolution"

# Backtest ROI from backtest_strategies.py (the 7 new market-edge strategies).
# These are stable numbers from the 12,942-match dataset; update by re-running
# backtest_strategies.py if the dataset grows.
BACKTEST_ROI = {
    "contrarian_home_coinflip": 5.1,
    "market_strong_plus": 4.1,
    "away_dominant": 3.7,
    "market_extreme": 3.6,
    "clear_favorite": 3.1,
    "home_market_favorite": 2.9,
    "moderate_home_favorite": 1.9,
    # original strategies — no fair-odds backtest run; left blank intentionally
    "pure_elo": None,
    "market_strong": None,
    "elo_market_agree": None,
    "contrarian_elo": None,
    "underdog_value": None,
    "home_court": None,
    "lightgbm_calibrated": None,
}


def live_performance(days: int = 30) -> Dict[str, dict]:
    """Per-strategy live accuracy + ROI from graded results."""
    if not DB_PATH.exists():
        return {}
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    out: Dict[str, dict] = {}
    for row in c.execute(
        """SELECT COALESCE(p.strategy,'?'), COUNT(*),
                  SUM(CASE WHEN r.pick_won=1 THEN 1 ELSE 0 END),
                  SUM(r.profit), SUM(r.roi_pct)
           FROM predictions p JOIN results r ON p.id=r.prediction_id
           WHERE p.created_at >= ? GROUP BY p.strategy""", (cutoff,)):
        name, bets, wins, profit, roi = row
        out[name] = {
            "bets": bets or 0,
            "wins": wins or 0,
            "accuracy": (wins / bets * 100) if bets else 0.0,
            "profit": round(profit or 0, 2),
            "roi": (profit / bets * 100) if bets else 0.0,
        }
    conn.close()
    return out


def recommend(name: str, bt: float | None, live: dict | None) -> str:
    """Evidence-based keep/watch/cut verdict."""
    if not live or live["bets"] < 10:
        # not enough live sample yet — judge on backtest
        if bt is None:
            return "NEW"
        return "WATCH (live sample too small)" if bt > 0 else "CUT (backtest negative)"
    live_roi = live["roi"]
    if live_roi >= 2.0 and (bt is None or bt >= 0):
        return "KEEP ★"
    if live_roi < -5.0:
        return "CUT"
    return "WATCH"


def build_report(live_days: int = 30) -> str:
    live = live_performance(live_days)
    today = date.today().isoformat()
    now = datetime.now().isoformat(timespec="seconds")

    # Union of all known strategies (backtest + live seen in DB).
    names = sorted(set(BACKTEST_ROI.keys()) | set(live.keys()))
    rows = []
    for name in names:
        bt = BACKTEST_ROI.get(name)
        lv = live.get(name)
        rows.append({
            "strategy": name,
            "backtest_roi": bt,
            "live_bets": lv["bets"] if lv else 0,
            "live_accuracy": lv["accuracy"] if lv else 0.0,
            "live_roi": lv["roi"] if lv else 0.0,
            "live_profit": lv["profit"] if lv else 0.0,
            "verdict": recommend(name, bt, lv),
        })
    # rank by live ROI (backtest-only strategies sink to the bottom fairly)
    rows.sort(key=lambda r: (r["live_roi"] if r["live_bets"] else -999), reverse=True)

    lines = [
        f"# Strategy Evolution Report — {today}",
        f"_Generated {now} | live window: last {live_days} days | "
        f"backtest: 12,942 matches_",
        "",
        "| Strategy | Backtest ROI | Live bets | Live acc | Live ROI | Live profit | Verdict |",
        "|----------|-------------:|----------:|---------:|---------:|------------:|---------|",
    ]
    for r in rows:
        bt = f"{r['backtest_roi']:+.1f}%" if r["backtest_roi"] is not None else "—"
        lines.append(
            f"| {r['strategy']} | {bt} | {r['live_bets']} | "
            f"{r['live_accuracy']:.1f}% | {r['live_roi']:+.1f}% | "
            f"{r['live_profit']:+.2f} | {r['verdict']} |"
        )

    keep = [r for r in rows if r["verdict"].startswith("KEEP")]
    cut = [r for r in rows if r["verdict"].startswith("CUT")]
    lines += [
        "",
        "## Action",
        f"- **Keep ({len(keep)})**: " + ", ".join(r["strategy"] for r in keep) if keep else "- **Keep**: none yet (need live sample)",
        f"- **Cut ({len(cut)})**: " + ", ".join(r["strategy"] for r in cut) if cut else "- **Cut**: none",
        "",
        "## How to read this",
        "- **Backtest ROI** = historical edge at fair odds. Anything below ~+5% likely breaks even or loses after the bookmaker margin.",
        "- **Live ROI** = real graded return. This is the number that matters for profit. A strategy must stay positive live to survive.",
        "- Verdict: KEEP = profitable live, CUT = clearly losing, WATCH = too early or borderline.",
        "- Re-run daily. Strategies that stay KEEP for 30+ live bets are your real winners.",
    ]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="Strategy evolution tournament report.")
    ap.add_argument("--live-days", type=int, default=30)
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    report = build_report(live_days=args.live_days)
    EVOLUTION_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d")
    out_path = Path(args.out) if args.out else EVOLUTION_DIR / f"evolution_{stamp}.md"
    out_path.write_text(report + "\n", encoding="utf-8")
    print(report)
    print(f"\nSaved → {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
