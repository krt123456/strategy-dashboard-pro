#!/usr/bin/env python3
"""Write scores24-scraped finished matches into the DB (runs on VPS).

The scraping happens on a residential IP (scores24 blocks the VPS IP via Cloudflare);
that scraper prints JSON [{date,sport,home,away,home_pts,away_pts,league}, ...].
This script reads that JSON (path arg or stdin), fuzzy-matches each finished match to
unresolved predictions, and persists results via betting_journal.add_result.

Usage:
  # on residential machine:
  python s24_resolver.py > /tmp/s24.json
  scp /tmp/s24.json root@VPS:/tmp/ ; ssh root@VPS '.../resolve_results_scores24.py /tmp/s24.json'
"""
import sys, json, sqlite3, datetime
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_DIR / "data" / "betting_journal.db"
sys.path.insert(0, str(PROJECT_DIR / "scripts"))


def _norm(s):
    return (s or "").lower().replace(" ", "").replace("-", "")


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else None
    raw = Path(src).read_text(encoding="utf-8") if src else sys.stdin.read()
    matches = json.loads(raw)
    if not isinstance(matches, list):
        print("bad input"); return 1

    import resolve_results_betexplorer as be
    from betting_journal import add_result

    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    today = datetime.date.today().isoformat()
    cutoff = (datetime.date.today() - datetime.timedelta(days=5)).isoformat()
    preds = c.execute(
        "SELECT id, match_date, sport, home, away, pick FROM predictions "
        "WHERE match_date >= ? AND id NOT IN (SELECT prediction_id FROM results)",
        (cutoff,),
    ).fetchall()

    # index finished matches by date
    by_date = {}
    for m in matches:
        by_date.setdefault(m.get("date"), []).append(m)

    stats = {"resolved": 0, "no_match": 0, "checked": 0}
    for pid, mdate, sport, home, away, pick in preds:
        if _norm(sport) != "tabletennis":
            continue
        cands = by_date.get(mdate, [])
        if not cands:
            continue
        stats["checked"] += 1
        best = None
        for m in cands:
            normal = be._name_similarity(home, m["home"]) + be._name_similarity(away, m["away"])
            swapped = be._name_similarity(home, m["away"]) + be._name_similarity(away, m["home"])
            sc = max(normal, swapped)
            if sc >= 4 and (best is None or sc > best[0]):
                best = (sc, m)
        if not best:
            stats["no_match"] += 1
            continue
        m = best[1]
        hp, ap = m["home_pts"], m["away_pts"]
        side = be._pick_side(pick, home, away, sport)
        if side == "unknown" or hp == ap:
            stats["no_match"] += 1
            continue
        home_won = hp > ap
        won = home_won if side == "home" else (not home_won)
        try:
            add_result(pid, hp, ap, bool(won), "scores24")
            stats["resolved"] += 1
        except Exception as e:
            print("add_result err:", str(e)[:60])
    conn.close()
    print(f"scores24 resolve: checked={stats['checked']} resolved={stats['resolved']} no_match={stats['no_match']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
