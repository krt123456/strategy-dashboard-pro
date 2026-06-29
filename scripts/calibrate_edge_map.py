#!/usr/bin/env python3
"""Calibrate actual win-rates by (sport, side_type, odds_band) from all resolved bets.
Output: which combos have positive EV. This becomes the brain of the advanced strategies."""
import sqlite3, json
from collections import defaultdict
c = sqlite3.connect("data/betting_journal.db"); cur = c.cursor()
rows = cur.execute("""SELECT p.sport,p.home,p.away,p.pick,p.odds_at_prediction,r.pick_won,r.profit
 FROM predictions p JOIN results r ON p.id=r.prediction_id
 WHERE r.pick_won IS NOT NULL AND p.odds_at_prediction > 1""").fetchall()

# classify each bet: sport + side(home/away) + odds band
def band(o):
    if o < 1.5: return "A_<1.5"
    if o < 2.0: return "B_1.5-2.0"
    if o < 2.5: return "C_2.0-2.5"
    if o < 3.5: return "D_2.5-3.5"
    if o < 5.0: return "E_3.5-5.0"
    if o < 8.0: return "F_5.0-8.0"
    return "G_8.0+"

agg = defaultdict(lambda: [0, 0, 0.0])  # n, wins, profit
for sp, h, a, pk, o, won, pf in rows:
    side = "home" if pk == h else "away"
    key = (sp, side, band(float(o)))
    agg[key][0] += 1
    agg[key][1] += won or 0
    agg[key][2] += pf or 0

# find positive-EV combos (enough sample, actual win% beats breakeven)
print("=== CALIBRATED EDGE MAP (positive EV combos, n>=30) ===")
print("%-14s %-5s %-10s %5s %5s %6s %6s %s" % ("sport","side","band","n","win%","ROI%","profit","edge"))
positive = []
for (sp, side, bd), (n, w, pf) in sorted(agg.items(), key=lambda x: -x[1][2]):
    if n < 30:
        continue
    wr = w / n
    avg_odds = (pf + n) / w if w > 0 else 0  # approximate avg odds from profit
    roi = 100 * pf / n
    # positive EV = actual win rate * avg odds > 1
    ev_positive = roi > 2  # at least +2% ROI
    if ev_positive:
        be = 100 / avg_odds if avg_odds > 1 else 100
        edge = wr - be/100
        print("%-14s %-5s %-10s %5d %5.0f %6.0f %+7.0f  +EV" % (sp, side, bd, n, 100*wr, roi, pf))
        positive.append({"sport": sp, "side": side, "band": bd, "n": n, "wr": round(wr,3), "roi": round(roi,1), "profit": round(pf,1)})

print("\ntotal +EV combos:", len(positive))
# save the edge map for strategy use
with open("data/calibrated_edge_map.json", "w") as f:
    json.dump(positive, f, indent=2)
print("saved to data/calibrated_edge_map.json")
