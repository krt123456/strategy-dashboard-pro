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

### ADDENDUM (same day, deep multi-day lab) — draw-corrected verdicts + APEX ELITE

A 16-day open→close lab (8,895 matches with final scores) re-tested everything with
**draws counted as losses** (the football numbers above were draw-excluded and
therefore inflated — 21% of football matches end drawn).

**Draw-corrected football verdicts:** away band 1.5-2.6 = **-4%** (dead), drift-fade =
0%, band+steam = -8%; only away-steam survives at +10-14% with weak day-stability
(7-10/16 days). Conclusion: football odds-band rules do NOT survive the draw tax;
the live tournament will pass final judgment on the football apex strategies.

**What survives every filter (draw-safe sports, day-by-day stability over 16 days):**

| edge | n | ROI | days positive |
|---|---|---|---|
| TT Czech home 2.0-3.5 | 509 | +28% | 15/16 |
| · night slice 00-04 UTC | 98 | **+55%** | 13/15 |
| Baseball away NON-minor 1.5-2.6 | 424 | +9% | 12/16 |
| · (Minor League away = -6% → excluded) | 191 | -6% | 5/13 |
| Tennis away 2.6-3.5 calm line (|mv|<5%) | 314 | +21% | 12/16 |
| Cricket away 1.7-2.8 | 60 | +22% | 6/10 |
| Volleyball home 1.5-3.0, line not against | 31 | +23% | 3/5 |

**APEX ELITE (4 strategies, append-only):**
- `apex_tt_night_cz` (A) — the crown jewel: Czech TT home, night window 00-04 UTC.
- `apex_bsb_road_pro` (B) — baseball away with the Minor-League poison removed.
- `apex_tennis_calm_dog` (B) — the calm-line filter that turns the tennis dog band
  into a stable edge (confirms: stable dogs win, drifting dogs lose).
- `apex_alpha_basket` (A) — one bankroll riding all five surviving legs across
  uncorrelated draw-free sports; the smoothest expected equity curve in the system.

Method note: every number here is unique-match, draw-as-loss, and day-stratified;
in-sample vs the same 16 days the system lived through — live performance from
2026-07-10 onward is the true out-of-sample test.


---

## 🔴 2026-07-12 — تصحيح جوهري: خطأ تسجيل التعادل (Draw-Scoring Bug) + دفعة OMEGA (الجيل الرابع)

### 1) الخطأ المكتشف (الأخطر في تاريخ النظام)
- **العطب**: في `resolve_results_betexplorer.py` كان `won = (side=="away" and not home_won)` — أي **كل تعادل يُسجَّل فوزًا لرهان الضيف** بكامل الـ odds (حتى 17x). نفس النمط كان في `check_results.py` (_grade).
- **السليم أصلًا**: `resolve_results_api_sports.py` (يشترط تفوّق الضيف الصريح)، `flashscore_resolver.py`، و`resolve_results_scores24.py` (يتخطى التساوي).
- **الحجم**: 1,367 نتيجة وهمية (football 1,343 + cricket 24، كلها betexplorer) بربح زائف **+2,671 وحدة**.
- **الإصلاح** (append-only على الكود، جراحي على البيانات):
  1. رقعة المحلّلَين → `away_pts > home_pts` صراحة.
  2. قلب الصفوف الملوثة: `pick_won=0, outcome=LOST, profit=-1.0, roi_pct=-100`.
  3. إعادة بناء مجاميع جدول `strategies` من الحقيقة الأرضية.
  4. نسخ احتياطية: `/root/backups_strategy/20260712_drawfix/`.

### 2) ما الذي تغيّر في الحقيقة بعد التنظيف؟ (dedup-by-match، دوريات حقيقية فقط)
| الادعاء القديم (الملوث) | الحقيقة النظيفة |
|---|---|
| football away band +27.8-65.9% | **ميت**: 1.5-2 = -7.3% (n=199)، 3.5-6 = -7.3% (n=88)، <1.5 = -12.5% |
| steam football +49-70% | **هامشي**: +0.2% (n=121) و drift-fade +2.5% (n=171) على التكرار المستقل draw-correct |
| رهان التعادل الضيّق (gap<0.4 → 41.7%) | **مرفوض**: على 2,105 مباراة مستقلة بأودز تعادل حقيقية: gap<0.30 = -22.2%، والقاعدة gap<0.45 & DO≥3 = **-15.1%** (n=213) — الإشارة كانت ضجيج n=12 |
| TT تشيكي ليلي | **صامد ويتضخم**: Pro League CZ home ليلًا 00-04 UTC = **+19.8% (n=213)** مقابل نهارًا **-13.6% (n=574)** |

### 3) الحواف النظيفة المؤكدة (أساس OMEGA)
1. **CZ ليلي بالنطاق**: home 1.7-2.0 = **+26.2% (n=114)**، 2.0+ = **+38.7% (n=27)** — والجزء 1.7-2.0 كانت `apex_tt_night_cz` تقصّه (أرضيتها 2.00 + تشترط بيانات حركة = تغطية ~40% فقط).
2. **تعميم الليل عبر الدوريات**: TT home 2.0-2.6 — ليل 00-04 = **+33.8% (n=89)**، متأخر 20-23 = **+21.0% (n=38)**، النهار ≈ 0/سالب.
3. **KBO فجرًا (مساء كوريا)**: baseball home ≤2.05 في 05-09 UTC = **+10.7% (n=46)** (NPB نفس الشريحة -0.8% → كوري فقط).
4. مرفوضات بعد التفكيك: foot away 6+ (+33% لكن LEAGUE-only n=20 والباقي وديات)؛ Wimbledon away +30% (تنتهي البطولة)؛ volleyball/handball/cricket لا شيء موجب.

### 4) العشرة OMEGA — لا، أربعة فقط (جودة لا كمّ، كلها بلا شرط حركة → تغطية كاملة)
| الاستراتيجية | القاعدة | الدليل النظيف | الفئة |
|---|---|---|---|
| `omega_tt_cz_night` | TT Pro League CZ، home، 00-04 UTC، odds 1.70-3.20 | +28.6% (n=141) | A |
| `omega_tt_dog_late` | TT أي دوري، home، 20:00-04:59 UTC، odds 2.00-2.60 | ~+30% (n=127) | B |
| `omega_kbo_dawn` | KBO home ≤2.05، 05-09 UTC | +10.7% (n=46) | C-راقب |
| `omega_clean_basket` | محفظة الأرجل الثلاث برأس مال واحد (بديل أطروحة alpha_basket بعد موت أرجلها) | مركّب | A |
- التنفيذ: قسم OMEGA في `expert_strategies.py` + تسجيل في `EXPERT_STRATEGIES` و`expert_fns`. smoke tests اجتازت. البوابات لا تشترط `home_move` → تشتعل على كل صف linefeed (بخلاف apex).
- ملاحظة توقيت: TT تُدرج قبل انطلاقها بقليل؛ أول اشتعال متوقع في دورات الليل (00/02/04 UTC).

### 5) القراءة الحية المصححة منذ 2026-07-10 (للسجل)
- apex_steam_foot +46.6% (n=21) و apex_multi_conviction +27.6% (n=22) — عينات صغيرة، البطولة تحكم.
- apex_foot_away_value انهارت من +27.9% الملوثة إلى **-17.7%** — تطابق تام مع تنبؤ تحليل ELITE (الحافة كانت أثر الخطأ).
- alpha_basket **-12.2% (n=71)** — أرجلها (bsb road -14.1، tennis calm -16.7، tennis dog -13.5) سالبة نظيفةً؛ خليفتها المفاهيمي omega_clean_basket.
- ⚠️ درس منهجي دائم: **أي تعدين مستقبلي يجب أن يحسب التعادل خسارة لرهانات الجانبين** — وأي رقم football تاريخي قبل 2026-07-12 مشكوك فيه ما لم يُعد حسابه من scores الخام.
