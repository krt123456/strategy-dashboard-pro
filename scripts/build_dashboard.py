#!/usr/bin/env python3
"""Build a professional bilingual (AR/EN) mobile dashboard PWA from the database.

Reads betting_journal.db and emits a single self-contained index.html plus a
PWA manifest and service worker into dashboard/. The result is a beautiful,
installable phone app (Add to Home Screen on Android/iOS, or wrap as a TWA APK)
that shows today's picks, strategy performance, recent results, and the
evolution snapshot. Bilingual with full RTL support.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_DIR / "data" / "betting_journal.db"
OUT_DIR = PROJECT_DIR / "dashboard"


def _f(v):
    try:
        return float(v) if v not in (None, "") else 0.0
    except Exception:
        return 0.0


def gather_data(today: str) -> dict:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # today's actionable picks (real-odds sources first)
    picks = []
    for r in c.execute(
        """SELECT sport, league, home, away, pick, odds_at_prediction, strategy, source
           FROM predictions WHERE match_date=? ORDER BY
           CASE source WHEN 'expert_vig' THEN 0 WHEN 'version_library' THEN 1
                       WHEN 'xbet_linefeed' THEN 2 ELSE 3 END, odds_at_prediction DESC""",
        (today,),
    ):
        picks.append({"sport": r[0], "league": r[1] or "", "home": r[2], "away": r[3],
                      "pick": r[4], "odds": r[5], "strategy": r[6], "source": r[7]})

    # strategy performance (graded results)
    perf = []
    for r in c.execute(
        """SELECT p.strategy, p.source, COUNT(*) bets,
                  SUM(CASE WHEN r.pick_won=1 THEN 1 ELSE 0 END) wins,
                  ROUND(SUM(r.profit), 2) profit
           FROM predictions p JOIN results r ON p.id=r.prediction_id
           GROUP BY p.strategy ORDER BY SUM(r.profit) DESC"""):
        b = r[2] or 0
        perf.append({"strategy": r[0], "source": r[1], "bets": b, "wins": r[3] or 0,
                     "profit": r[4] or 0, "roi": round((r[4] or 0) / b * 100, 1) if b else 0})

    # recent graded results
    recent = []
    for r in c.execute(
        """SELECT p.match_date, p.sport, p.home, p.away, p.pick, r.home_score,
                  r.away_score, r.pick_won, ROUND(r.profit,2), p.odds_at_prediction, p.strategy
           FROM predictions p JOIN results r ON p.id=r.prediction_id
           ORDER BY r.checked_at DESC LIMIT 60"""):
        recent.append({"date": r[0], "sport": r[1], "home": r[2], "away": r[3],
                       "pick": r[4], "hs": r[5], "as_": r[6], "won": bool(r[7]),
                       "profit": r[8], "odds": r[9], "strategy": r[10]})

    # headline stats
    tot = c.execute(
        "SELECT COUNT(*), SUM(CASE WHEN pick_won=1 THEN 1 ELSE 0 END), SUM(profit) FROM results"
    ).fetchone()
    tp = c.execute("SELECT COUNT(*) FROM predictions WHERE match_date=?", (today,)).fetchone()[0]

    conn.close()
    return {
        "today": today,
        "generated": datetime.now().isoformat(timespec="seconds"),
        "headline": {"total_results": tot[0] or 0, "total_wins": tot[1] or 0,
                     "total_profit": round(tot[2] or 0, 2),
                     "today_picks": tp},
        "picks": picks[:200],
        "performance": perf,
        "recent": recent,
    }


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no">
<meta name="theme-color" content="#0f172a">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<title>استراتيجي | Strategy Pro</title>
<link rel="manifest" href="manifest.json">
<style>
:root{--bg:#0b1220;--card:#131c2e;--card2:#1a2540;--txt:#e8edf6;--mut:#8a98b8;--acc:#22d3ee;
--green:#22c55e;--red:#ef4444;--gold:#f59e0b;--bord:#243149}
*{box-sizing:border-box;margin:0;padding:0;-webkit-tap-highlight-color:transparent}
body{font-family:'Segoe UI',system-ui,sans-serif;background:var(--bg);color:var(--txt);
line-height:1.5;padding-bottom:70px}
.wrap{max-width:680px;margin:0 auto;padding:14px}
.hd{display:flex;justify-content:space-between;align-items:center;padding:14px 16px;
background:linear-gradient(135deg,#0f172a,#162042);position:sticky;top:0;z-index:10;
border-bottom:1px solid var(--bord)}
.hd h1{font-size:18px;font-weight:800;background:linear-gradient(90deg,var(--acc),var(--gold));
-webkit-background-clip:text;-webkit-text-fill-color:transparent}
.hd .lang{background:var(--card2);border:1px solid var(--bord);color:var(--acc);padding:6px 12px;
border-radius:20px;font-size:12px;font-weight:700;cursor:pointer}
.stats{display:grid;grid-template-columns:repeat(2,1fr);gap:10px;margin:14px 0}
.stat{background:var(--card);border:1px solid var(--bord);border-radius:14px;padding:14px}
.stat .v{font-size:22px;font-weight:800}
.stat .l{font-size:11px;color:var(--mut);margin-top:2px}
.pos{color:var(--green)}.neg{color:var(--red)}
.tabs{display:flex;gap:6px;margin:10px 0;overflow-x:auto;padding-bottom:4px}
.tab{background:var(--card);border:1px solid var(--bord);color:var(--mut);padding:8px 14px;
border-radius:20px;font-size:13px;font-weight:600;cursor:pointer;white-space:nowrap}
.tab.active{background:var(--acc);color:#001;border-color:var(--acc)}
.card{background:var(--card);border:1px solid var(--bord);border-radius:14px;padding:13px;margin:8px 0}
.card .top{display:flex;justify-content:space-between;align-items:flex-start;gap:8px}
.card .match{font-weight:700;font-size:14px}
.card .sub{font-size:11px;color:var(--mut);margin-top:3px}
.odds{background:var(--card2);border:1px solid var(--acc);color:var(--acc);padding:4px 10px;
border-radius:8px;font-weight:800;font-size:14px;white-space:nowrap}
.tag{display:inline-block;background:var(--card2);border:1px solid var(--bord);color:var(--mut);
padding:2px 8px;border-radius:6px;font-size:10px;margin-top:5px}
.row{display:flex;justify-content:space-between;align-items:center;padding:11px 13px;
border-bottom:1px solid var(--bord)}
.row:last-child{border-bottom:none}
.row .nm{font-size:13px;font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:55%}
.row .meta{font-size:11px;color:var(--mut)}
.bar{width:60px;height:6px;background:var(--card2);border-radius:3px;overflow:hidden;margin:4px 0}
.bar>i{display:block;height:100%;background:var(--green)}
.sec{font-size:13px;font-weight:700;color:var(--acc);margin:16px 0 6px;display:flex;align-items:center;gap:6px}
.empty{text-align:center;color:var(--mut);padding:40px 20px;font-size:13px}
.pill{font-size:10px;padding:2px 7px;border-radius:5px;font-weight:700}
.pill.w{background:rgba(34,197,94,.15);color:var(--green)}.pill.l{background:rgba(239,68,68,.15);color:var(--red)}
.score{font-weight:800;font-size:13px}
.bottombar{position:fixed;bottom:0;left:0;right:0;background:var(--card);border-top:1px solid var(--bord);
display:flex;z-index:20;max-width:680px;margin:0 auto}
.bb{flex:1;text-align:center;padding:10px 0 12px;color:var(--mut);cursor:pointer;font-size:10px}
.bb svg{width:22px;height:22px;fill:currentColor;margin-bottom:2px}
.bb.active{color:var(--acc)}
.foot{font-size:10px;color:var(--mut);text-align:center;padding:18px}
.hide{display:none}
</style>
</head>
<body>
<div class="hd"><h1 id="title">⚡ Strategy Pro</h1><button class="lang" id="langBtn">EN</button></div>
<div class="wrap" id="app"></div>
<div class="bottombar">
<div class="bb active" data-t="picks"><svg viewBox="0 0 24 24"><path d="M12 2l3 7h7l-5.5 4.5L18 21l-6-4-6 4 1.5-7.5L2 9h7z"/></svg><span data-i="picks"></span></div>
<div class="bb" data-t="perf"><svg viewBox="0 0 24 24"><path d="M5 9h3v11H5zm5-5h3v16h-3zm5 8h3v8h-3z"/></svg><span data-i="perf"></span></div>
<div class="bb" data-t="results"><svg viewBox="0 0 24 24"><path d="M9 16.2L4.8 12l-1.4 1.4L9 19 21 7l-1.4-1.4z"/></svg><span data-i="results"></span></div>
</div>
<script>
const DATA = __DATA__;
const I = {
 ar:{title:"⚡ استراتيجي برو",picks:"التوقعات",perf:"الأداء",results:"النتائج",todayPicks:"توقعات اليوم",
   strategyPerf:"أداء الاستراتيجيات",recentResults:"أحدث النتائج",profit:"الربح",wins:"فوز",bets:"رهانات",
   roi:"العائد",odds:"السعر",noData:"لا توجد بيانات بعد",results_count:"نتائج محلولة",todayCount:"توقع اليوم",
   netProfit:"صافي الربح",winRate:"نسبة الفوز",verified:"odds حقيقية",pick:"الرهان",score:"النتيجة"},
 en:{title:"⚡ Strategy Pro",picks:"Picks",perf:"Performance",results:"Results",todayPicks:"Today's Picks",
   strategyPerf:"Strategy Performance",recentResults:"Recent Results",profit:"Profit",wins:"Wins",bets:"Bets",
   roi:"ROI",odds:"Odds",noData:"No data yet",results_count:"Graded",todayCount:"Today's Picks",
   netProfit:"Net Profit",winRate:"Win Rate",verified:"real odds",pick:"Pick",score:"Score"}
};
let lang='ar', tab='picks';
const $=s=>document.querySelector(s);
function t(k){return I[lang][k]}
function setLang(l){lang=l;document.documentElement.lang=l;document.documentElement.dir=l=='ar'?'rtl':'ltr';
  $('#langBtn').textContent=l=='ar'?'EN':'ع';$('#title').textContent=t('title');
  document.querySelectorAll('[data-i]').forEach(e=>e.textContent=t(e.dataset.i));render()}
function fmt(n){return (n>=0?'+':'')+Number(n).toFixed(2)}
function pickCard(p){
 const real = ['expert_vig','xbet_linefeed','version_library'].includes(p.source);
 return `<div class="card"><div class="top"><div><div class="match">${p.home} v ${p.away}</div>
  <div class="sub">→ <b>${p.pick}</b> · ${p.sport}${p.league?' · '+p.league:''}</div>
  <span class="tag">${p.strategy.length>30?p.strategy.slice(0,28)+'…':p.strategy}</span>
  ${real?`<span class="tag" style="color:var(--green);border-color:var(--green)">✓ ${t('verified')}</span>`:''}</div>
  <div class="odds">${p.odds}</div></div></div>`}
function perfCard(p){const wr=p.bets?Math.round(p.wins/p.bets*100):0;const pc=p.profit>=0?'pos':'neg';
 return `<div class="row"><div class="nm">${p.strategy}</div>
  <div style="text-align:end"><div class="${pc}" style="font-weight:800;font-size:14px">${fmt(p.profit)}</div>
  <div class="meta">${p.wins}/${p.bets} (${wr}%) · ROI ${p.roi>=0?'+':''}${p.roi}%</div>
  <div class="bar"><i style="width:${wr}%;background:${wr>=60?'var(--green)':wr>=45?'var(--gold)':'var(--red)'}"></i></div></div></div>`}
function resRow(r){const sc=`${r.hs}-${r.as_}`;return `<div class="row">
  <div><div class="nm" style="max-width:200px">${r.home} v ${r.away}</div>
  <div class="meta">${r.sport} · ${r.strategy.slice(0,20)}</div></div>
  <div style="text-align:end"><div class="score">${sc}</div>
  <span class="pill ${r.won?'w':'l'}">${r.won?'فوز':'خسارة'}</span>
  <div class="meta ${r.profit>=0?'pos':'neg'}">${fmt(r.profit)} @${r.odds}</div></div></div>`}
function render(){
 const h=DATA.headline, d=$('#app');
 const prof=h.total_profit>=0?'pos':'neg';
 const stats=`<div class="stats"><div class="stat"><div class="v">${h.today_picks}</div><div class="l">${t('todayCount')}</div></div>
  <div class="stat"><div class="v">${h.total_results}</div><div class="l">${t('results_count')}</div></div>
  <div class="stat"><div class="v">${h.total_wins}/${h.total_results}</div><div class="l">${t('winRate')}</div></div>
  <div class="stat"><div class="v ${prof}">${fmt(h.total_profit)}</div><div class="l">${t('netProfit')}</div></div></div>`;
 let body='';
 if(tab==='picks'){body=`<div class="sec">🎯 ${t('todayPicks')} · ${DATA.today}</div>`;
   const ps=DATA.picks;body+= ps.length?ps.map(pickCard).join(''):`<div class="empty">${t('noData')}</div>`}
 else if(tab==='perf'){body=`<div class="sec">📊 ${t('strategyPerf')}</div>`;
   body+= DATA.performance.length?`<div class="card">${DATA.performance.map(perfCard).join('')}</div>`:`<div class="empty">${t('noData')}</div>`}
 else{body=`<div class="sec">✅ ${t('recentResults')}</div>`;
   body+= DATA.recent.length?`<div class="card">${DATA.recent.map(resRow).join('')}</div>`:`<div class="empty">${t('noData')}</div>`}
 d.innerHTML=stats+body+`<div class="foot">Updated ${DATA.generated.slice(0,16).replace('T',' ')} · Strategy Pro</div>`;
 document.querySelectorAll('.bb').forEach(b=>b.classList.toggle('active',b.dataset.t===tab))}
document.querySelectorAll('.bb').forEach(b=>b.addEventListener('click',()=>{tab=b.dataset.t;render()}));
$('#langBtn').addEventListener('click',()=>setLang(lang==='ar'?'en':'ar'));
if('serviceWorker'in navigator)navigator.serviceWorker.register('sw.js').catch(()=>{});
setLang('ar');render();
</script>
</body></html>
"""

MANIFEST = {
    "name": "Strategy Pro — استراتيجي برو",
    "short_name": "Strategy Pro",
    "description": "Professional betting strategy prediction dashboard",
    "lang": "ar", "dir": "rtl",
    "start_url": "./index.html",
    "display": "standalone",
    "background_color": "#0b1220",
    "theme_color": "#0f172a",
    "icons": [
        {"src": "icon.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"}
    ],
}

SW_JS = r"""const C='strategy-pro-v1';
self.addEventListener('install',e=>{self.skipWaiting()});
self.addEventListener('activate',e=>{e.waitUntil(self.clients.claim())});
self.addEventListener('fetch',e=>{
  if(e.request.method!=='GET')return;
  e.respondWith(
    caches.open(C).then(c=>c.match(e.request).then(r=>
      r||fetch(e.request).then(resp=>{c.put(e.request,resp.clone());return resp}).catch(()=>r)
    ))
  );
});
"""


def build_icon(path: Path):
    """Generate a simple 512x512 PNG icon (gradient + lightning)."""
    import base64, struct, zlib
    W = H = 512
    px = bytearray()
    for y in range(H):
        px += b"\x00"
        for x in range(W):
            t = x / W
            r = int(15 + (34 - 15) * t)
            g = int(23 + (211 - 23) * t)
            b = int(42 + (238 - 42) * t)
            cx, cy = W / 2, H / 2
            d = ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5
            if abs(x - cx) < 70 and y > cy - 160 and y < cy + 90 and (y - cy) > -1.6 * (x - cx) - 10:
                r, g, b = 245, 200, 60
            px += bytes([r, g, b])

    def chunk(tag, data):
        c = tag + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xffffffff)

    raw = b"".join(bytes([0]) + bytes(px[i * W * 3 + 1:(i + 1) * W * 3 + 1]) for i in range(H))
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", W, H, 8, 2, 0, 0, 0)) + \
          chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")
    path.write_bytes(png)


def main() -> int:
    today = date.today().isoformat()
    data = gather_data(today)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    html = HTML_TEMPLATE.replace("__DATA__", json.dumps(data, ensure_ascii=False))
    (OUT_DIR / "index.html").write_text(html, encoding="utf-8")
    (OUT_DIR / "manifest.json").write_text(json.dumps(MANIFEST, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT_DIR / "sw.js").write_text(SW_JS, encoding="utf-8")
    build_icon(OUT_DIR / "icon.png")
    print(f"Dashboard built → {OUT_DIR}/index.html")
    print(f"  today picks: {data['headline']['today_picks']} | results: {data['headline']['total_results']} "
          f"| net profit: {data['headline']['total_profit']:+.2f}")
    print(f"  strategies ranked: {len(data['performance'])} | recent results: {len(data['recent'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
