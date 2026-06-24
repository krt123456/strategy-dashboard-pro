import requests, re, sqlite3, sys, time
BASE = "/root/strategy-pred"; sys.path.insert(0, BASE+"/scripts")
from betting_journal import add_result
KEY = "040f91a4b33ddd6619e375ab33978e57"; DB = BASE+"/data/betting_journal.db"

def clean(n):
    return re.sub(r"\s*\(.*?\)", "", str(n)).strip()

all_results = []
for sk in ["baseball","volleyball","hockey","handball"]:
    try:
        r = requests.get(f"https://v1.{sk}.api-sports.io/games?date=2026-06-23",
                         headers={"x-apisports-key": KEY}, timeout=15)
        if r.status_code != 200: continue
        for g in r.json().get("response", []):
            t = g.get("teams", {})
            home = clean(str(t.get("home", {}).get("name", "")))
            away = clean(str(t.get("away", {}).get("name", "")))
            sc = g.get("scores", {})
            if not isinstance(sc, dict): continue
            hs = sc.get("home", 0); as_ = sc.get("away", 0)
            if isinstance(hs, dict): hs = hs.get("total", 0)
            if isinstance(as_, dict): as_ = as_.get("total", 0)
            hs, as_ = int(hs) if hs else 0, int(as_) if as_ else 0
            if (hs or as_) and home and away:
                all_results.append({"h": home, "a": away, "hs": hs, "as": as_, "sport": sk})
        time.sleep(1)
    except: pass

def s(a, b):
    na = re.sub(r"[^a-z0-9]", "", a.lower())
    nb = re.sub(r"[^a-z0-9]", "", b.lower())
    return 4 if na == nb else (3 if (na in nb or nb in na) else 0)

conn = sqlite3.connect(DB); c = conn.cursor()
pending = c.execute("SELECT p.id,p.sport,p.home,p.away,p.pick FROM predictions p LEFT JOIN results r ON p.id=r.prediction_id WHERE p.match_date='2026-06-23' AND r.id IS NULL AND p.sport IN ('baseball','volleyball','hockey','handball')").fetchall()
resolved = 0
for pid, sport, home, away, pick in pending:
    best, best_sc = None, 0
    for r in all_results:
        if r["sport"] != sport: continue
        n = s(clean(home), r["h"]) + s(clean(away), r["a"])
        sw = s(clean(home), r["a"]) + s(clean(away), r["h"])
        sc = max(n, sw)
        if sc > best_sc and sc >= 4: best_sc, best = sc, r
    if not best: continue
    hs, aso = best["hs"], best["as"]
    side = "home" if s(pick, home) >= s(pick, away) else "away"
    won = (side == "home" and hs > aso) or (side == "away" and aso > hs)
    try: add_result(pid, hs, aso, won, result_source="apisports"); resolved += 1
    except: pass

conn.commit()
p2 = c.execute("SELECT COUNT(*) FROM predictions p JOIN results r ON p.id=r.prediction_id WHERE p.match_date='2026-06-23'").fetchone()[0]
print(f"API-Sports resolved: {resolved} | 2026-06-23 total: {p2}")
conn.close()
