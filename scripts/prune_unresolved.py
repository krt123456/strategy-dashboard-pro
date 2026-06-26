#!/usr/bin/env python3
"""Prune ended-but-unresolved predictions older than a grace window.

These are matches that finished but have no result in any accessible source
(1xBet niche markets: lower divisions, women's/youth, tabletennis). They are
invisible in the dashboard (excluded from the predictions JOIN results) and can
never be graded, so they are pruned as DB hygiene after a grace window.

Investigation (2026-06-26) confirmed: betexplorer/flashscore/scoretennis do not
cover these matches (best fuzzy-match score 0-1, i.e. not present, not mismatched);
1xBet's public linefeed API is prematch-only (finished events vanish from
GetGameZip); only 1xBet's Cloudflare-blocked website holds them. So pruning is the
correct hygiene, not a matching failure. Run from the cron after resolve.
"""
import argparse
import datetime
import sqlite3
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "data" / "betting_journal.db"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--days", type=int, default=1,
                    help="prune unresolved predictions with match_date older than this many days (0 = all before today)")
    args = ap.parse_args()
    cutoff = (datetime.date.today() - datetime.timedelta(days=args.days)).isoformat()
    c = sqlite3.connect(DB)
    cur = c.cursor()
    n = cur.execute(
        "DELETE FROM predictions WHERE match_date < ? "
        "AND id NOT IN (SELECT prediction_id FROM results)",
        (cutoff,),
    ).rowcount
    c.commit()
    c.close()
    print(f"pruned {n} ended-unresolved predictions older than {args.days} day(s) (cutoff {cutoff})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
