"""Betting math utilities — integrated from open-source betting projects.

Sources:
- tennis-prediction (JulienHeiduk): Kelly stake + simultaneous scaling + vig removal
- WagerBrain (sedemmler): odds conversion + EV + implied prob
- sports-betting (georgedouzas): value bet detection + Sharpe scoring
- NBA-Machine-Learning (kyleskom): compact Kelly in American odds
"""
from __future__ import annotations

import math
from typing import Optional


# ── Odds conversion ──────────────────────────────────────────────────────────

def decimal_to_american(dec: float) -> int:
    if dec >= 2.0:
        return int((dec - 1) * 100)
    if dec > 1.0:
        return int(-100 / (dec - 1))
    return 0


def american_to_decimal(amer: int) -> float:
    if amer > 0:
        return amer / 100 + 1.0
    return 100 / abs(amer) + 1.0


def implied_prob(dec_odds: float) -> float:
    if dec_odds <= 1.0:
        return 0.0
    return 1.0 / dec_odds


def prob_to_decimal_odds(prob: float) -> float:
    if prob <= 0 or prob >= 1:
        return 0.0
    return 1.0 / prob


# ── Vig removal ───────────────────────────────────────────────────────────────

def remove_vig(dec_odds_list: list[float]) -> list[float]:
    implied = [1.0 / o for o in dec_odds_list if o > 1.0]
    total = sum(implied)
    if total <= 0:
        return [0.0] * len(dec_odds_list)
    return [imp / total for imp in implied]


def bookmaker_margin(dec_odds_list: list[float]) -> float:
    return sum(1.0 / o for o in dec_odds_list if o > 1.0) - 1.0


# ── EV calculation ─────────────────────────────────────────────────────────────

def expected_value(model_prob: float, dec_odds: float) -> float:
    if model_prob <= 0 or model_prob >= 1 or dec_odds <= 1.0:
        return -1.0
    return model_prob * (dec_odds - 1.0) - (1.0 - model_prob)


def ev_percent(model_prob: float, dec_odds: float) -> float:
    return expected_value(model_prob, dec_odds) * 100.0


def edge(model_prob: float, dec_odds: float) -> float:
    market_prob = implied_prob(dec_odds)
    return model_prob - market_prob


# ── Value bet detection ───────────────────────────────────────────────────────

def is_value_bet(model_prob: float, dec_odds: float, min_edge: float = 0.0) -> bool:
    return model_prob * dec_odds > (1.0 + min_edge)


def best_value_pick(
    probs: dict[str, float],
    odds: dict[str, float],
) -> Optional[str]:
    if not probs or not odds:
        return None
    best_pick = None
    best_return = 0.0
    for pick in probs:
        if pick not in odds:
            continue
        ret = probs[pick] * odds[pick] - 1.0
        if ret > best_return:
            best_return = ret
            best_pick = pick
    return best_pick


# ── Kelly criterion ────────────────────────────────────────────────────────────

def kelly_fraction(model_prob: float, dec_odds: float) -> float:
    if model_prob <= 0 or model_prob >= 1 or dec_odds <= 1.0:
        return 0.0
    b = dec_odds - 1.0
    if b <= 0:
        return 0.0
    return model_prob - (1.0 - model_prob) / b


def kelly_stake(
    model_prob: float,
    dec_odds: float,
    bankroll: float,
    kelly_size: float = 0.25,
    max_fraction: float = 0.05,
) -> float:
    full_kelly = kelly_fraction(model_prob, dec_odds)
    if full_kelly <= 0:
        return 0.0
    fraction = min(full_kelly * kelly_size, max_fraction)
    return round(fraction * bankroll, 2)


def simultaneous_kelly(
    picks: list[dict],
    bankroll: float,
    kelly_size: float = 0.25,
    max_bet_fraction: float = 0.05,
    max_daily_exposure: float = 0.20,
) -> list[dict]:
    for p in picks:
        p["raw_kelly_stake"] = kelly_stake(
            p["prob"], p["odds"], bankroll, kelly_size, max_bet_fraction
        )
    total_stakes = sum(p["raw_kelly_stake"] for p in picks)
    max_daily = max_daily_exposure * bankroll
    if total_stakes > max_daily and total_stakes > 0:
        scale = max_daily / total_stakes
        for p in picks:
            p["raw_kelly_stake"] = round(p["raw_kelly_stake"] * scale, 2)
    for p in picks:
        p["final_stake"] = p["raw_kelly_stake"]
        p["potential_profit"] = round(p["final_stake"] * (p["odds"] - 1), 2)
    return picks


# ── Sharpe ratio scoring ───────────────────────────────────────────────────────

def sharpe_ratio(
    outcomes: list[dict],
    annualize: bool = True,
) -> float:
    if not outcomes:
        return 0.0
    daily_returns = []
    by_date: dict[str, list[float]] = {}
    for o in outcomes:
        d = o.get("date", "unknown")
        stake = o.get("stake", 0)
        odds = o.get("odds", 0)
        won = o.get("won", False)
        if stake <= 0:
            continue
        ret = (odds - 1) * stake if won else -stake
        by_date.setdefault(d, []).append(ret)
    for d, rets in by_date.items():
        daily_returns.append(sum(rets))
    if len(daily_returns) < 2:
        return 0.0
    mean_ret = sum(daily_returns) / len(daily_returns)
    variance = sum((r - mean_ret) ** 2 for r in daily_returns) / len(daily_returns)
    std = math.sqrt(variance)
    if std == 0:
        return 0.0
    sharpe = mean_ret / std
    if annualize:
        sharpe *= math.sqrt(365)
    return sharpe


# ── Summary helpers ────────────────────────────────────────────────────────────

def summarize_picks(picks: list[dict], bankroll: float = 100.0) -> dict:
    total_stake = sum(p.get("final_stake", 0) for p in picks)
    total_profit_if_win = sum(p.get("potential_profit", 0) for p in picks)
    avg_ev = sum(ev_percent(p["prob"], p["odds"]) for p in picks) / len(picks) if picks else 0
    value_bets = sum(1 for p in picks if is_value_bet(p["prob"], p["odds"]))
    return {
        "total_picks": len(picks),
        "value_bets": value_bets,
        "total_stake": round(total_stake, 2),
        "exposure_pct": round(total_stake / bankroll * 100, 1) if bankroll > 0 else 0,
        "potential_profit": round(total_profit_if_win, 2),
        "avg_ev_pct": round(avg_ev, 2),
    }
