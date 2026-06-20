# Strategy Intelligence — Expert Analysis Log

> This file captures the expert root-cause analysis that drives strategy evolution.
> The mechanical variant generator sweeps parameters; THIS document holds the
> reasoning behind fundamental strategy improvements. Updated each analysis pass.

## Pass 1 — 2026-06-20 (after 188 graded results)

### Root cause #1: the favorite–longshot vig trap
Live results by odds zone:
| Odds zone | Bets | Win% | Profit | Diagnosis |
|-----------|-----:|-----:|-------:|-----------|
| <1.5      | 65   | 80%  | −4.87  | High accuracy, but low payout barely covers the 20% losses |
| 1.5–2.0   | 92   | 50%  | **−82.27** | THE KILLER. Breakeven at ~1.75 odds is 57%; we hit 50% |
| 2.0–2.8   | 30   | 30%  | −26.98 | Betting mid underdogs with no edge |

**Why:** The "value" filters (EV > 0) measure edge at FAIR odds. But we bet at
BOOKMAKER odds which carry a ~5–9% vig. A thin fair-odds edge (EV +3%) becomes
EV −2% to −6% after vig. The 1.5–1.8 zone has only +2% fair edge → it dies live.

**Fix:** A real value bet needs `model_prob − fair_market_prob > vig + margin`,
not just `EV > 0`. Compute the vig from the two bookmaker odds (overround − 1)
and require the edge to clear it. This is the single highest-leverage fix.

### Root cause #2: broken contrarian strategies
`aggressive` / `balanced` / `conservative` basketball strategies hit ~27% win
rate — BELOW the 59% home baseline. They bet against favorites with no model
edge (pure gambler's fallacy). They are anti-edges and must be cut.

### Root cause #3: LightGBM fed placeholder features
At prediction time `multi_strategy_agent` feeds the model hardcoded values
(home_form_5=0.5, rest_days=3, pts=75, h2h=0.5). The model was trained on real
features, so its signal is destroyed → 25% accuracy, −339% ROI. Either compute
real form/rest features or retire the model. Until fixed, treat its picks as
noise.

### Root cause #4: sport specialization matters
`qualifier_value` wins on tennis (+73%, +$4.96) but the same logic on baseball
loses. `market_consensus` is +0.16 on tennis but −2.80 on baseball. Edge is
sport-specific — one rule does not fit all. Per-sport variant selection is the
next evolution step.

### The one genuinely durable edge found
`contrarian_home_coinflip` (home market prob 0.48–0.58) → +7.3% backtest ROI.
**Why it works:** when the market is genuinely unsure (coin-flip), bookmakers
slightly underprice home advantage (a real structural edge). This is a
behavioural inefficiency, not a parameter fluke — it should survive live.

---

## New expert strategies from this analysis

1. **vig_aware_value** — bet only when model edge exceeds the actual bookmaker
   vig. Kills the 1.5–1.8 trap. Implemented in `expert_strategies.py`.
2. **thick_edge_favorite** — restrict to the extreme-favorite zone (fair odds
   <1.3) where the edge is thickest and most vig-resistant.
3. **retire broken** — cut aggressive/balanced/conservative/uncalibrated-lightgbm.
4. **per-sport variant routing** — route each variant only to sports where it
   backtests positive (next pass).
