#!/usr/bin/env python3
"""Build the professional bilingual strategy-monitoring PWA from the database.

Home = best-bet hero + full strategy monitor (bankroll from $100, 4% flat stake,
record, days, trust score 0-100, risk badge, streak). Tap a strategy -> its
matches with the bet-on team highlighted. ⋮ menu sorts/ filters. History tab.
Auto-rebuilt daily so it tracks every GitHub update and new strategy.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_DIR / "data" / "betting_journal.db"
OUT_DIR = PROJECT_DIR / "dashboard"
FLAT_STAKE = 4.0  # 4% of $100 bankroll per bet

# استراتيجيات معطّلة (تشخيص خبير: anti-edges / نموذج بميزات وهمية) — تُعرض كـ"مُقصاة" للشفافية
DISABLED_BASES = {"aggressive", "balanced", "conservative", "lightgbm_calibrated"}


def _f(v):
    try:
        return float(v) if v not in (None, "") else 0.0
    except Exception:
        return 0.0


def _risk(avg_odds: float) -> str:
    if avg_odds <= 0:
        return "?"
    if avg_odds < 1.6:
        return "safe"
    if avg_odds < 2.3:
        return "balanced"
    return "bold"


def _trust(wins: int, bets: int, roi: float, days: int) -> int:
    if bets == 0:
        return 0
    wr = wins / bets
    wr_pts = min(wr, 0.85) / 0.85 * 35
    sample_pts = min(bets / 50, 1) * 25
    roi_pts = max(-1, min(roi / 40, 1)) * 25
    if roi < 0:
        roi_pts = max(-15, roi / 10)  # negative ROI penalizes but not catastrophically
    days_pts = min(days / 14, 1) * 15
    return max(0, min(100, int(wr_pts + sample_pts + roi_pts + days_pts)))


def _rationale(strategy: str, odds: float, prob: float) -> dict:
    """One-line AR/EN explanation for why a pick was made."""
    base = strategy.split("__")[0].split("_v")[0]
    r = {
        "market_strong": ("توافق السوق على مرشّح قوي", "Strong market consensus favorite"),
        "pure_elo": ("نموذج ELO يفضّل هذا الفريق", "ELO model favors this side"),
        "market_extreme": ("مرشّح متطّرف جداً (ثقة عالية)", "Extreme favorite, high confidence"),
        "clear_favorite": ("هامش واضح لصالح هذا الفريق", "Clear favorite by margin"),
        "away_dominant": ("الفريق الضيف هو الأقوى", "Dominant away side"),
        "coinflip_home_premium": ("ميزة الأرض في مباراة متقاربة", "Home edge in a coinflip"),
        "contrarian_home_coinflip": ("ميزة الأرض المسعّرة ناقصاً", "Underpriced home advantage"),
        "home_market_favorite": ("المضيف + السوق يراه مرشّحاً", "Home + market favorite"),
        "underdog_value": ("قيمة في الخارج المهمَّش", "Value in the underdog"),
        "trapfree_favorite": ("مرشّح واضح بتجنّب فخّ الأسعار", "Clear fav, avoids odds trap"),
        "elo_coinflip_combo": ("ELO + مباراة متقاربة (تأكيد مزدوج)", "ELO + coinflip double-confirm"),
        "vig_aware_value": ("حافة تتجاوز عمولة الـ bookmaker", "Edge exceeds the vig"),
        "thick_edge_favorite": ("حافة سميكة vig-resistant", "Thick vig-resistant edge"),
    }.get(base, ("إشارة استراتيجية موجبة", "Positive strategy signal"))
    return {"ar": r[0], "en": r[1]}


def gather(today: str) -> dict:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # all graded results with everything needed for capital sim + display
    res_rows = c.execute(
        """SELECT p.id, p.strategy, p.source, p.match_date, p.sport, p.home, p.away,
                  p.pick, p.odds_at_prediction, r.home_score, r.away_score,
                  r.pick_won, r.checked_at
           FROM predictions p JOIN results r ON p.id = r.prediction_id
           ORDER BY r.checked_at"""
    ).fetchall()

    # group by strategy
    by_strat: dict = {}
    for row in res_rows:
        pid, strat, src, mdate, sport, home, away, pick, odds, hs, aso, won, chk = row
        odds = _f(odds)
        by_strat.setdefault(strat, []).append({
            "date": mdate, "sport": sport, "home": home, "away": away, "pick": pick,
            "odds": round(odds, 2), "won": bool(won), "hs": hs, "as": aso,
            "fp": 0.0,  # يُحسب أدناه حسب قاعدة 40% يومياً
        })

    DAILY_RISK = 0.40  # كل استراتيجية تراهن 40% من رأس مالها موزّعة على صفقات اليوم

    strategies = []
    strat_matches = {}
    for strat, items in by_strat.items():
        bets = len(items)
        wins = sum(1 for x in items if x["won"])
        # محاكاة رأس المال: 40% من الرصيد الحالي يومياً، موزّعة بالتساوي على صفقات اليوم
        # مهما كان عددها (compounding ديناميكي). مثال: $100 × 40% ÷ 4 صفقات = $10 لكل صفقة.
        bal = 100.0
        spark = [100.0]
        # رتّب حسب التاريخ ثم زمن التسجيل لمعالجة الأيام بالترتيب
        ordered = sorted(items, key=lambda x: (x["date"] or "",))
        i = 0
        while i < len(ordered):
            day = ordered[i]["date"]
            day_bets = [ordered[j] for j in range(i, len(ordered)) if ordered[j]["date"] == day]
            per_stake = (bal * DAILY_RISK) / len(day_bets)  # حصة كل صفقة من 40% اليومية
            for x in day_bets:
                fp = per_stake * (x["odds"] - 1) if x["won"] else -per_stake
                x["fp"] = round(fp, 2)
                bal += fp
            spark.append(round(bal, 1))
            i += len(day_bets)
        bankroll = round(bal, 2)
        profit = round(bal - 100, 2)
        total_staked = round(sum(abs(x["fp"]) for x in ordered), 2)
        roi = round(profit / total_staked * 100, 1) if total_staked else 0
        days = len({x["date"] for x in items})
        avg_odds = round(sum(x["odds"] for x in items) / bets, 2) if bets else 0
        # streak (most recent consecutive same-outcome)
        streak = 0
        streak_kind = "w" if (items and items[-1]["won"]) else "l"
        for x in reversed(items):
            if (x["won"] and streak_kind == "w") or (not x["won"] and streak_kind == "l"):
                streak += 1
            else:
                break
        strategies.append({
            "name": strat, "source": items[0].get("source", src) if items else src,
            "bets": bets, "wins": wins, "losses": bets - wins, "bankroll": bankroll,
            "profit": profit, "roi": roi, "days": days, "avg_odds": avg_odds,
            "trust": _trust(wins, bets, roi, days), "streak": streak,
            "streak_kind": streak_kind, "risk": _risk(avg_odds),
            "spark": spark[-20:],
            "disabled": strat in DISABLED_BASES or any(strat.startswith(b + "__") for b in DISABLED_BASES)
            or strat in DISABLED_BASES,
        })
        strat_matches[strat] = items

    # today's picks (actionable)
    today_picks = []
    for r in c.execute(
        """SELECT sport, league, home, away, pick, odds_at_prediction, strategy, source, model_prob
           FROM predictions WHERE match_date=? ORDER BY
           CASE source WHEN 'expert_vig' THEN 0 WHEN 'version_library' THEN 1
                       WHEN 'xbet_linefeed' THEN 2 ELSE 3 END, odds_at_prediction DESC""",
        (today,),
    ):
        odds = _f(r[5])
        prob = _f(r[8])
        rat = _rationale(r[6], odds, prob)
        today_picks.append({
            "sport": r[0], "league": r[1] or "", "home": r[2], "away": r[3],
            "pick": r[4], "odds": round(odds, 2), "strategy": r[6], "source": r[7],
            "real": r[7] in ("expert_vig", "xbet_linefeed", "version_library"),
            "pay10": round(10 * odds, 2), "rat_ar": rat["ar"], "rat_en": rat["en"],
        })

    # best bet today = a confirmed-edge strategy pick with odds in the profitable zone (1.6-2.8)
    best = None
    for p in today_picks:
        if p["real"] and 1.6 <= p["odds"] <= 2.8:
            tr = next((s["trust"] for s in strategies if s["name"].startswith(p["strategy"].split("__")[0])), 0)
            if tr >= 40:
                best = p
                best["trust"] = tr
                break
    if best is None and today_picks:
        best = today_picks[0]
        best["trust"] = 0

    # headline
    tot_bets = sum(s["bets"] for s in strategies)
    tot_wins = sum(s["wins"] for s in strategies)
    tot_profit = round(sum(s["profit"] for s in strategies), 2)

    conn.close()
    return {
        "today": today,
        "generated": datetime.now().isoformat(timespec="seconds"),
        "headline": {"strategies": len(strategies), "bets": tot_bets, "wins": tot_wins,
                     "profit": tot_profit, "winrate": round(tot_wins / tot_bets * 100, 1) if tot_bets else 0},
        "best": best,
        "strategies": sorted(strategies, key=lambda s: s["trust"], reverse=True),
        "matches": strat_matches,
        "picks": today_picks[:150],
    }


HTML = r"""<!DOCTYPE html>
<html lang="ar" dir="rtl"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<meta name="theme-color" content="#0b1220"><meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<title>استراتيجي برو</title><link rel="manifest" href="manifest.json">
<style>
:root{--bg:#0a0f1c;--card:#121a2e;--card2:#1a2540;--txt:#eaf0fb;--mut:#8696b8;--acc:#22d3ee;
--green:#22c55e;--red:#ef4444;--gold:#f59e0b;--bord:#22304d;--safe:#22c55e;--bal:#f59e0b;--bold:#f43f5e}
*{box-sizing:border-box;margin:0;padding:0;-webkit-tap-highlight-color:transparent}
body{font-family:'Segoe UI',system-ui,-apple-system,sans-serif;background:var(--bg);color:var(--txt);
line-height:1.5;padding-bottom:64px}
.wrap{max-width:680px;margin:0 auto;padding:12px}
.hd{display:flex;align-items:center;justify-content:space-between;padding:13px 15px;
background:linear-gradient(135deg,#0a0f1c,#15203f);position:sticky;top:0;z-index:30;border-bottom:1px solid var(--bord)}
.hd h1{font-size:17px;font-weight:800;background:linear-gradient(90deg,var(--acc),var(--gold));-webkit-background-clip:text;-webkit-text-fill-color:transparent}
.hd .r{display:flex;gap:7px;align-items:center}
.iconbtn{background:var(--card2);border:1px solid var(--bord);color:var(--acc);width:34px;height:34px;
border-radius:50%;display:flex;align-items:center;justify-content:center;cursor:pointer;font-size:16px;font-weight:700}
.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:7px;margin:12px 0}
.stat{background:var(--card);border:1px solid var(--bord);border-radius:12px;padding:10px 6px;text-align:center}
.stat .v{font-size:18px;font-weight:800}.stat .l{font-size:9px;color:var(--mut);margin-top:1px}
.hero{background:linear-gradient(135deg,#0d2818,#143a23);border:1px solid var(--green);border-radius:16px;
padding:15px;margin:10px 0}
.hero .lbl{font-size:11px;color:var(--green);font-weight:800;letter-spacing:.5px}
.hero .mtch{font-size:16px;font-weight:800;margin:7px 0 3px}
.hero .pk{display:inline-block;background:var(--green);color:#001;padding:3px 10px;border-radius:7px;
font-weight:800;font-size:14px}
.hero .row{display:flex;justify-content:space-between;align-items:center;margin-top:9px}
.hero .od{background:#001a;color:var(--gold);padding:5px 13px;border-radius:8px;font-weight:800;font-size:17px}
.hero .pay{font-size:11px;color:var(--mut)}.hero .why{font-size:11px;color:var(--acc);margin-top:8px;line-height:1.4}
.sec{font-size:13px;font-weight:800;color:var(--acc);margin:18px 0 8px;display:flex;align-items:center;gap:6px}
.scard{background:var(--card);border:1px solid var(--bord);border-radius:14px;padding:13px;margin:8px 0;cursor:pointer;
transition:border-color .15s}.scard:active{border-color:var(--acc)}
.scard .top{display:flex;justify-content:space-between;align-items:flex-start;gap:8px}
.scard .nm{font-weight:700;font-size:14px;max-width:62%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.badges{display:flex;gap:5px;flex-wrap:wrap;margin-top:6px}
.bg{font-size:10px;padding:2px 7px;border-radius:6px;font-weight:700}
.bg.risk-safe{background:rgba(34,197,94,.16);color:var(--safe)}
.bg.risk-balanced{background:rgba(245,158,11,.16);color:var(--bal)}
.bg.risk-bold{background:rgba(244,63,94,.16);color:var(--bold)}
.bg.streak-w{background:rgba(245,158,11,.18);color:var(--gold)}
.bg.streak-l{background:rgba(239,68,68,.16);color:var(--red)}
.bg.src{background:var(--card2);color:var(--mut)}
.scard .mid{display:flex;justify-content:space-between;align-items:center;margin-top:9px}
.bank{font-size:22px;font-weight:800}
.bank.up{color:var(--green)}.bank.dn{color:var(--red)}
.rec{font-size:12px;color:var(--mut);font-weight:600}
.rec b{color:var(--txt)}
.trust{display:flex;flex-direction:column;align-items:center;min-width:42px}
.trust .tv{font-size:17px;font-weight:800;width:38px;height:38px;border-radius:50%;display:flex;
align-items:center;justify-content:center;border:3px solid}
.tl{font-size:9px;color:var(--mut);margin-top:2px}
.spark{height:26px;width:100%;margin-top:8px}
.scard .foot{font-size:11px;color:var(--mut);margin-top:6px;display:flex;justify-content:space-between}
.mrow{padding:11px 0;border-bottom:1px solid var(--bord)}.mrow:last-child{border:none}
.mrow .top{display:flex;justify-content:space-between;align-items:flex-start;gap:8px}
.mrow .teams{font-size:13px}.mrow .opp{color:var(--mut)}
.mrow .mypick{font-weight:800;color:var(--green)}
.mrow .od{background:var(--card2);border:1px solid var(--bord);padding:3px 9px;border-radius:7px;
font-weight:800;font-size:13px;white-space:nowrap}
.mrow .meta{display:flex;justify-content:space-between;margin-top:6px;font-size:11px;color:var(--mut)}
.pill{font-size:10px;padding:2px 8px;border-radius:6px;font-weight:700}
.pill.w{background:rgba(34,197,94,.16);color:var(--green)}.pill.l{background:rgba(239,68,68,.16);color:var(--red)}
.score{font-weight:800}
.back{display:flex;align-items:center;gap:8px;cursor:pointer;color:var(--acc);font-weight:700;font-size:14px;
padding:6px 0}
.menu-modal{position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:50;display:none}
.menu-modal.open{display:flex;justify-content:flex-end}
.menu-panel{background:var(--card);width:78%;max-width:300px;padding:16px;border-left:1px solid var(--bord}
.menu-panel h3{font-size:14px;margin-bottom:12px;color:var(--acc)}
.menu-opt{padding:13px;border-radius:10px;cursor:pointer;margin-bottom:6px;background:var(--card2);
border:1px solid var(--bord)}.menu-opt:active{border-color:var(--acc)}
.menu-opt .t{font-weight:700;font-size:13px}.menu-opt .d{font-size:11px;color:var(--mut);margin-top:2px}
.empty{text-align:center;color:var(--mut);padding:40px 20px;font-size:13px}
.bottombar{position:fixed;bottom:0;left:0;right:0;background:var(--card);border-top:1px solid var(--bord);
display:flex;z-index:40;max-width:680px;margin:0 auto}
.bb{flex:1;text-align:center;padding:9px 0 11px;color:var(--mut);cursor:pointer;font-size:10px;font-weight:600}
.bb svg{width:21px;height:21px;fill:currentColor;margin-bottom:2px}.bb.active{color:var(--acc)}
.foot{text-align:center;font-size:10px;color:var(--mut);padding:16px}
.hide{display:none!important}
.lock{position:fixed;inset:0;background:linear-gradient(160deg,#0a0f1c,#15203f);z-index:100;display:flex;
flex-direction:column;align-items:center;justify-content:center;padding:30px}
.lock .logo{font-size:42px;margin-bottom:8px}.lock h2{font-size:20px;font-weight:800;margin-bottom:4px;
background:linear-gradient(90deg,var(--acc),var(--gold));-webkit-background-clip:text;-webkit-text-fill-color:transparent}
.lock .sub{font-size:12px;color:var(--mut);margin-bottom:24px}
.lock input{width:200px;text-align:center;background:var(--card);border:2px solid var(--bord);color:var(--txt);
font-size:22px;letter-spacing:8px;padding:13px;border-radius:12px;outline:none;font-weight:800}
.lock input:focus{border-color:var(--acc)}
.lock button{margin-top:14px;background:linear-gradient(90deg,var(--acc),var(--gold));color:#001;border:none;
padding:13px 36px;border-radius:25px;font-weight:800;font-size:15px;cursor:pointer}
.lock .err{color:var(--red);font-size:12px;margin-top:10px;min-height:16px}
</style></head><body>
<div class="lock" id="lock"><div class="logo">⚡</div><h2 id="lkTitle">استراتيجي برو</h2>
<div class="sub" id="lkSub">أدخل الرقم السري</div>
<input type="password" id="pin" inputmode="numeric" maxlength="8" autofocus>
<button id="pinBtn" onclick="checkPin()">دخول</button>
<div class="err" id="pinErr"></div></div>
<div class="hd"><h1 id="title">⚡ استراتيجي برو</h1>
<div class="r"><div class="iconbtn" id="langBtn">EN</div><div class="iconbtn" id="menuBtn">⋮</div></div></div>
<div class="wrap" id="app"></div>
<div class="bottombar">
<div class="bb active" data-v="home"><svg viewBox="0 0 24 24"><path d="M12 3l9 8h-3v9h-4v-6H10v6H6v-9H3z"/></svg><span data-i="home"></span></div>
<div class="bb" data-v="picks"><svg viewBox="0 0 24 24"><path d="M12 2l3 7h7l-5.5 4.5L18 21l-6-4-6 4 1.5-7.5L2 9h7z"/></svg><span data-i="picks"></span></div>
<div class="bb" data-v="history"><svg viewBox="0 0 24 24"><path d="M13 3a9 9 0 00-9 9H1l3.9 3.9L5 16l4-4H6a7 7 0 117 7 7 7 0 01-5-2l-1.4 1.4A9 9 0 1013 3zm-1 5v5l4.3 2.5.7-1.2-3.5-2V8z"/></svg><span data-i="history"></span></div>
</div>
<div class="menu-modal" id="menuModal"><div class="menu-panel" id="menuPanel"></div></div>
<script>
const D=__DATA__,FLAT=4;
const I={
 ar:{title:"⚡ استراتيجي برو",home:"الرئيسية",picks:"توقعات اليوم",history:"السجل",
   bestBet:"⭐ أفضل رهان اليوم",monitor:"مراقبة الاستراتيجيات",todayPicks:"توقعات اليوم",
   historyLog:"سجلّ النتائج",profit:"ربح",bankroll:"رأس المال",record:"السجل",days:"أيام",
   trust:"ثقة",roi:"العائد",bets:"رهانات",strategies:"استراتيجيات",winrate:"نسبة الفوز",
   odds:"السعر",noData:"لا توجد بيانات بعد",myPick:"رهاني",score:"النتيجة",won:"فاز",lost:"خسر",
   bet10:"اراهن ١٠",win10:"تكسب",why:"لماذا؟",real:"odds حقيقية",safe:"آمن",balanced:"متوازن",bold:"جريء",
   mProfit:"أعلى ربحاً",mWin:"أعلى نسبة فوز",mTrust:"الأكثر ثقة",mSafe:"الآمنة فقط",
   mProfitD:"رتّب الاستراتيجيات حسب صافي الربح",mWinD:"رتّب حسب نسبة الفوز",mTrustD:"رتّب حسب درجة الثقة",
   mSafeD:"أظهر الاستراتيجيات الآمنة فقط",sortMenu:"ترتيب وفلترة",streakW:"فوز متتالي",streakL:"خسارة متتالية",
   tapHint:"اضغط لرؤية المباريات",netPro:"صافي الربح",allRes:"كل النتائج",cut:"مُقصاة"},
 en:{title:"⚡ Strategy Pro",home:"Home",picks:"Today",history:"History",
   bestBet:"⭐ Best Bet Today",monitor:"Strategy Monitor",todayPicks:"Today's Picks",
   historyLog:"Results Log",profit:"Profit",bankroll:"Bankroll",record:"Record",days:"days",
   trust:"Trust",roi:"ROI",bets:"bets",strategies:"strategies",winrate:"Win rate",
   odds:"Odds",noData:"No data yet",myPick:"my pick",score:"Score",won:"WON",lost:"LOST",
   bet10:"Bet 10",win10:"win",why:"Why?",real:"real odds",safe:"Safe",balanced:"Balanced",bold:"Bold",
   mProfit:"Top Profit",mWin:"Top Win Rate",mTrust:"Most Trusted",mSafe:"Safe only",
   mProfitD:"Sort strategies by net profit",mWinD:"Sort by win rate",mTrustD:"Sort by trust score",
   mSafeD:"Show only safe strategies",sortMenu:"Sort & Filter",streakW:"win streak",streakL:"loss streak",
   tapHint:"Tap to view matches",netPro:"Net profit",allRes:"All results",cut:"CUT"}
};
let lang='ar',view='home',sortKey='trust',filterRisk=null,openName=null;
const $=s=>document.querySelector(s),t=k=>I[lang][k];
function setLang(l){lang=l;document.documentElement.lang=l;document.documentElement.dir=l=='ar'?'rtl':'ltr';
 $('#langBtn').textContent=l=='ar'?'EN':'ع';$('#title').textContent=t('title');
 document.querySelectorAll('[data-i]').forEach(e=>e.textContent=t(e.dataset.i));render()}
function money(n){return(lang=='ar'?'$':'$')+Math.abs(n).toFixed(0)+(n>=0?'':'-')}
function sign(n){return n>=0?'+':''+n.toFixed(0)}
function riskBadge(r){const map={safe:t('safe'),balanced:t('balanced'),bold:t('bold')};return`<span class="bg risk-${r}">${map[r]||r}</span>`}
function trustColor(v){return v>=70?'var(--green)':v>=45?'var(--gold)':'var(--red)'}
function sparkline(arr,w,h){if(!arr||arr.length<2)return'';const mn=Math.min(...arr),mx=Math.max(...arr),rg=(mx-mn)||1;
 const pts=arr.map((v,i)=>`${(i/(arr.length-1)*w).toFixed(1)},${(h-(v-mn)/rg*h).toFixed(1)}`).join(' ');
 const up=arr[arr.length-1]>=arr[0];return`<svg class="spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">
 <polyline points="${pts}" fill="none" stroke="${up?'var(--green)':'var(--red)'}" stroke-width="1.5"/></svg>`}
function heroCard(){const b=D.best;if(!b)return'';const tr=b.trust||0;
 return`<div class="hero"><div class="lbl">★ ${t('bestBet')}</div>
 <div class="mtch">${b.home} <span style="color:var(--mut)">vs</span> ${b.away}</div>
 <div><span class="pk">${b.pick}</span> <span style="color:var(--mut);font-size:12px">· ${b.sport}</span></div>
 <div class="row"><div class="od">${b.odds}</div>
 <div style="text-align:end"><div style="font-weight:800;font-size:15px;color:var(--green)">${money(b.pay10)}</div>
 <div class="pay">${t('bet10')} → ${t('win10')}</div></div></div>
 ${b.rat_ar?`<div class="why">💡 ${lang=='ar'?b.rat_ar:b.rat_en}</div>`:''}
 ${b.real?`<div class="pay" style="margin-top:5px">✓ ${t('real')}</div>`:''}</div>`}
function stratCard(s){const up=s.bankroll>=100;const sk=s.streakKind==='w'?`<span class="bg streak-w">🔥 ${s.streak} ${t('streakW')}</span>`:(s.streak>1?`<span class="bg streak-l">❄️ ${s.streak} ${t('streakL')}</span>`:'');
 const dis=s.disabled?`<span class="bg" style="background:rgba(239,68,68,.16);color:var(--red)">✂️ ${t('cut')}</span>`:'';
 return`<div class="scard" ${s.disabled?'style="opacity:.6"':''} onclick="showStrat('${s.name.replace(/'/g,"\\'")}')">
 <div class="top"><div class="nm">${s.name}${dis}</div>
 <div class="trust"><div class="tv" style="color:${trustColor(s.trust)};border-color:${trustColor(s.trust)}">${s.trust}</div><div class="tl">${t('trust')}</div></div></div>
 <div class="badges">${riskBadge(s.risk)}${sk}<span class="bg src">${s.days}${t('days')}</span></div>
 <div class="mid"><div><div class="bank ${up?'up':'dn'}">${money(s.bankroll)}</div>
 <div class="rec">${sign(s.profit)} (${s.roi>=0?'+':''}${s.roi}%) · <b>${s.wins}</b>-${s.losses}</div></div>
 <div style="text-align:end"><div class="rec"><b>${s.bets}</b> ${t('bets')}</div>
 <div class="rec">${Math.round(s.wins/s.bets*100)}% ${t('winrate')}</div></div></div>
 ${sparkline(s.spark,200,26)}<div class="foot"><span>${t('tapHint')}</span><span>avg ${s.avg_odds}</span></div></div>`}
function matchRow(m){const isHome=m.pick===m.home;const opp=isHome?m.away:m.home;
 return`<div class="mrow"><div class="top"><div class="teams">
 <span class="${isHome?'mypick':'opp'}">${m.home}</span> <span class="opp">vs</span> <span class="${!isHome?'mypick':'opp'}">${m.away}</span></div>
 <div class="od">${m.odds}</div></div>
 <div class="meta"><span>${m.date} · ${m.sport}</span><span><span class="pill ${m.won?'w':'l'}">${m.won?t('won'):t('lost')}</span> <span class="score">${m.hs}-${m.as}</span> ${sign(m.fp)}</span></div></div>`}
function render(){
 const h=D.headline,a=$('#app');let x='';
 if(view==='home'){
  x+=`<div class="stats"><div class="stat"><div class="v">${h.strategies}</div><div class="l">${t('strategies')}</div></div>
  <div class="stat"><div class="v">${h.bets}</div><div class="l">${t('bets')}</div></div>
  <div class="stat"><div class="v">${h.winrate}%</div><div class="l">${t('winrate')}</div></div>
  <div class="stat"><div class="v ${h.profit>=0?'up':'dn'}" style="color:${h.profit>=0?'var(--green)':'var(--red)'}">${sign(h.profit)}</div><div class="l">${t('netPro')}</div></div></div>`;
  x+=heroCard();
  x+=`<div class="sec">📊 ${t('monitor')}</div>`;
  let strs=[...D.strategies];
  if(filterRisk)strs=strs.filter(s=>s.risk===filterRisk);
  strs.sort((a,b)=>{
   if(a.disabled!==b.disabled)return a.disabled?1:-1; // المعطّلة للأسفل دائماً
   if(sortKey==='profit')return b.profit-a.profit;
   if(sortKey==='win')return (b.wins/b.bets)-(a.wins/a.bets);
   return b.trust-a.trust;
  });
  x+=strs.length?strs.map(stratCard).join(''):`<div class="empty">${t('noData')}</div>`;
 }else if(view==='picks'){x+=`<div class="sec">🎯 ${t('todayPicks')} · ${D.today}</div>`;
  x+=D.picks.length?D.picks.map(p=>{const isH=p.pick===p.home;
   return`<div class="scard"><div class="top"><div class="teams" style="font-size:14px">
   <span class="${isH?'mypick':'opp'}" style="font-weight:800;color:var(--green)">${p.home}</span> <span class="opp">vs</span> <span class="${!isH?'mypick':'opp'}" style="font-weight:${!isH?'800':'400'};color:${!isH?'var(--green)':'var(--mut)'}">${p.away}</span></div>
   <div class="od">${p.odds}</div></div>
   <div class="meta" style="display:flex;justify-content:space-between;margin-top:8px;font-size:11px;color:var(--mut)">
   <span>${p.sport}${p.league?' · '+p.league:''}</span><span>${t('bet10')}→<b style="color:var(--green)">${money(p.pay10)}</b></span></div>
   <div style="font-size:11px;color:var(--acc);margin-top:5px">💡 ${lang=='ar'?p.rat_ar:p.rat_en}</div>
   ${p.real?`<div class="bg risk-safe" style="margin-top:5px;display:inline-block">✓ ${t('real')}</div>`:''}</div>`}).join(''):`<div class="empty">${t('noData')}</div>`;
 }else if(view==='history'){x+=`<div class="sec">📜 ${t('historyLog')}</div><div class="scard">`;
  const all=Object.entries(D.matches).flatMap(([n,ms])=>ms.map(m=>({...m,s:n}))).sort((a,b)=>(b.date||'').localeCompare(a.date||''));
  x+=all.slice(0,80).map(matchRow).join('')||t('noData');x+='</div>';}
 a.innerHTML=x+`<div class="foot">${t('allRes')} · ${D.generated.slice(0,10)} · ⚡ Strategy Pro</div>`;
 document.querySelectorAll('.bb').forEach(b=>b.classList.toggle('active',b.dataset.v===view));}
function openMenu(){$('#menuPanel').innerHTML=`<h3>⚙️ ${t('sortMenu')}</h3>
 <div class="menu-opt" onclick="setSort('profit')"><div class="t">💰 ${t('mProfit')}</div><div class="d">${t('mProfitD')}</div></div>
 <div class="menu-opt" onclick="setSort('win')"><div class="t">🎯 ${t('mWin')}</div><div class="d">${t('mWinD')}</div></div>
 <div class="menu-opt" onclick="setSort('trust')"><div class="t">⭐ ${t('mTrust')}</div><div class="d">${t('mTrustD')}</div></div>
 <div class="menu-opt" onclick="toggleSafe()"><div class="t">🟢 ${t('mSafe')}</div><div class="d">${t('mSafeD')}</div></div>`;
 $('#menuModal').classList.add('open');}
function setSort(k){sortKey=k;closeMenu();render()}
function toggleSafe(){filterRisk=filterRisk?null:'safe';closeMenu();render()}
function closeMenu(){$('#menuModal').classList.remove('open')}
window.showStrat=function(n){openName=n;const s=D.strategies.find(x=>x.name===n);if(!s)return;
 const ms=D.matches[n]||[];const up=s.bankroll>=100;
 $('#app').innerHTML=`<div class="back" onclick="openName=null;render()">← ${t('monitor')}</div>
 <div class="scard" style="cursor:default;border-color:var(--acc)"><div class="top"><div class="nm" style="font-size:16px">${s.name}</div>
 <div class="trust"><div class="tv" style="color:${trustColor(s.trust)};border-color:${trustColor(s.trust)}">${s.trust}</div><div class="tl">${t('trust')}</div></div></div>
 <div class="badges">${riskBadge(s.risk)}${s.streakKind==='w'?`<span class="bg streak-w">🔥 ${s.streak} ${t('streakW')}</span>`:''}<span class="bg src">${s.days}${t('days')}</span></div>
 <div class="mid"><div><div class="bank ${up?'up':'dn'}">${money(s.bankroll)}</div><div class="rec">${sign(s.profit)} (${s.roi>=0?'+':''}${s.roi}%)</div></div>
 <div style="text-align:end"><div class="rec"><b>${s.wins}</b>${t('won')} · <b>${s.losses}</b>${t('lost')}</div><div class="rec">${s.bets} ${t('bets')} · ${Math.round(s.wins/s.bets*100)}%</div></div></div>
 ${sparkline(s.spark,200,30)}</div><div class="sec">🎾 ${ms.length} ${t('bets')}</div><div class="scard" style="cursor:default">${ms.slice().reverse().map(matchRow).join('')||t('noData')}</div>`;
 document.querySelectorAll('.bb').forEach(b=>b.classList.remove('active'));}
document.querySelectorAll('.bb').forEach(b=>b.addEventListener('click',()=>{view=b.dataset.v;openName=null;render()}));
$('#langBtn').addEventListener('click',()=>setLang(lang==='ar'?'en':'ar'));
$('#menuBtn').addEventListener('click',openMenu);
$('#menuModal').addEventListener('click',e=>{if(e.target.id==='menuModal')closeMenu()});
// لا service worker — التطبيق يُحمّل نسخة طازجة دائماً (أبسط وأضمن، لا مشاكل cache)
// للتثبيت كأيقونة على الهاتف: Chrome ← قائمة ← "تثبيت التطبيق" / "Add to Home screen"
const PIN='08031998';
function checkPin(){const v=document.getElementById('pin').value;
 if(v===PIN){sessionStorage.setItem('sp_auth','1');document.getElementById('lock').classList.add('hide');
  setLang('ar');render();}else{document.getElementById('pinErr').textContent=
   lang=='ar'?'رقم سري خاطئ، حاول مجدداً':'Wrong PIN, try again';
   document.getElementById('pin').value='';}}
document.getElementById('pin').addEventListener('keydown',e=>{if(e.key==='Enter')checkPin()});
document.getElementById('pin').addEventListener('input',()=>document.getElementById('pinErr').textContent='');
if(sessionStorage.getItem('sp_auth')==='1'){document.getElementById('lock').classList.add('hide');setLang('ar');render();}
</script></body></html>"""

MANIFEST={"name":"استراتيجي برو · Strategy Pro","short_name":"Strategy Pro",
"description":"Professional strategy monitoring & picks","lang":"ar","dir":"rtl","start_url":"./index.html",
"display":"standalone","background_color":"#0a0f1c","theme_color":"#0b1220",
"icons":[{"src":"icon.png","sizes":"512x512","type":"image/png","purpose":"any maskable"}]}

SW=r"""const C='sp-v5';
self.addEventListener('install',e=>self.skipWaiting());
self.addEventListener('activate',e=>e.waitUntil(caches.keys().then(ks=>
  Promise.all(ks.filter(k=>k!==C).map(k=>caches.delete(k)))).then(()=>self.clients.claim())));
self.addEventListener('fetch',e=>{
  if(e.request.method!=='GET')return;
  const url=new URL(e.request.url);
  // network-first للصفحة الرئيسية والـ JS/manifest حتى تظهر التحديثات فوراً
  const netFirst=url.pathname.endsWith('/')||url.pathname.endsWith('.html')||
    url.pathname.endsWith('sw.js')||url.pathname.endsWith('manifest.json');
  if(netFirst){
    e.respondWith(fetch(e.request).then(x=>{caches.open(C).then(c=>c.put(e.request,x.clone()));return x;})
      .catch(()=>caches.match(e.request)));
    return;
  }
  // cache-first للأصول الثابتة (أيقونة)
  e.respondWith(caches.open(C).then(c=>c.match(e.request).then(r=>r||fetch(e.request)
    .then(x=>{c.put(e.request,x.clone());return x}).catch(()=>r)))));
});"""


def build_icon(path: Path):
    import struct, zlib
    W = H = 512; px = bytearray()
    for y in range(H):
        px += b"\x00"
        for x in range(W):
            tt = x / W; r = int(10 + 20 * tt); g = int(15 + 200 * tt); b = int(28 + 100 * tt)
            cx, cy = W * .42, H * .5
            if abs(x - cx) < 60 and cy - 170 < y < cy + 80 and (y - cy) > -1.7 * (x - cx):
                r, g, b = 245, 210, 70
            px += bytes([r, g, b])
    def ck(tag, data):
        c = tag + data; return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xffffffff)
    raw = b"".join(bytes([0]) + bytes(px[i*W*3+1:(i+1)*W*3+1]) for i in range(H))
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + ck(b"IHDR", struct.pack(">IIBBBBB", W, H, 8, 2, 0, 0, 0)) +
                     ck(b"IDAT", zlib.compress(raw, 9)) + ck(b"IEND", b""))


def main():
    today = date.today().isoformat()
    data = gather(today)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "index.html").write_text(HTML.replace("__DATA__", json.dumps(data, ensure_ascii=False)), encoding="utf-8")
    (OUT_DIR / "manifest.json").write_text(json.dumps(MANIFEST, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT_DIR / "sw.js").write_text(SW, encoding="utf-8")
    build_icon(OUT_DIR / "icon.png")
    print(f"✓ Dashboard built → {OUT_DIR}/index.html")
    print(f"  strategies: {data['headline']['strategies']} | bets: {data['headline']['bets']} | "
          f"net: {data['headline']['profit']:+.0f} | best bet: {bool(data['best'])}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
