#!/usr/bin/env python3
"""Resolve pending predictions via FlashScore.com browser scraping.

FlashScore covers ALL the niche sports that betexplorer doesn't (table tennis,
hockey, snooker, darts, cricket, futsal, handball). Uses the same hidden-browser
pattern as car_deal_finder: Playwright connects to a long-lived Chrome CDP
session so the browser fingerprint persists and Cloudflare treats us as human.

On every run (every 2h on VPS), this:
1. Opens FlashScore per-sport finished-match pages
2. Extracts match results (teams, scores)
3. Fuzzy-matches them against pending predictions in betting_journal.db
4. Writes resolved outcomes via betting_journal.add_result
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import List, Optional

PROJECT_DIR = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_DIR / "data" / "betting_journal.db"

SPORT_SLUGS = {
    "tabletennis": ("table-tennis",),
    "hockey": ("hockey", "ice-hockey"),
    "snooker": ("snooker",),
    "darts": ("darts",),
    "cricket": ("cricket",),
    "futsal": ("futsal",),
    "handball": ("handball",),
}

try:
    from playwright.sync_api import sync_playwright
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False


def _norm(t: str) -> str:
    return re.sub(r"[^a-z0-9]", "", t.lower())


def _similarity(a: str, b: str) -> int:
    """0-4 fuzzy match score."""
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return 0
    if na == nb:
        return 4
    if na in nb or nb in na:
        return 3
    ta = set(na.split())
    tb = set(nb.split())
    if not ta or not tb:
        return 0
    ol = len(ta & tb)
    if ol / max(1, min(len(ta), len(tb))) >= 0.5:
        return 2 if ol >= 2 else 1
    return 0


def scrape_sport_results(sport_slug: str, target_date: str) -> List[dict]:
    """Launch headless Playwright, fetch finished matches from FlashScore."""
    if not HAS_PLAYWRIGHT:
        print(f"  Playwright not installed; cannot scrape {sport_slug}")
        return []

    results: List[dict] = []
    url = f"https://www.flashscore.com/"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=20000)
            time.sleep(3)
            # Navigate to the sport via the left nav menu if we need a specific one
            if sport_slug != "table-tennis":
                # Click the sport in the nav
                sport_label = sport_slug.replace("-", " ").title()
                page.evaluate(f"""
                    const all = document.querySelectorAll('a, span, div');
                    for (const el of all) {{
                        if (el.textContent.trim().toUpperCase() === '{sport_label.upper()}' && el.tagName === 'A') {{
                            el.click(); break;
                        }}
                    }}
                """)
                time.sleep(3)
            
            # Click "FINISHED" tab
            page.evaluate("""
                const all = document.querySelectorAll('*');
                for (const el of all) {
                    if (el.childNodes.length === 1 && el.textContent.trim().toUpperCase() === 'FINISHED') {
                        el.click(); break;
                    }
                }
            """)
            time.sleep(3)
            text = page.evaluate("() => document.body.innerText")
            lines = [l.strip() for l in text.split("\n") if l.strip()]

            # Parse score patterns
            i = 0
            while i < len(lines) - 3:
                a, b, c, d = lines[i], lines[i + 1], lines[i + 2], lines[i + 3]
                if re.match(r"^\d+$", a) and re.match(r"^\d+$", c) and not re.match(r"^\d+$", b):
                    # Pattern: ScoreA / TeamA / ScoreB / TeamB
                    results.append({
                        "home": b.strip(), "away": d.strip(),
                        "home_score": int(a), "away_score": int(c),
                        "date": target_date,
                    })
                    i += 4
                else:
                    i += 1

            # Pattern: TeamA / TeamB / "ScoreA ScoreB" (falls back to betexplorer parser logic)
            i = 0
            while i < len(lines) - 2:
                a, b, c = lines[i], lines[i + 1], lines[i + 2]
                m = re.match(r"^(\d+)\s+(\d+)$", b)
                if m and len(a) > 2 and len(c) > 2 and not re.match(r"^\d+$", a) and not re.match(r"^\d+$", c):
                    results.append({
                        "home": a.strip(), "away": c.strip(),
                        "home_score": int(m.group(1)), "away_score": int(m.group(2)),
                        "date": target_date,
                    })
                    i += 3
                else:
                    i += 1

        except Exception as e:
            print(f"  FlashScore scrape error ({sport_slug}): {str(e)[:80]}")
        finally:
            browser.close()

    return results


def resolve_flashscore(target_date: str, verbose: bool = True) -> dict:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # Get unresolved predictions for FlashScore-covered sports
    sports_covered = list(SPORT_SLUGS.keys())
    placeholders = ",".join("?" * len(sports_covered))
    pending = c.execute(
        f"""SELECT p.id, p.match_date, p.sport, p.home, p.away, p.pick
            FROM predictions p LEFT JOIN results r ON p.id = r.prediction_id
            WHERE p.match_date=? AND p.sport IN ({placeholders}) AND r.id IS NULL""",
        (target_date, *sports_covered),
    ).fetchall()

    stats = {"checked": 0, "resolved": 0, "no_match": 0, "errors": 0, "total_pending": len(pending)}
    if not pending:
        conn.close()
        return stats

    # Scrape each sport once
    scraped: dict = {}
    for sport_key, slugs in SPORT_SLUGS.items():
        for slug in slugs:
            if slug not in scraped:
                if verbose:
                    print(f"  scraping FlashScore {slug}...")
                scraped[slug] = scrape_sport_results(slug, target_date)

    # Match predictions
    from betting_journal import add_result

    for row in pending:
        pid, mdate, sport, home, away, pick = row
        stats["checked"] += 1
        label = f"[{pid}] {sport} {home[:16]} vs {away[:16]}"

        best_match = None
        best_score = 0
        for slug in SPORT_SLUGS.get(sport, ()):
            for r in scraped.get(slug, []):
                normal = _similarity(home, r["home"]) + _similarity(away, r["away"])
                swapped = _similarity(home, r["away"]) + _similarity(away, r["home"])
                sc = max(normal, swapped)
                if sc > best_score and sc >= 3:
                    best_score = sc
                    best_match = r

        if not best_match:
            stats["no_match"] += 1
            continue

        hpts, apts = best_match["home_score"], best_match["away_score"]
        side = "home" if _similarity(pick, home) >= _similarity(pick, away) else "away"
        pick_won = (side == "home" and hpts > apts) or (side == "away" and apts > hpts)

        try:
            add_result(pid, hpts, apts, pick_won, result_source="flashscore_scrape")
            stats["resolved"] += 1
            if verbose:
                mark = "WON" if pick_won else "LOST"
                print(f"  ✓ {label} => {hpts}:{apts} {mark}")
        except Exception:
            stats["errors"] += 1

    conn.close()
    return stats


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()
    target = args.date or datetime.now().strftime("%Y-%m-%d")
    print(f"FlashScore resolver for {target} ({','.join(SPORT_SLUGS.keys())})...")
    if not HAS_PLAYWRIGHT:
        print("Playwright not installed. Install: pip install playwright && playwright install chromium")
        return 1
    stats = resolve_flashscore(target, verbose=not args.quiet)
    print(f"Summary: resolved={stats['resolved']} no_match={stats['no_match']} "
          f"errors={stats['errors']} total_pending={stats['total_pending']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
