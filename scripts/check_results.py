#!/usr/bin/env python3
"""Multi-sport result checker — resolves prediction outcomes via the public 1xBet API.

This is deliberately browser-free (uses curl under the hood) so it runs unchanged
on GitHub Actions. It covers every sport the prediction system bets on, because
all markets originate from 1xBet and the public GetGameZip endpoint returns the
final score for finished events regardless of how obscure the league is.

Strategy per unresolved prediction:
  1. Fast path — look the event up in the local 1xBet linefeed snapshot CSV
     (home/away/sport/date fuzzy match). If found, we already have the event id.
  2. Slow path — call the public Web_SearchZip endpoint and pick the best
     fuzzy-matched event.
  3. Fetch the game via GetGameZip, parse its score, decide if it is finished,
     grade the pick, and persist the result into betting_journal.db.

All result rows flow into the same SQLite store that generate_report.py reads,
so the daily report starts showing real strategy/sport accuracy and profit.
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR / "scripts"))

from betting_journal import add_result, get_unresolved_predictions
import one_xbet_public_odds_sync as one_xbet

DEFAULT_SNAPSHOT = PROJECT_DIR / "data" / "one_xbet_linefeed_snapshot.csv"

# Sports where a draw is a real final outcome (pick may legitimately be "draw").
DRAW_SPORTS = {"football", "soccer", "hockey", "icehockey", "handball", "cricket", "futsal"}


def _as_float(value: Any) -> Optional[float]:
    try:
        if value in (None, ""):
            return None
        out = float(value)
        if out != out:  # NaN
            return None
        return out
    except Exception:
        return None


def _scores_from_game(game: Dict[str, Any]) -> Tuple[Optional[float], Optional[float]]:
    """Extract (home_total, away_total) final score from a GetGameZip value.

    The 1xBet SC container differs by sport, so we try a cascade of layouts:
      - explicit final/current pair keys (FS1/FS2, S1/S2, HomeScore/AwayScore)
      - a per-period sub-dictionary that we sum (basketball quarters, tennis sets)
      - the SS status string of the form "X:Y"
    """
    sc = game.get("SC")
    if isinstance(sc, dict):
        for h_key, a_key in (("FS1", "FS2"), ("S1", "S2"), ("HomeScore", "AwayScore")):
            h = _as_float(sc.get(h_key))
            a = _as_float(sc.get(a_key))
            if h is not None and a is not None:
                return h, a

        home_sum = 0.0
        away_sum = 0.0
        found = False
        for val in sc.values():
            if not isinstance(val, dict):
                continue
            h = _as_float(val.get("Home") or val.get("S1") or val.get("Value1") or val.get("H"))
            a = _as_float(val.get("Away") or val.get("S2") or val.get("Value2") or val.get("A"))
            if h is not None and a is not None:
                home_sum += h
                away_sum += a
                found = True
        if found:
            return home_sum, away_sum

        nums = [v for v in (_as_float(v) for v in sc.values()) if v is not None]
        if len(nums) >= 2:
            return nums[0], nums[1]

    ss = str(game.get("SS") or "")
    m = re.search(r"(\d+)\s*:\s*(\d+)", ss)
    if m:
        return float(m.group(1)), float(m.group(2))
    return None, None


def _start_dt(game: Dict[str, Any]) -> Optional[datetime]:
    ts = game.get("S")
    if ts in (None, ""):
        return None
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc)
    except Exception:
        return None


def _is_finished(game: Dict[str, Any], now: datetime) -> bool:
    start = _start_dt(game)
    if start is not None and start > now:
        return False
    h, a = _scores_from_game(game)
    if h is None or a is None:
        return False
    # Require at least one hour of elapsed playtime so we don't grade a live game.
    if start is not None and (now - start).total_seconds() < 3600:
        return False
    return True


def _pick_side(pick: str, home: str, away: str, sport: str) -> str:
    """Map a pick string to 'home' / 'away' / 'draw' / 'unknown'."""
    p = one_xbet._norm_key(pick)
    if p in {"draw", "x", "tie", "draws"}:
        return "draw"
    h_key = one_xbet._norm_key(home)
    a_key = one_xbet._norm_key(away)
    if p and p == h_key:
        return "home"
    if p and p == a_key:
        return "away"
    # fuzzy token overlap
    h_score = one_xbet._score_side(pick, home)
    a_score = one_xbet._score_side(pick, away)
    if h_score > a_score and h_score > 0:
        return "home"
    if a_score > h_score and a_score > 0:
        return "away"
    # pick labels sometimes read like "Team (Home)" or "Over 2.5 Goals (Team)"
    low = pick.lower()
    if "over" in low or "under" in low or "btts" in low:
        # goals-based market; side is encoded in parentheses if present
        m = re.search(r"\(([^)]+)\)", pick)
        if m:
            return _pick_side(m.group(1), home, away, sport)
        return "unknown"
    if "home" in low and "away" not in low:
        return "home"
    if "away" in low and "home" not in low:
        return "away"
    return "unknown"


def _grade(side: str, home_pts: float, away_pts: float, sport: str) -> Optional[bool]:
    if side == "unknown":
        return None
    if side == "draw":
        return home_pts == away_pts
    home_won = home_pts > away_pts
    if home_pts == away_pts and sport not in DRAW_SPORTS:
        # ambiguous equal score for a non-draw sport -> do not guess
        return None
    return (side == "home" and home_won) or (side == "away" and not home_won)


def _load_snapshot(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _snapshot_event(
    pred: Dict[str, Any],
    snapshot: List[Dict[str, Any]],
    target: date,
    min_score: int,
) -> Optional[one_xbet.MatchedEvent]:
    """Fast path: resolve an event id from the local linefeed snapshot CSV."""
    sport_id = one_xbet.SPORT_IDS.get(one_xbet._normalize(pred["sport"]))
    if sport_id is None:
        return None
    home = pred["home"]
    away = pred["away"]
    best: Optional[Tuple[int, Dict[str, Any]]] = None
    for snap in snapshot:
        try:
            if int(float(snap.get("SportId") or 0)) != sport_id:
                continue
        except Exception:
            if one_xbet._normalize(snap.get("Sport")) != one_xbet._normalize(pred["sport"]):
                continue
        snap_date = str(snap.get("Date") or "")[:10]
        if snap_date and snap_date != target.isoformat():
            continue
        normal = one_xbet._score_side(home, snap.get("Home")) + one_xbet._score_side(away, snap.get("Away"))
        swapped = one_xbet._score_side(home, snap.get("Away")) + one_xbet._score_side(away, snap.get("Home"))
        score = max(normal, swapped)
        if score < min_score:
            continue
        if best is None or score > best[0]:
            best = (score, snap)
    if best is None:
        return None
    score, snap = best
    return one_xbet.MatchedEvent(
        event_id=snap.get("EventId"),
        canonical_id=snap.get("CanonicalId"),
        home=str(snap.get("Home") or ""),
        away=str(snap.get("Away") or ""),
        league=str(snap.get("League") or ""),
        event_date=target,
        score=score,
        base_url=str(snap.get("PublicBase") or "snapshot"),
    )


def resolve(target_date: str, *, timeout: int = 10, min_score: int = 3,
            sleep: float = 0.1, days_back: int = 2, limit: int = 250,
            time_budget_min: float = 11.0, snapshot_path: Path = DEFAULT_SNAPSHOT,
            verbose: bool = True) -> Dict[str, int]:
    unresolved = get_unresolved_predictions(target_date)
    # restrict to a recent date window so daily runs stay bounded
    cutoff = (date.fromisoformat(target_date) - timedelta(days=days_back)).isoformat()
    window = [p for p in unresolved if p["match_date"] >= cutoff]

    snapshot = _load_snapshot(snapshot_path)
    now = datetime.now(timezone.utc)
    deadline = now + timedelta(minutes=time_budget_min)

    stats = {"checked": 0, "resolved": 0, "not_started": 0,
             "no_match": 0, "no_score": 0, "errors": 0, "window": len(window)}

    for pred in window:
        if stats["checked"] >= limit:
            break
        if datetime.now(timezone.utc) > deadline:
            if verbose:
                print(f"⏱  time budget ({time_budget_min} min) reached, stopping early")
            break
        stats["checked"] += 1
        sport = pred["sport"]
        home = pred["home"]
        away = pred["away"]
        pick = pred["pick"]
        label = f"[{pred['id']}] {sport} {home} vs {away} pick={pick}"

        row = {"Sport": sport, "Home": home, "Away": away, "Date": pred["match_date"]}
        try:
            event = _snapshot_event(pred, snapshot, date.fromisoformat(pred["match_date"]), min_score)
            if event is None:
                event = one_xbet._best_event(
                    row, date.fromisoformat(pred["match_date"]),
                    timeout, min_score, broad_queries=False,
                )
        except Exception:
            event = None

        if event is None:
            stats["no_match"] += 1
            continue

        try:
            game = one_xbet._fetch_game(event, timeout)
        except Exception:
            stats["errors"] += 1
            if sleep:
                time.sleep(sleep)
            continue

        start = _start_dt(game)
        if start is not None and start > now:
            stats["not_started"] += 1
            if sleep:
                time.sleep(sleep)
            continue

        home_pts, away_pts = _scores_from_game(game)
        if home_pts is None or away_pts is None:
            stats["no_score"] += 1
            if sleep:
                time.sleep(sleep)
            continue

        # equal score for a non-draw sport is ambiguous at this stage -> skip
        if home_pts == away_pts and one_xbet._normalize(sport) not in DRAW_SPORTS:
            stats["no_score"] += 1
            continue

        side = _pick_side(pick, home, away, sport)
        if side == "unknown":
            side = _pick_side(pick, event.home, event.away, sport)
        won = _grade(side, home_pts, away_pts, sport)
        if won is None:
            stats["no_score"] += 1
            continue

        add_result(pred["id"], int(home_pts), int(away_pts), bool(won),
                   result_source="1xbet_public_api")
        stats["resolved"] += 1
        if verbose:
            mark = "WON" if won else "LOST"
            print(f"  ✓ {label} => {int(home_pts)}:{int(away_pts)} {mark} [{event.event_id}]")
        if sleep:
            time.sleep(sleep)

    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description="Resolve prediction results via 1xBet public API (multi-sport, browser-free).")
    parser.add_argument("--date", default=None, help="target date YYYY-MM-DD (default: yesterday)")
    parser.add_argument("--days-back", type=int, default=2, help="only grade predictions within this many days before target")
    parser.add_argument("--limit", type=int, default=250, help="max predictions to grade this run")
    parser.add_argument("--timeout", type=int, default=10, help="per-call HTTP timeout seconds")
    parser.add_argument("--min-score", type=int, default=3, help="minimum fuzzy match score (1-4)")
    parser.add_argument("--sleep", type=float, default=0.1, help="pause between calls (seconds)")
    parser.add_argument("--time-budget-min", type=float, default=11.0, help="hard stop after this many minutes")
    parser.add_argument("--snapshot", default=str(DEFAULT_SNAPSHOT), help="linefeed snapshot CSV path")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    target = args.date or (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    print(f"Checking results for {target} (window: -{args.days_back}d, limit {args.limit}, budget {args.time_budget_min}m)...")

    try:
        stats = resolve(
            target,
            timeout=args.timeout,
            min_score=args.min_score,
            sleep=args.sleep,
            days_back=args.days_back,
            limit=args.limit,
            time_budget_min=args.time_budget_min,
            snapshot_path=Path(args.snapshot),
            verbose=not args.quiet,
        )
    except Exception as exc:
        print(f"✗ fatal: {exc}")
        return 2

    print(
        f"\nSummary: resolved={stats['resolved']}  not_started={stats['not_started']}  "
        f"no_match={stats['no_match']}  no_score={stats['no_score']}  errors={stats['errors']}  "
        f"checked={stats['checked']}/{stats['window']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
