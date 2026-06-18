# Strategy Dashboard Pro — Cloud Agent

نظام توقع رياضي يعمل تلقائياً على GitHub Actions.
يجمع توقعات من مصادر متعددة، يطبّق استراتيجيات مختلفة، ويتابع النتائج يومياً.

## التشغيل التلقائي
- يعمل كل يوم 08:00 UTC على GitHub Actions
- لا يحتاج تشغيل حاسوبك
- النتائج محفوظة في المستودع

## المصادر
- LightGBM model (مدرب على 12,942 مباراة)
- 1xBet linefeed (أسعار حقيقية)
- ESPN scoreboard (نتائج مجانية)
- OpenLigaDB (Bundesliga)
- football-data.co.uk (EPL)

## الاستراتيجيات (11)
1. `lightgbm_calibrated` — LightGBM + isotonic calibration
2. `pure_elo` — ELO ratings only
3. `market_strong` — market consensus when prob > 70%
4. `elo_market_agree` — bet when ELO and market agree
5. `contrarian_elo` — bet against market when ELO disagrees
6. `underdog_value` — bet underdogs with close ELO
7. `home_court` — bet home teams with advantage
8. `market_consensus` — 1xBet real odds
9. `conservative` — high prob only
10. `balanced` — moderate risk
11. `aggressive` — more bets, higher variance

## الملفات
- `scripts/` — كل السكربتات
- `models/` — النماذج المدربة
- `data/` — البيانات و betting_journal.db
- `reports/` — التقارير اليومية
- `.github/workflows/daily-agent.yml` — جدولة GitHub Actions

## API Keys (اختياري)
أضف في Settings → Secrets:
- `ODDS_API_KEY` — The Odds API key (500 req/month free)
