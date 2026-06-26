import sqlite3, csv, glob, datetime
c = sqlite3.connect("data/betting_journal.db"); cur = c.cursor()
start_map = {}
for fn in glob.glob("data/one_xbet_linefeed_*.csv"):
    try:
        with open(fn) as f:
            for r in csv.DictReader(f):
                su = (r.get("StartUtc") or "").strip()
                if not su:
                    continue
                k = ((r.get("Date") or "")[:10], (r.get("Home") or "").strip().lower(), (r.get("Away") or "").strip().lower())
                start_map[k] = su
    except Exception:
        pass
print("start_map entries:", len(start_map))
rows = cur.execute("SELECT id,match_date,home,away FROM predictions WHERE start_utc IS NULL OR start_utc=''").fetchall()
upd = 0
for pid, md, h, a in rows:
    su = start_map.get((md, (h or "").strip().lower(), (a or "").strip().lower()))
    if su:
        cur.execute("UPDATE predictions SET start_utc=? WHERE id=?", (su, pid)); upd += 1
c.commit()
print("backfilled:", upd)
t = datetime.date.today().isoformat()
n = cur.execute("SELECT COUNT(*) FROM predictions WHERE match_date=? AND start_utc!=''", (t,)).fetchone()[0]
print("today preds with start_utc now:", n)
