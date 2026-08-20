# Publication strategy audit — 2026-08-20

This report was generated from the VPS database in read-only mode. The reproducible implementation is `scripts/publication_backtest.py`; no VPS files were changed.

## Decision

- Keep `nova_fade_favorite__xbet_linefeed` as **legacy active**.
- Do **not** promote a newly tuned strategy from this retrospective sample.
- Treat “at most five lowest decimal odds per day” only as an **operational forward-validation policy**.
- Publication guard: first prediction per `(strategy, date, sport, home, away, pick)`, captured odds greater than 1.0, valid `start_utc`, and prediction at least 15 minutes before the match.
- Historical naive `created_at` values are localized with `ZoneInfo("Europe/Berlin")` and then converted to UTC before comparison with `start_utc`.

## Snapshot and cutoff

- Database range: 2026-06-18 through 2026-08-09.
- Predictions: 309,417; settled prediction IDs: 273,660; duplicate result rows: 0.
- Used strategy names: 165; registered strategy rows: 7; unregistered names: 158.
- `real_odds` is null on all 309,417 rows. For this strategy, the captured raw price is stored in `odds_at_prediction`.
- Automatic conservative cutoff: **2026-08-03**, the last complete day before the first incomplete day (2026-08-04).
- Pending rows from 2026-08-04 through 2026-08-09: 35,757.

For the legacy strategy, 6,238 raw rows equal 6,238 first unique rows, so deduplication removed zero rows. After timezone correction, 363 rows (5.82%) were after start, 306 were 0–15 minutes before start, 22 had missing/invalid start time, and 5,547 passed the lead/odds gate.

## Complete-cutoff unit backtest

| Set | Bets | W-L | Win rate | Average odds | Unit P&L | ROI | Day-cluster 95% CI |
|---|---:|---:|---:|---:|---:|---:|---:|
| Legacy, all eligible | 4,281 | 1,559–2,722 | 36.42% | 3.49 | +939.57 | +21.95% | +16.01% to +27.93% |
| Max-5 lowest odds, retrospective | 195 | 94–101 | 48.21% | 2.57 | +47.11 | +24.16% | +6.89% to +41.37% |

The bootstrap uses 5,000 deterministic resamples of whole match days, seed `20260820`, preserving every bet within a sampled day. The max-5 row is descriptive and must not be treated as an independently validated strategy.

## Weekly stability

### Legacy all eligible

| ISO week | Dates | Bets | W-L | Unit P&L | ROI |
|---|---|---:|---:|---:|---:|
| 2026-W26 | Jun 26–28 | 332 | 129–203 | +90.10 | +27.14% |
| 2026-W27 | Jun 29–Jul 5 | 747 | 265–482 | +150.24 | +20.11% |
| 2026-W28 | Jul 6–12 | 867 | 308–559 | +174.26 | +20.10% |
| 2026-W29 | Jul 13–19 | 796 | 273–523 | +111.07 | +13.95% |
| 2026-W30 | Jul 20–26 | 783 | 302–481 | +230.72 | +29.47% |
| 2026-W31 | Jul 27–Aug 2 | 717 | 266–451 | +169.90 | +23.70% |
| 2026-W32 | Aug 3 only | 39 | 16–23 | +13.28 | +34.05% |

### Max-5 lowest odds, retrospective

| ISO week | Dates | Bets | W-L | Unit P&L | ROI |
|---|---|---:|---:|---:|---:|
| 2026-W26 | Jun 26–28 | 15 | 5–10 | -2.25 | -15.00% |
| 2026-W27 | Jun 29–Jul 5 | 35 | 19–16 | +13.86 | +39.60% |
| 2026-W28 | Jul 6–12 | 35 | 15–20 | +3.56 | +10.17% |
| 2026-W29 | Jul 13–19 | 35 | 18–17 | +11.33 | +32.37% |
| 2026-W30 | Jul 20–26 | 35 | 17–18 | +8.69 | +24.83% |
| 2026-W31 | Jul 27–Aug 2 | 35 | 17–18 | +9.10 | +26.00% |
| 2026-W32 | Aug 3 only | 5 | 3–2 | +2.82 | +56.40% |

## Resolution and result-source bias

Across every eligible legacy row through 2026-08-09, 4,758/5,547 (85.78%) are settled and 789 remain pending. Observed settled ROI is +23.15%. Treating every pending row as a loss gives +5.63%; treating every pending row as a win at captured odds gives +54.00%. Results after the complete cutoff must therefore remain excluded.

| Result source | Rows | Share | Settled | Win rate | ROI |
|---|---:|---:|---:|---:|---:|
| BetExplorer | 4,089 | 73.72% | 4,089 | 36.56% | +23.45% |
| Pending | 789 | 14.22% | 0 | — | — |
| Scores24 | 526 | 9.48% | 526 | 41.25% | +25.04% |
| API-Sports | 93 | 1.68% | 93 | 30.11% | +29.31% |
| Flashscore scrape | 50 | 0.90% | 50 | 16.00% | -33.18% |

## Paper $100 convention

The same notional $100 is reset each active day and divided equally across that day's published picks; this is not a continuously compounded bankroll.

- Legacy all-eligible illustration: 39 days, $3,900 turnover, +$862.20 net, +22.11% on turnover; 34 winning and 5 losing days.
- Max-5 illustration: 39 days, exactly five $20 paper stakes per day, $3,900 turnover, +$942.20 net, +24.16%; 31 winning and 8 losing days. Worst day -$100; best day +$162.

For a daily result post with `n` picks, each paper stake is `$100 / n`. A win at decimal odds `o` contributes `stake × (o - 1)` and a loss contributes `-stake`. Daily net is the sum and displayed ending value is `$100 + net`.
