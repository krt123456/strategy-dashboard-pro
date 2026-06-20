#!/usr/bin/env python3
"""Strategy backtester + tournament.

Runs a library of candidate strategies over the historical betexplorer
basketball dataset (12,942 finished matches with market-implied probabilities)
and ranks them by real return on a flat 1-unit stake at market-implied odds.

The goal is an evidence-driven strategy tournament: every candidate is scored
on the same data, winners get promoted into the live rotation, losers get cut.
Re-run after the dataset grows to keep the selection honest.
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

PROJECT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DATASET = PROJECT_DIR / "data" / "basketball_betexplorer_backtest.csv"

# A strategy inspects a match dict and returns the side it backs ('home'/'away')
# plus a confidence weight, or None to pass. Sizing is normalized to 1 unit
# regardless of confidence so strategies are comparable on ROI alone.
StrategyFn = Callable[[dict], Optional[str]]


@dataclass
class BacktestResult:
    name: str
    bets: int = 0
    wins: int = 0
    stake: float = 0.0
    profit: float = 0.0

    @property
    def win_rate(self) -> float:
        return (self.wins / self.bets * 100) if self.bets else 0.0

    @property
    def roi(self) -> float:
        return (self.profit / self.stake * 100) if self.stake else 0.0


def _f(v) -> float:
    try:
        return float(v) if v not in (None, "") else 0.0
    except Exception:
        return 0.0


def implied_odds(prob: float) -> float:
    """Fair decimal odds implied by a probability (1/prob)."""
    return 1.0 / prob if 0 < prob < 1 else 1.0


# ---------------------------------------------------------------------------
# Candidate strategies. Each returns 'home'/'away'/None for a finished match.
# ---------------------------------------------------------------------------

def strat_market_extreme(m) -> Optional[str]:
    """Back any side the market rates >= 80% — extreme favorite grinding."""
    ph, pa = _f(m["MARKET_PROB_home"]), _f(m["MARKET_PROB_away"])
    if ph >= 0.80:
        return "home"
    if pa >= 0.80:
        return "away"
    return None


def strat_market_strong_plus(m) -> Optional[str]:
    """Market favorite >= 75% (tighter than the live 70% threshold)."""
    ph, pa = _f(m["MARKET_PROB_home"]), _f(m["MARKET_PROB_away"])
    if ph >= 0.75:
        return "home"
    if pa >= 0.75:
        return "away"
    return None


def strat_home_market_favorite(m) -> Optional[str]:
    """Home side that the market also rates as favorite (>= 60%)."""
    ph = _f(m["MARKET_PROB_home"])
    if ph >= 0.60:
        return "home"
    return None


def strat_value_away_underdog(m) -> Optional[str]:
    """Away underdog in the 35%-50% band — live away value with a real chance."""
    pa = _f(m["MARKET_PROB_away"])
    if 0.35 <= pa <= 0.50:
        return "away"
    return None


def strat_clear_favorite(m) -> Optional[str]:
    """prob_margin >= 0.25 -> back the decisive market favorite."""
    ph, pa = _f(m["MARKET_PROB_home"]), _f(m["MARKET_PROB_away"])
    if ph - pa >= 0.25:
        return "home"
    if pa - ph >= 0.25:
        return "away"
    return None


def strat_contrarian_home_coinflip(m) -> Optional[str]:
    """Market sees a coin-flip (home 0.42-0.52) -> exploit home advantage edge."""
    ph = _f(m["MARKET_PROB_home"])
    if 0.42 <= ph <= 0.52:
        return "home"
    return None


def strat_moderate_home_favorite(m) -> Optional[str]:
    """Home in the 0.60-0.72 sweet spot — strong but still value odds."""
    ph = _f(m["MARKET_PROB_home"])
    if 0.60 <= ph <= 0.72:
        return "home"
    return None


def strat_away_dominant(m) -> Optional[str]:
    """Away side rated >= 70% — strong road favorite (less common, high signal)."""
    pa = _f(m["MARKET_PROB_away"])
    if pa >= 0.70:
        return "away"
    return None


CANDIDATE_STRATEGIES: Dict[str, StrategyFn] = {
    "market_extreme": strat_market_extreme,
    "market_strong_plus": strat_market_strong_plus,
    "home_market_favorite": strat_home_market_favorite,
    "value_away_underdog": strat_value_away_underdog,
    "clear_favorite": strat_clear_favorite,
    "contrarian_home_coinflip": strat_contrarian_home_coinflip,
    "moderate_home_favorite": strat_moderate_home_favorite,
    "away_dominant": strat_away_dominant,
}


def backtest(matches: List[dict], strategies: Dict[str, StrategyFn]) -> List[BacktestResult]:
    results: Dict[str, BacktestResult] = {name: BacktestResult(name) for name in strategies}
    for m in matches:
        if m["STATUS"] != "finished" or not m["PTS_home"] or not m["PTS_away"]:
            continue
        home_won = int(m["HOME_TEAM_WINS"]) == 1
        for name, fn in strategies.items():
            side = fn(m)
            if side is None:
                continue
            prob = _f(m["MARKET_PROB_home"]) if side == "home" else _f(m["MARKET_PROB_away"])
            if prob <= 0:
                continue
            odds = implied_odds(prob)
            r = results[name]
            r.bets += 1
            r.stake += 1.0
            won = (side == "home" and home_won) or (side == "away" and not home_won)
            if won:
                r.wins += 1
                r.profit += odds - 1.0
            else:
                r.profit -= 1.0
    ranked = sorted(results.values(), key=lambda r: r.roi, reverse=True)
    return ranked


def main() -> int:
    ap = argparse.ArgumentParser(description="Backtest candidate basketball strategies.")
    ap.add_argument("--dataset", default=str(DEFAULT_DATASET))
    ap.add_argument("--min-bets", type=int, default=50,
                    help="minimum sample size to consider a result trustworthy")
    ap.add_argument("--top", type=int, default=8, help="how many strategies to show")
    args = ap.parse_args()

    with open(args.dataset, encoding="utf-8") as f:
        matches = list(csv.DictReader(f))
    print(f"Loaded {len(matches)} matches from {args.dataset}")
    print(f"Backtesting {len(CANDIDATE_STRATEGIES)} candidate strategies on a flat 1-unit stake "
          f"at market-implied odds (min {args.min_bets} bets to qualify):\n")

    ranked = backtest(matches, CANDIDATE_STRATEGIES)
    print(f"{'Strategy':<26} {'Bets':>6} {'Wins':>6} {'Win%':>7} {'Profit':>9} {'ROI%':>8}  Verdict")
    print("-" * 82)
    for r in ranked:
        verdict = "PROMOTE" if (r.bets >= args.min_bets and r.roi > 0) else \
                  ("thin" if r.bets < args.min_bets else "cut")
        flag = "  *" if r.bets >= args.min_bets and r.roi > 0 else ""
        print(f"{r.name:<26} {r.bets:>6} {r.wins:>6} {r.win_rate:>6.1f}% "
              f"{r.profit:>9.1f} {r.roi:>7.1f}%  {verdict}{flag}")

    winners = [r for r in ranked if r.bets >= args.min_bets and r.roi > 0]
    print(f"\n{len(winners)} strategy/strategies beat the market on this dataset.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
