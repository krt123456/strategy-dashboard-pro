#!/usr/bin/env python3
"""Resolve prediction outcomes from betexplorer.com per-sport results pages.

This closes the prediction -> result -> report loop without a browser and without
the 1xBet public API (which purges finished matches). betexplorer keeps full
historical results across every league it covers, including the niche markets
(ITF/Challenger tennis, lower-division football, NBL1/Setka-cup style leagues)
that this prediction system bets on.

Flow:
  1. For each supported sport, fetch the betexplorer results page for the target
     date window. One fetch covers every league in that sport.
  2. Parse every finished match row (date, home, away, home_score, away_score,
     league) using the shared `table-main__*` markup.
  3. For every unresolved prediction in betting_journal.db, fuzzy-match it
     against the parsed results by sport + date + team names, grade the pick,
     and persist the result via betting_journal.add_result.

generate_report.py reads the same DB, so the daily report immediately starts
showing real per-strategy / per-sport accuracy and profit.
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from datetime import date, datetime, timedelta
from html import unescape
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR / "scripts"))

from betting_journal import add_result, get_unresolved_predictions

BASE_URL = "https://www.betexplorer.com"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"

# 1xBet sport token -> betexplorer results URL slug.
# None = no betexplorer results page available (JS-rendered, per-league only, or not covered).
SPORT_SLUGS: Dict[str, Optional[str]] = {
    "basketball": "basketball",
    "volleyball": "volleyball",
    "tennis": "tennis",
    "football": "football",
    "soccer": "football",
    "baseball": "baseball",
    "hockey": "hockey",
    "icehockey": "hockey",
    # --- no date-based results page (use per-league / alternative source) ---
    "handball": None,       # JS-rendered skeleton — needs browser
    "tabletennis": None,    # /table-tennis/results returns 404; use per-league scraper
    "table_tennis": None,
    "table tennis": None,
    "darts": None,          # /darts/results returns empty page
}

# Sports where a draw is a legitimate final result.
DRAW_SPORTS = {"football", "soccer", "hockey", "icehockey", "handball", "futsal", "cricket"}

# Cache of fetched+parsed results per sport, so we fetch once per sport.
_RESULTS_CACHE: Dict[str, List[Dict[str, Any]]] = {}


def _normalize(text: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(text or "").lower())


def _norm_key(text: Any) -> str:
    raw = str(text or "").lower()
    raw = re.sub(r"[^a-z0-9\s]", " ", raw)
    return re.sub(r"\s+", " ", raw).strip()


def _tokens(text: Any) -> List[str]:
    drop = {"fc", "sc", "cf", "bc", "ac", "afc", "bk", "fk", "kk", "club",
            "united", "town", "city", "the", "w", "l", "ii", "iii", "2", "3"}
    return [t for t in _norm_key(text).split() if t and t not in drop]


def _name_similarity(a: Any, b: Any) -> int:
    """Return 0-4 similarity score between two team/player names."""
    ka, kb = _normalize(a), _normalize(b)
    if not ka or not kb:
        return 0
    if ka == kb:
        return 4
    if ka in kb or kb in ka:
        return 3
    ta, tb = set(_tokens(a)), set(_tokens(b))
    if ta and tb:
        overlap = len(ta & tb)
        if overlap == 0:
            return 0
        ratio = overlap / max(1, min(len(ta), len(tb)))
        if ratio >= 0.67:
            return 2
        if ratio >= 0.5:
            return 1
    return 0


def _http_get(url: str, timeout_s: int = 25, retries: int = 2, sleep_s: float = 1.0) -> Optional[str]:
    last_err: Optional[str] = None
    try:
        import requests
    except Exception:
        return None
    for attempt in range(retries + 1):
        try:
            resp = requests.get(url, headers={"User-Agent": UA}, timeout=timeout_s)
            if resp.status_code == 200:
                return resp.text
            last_err = f"HTTP {resp.status_code}"
        except Exception as exc:
            last_err = str(exc)[:120]
        if attempt < retries:
            time.sleep(sleep_s)
    return None


def _results_url(sport_slug: str, target: Optional[date] = None) -> str:
    url = f"{BASE_URL}/{sport_slug}/results/"
    if target is not None:
        # betexplorer accepts year/month/day filters; this targets a specific date.
        url += f"?year={target.year}&month={target.month}&day={target.day}"
    return url


def _parse_dt_attr(value: str) -> Optional[date]:
    # data-dt="18,6,2026,2,00" -> day,month,year,hour,min
    parts = [p.strip() for p in str(value).split(",") if p.strip()]
    if len(parts) >= 3:
        try:
            return date(int(parts[2]), int(parts[1]), int(parts[0]))
        except Exception:
            return None
    return None


def _parse_tt_format(tr: str, current_league: str, sport: str) -> Optional[Dict[str, Any]]:
    """Parse a <tr> that uses the table-main__tt format (football, volleyball, etc.).

    Format: <td class=\"table-main__tt\"><a>TeamA - <strong>TeamB</strong></a></td>
            <td class=\"table-main__result\"><a> <strong>2:0</strong></a></td>
    Returns a result dict or None if the row is not a valid finished match.
    """
    tt_cell = re.search(r'table-main__tt[^>]*>(.*?)</td>', tr, re.DOTALL)
    if not tt_cell:
        return None
    tt_text = unescape(re.sub(r"<[^>]+>", "", tt_cell.group(1))).strip()
    # Remove leading time (e.g. "20:00" or "FIN ") before the team names
    tt_text = re.sub(r"^\d{1,2}:\d{2}\s*", "", tt_text).strip()
    tt_text = re.sub(r"^FIN\s*", "", tt_text).strip()
    # Split on " - " to get home and away
    parts = tt_text.split(" - ", 1)
    if len(parts) != 2:
        return None
    home = parts[0].strip()
    away = parts[1].strip()
    if not home or not away:
        return None

    score_cell = re.search(r'table-main__result[^>]*>(.*?)</td>', tr, re.DOTALL)
    if not score_cell:
        return None
    score_text = unescape(re.sub(r"<[^>]+>", "", score_cell.group(1))).strip()
    m_score = re.search(r"(\d+)\s*:\s*(\d+)", score_text)
    if not m_score:
        return None
    home_pts, away_pts = int(m_score.group(1)), int(m_score.group(2))

    return {
        "sport": sport,
        "date": None,  # filled by caller
        "home": home,
        "away": away,
        "home_pts": home_pts,
        "away_pts": away_pts,
        "league": current_league,
        "finished": True,
    }


def parse_sport_results(html: str, sport: str) -> List[Dict[str, Any]]:
    """Extract finished matches from a betexplorer per-sport results page.

    Handles two HTML layouts:
      1. teamLine--home/--away  (tennis, basketball, baseball)
      2. table-main__tt         (football, volleyball, hockey hybrid)
    """
    rows: List[Dict[str, Any]] = []
    current_league = ""
    for m_tr in re.finditer(r"<tr[^>]*>.*?</tr>", html, re.DOTALL):
        tr = m_tr.group(0)
        # tournament header: capture league name for context
        m_tour = re.search(r'table-main__tournament"[^>]*>(?:<i>.*?</i>)?([^<]+)</a>', tr, re.DOTALL)
        if m_tour:
            current_league = unescape(re.sub(r"<[^>]+>", "", m_tour.group(1))).strip()
            continue

        dt_match = re.search(r'data-dt="([^"]+)"', tr)
        if not dt_match:
            continue
        mdate = _parse_dt_attr(dt_match.group(1))

        # finished indicator: a FIN time badge OR a real score in the result cell
        is_fin = "table-main__time--fin" in tr or ">FIN<" in tr

        # --- Layout 1: teamLine--home/--away (basketball, tennis, baseball) ---
        home_raw = re.search(r'table-main__teamLine--home[^>]*>(.*?)</span>', tr, re.DOTALL)
        away_raw = re.search(r'table-main__teamLine--away[^>]*>(.*?)</span>', tr, re.DOTALL)
        if home_raw and away_raw:
            home = unescape(re.sub(r"<[^>]+>", "", home_raw.group(1))).strip()
            away = unescape(re.sub(r"<[^>]+>", "", away_raw.group(1))).strip()
            if home and away:
                score_cell = re.search(r'table-main__result[^>]*>(.*?)</td>', tr, re.DOTALL)
                score_text = ""
                if score_cell:
                    score_text = unescape(re.sub(r"<[^>]+>", "", score_cell.group(1))).strip()
                m_score = re.search(r"(\d+)\s*:\s*(\d+)", score_text)
                if m_score:
                    home_pts, away_pts = int(m_score.group(1)), int(m_score.group(2))
                    rows.append({
                        "sport": sport,
                        "date": mdate,
                        "home": home,
                        "away": away,
                        "home_pts": home_pts,
                        "away_pts": away_pts,
                        "league": current_league,
                        "finished": is_fin or bool(m_score),
                    })
                continue

        # --- Layout 2: table-main__tt (football, volleyball, hockey hybrid) ---
        result = _parse_tt_format(tr, current_league, sport)
        if result is not None:
            result["date"] = mdate
            rows.append(result)

    return rows


def fetch_sport_results(sport: str, target: date, *, days_back: int = 2,
                        timeout: int = 25) -> List[Dict[str, Any]]:
    """Fetch (with simple per-sport cache) finished results near the target date."""
    slug = SPORT_SLUGS.get(_norm_key(sport) and sport.lower() or sport)
    if slug is None:
        # try normalized lookup
        slug = SPORT_SLUGS.get(_norm_key(sport))
    if slug is None:
        return []
    cache_key = f"{slug}:{target.isoformat()}:{days_back}"
    if cache_key in _RESULTS_CACHE:
        return _RESULTS_CACHE[cache_key]

    all_rows: List[Dict[str, Any]] = []
    seen: set = set()
    # fetch the default page plus a targeted page for the date and previous days
    targets: List[Optional[date]] = [None]
    for d in range(days_back + 1):
        targets.append(target - timedelta(days=d))
    for tgt in targets:
        url = _results_url(slug, tgt)
        html = _http_get(url, timeout_s=timeout)
        if not html:
            continue
        for row in parse_sport_results(html, sport):
            key = (row["date"].isoformat() if row["date"] else "", _normalize(row["home"]),
                   _normalize(row["away"]), row["home_pts"], row["away_pts"])
            if key in seen:
                continue
            seen.add(key)
            all_rows.append(row)
        time.sleep(0.4)
    _RESULTS_CACHE[cache_key] = all_rows
    return all_rows


def _pick_side(pick: str, home: str, away: str, sport: str) -> str:
    p = _normalize(pick)
    if p in {"draw", "x", "tie", "draws"}:
        return "draw"
    if p and p == _normalize(home):
        return "home"
    if p and p == _normalize(away):
        return "away"
    hs = _name_similarity(pick, home)
    as_ = _name_similarity(pick, away)
    if hs > as_ and hs >= 2:
        return "home"
    if as_ > hs and as_ >= 2:
        return "away"
    low = pick.lower()
    if "over" in low or "under" in low or "btts" in low:
        return "unknown"
    if "home" in low and "away" not in low:
        return "home"
    if "away" in low and "home" not in low:
        return "away"
    return "unknown"


def _match_prediction(pred: Dict[str, Any], results: List[Dict[str, Any]],
                      min_score: int = 4) -> Optional[Dict[str, Any]]:
    """Find the best fuzzy-matched result row for a prediction."""
    pdate = pred["match_date"]
    ph, pa = pred["home"], pred["away"]
    best: Optional[Tuple[int, Dict[str, Any]]] = None
    for r in results:
        if r["date"] and r["date"].isoformat() != pdate:
            continue
        normal = _name_similarity(ph, r["home"]) + _name_similarity(pa, r["away"])
        swapped = _name_similarity(ph, r["away"]) + _name_similarity(pa, r["home"])
        score = max(normal, swapped)
        if score < min_score:
            continue
        if best is None or score > best[0]:
            best = (score, r)
    return best[1] if best else None


def resolve(target_date: str, *, days_back: int = 2, limit: int = 0,
            min_match_score: int = 4, verbose: bool = True) -> Dict[str, int]:
    unresolved = get_unresolved_predictions(target_date)
    cutoff = (date.fromisoformat(target_date) - timedelta(days=days_back)).isoformat()
    window = [p for p in unresolved if p["match_date"] >= cutoff]
    if limit:
        window = window[:limit]

    stats = {"checked": 0, "resolved": 0, "no_match": 0,
             "unmapped_sport": 0, "no_score": 0, "window": len(window)}
    # group predictions by sport to fetch each sport's results once
    by_sport: Dict[str, List[Dict[str, Any]]] = {}
    for p in window:
        if SPORT_SLUGS.get(p["sport"].lower().replace(" ", "")) or SPORT_SLUGS.get(_norm_key(p["sport"])):
            by_sport.setdefault(p["sport"], []).append(p)
        else:
            stats["unmapped_sport"] += 1

    sport_results: Dict[str, List[Dict[str, Any]]] = {}
    for sport, preds in by_sport.items():
        if verbose:
            print(f"  fetching betexplorer results for {sport} ({len(preds)} predictions)...")
        try:
            sport_results[sport] = fetch_sport_results(
                sport, date.fromisoformat(target_date), days_back=days_back
            )
        except Exception as exc:
            if verbose:
                print(f"    fetch failed for {sport}: {str(exc)[:80]}")
            sport_results[sport] = []
        if verbose:
            print(f"    parsed {len(sport_results[sport])} finished matches")

    for sport, preds in by_sport.items():
        results = sport_results.get(sport, [])
        for pred in preds:
            stats["checked"] += 1
            matched = _match_prediction(pred, results, min_score=min_match_score)
            if matched is None:
                stats["no_match"] += 1
                continue
            home_pts = matched["home_pts"]
            away_pts = matched["away_pts"]
            side = _pick_side(pred["pick"], pred["home"], pred["away"], sport)
            if side == "unknown":
                stats["no_score"] += 1
                continue
            nsport = _norm_key(sport)
            if side == "draw":
                won = home_pts == away_pts
            else:
                if home_pts == away_pts and nsport not in DRAW_SPORTS:
                    stats["no_score"] += 1
                    continue
                home_won = home_pts > away_pts
                won = (side == "home" and home_won) or (side == "away" and not home_won)
            add_result(pred["id"], home_pts, away_pts, bool(won),
                       result_source="betexplorer")
            stats["resolved"] += 1
            if verbose:
                mark = "WON" if won else "LOST"
                print(f"  ✓ [{pred['id']}] {sport} {pred['home']} vs {pred['away']} "
                      f"pick={pred['pick']} => {home_pts}:{away_pts} {mark} [{matched.get('league','')}]")
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description="Resolve prediction results via betexplorer (multi-sport, browser-free).")
    parser.add_argument("--date", default=None, help="target date YYYY-MM-DD (default: yesterday UTC)")
    parser.add_argument("--days-back", type=int, default=2)
    parser.add_argument("--limit", type=int, default=0, help="max predictions per sport (0 = all)")
    parser.add_argument("--min-match-score", type=int, default=4, help="min fuzzy match score (4=strict,3=lenient)")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    target = args.date or (datetime.utcnow() - timedelta(days=1)).strftime("%Y-%m-%d")
    print(f"Resolving results from betexplorer for {target} (window -{args.days_back}d)...")
    try:
        stats = resolve(target, days_back=args.days_back, limit=args.limit,
                        min_match_score=args.min_match_score, verbose=not args.quiet)
    except Exception as exc:
        print(f"✗ fatal: {exc}")
        return 2
    print(
        f"\nSummary: resolved={stats['resolved']}  no_match={stats['no_match']}  "
        f"no_score={stats['no_score']}  unmapped_sport={stats['unmapped_sport']}  "
        f"checked={stats['checked']}/{stats['window']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
