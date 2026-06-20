# Strategy Evolution Report — 2026-06-20
_Generated 2026-06-20T17:30:39 | live window: last 30 days | backtest: 12,942 matches_

| Strategy | Backtest ROI | Live bets | Live acc | Live ROI | Live profit | Verdict |
|----------|-------------:|----------:|---------:|---------:|------------:|---------|
| contrarian_elo | — | 1 | 100.0% | +0.0% | +0.00 | NEW |
| home_court | — | 3 | 100.0% | +0.0% | +0.00 | NEW |
| elo_market_agree | — | 16 | 75.0% | -0.7% | -0.11 | WATCH |
| market_strong | — | 12 | 91.7% | -1.7% | -0.20 | WATCH |
| pure_elo | — | 23 | 73.9% | -1.7% | -0.40 | WATCH |
| underdog_value | — | 5 | 60.0% | -80.4% | -4.02 | NEW |
| conservative | — | 3 | 33.3% | -333.3% | -10.00 | NEW |
| aggressive | — | 11 | 27.3% | -336.3% | -36.99 | CUT |
| balanced | — | 11 | 27.3% | -336.3% | -36.99 | CUT |
| lightgbm_calibrated | — | 12 | 25.0% | -349.9% | -41.99 | CUT |
| away_dominant | +3.7% | 0 | 0.0% | +0.0% | +0.00 | WATCH (live sample too small) |
| clear_favorite | +3.1% | 0 | 0.0% | +0.0% | +0.00 | WATCH (live sample too small) |
| contrarian_home_coinflip | +5.1% | 0 | 0.0% | +0.0% | +0.00 | WATCH (live sample too small) |
| home_market_favorite | +2.9% | 0 | 0.0% | +0.0% | +0.00 | WATCH (live sample too small) |
| market_extreme | +3.6% | 0 | 0.0% | +0.0% | +0.00 | WATCH (live sample too small) |
| market_strong_plus | +4.1% | 0 | 0.0% | +0.0% | +0.00 | WATCH (live sample too small) |
| moderate_home_favorite | +1.9% | 0 | 0.0% | +0.0% | +0.00 | WATCH (live sample too small) |

## Action
- **Keep**: none yet (need live sample)
- **Cut (3)**: aggressive, balanced, lightgbm_calibrated

## How to read this
- **Backtest ROI** = historical edge at fair odds. Anything below ~+5% likely breaks even or loses after the bookmaker margin.
- **Live ROI** = real graded return. This is the number that matters for profit. A strategy must stay positive live to survive.
- Verdict: KEEP = profitable live, CUT = clearly losing, WATCH = too early or borderline.
- Re-run daily. Strategies that stay KEEP for 30+ live bets are your real winners.
