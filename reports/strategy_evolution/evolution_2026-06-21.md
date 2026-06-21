# Strategy Evolution Report — 2026-06-21
_Generated 2026-06-21T12:11:18 | live window: last 30 days | backtest: 12,942 matches_

| Strategy | Backtest ROI | Live bets | Live acc | Live ROI | Live profit | Verdict |
|----------|-------------:|----------:|---------:|---------:|------------:|---------|
| market_extreme_v8__src_market | — | 1 | 100.0% | +13.0% | +0.13 | NEW |
| market_extreme_v7__src_market | — | 2 | 100.0% | +12.0% | +0.24 | NEW |
| moderate_home_favorite_v1__src_market | — | 7 | 85.7% | +11.6% | +0.81 | NEW |
| moderate_home_favorite | +1.9% | 6 | 66.7% | +10.2% | +0.61 | WATCH (live sample too small) |
| market_extreme_v8__src_elo | — | 1 | 100.0% | +8.0% | +0.08 | NEW |
| market_strong_plus | +4.1% | 7 | 71.4% | +3.9% | +0.27 | WATCH (live sample too small) |
| market_strong_plus_v3__src_market | — | 7 | 71.4% | +3.9% | +0.27 | NEW |
| market_strong_plus_v4__src_market | — | 7 | 71.4% | +3.9% | +0.27 | NEW |
| market_extreme | +3.6% | 6 | 66.7% | +3.7% | +0.22 | WATCH (live sample too small) |
| away_dominant_v4__src_market | — | 9 | 77.8% | +2.0% | +0.18 | NEW |
| contrarian_elo | — | 1 | 100.0% | +0.0% | +0.00 | NEW |
| clear_favorite | +3.1% | 19 | 68.4% | -0.6% | -0.11 | WATCH |
| contrarian_home_coinflip_v5__src_market | — | 4 | 25.0% | -1.7% | -0.07 | NEW |
| home_market_favorite | +2.9% | 10 | 60.0% | -2.9% | -0.29 | WATCH |
| moderate_home_favorite_v7__src_market | — | 9 | 66.7% | -3.0% | -0.27 | NEW |
| elo_market_agree | — | 31 | 67.7% | -4.1% | -1.28 | WATCH |
| contrarian_home_coinflip_v4__src_market | — | 3 | 0.0% | -4.3% | -0.13 | NEW |
| home_court | — | 7 | 71.4% | -6.0% | -0.42 | NEW |
| away_dominant_v4__src_elo | — | 5 | 80.0% | -6.8% | -0.34 | NEW |
| away_dominant_v5__src_elo | — | 5 | 80.0% | -6.8% | -0.34 | NEW |
| clear_favorite_v6__src_market | — | 16 | 62.5% | -7.9% | -1.27 | CUT |
| home_market_favorite_v1__src_market | — | 13 | 61.5% | -9.0% | -1.17 | CUT |
| pure_elo | — | 43 | 67.4% | -10.6% | -4.55 | CUT |
| market_strong | — | 23 | 73.9% | -12.4% | -2.85 | CUT |
| away_dominant | +3.7% | 6 | 66.7% | -12.7% | -0.76 | WATCH (live sample too small) |
| away_dominant_v5__src_market | — | 6 | 66.7% | -12.7% | -0.76 | NEW |
| contrarian_home_coinflip_v4__src_elo | — | 7 | 57.1% | -13.0% | -0.91 | NEW |
| contrarian_home_coinflip_v5__src_elo | — | 5 | 60.0% | -18.2% | -0.91 | NEW |
| contrarian_home_coinflip | +5.1% | 7 | 28.6% | -21.0% | -1.47 | WATCH (live sample too small) |
| clear_favorite_v6__src_elo | — | 16 | 62.5% | -22.4% | -3.59 | CUT |
| market_strong_plus_v3__src_elo | — | 6 | 66.7% | -22.7% | -1.36 | NEW |
| clear_favorite_v8__src_market | — | 11 | 54.5% | -24.2% | -2.66 | CUT |
| moderate_home_favorite_v1__src_elo | — | 9 | 33.3% | -25.3% | -2.28 | NEW |
| moderate_home_favorite_v7__src_elo | — | 9 | 33.3% | -25.3% | -2.28 | NEW |
| clear_favorite_v8__src_elo | — | 13 | 61.5% | -27.1% | -3.52 | CUT |
| home_market_favorite_v1__src_elo | — | 17 | 41.2% | -32.1% | -5.46 | CUT |
| home_market_favorite_v7__src_market | — | 6 | 33.3% | -33.0% | -1.98 | NEW |
| market_strong_plus_v4__src_elo | — | 5 | 60.0% | -33.8% | -1.69 | NEW |
| home_market_favorite_v7__src_elo | — | 8 | 50.0% | -39.8% | -3.18 | NEW |
| market_extreme_v7__src_elo | — | 2 | 50.0% | -46.0% | -0.92 | NEW |
| underdog_value | — | 17 | 41.2% | -52.5% | -8.92 | CUT |
| aggressive | — | 24 | 45.8% | -161.1% | -38.66 | CUT |
| balanced | — | 22 | 40.9% | -205.9% | -45.29 | CUT |
| lightgbm_calibrated | — | 19 | 31.6% | -267.6% | -50.85 | CUT |
| conservative | — | 4 | 25.0% | -375.0% | -15.00 | NEW |

## Action
- **Keep**: none yet (need live sample)
- **Cut (12)**: clear_favorite_v6__src_market, home_market_favorite_v1__src_market, pure_elo, market_strong, clear_favorite_v6__src_elo, clear_favorite_v8__src_market, clear_favorite_v8__src_elo, home_market_favorite_v1__src_elo, underdog_value, aggressive, balanced, lightgbm_calibrated

## How to read this
- **Backtest ROI** = historical edge at fair odds. Anything below ~+5% likely breaks even or loses after the bookmaker margin.
- **Live ROI** = real graded return. This is the number that matters for profit. A strategy must stay positive live to survive.
- Verdict: KEEP = profitable live, CUT = clearly losing, WATCH = too early or borderline.
- Re-run daily. Strategies that stay KEEP for 30+ live bets are your real winners.
