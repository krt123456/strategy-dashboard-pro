#!/usr/bin/env python3
"""Version engine — interprets the strategy_versions_library and emits picks.

Every version in data/strategy_versions_library.json has a `rule` describing how
to decide a pick from the available inputs (market probabilities, bookmaker odds,
ELO). This module is the single interpreter both runners call, so a new version
appended to the library is immediately live across basketball (multi_strategy)
and all cached-source sports (cross_source_runner).

Inputs are optional: rules that need ELO simply return None when ELO isn't
provided, so the same engine works in any context.
"""
from __future__ import annotations
from typing import Optional


def apply_version(v: dict, home: str, away: str, market_ph: float, market_pa: float,
                  home_odds: Optional[float] = None, away_odds: Optional[float] = None,
                  elo_home: Optional[float] = None, elo_away: Optional[float] = None) -> Optional[dict]:
    rule = v.get("rule", "home_band")
    p = v.get("params", {})

    def _emit(side: str, prob: float, odds: Optional[float], note: str) -> dict:
        o = odds or (1.0 / max(prob, 0.01))
        return {
            "pick": home if side == "home" else away,
            "model_prob": round(prob, 4),
            "odds_at_prediction": round(o, 2),
            "strategy": v["name"],
            "source": "version_library",
            "confidence": "C",
            "notes": f"{v['name']}: {note}",
        }

    if rule == "home_band":
        lo, hi = p.get("lo", 0), p.get("hi", 1)
        if lo <= market_ph <= hi:
            return _emit("home", market_ph, home_odds, f"home band {lo:.2f}-{hi:.2f}")
        return None

    if rule == "elo_and_coinflip":
        if elo_home is None or elo_away is None:
            return None
        elo_prob_home = elo_home  # caller passes ELO win-prob as elo_home
        band_lo, band_hi = p.get("band_lo", 0.45), p.get("band_hi", 0.58)
        if p.get("elo_min_home", 0.52) <= elo_prob_home and band_lo <= market_ph <= band_hi:
            return _emit("home", market_ph, home_odds,
                         f"ELO {elo_prob_home:.0%} + coinflip {market_ph:.0%}")
        return None

    if rule == "elo_strong_margin":
        if elo_home is None or elo_away is None:
            return None
        diff = elo_home - elo_away  # caller passes raw ELO ratings here
        if abs(diff) >= p.get("elo_diff_min", 100):
            side = "home" if diff > 0 else "away"
            prob = market_ph if side == "home" else market_pa
            return _emit(side, prob, home_odds if side == "home" else away_odds,
                         f"ELO gap {abs(diff):.0f}")
        return None

    if rule == "favorite_exclude_trap":
        margin = p.get("margin", 0.25)
        trap_lo, trap_hi = p.get("trap_lo", 1.5), p.get("trap_hi", 1.8)
        # pick the clear favourite by prob margin
        if market_ph - market_pa >= margin:
            side, prob, odds = "home", market_ph, home_odds
        elif market_pa - market_ph >= margin:
            side, prob, odds = "away", market_pa, away_odds
        else:
            return None
        fair_odds = 1.0 / max(prob, 0.01)
        if trap_lo <= fair_odds <= trap_hi:
            return None  # skip the vig trap zone
        return _emit(side, prob, odds, f"fav margin {margin:.0%} excl trap")

    return None
