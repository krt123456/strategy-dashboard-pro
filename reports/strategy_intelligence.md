# Strategy Intelligence — Expert Analysis Log

> This file captures the expert root-cause analysis that drives strategy evolution.
> The mechanical variant generator sweeps parameters; THIS document holds the
> reasoning behind fundamental strategy improvements. Updated each analysis pass.

## Pass 2 — 2026-06-20 (after 278 graded results, football+volleyball resolver fix)

### Diagnostic: why 64% of results were missing
After the fix, we went from 234 → 278 graded results (+44 in one run). Root cause:
- Football (693 matches visible) and volleyball (57) use `table-main__tt` format, not `teamLine--home/away`
- Handball is JS-rendered (skeleton-only page) — needs browser
- Darts and tabletennis result pages are empty (no date-based URL on betexplorer)

### Fix applied (resolve_results_betexplorer.py)
Added `_parse_tt_format()` fallback path that extracts:
- Team names from "TeamA - TeamB" in `<td class="table-main__tt">`
- Scores from `<td class="table-main__result">`
- Applied to football, volleyball, and hockey hybrid rows

Remaining gap: tabletennis (244 pending, 0 graded) — betexplorer has 856 matches parsed but name-matching fails (doubles, non-Latin scripts). Needs per-league approach.

---

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

---

## APEX suite (Generation 3) — 2026-07-10 expert analysis

**Evidence base:** 94,088 resolved live bets (dedup-aware: unique matches only) + an
independent replication on the raw linefeed history file (17,558 matches, open→close
odds joined to final scores — a dataset no prior strategy was mined from).

### Root-cause findings

1. **Football linefeed is the softest market in the feed.** The away side is
   systematically underpriced: dedup +27.8% ROI in real leagues (n=303) and +34.6% in
   friendlies (n=189) at odds 1.5-2.6, while the home side loses in every band.
   Explanation: the niche feed prices a stock home advantage into fixtures
   (friendlies, minor leagues, youth) where it barely exists.
2. **Steam works away-only, and football-first.** nova_steam_away is +40.7% overall
   (dedup n=698) with football at +70.9% (n=319); the open→close replication confirms
   +49.7% (n=162) football, +8.8% baseball, ~0 tennis, negative elsewhere.
   nova_steam_home is NEGATIVE (-24.5) — sharp money shows on away prices only
   (retail piles on home/fav, so a home shortening is noise, an away shortening is signal).
3. **NEW SIGNAL — drift-fade:** when the favorite's odds LENGTHEN ≥3% open→close,
   backing the dog at 1.5-6.0 returns +38.5% (n=152) in football — and is negative in
   tennis/baseball/volleyball. First strategy family to exploit lengthening (not
   shortening) odds.
4. **The TT home edge is Czech-specific, not global.** Pro League CZ +28.1% (n=197),
   Setka CZ +27.9% (n=73), Setka Women +53.6% (n=52) — but plain (UA) Setka Cup is
   flat (+0.4%, n=251). League-conditioning is mandatory in TT.
5. **Novelty football markets are toxic:** Team-vs-Player / 8x8 / FIFA-cyber run
   -24.3% on the same away rule that earns +28% in real leagues → excluded from every
   apex strategy.
6. **Smaller pockets (watch-list):** cricket away 1.8-2.6 +52.3% (n=83), volleyball
   home-dog 2.0-2.6 +45.5% (n=83), tennis away 2.6-3.5 +17.7% (n=290). Shipped at
   confidence C; the live tournament will confirm or cut.

### The 10 apex_* strategies (append-only, linefeed real-odds only)

| strategy | rule | evidence |
|---|---|---|
| apex_steam_foot (A) | football away steam ≤-3%, 1.5-8.0 | +71% dedup n=319; +50% hist n=162 |
| apex_drift_fade_foot (B) | football fav drift ≥+3% → dog 1.5-6.0 | +39% hist n=152 |
| apex_foot_away_value (B) | football away 1.5-2.6, no novelty | +28% dedup n=492 |
| apex_tt_czech_home (B) | TT home 2.0-3.5, Czech/Setka-Women | +28% dedup n=479 |
| apex_tennis_dog_away (C) | tennis away 2.6-3.5 | +18% dedup n=290 |
| apex_cricket_away (C) | cricket away 1.8-2.6 | +52% n=83 (small) |
| apex_volley_home_dog (C) | volleyball home-dog 2.0-3.5 | +45% n=83 (small) |
| apex_away_convergence (B) | foot/bsb away 1.7-2.6 + line not against | composite |
| apex_steam_dog (B) | foot/bsb away steam on the dog 2.2-6.0 | steam × dog sharpening |
| apex_multi_conviction (A) | football away, 2 of 3 signals agree | stacked +EV signals |

All apex functions require the odds-movement fields to be present, which restricts
them to the real-odds 1xBet linefeed — the market the evidence came from — instead of
prob-derived pseudo-odds sources.

**Honesty note:** the dedup-live numbers are in-sample (mined from the same journal
that will grade them); the history-replication numbers are out-of-dataset. Treat live
performance from 2026-07-10 onward as the real test. Nothing existing was modified:
all 59 prior strategies keep running unchanged.
