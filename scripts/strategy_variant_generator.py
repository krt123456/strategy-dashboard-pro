#!/usr/bin/env python3
"""Strategy variant evolution engine.

For every base strategy, generate a parameter sweep of evolved variants
(version 1..N), backtest each one on the full historical dataset, rank them by
real return, and promote the winners into the live rotation via a JSON config.

This is the engine behind the daily strategy tournament: each run explores the
neighborhood of every strategy, keeps what wins at scale, and drops what does
not. Over weeks the live pool converges on the strongest parameter settings,
and re-running after new data arrives keeps the selection honest.

Variants are named {base}_v{N} (the version word, per the naming convention).
The winners file (data/winning_variants.json) is consumed by
multi_strategy_agent.py so the live pick generator adapts automatically.
"""
from __future__ import annotations

import argparse
import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

PROJECT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DATASET = PROJECT_DIR / "data" / "basketball_betexplorer_backtest.csv"
WINNERS_PATH = PROJECT_DIR / "data" / "winning_variants.json"


@dataclass
class VariantResult:
    name: str
    base: str
    param_label: str
    bets: int = 0
    wins: int = 0
    profit: float = 0.0
    stake: float = 0.0

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


def implied_odds(p: float) -> float:
    return 1.0 / p if 0 < p < 1 else 1.0


# A variant rule: given a match dict + the variant's parameters, return 'home'/'away'/None.
RuleFn = Callable[[dict, dict], Optional[str]]


# ---------------------------------------------------------------------------
# Base strategy rules, parameterized so each can be swept.
# ---------------------------------------------------------------------------

def rule_favorite_threshold(m, p) -> Optional[str]:
    """Back any side whose market prob >= threshold (covers market_extreme /
    market_strong_plus style strategies)."""
    t = p["threshold"]
    ph, pa = _f(m["MARKET_PROB_home"]), _f(m["MARKET_PROB_away"])
    if ph >= t:
        return "home"
    if pa >= t:
        return "away"
    return None


def rule_favorite_margin(m, p) -> Optional[str]:
    """Back the side when the prob margin >= margin (clear_favorite family)."""
    mg = p["margin"]
    ph, pa = _f(m["MARKET_PROB_home"]), _f(m["MARKET_PROB_away"])
    if ph - pa >= mg:
        return "home"
    if pa - ph >= mg:
        return "away"
    return None


def rule_home_threshold(m, p) -> Optional[str]:
    """Back home when market prob in [lo, hi] band (home_market_favorite /
    moderate_home_favorite / contrarian_home_coinflip family)."""
    lo, hi = p["lo"], p["hi"]
    ph = _f(m["MARKET_PROB_home"])
    if lo <= ph <= hi:
        return "home"
    return None


def rule_away_threshold(m, p) -> Optional[str]:
    """Back away when market prob in [lo, hi] band (away_dominant family)."""
    lo, hi = p["lo"], p["hi"]
    pa = _f(m["MARKET_PROB_away"])
    if lo <= pa <= hi:
        return "away"
    return None


# Variant factory: base name -> (rule function, list of param dicts to sweep).
VARIANT_SPEC: Dict[str, Tuple[RuleFn, List[dict]]] = {
    "market_extreme": (rule_favorite_threshold, [
        {"threshold": t} for t in (0.74, 0.76, 0.78, 0.80, 0.82, 0.84, 0.86, 0.88)
    ]),
    "market_strong_plus": (rule_favorite_threshold, [
        {"threshold": t} for t in (0.71, 0.73, 0.75, 0.77, 0.79, 0.81)
    ]),
    "clear_favorite": (rule_favorite_margin, [
        {"margin": t} for t in (0.18, 0.20, 0.22, 0.25, 0.28, 0.30, 0.35, 0.40)
    ]),
    "home_market_favorite": (rule_home_threshold, [
        {"lo": lo, "hi": hi} for lo, hi in
        [(0.56, 1.0), (0.58, 1.0), (0.60, 1.0), (0.62, 1.0), (0.64, 1.0), (0.66, 1.0), (0.68, 1.0)]
    ]),
    "contrarian_home_coinflip": (rule_home_threshold, [
        {"lo": lo, "hi": hi} for lo, hi in
        [(0.40, 0.50), (0.42, 0.52), (0.44, 0.54), (0.46, 0.56), (0.48, 0.58), (0.40, 0.54), (0.44, 0.58)]
    ]),
    "moderate_home_favorite": (rule_home_threshold, [
        {"lo": lo, "hi": hi} for lo, hi in
        [(0.56, 0.68), (0.58, 0.70), (0.60, 0.72), (0.62, 0.74), (0.58, 0.74), (0.60, 0.70), (0.56, 0.72)]
    ]),
    "away_dominant": (rule_away_threshold, [
        {"lo": lo, "hi": 1.0} for lo in (0.62, 0.64, 0.66, 0.68, 0.70, 0.72, 0.74, 0.76)
    ]),
}


def _param_label(p: dict) -> str:
    if "threshold" in p:
        return f"thr={p['threshold']:.2f}"
    if "margin" in p:
        return f"margin={p['margin']:.2f}"
    return f"band={p['lo']:.2f}-{p['hi']:.2f}"


def generate_variants() -> List[Tuple[str, str, str, RuleFn, dict]]:
    """Return (variant_name, base, param_label, rule_fn, params) for every variant."""
    out: List[Tuple[str, str, str, RuleFn, dict]] = []
    for base, (rule_fn, params_list) in VARIANT_SPEC.items():
        for i, params in enumerate(params_list, start=1):
            name = f"{base}_v{i}"
            out.append((name, base, _param_label(params), rule_fn, params))
    return out


def backtest_variant(matches: List[dict], rule_fn: RuleFn, params: dict) -> Tuple[int, int, float, float]:
    bets = wins = 0
    profit = stake = 0.0
    for m in matches:
        if m["STATUS"] != "finished" or not m["PTS_home"] or not m["PTS_away"]:
            continue
        side = rule_fn(m, params)
        if side is None:
            continue
        prob = _f(m["MARKET_PROB_home"]) if side == "home" else _f(m["MARKET_PROB_away"])
        if prob <= 0:
            continue
        odds = implied_odds(prob)
        home_won = int(m["HOME_TEAM_WINS"]) == 1
        won = (side == "home" and home_won) or (side == "away" and not home_won)
        bets += 1
        stake += 1.0
        if won:
            wins += 1
            profit += odds - 1.0
        else:
            profit -= 1.0
    return bets, wins, profit, stake


def run_tournament(min_bets: int = 100) -> Tuple[List[VariantResult], List[dict]]:
    with open(DEFAULT_DATASET, encoding="utf-8") as f:
        matches = list(csv.DictReader(f))

    variants = generate_variants()
    results: List[VariantResult] = []
    winners: List[dict] = []
    for name, base, label, rule_fn, params in variants:
        bets, wins, profit, stake = backtest_variant(matches, rule_fn, params)
        r = VariantResult(name, base, label, bets, wins, profit, stake)
        results.append(r)
        # promote: positive ROI at scale, robust sample
        if bets >= min_bets and r.roi > 0:
            winners.append({
                "name": name,
                "base": base,
                "params": params,
                "backtest_roi": round(r.roi, 2),
                "backtest_bets": bets,
                "backtest_win_rate": round(r.win_rate, 1),
            })

    results.sort(key=lambda r: r.roi, reverse=True)
    # winners: keep top variant per base, plus any others clearly ahead.
    winners.sort(key=lambda w: w["backtest_roi"], reverse=True)
    return results, winners


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate + backtest strategy variants and promote winners.")
    ap.add_argument("--min-bets", type=int, default=100, help="min backtest sample to promote a variant")
    ap.add_argument("--top-per-base", type=int, default=2, help="max winners kept per base strategy")
    ap.add_argument("--save", action="store_true", default=True, help="write data/winning_variants.json")
    args = ap.parse_args()

    results, winners = run_tournament(min_bets=args.min_bets)
    print(f"Generated + backtested {len(results)} variants on the historical dataset.\n")
    print(f"{'Variant':<32} {'Base':<26} {'Param':<16} {'Bets':>6} {'Win%':>6} {'ROI%':>7}  Verdict")
    print("-" * 100)
    for r in results:
        ok = r.bets >= args.min_bets and r.roi > 0
        verdict = "WINNER" if ok else ("thin" if r.bets < args.min_bets else "neg")
        print(f"{r.name:<32} {r.base:<26} {r.param_label:<16} {r.bets:>6} "
              f"{r.win_rate:>5.1f}% {r.roi:>6.1f}%  {verdict}")

    # cap winners per base to the strongest few
    by_base: Dict[str, List[dict]] = {}
    for w in winners:
        by_base.setdefault(w["base"], []).append(w)
    capped: List[dict] = []
    for base, lst in by_base.items():
        capped.extend(lst[:args.top_per_base])
    capped.sort(key=lambda w: w["backtest_roi"], reverse=True)

    print(f"\n{len(winners)} variants beat the market at scale; promoting top "
          f"{args.top_per_base} per base = {len(capped)} live variants:")
    for w in capped:
        print(f"  {w['name']:<32} ROI {w['backtest_roi']:+.1f}%  "
              f"({w['backtest_bets']} bets, {w['backtest_win_rate']}% win)  [{w['params']}]")

    if args.save:
        WINNERS_PATH.parent.mkdir(parents=True, exist_ok=True)
        WINNERS_PATH.write_text(json.dumps(capped, indent=2), encoding="utf-8")
        print(f"\nSaved {len(capped)} winners → {WINNERS_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
