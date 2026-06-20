#!/usr/bin/env python3
"""Backtest betting_math.py against historical prediction memory.

Tests Kelly staking, value bet detection, EV filtering, and Sharpe scoring
on the 82 finished predictions in prediction_result_memory.csv.
"""
from __future__ import annotations

import csv
import os
import sys
from collections import defaultdict
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR / "scripts"))

from betting_math import (
    kelly_fraction,
    kelly_stake,
    simultaneous_kelly,
    ev_percent,
    edge,
    is_value_bet,
    implied_prob,
    sharpe_ratio,
    summarize_picks,
)


def load_memory():
    path = PROJECT_DIR / "data" / "prediction_result_memory.csv"
    if not path.exists():
        print(f"Memory file not found: {path}")
        return []
    with open(path) as f:
        return list(csv.DictReader(f))


def parse_outcome(row):
    outcome = (row.get("PickOutcome", "") or "").lower()
    status = (row.get("ResultStatus", "") or "").lower()
    if outcome in ("correct", "win", "won", "right", "yes", "1") or "correct" in status:
        return True
    if outcome in ("wrong", "loss", "lost", "no", "0") or "wrong" in status:
        return False
    return None


def run_backtest():
    rows = load_memory()
    finished = []
    for r in rows:
        won = parse_outcome(r)
        if won is None:
            continue
        prob = float(r.get("Prob", 0) or 0)
        odds_str = r.get("PickOdds", "") or r.get("OneXBetManualOdds", "") or ""
        try:
            odds = float(odds_str)
        except (ValueError, TypeError):
            continue
        if prob <= 0 or odds <= 1.0:
            continue
        finished.append({
            "sport": r.get("Sport", "?"),
            "date": r.get("Date", ""),
            "home": r.get("Home", ""),
            "away": r.get("Away", ""),
            "pick": r.get("Pick", ""),
            "prob": prob,
            "odds": odds,
            "won": won,
        })

    print(f"=== BACKTEST: {len(finished)} finished predictions ===\n")

    # ── Strategy 1: Bet on everything (flat stake) ──
    bankroll = 100.0
    flat_stake = 2.0
    s1_profit = sum((flat_stake * (r["odds"] - 1)) if r["won"] else -flat_stake for r in finished)
    s1_wins = sum(1 for r in finished if r["won"])
    s1_roi = (s1_profit / (flat_stake * len(finished))) * 100

    print("── Strategy 1: Flat stake on everything ──")
    print(f"  Bets: {len(finished)} | Wins: {s1_wins} | Acc: {s1_wins/len(finished)*100:.1f}%")
    print(f"  Total staked: ${flat_stake * len(finished):.0f}")
    print(f"  Profit: ${s1_profit:+.2f} | ROI: {s1_roi:+.1f}%\n")

    # ── Strategy 2: EV filter only (EV > 0) ──
    ev_filtered = [r for r in finished if ev_percent(r["prob"], r["odds"]) > 0]
    if ev_filtered:
        s2_profit = sum((flat_stake * (r["odds"] - 1)) if r["won"] else -flat_stake for r in ev_filtered)
        s2_wins = sum(1 for r in ev_filtered if r["won"])
        s2_roi = (s2_profit / (flat_stake * len(ev_filtered))) * 100 if ev_filtered else 0
        print(f"── Strategy 2: EV > 0 filter ({len(ev_filtered)} bets) ──")
        print(f"  Bets: {len(ev_filtered)} | Wins: {s2_wins} | Acc: {s2_wins/len(ev_filtered)*100:.1f}%")
        print(f"  Profit: ${s2_profit:+.2f} | ROI: {s2_roi:+.1f}%\n")
    else:
        print("── Strategy 2: EV > 0 filter — 0 bets passed ─\n")

    # ── Strategy 3: Value bet detection (prob * odds > 1) ──
    value_bets = [r for r in finished if is_value_bet(r["prob"], r["odds"])]
    if value_bets:
        s3_profit = sum((flat_stake * (r["odds"] - 1)) if r["won"] else -flat_stake for r in value_bets)
        s3_wins = sum(1 for r in value_bets if r["won"])
        s3_roi = (s3_profit / (flat_stake * len(value_bets))) * 100
        print(f"── Strategy 3: Value bet (p*O>1) ({len(value_bets)} bets) ──")
        print(f"  Bets: {len(value_bets)} | Wins: {s3_wins} | Acc: {s3_wins/len(value_bets)*100:.1f}%")
        print(f"  Profit: ${s3_profit:+.2f} | ROI: {s3_roi:+.1f}%\n")
    else:
        print("── Strategy 3: Value bet (p*O>1) — 0 bets passed ─\n")

    # ── Strategy 4: Kelly staking on everything ──
    kelly_picks = []
    for r in finished:
        stake = kelly_stake(r["prob"], r["odds"], bankroll, kelly_size=0.25, max_fraction=0.05)
        kelly_picks.append({**r, "stake": stake})
    kelly_picks = [p for p in kelly_picks if p["stake"] > 0]

    if kelly_picks:
        s4_profit = sum(
            (p["stake"] * (p["odds"] - 1)) if p["won"] else -p["stake"]
            for p in kelly_picks
        )
        s4_staked = sum(p["stake"] for p in kelly_picks)
        s4_wins = sum(1 for p in kelly_picks if p["won"])
        s4_roi = (s4_profit / s4_staked) * 100 if s4_staked > 0 else 0
        print(f"── Strategy 4: Quarter-Kelly staking ({len(kelly_picks)} bets) ──")
        print(f"  Bets: {len(kelly_picks)} | Wins: {s4_wins} | Acc: {s4_wins/len(kelly_picks)*100:.1f}%")
        print(f"  Total staked: ${s4_staked:.2f}")
        print(f"  Profit: ${s4_profit:+.2f} | ROI: {s4_roi:+.1f}%\n")

    # ── Strategy 5: Kelly + EV filter ──
    kelly_ev = [
        {**r, "stake": kelly_stake(r["prob"], r["odds"], bankroll, 0.25, 0.05)}
        for r in finished
        if ev_percent(r["prob"], r["odds"]) > 0
    ]
    kelly_ev = [p for p in kelly_ev if p["stake"] > 0]
    if kelly_ev:
        s5_profit = sum((p["stake"] * (p["odds"] - 1)) if p["won"] else -p["stake"] for p in kelly_ev)
        s5_staked = sum(p["stake"] for p in kelly_ev)
        s5_wins = sum(1 for p in kelly_ev if p["won"])
        s5_roi = (s5_profit / s5_staked) * 100 if s5_staked > 0 else 0
        print(f"── Strategy 5: Kelly + EV>0 ({len(kelly_ev)} bets) ──")
        print(f"  Bets: {len(kelly_ev)} | Wins: {s5_wins} | Acc: {s5_wins/len(kelly_ev)*100:.1f}%")
        print(f"  Total staked: ${s5_staked:.2f}")
        print(f"  Profit: ${s5_profit:+.2f} | ROI: {s5_roi:+.1f}%\n")
    else:
        print("── Strategy 5: Kelly + EV>0 — 0 bets passed ─\n")

    # ── Per-sport breakdown ──
    print("── Per-sport accuracy + value rate ──")
    by_sport = defaultdict(list)
    for r in finished:
        by_sport[r["sport"]].append(r)
    print(f"{'Sport':12s} {'Total':>5s} {'Wins':>5s} {'Acc':>7s} {'ValueBets':>10s} {'AvgEV':>8s}")
    for sport in sorted(by_sport, key=lambda x: len(by_sport[x]), reverse=True):
        preds = by_sport[sport]
        wins = sum(1 for r in preds if r["won"])
        acc = wins / len(preds) * 100 if preds else 0
        vb = sum(1 for r in preds if is_value_bet(r["prob"], r["odds"]))
        avg_ev = sum(ev_percent(r["prob"], r["odds"]) for r in preds) / len(preds) if preds else 0
        print(f"{sport:12s} {len(preds):5d} {wins:5d} {acc:6.1f}% {vb:10d} {avg_ev:+7.1f}%")

    # ── Sharpe ratio ──
    sharpe_input = [
        {"date": r["date"], "stake": 2.0, "odds": r["odds"], "won": r["won"]}
        for r in finished
    ]
    sr = sharpe_ratio(sharpe_input)
    print(f"\n── Sharpe Ratio (flat stake): {sr:.3f} ──")
    if sr > 1.0:
        print("  → Positive risk-adjusted edge detected")
    elif sr > 0:
        print("  → Marginal edge — needs more data")
    else:
        print("  → No edge detected with current approach")

    print("\n=== RECOMMENDATION ===")
    strategies = [
        ("Flat stake", s1_profit, s1_roi, len(finished)),
        ("EV>0 filter", s2_profit if ev_filtered else -999, s2_roi if ev_filtered else -999, len(ev_filtered)),
        ("Value bet", s3_profit if value_bets else -999, s3_roi if value_bets else -999, len(value_bets)),
        ("Quarter-Kelly", s4_profit if kelly_picks else -999, s4_roi if kelly_picks else -999, len(kelly_picks)),
        ("Kelly+EV", s5_profit if kelly_ev else -999, s5_roi if kelly_ev else -999, len(kelly_ev)),
    ]
    strategies.sort(key=lambda x: x[1], reverse=True)
    best = strategies[0]
    print(f"  Best strategy: {best[0]} (${best[1]:+.2f}, ROI {best[2]:+.1f}%, {best[3]} bets)")


if __name__ == "__main__":
    run_backtest()
