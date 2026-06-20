#!/usr/bin/env python3
"""Cross-source strategy runner — the variant x source tournament engine.

Takes the cached fixtures from every data source (see data_source_registry.py)
and runs each winning strategy variant over them. Because the variant rules are
probability-threshold rules (not sport-specific), a variant tuned on basketball
can be tested on tennis, table tennis, darts, etc. — the exact cross-sport,
cross-source matrix the tournament needs.

Each generated pick is tagged with both its strategy (variant name) and its data
source, so the daily resolver + report can rank (variant, source) combinations
and reveal which source's odds produce the most profitable signals.
"""
from __future__ import annotations

import argparse
import csv
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

PROJECT_DIR = Path(__file__).resolve().parent.parent
CACHE_DIR = PROJECT_DIR / "data" / "cache"
WINNERS_PATH = PROJECT_DIR / "data" / "winning_variants.json"
DB_PATH = PROJECT_DIR / "data" / "betting_journal.db"

# Sports with a draw as a real outcome (pick may be a team or a draw).
DRAW_SPORTS = {"football", "soccer", "hockey", "icehockey", "handball", "futsal", "cricket"}


def _normalize_sport(s: str) -> str:
    return (s or "").lower().replace(" ", "").replace("-", "")


def apply_variant(params: dict, base: str, home_prob: float, away_prob: float) -> Optional[str]:
    """Return 'home'/'away'/None for a variant given two outcome probabilities."""
    if "threshold" in params:
        t = params["threshold"]
        if home_prob >= t:
            return "home"
        if away_prob >= t:
            return "away"
    elif "margin" in params:
        m = params["margin"]
        if home_prob - away_prob >= m:
            return "home"
        if away_prob - home_prob >= m:
            return "away"
    elif "lo" in params and "hi" in params:
        lo, hi = params["lo"], params["hi"]
        if base == "away_dominant":
            if lo <= away_prob <= hi:
                return "away"
        else:
            if lo <= home_prob <= hi:
                return "home"
    return None


def load_fixtures() -> List[dict]:
    """Load the latest cached fixtures (all sources merged)."""
    caches = sorted(CACHE_DIR.glob("sources_*.json"))
    if not caches:
        return []
    data = json.loads(caches[-1].read_text(encoding="utf-8"))
    fixtures: List[dict] = []
    for src, items in data.get("sources", {}).items():
        for f in items:
            hp, ap = f.get("home_prob", 0), f.get("away_prob", 0)
            if not (0 < hp < 1 and 0 < ap < 1):
                continue  # need real probabilities to run a strategy
            f["source"] = src
            fixtures.append(f)
    return fixtures


def dedupe_key(p: dict) -> tuple:
    return (p["match_date"], p["home"], p["away"], p["pick"], p["source"], p["strategy"])


def run(target_date: Optional[str] = None, limit_per_combo: int = 0) -> dict:
    target = target_date or datetime.utcnow().strftime("%Y-%m-%d")
    if not WINNERS_PATH.exists():
        print("No winning_variants.json — run strategy_variant_generator.py first.")
        return {"picks": 0}
    variants = json.loads(WINNERS_PATH.read_text(encoding="utf-8"))
    fixtures = load_fixtures()
    if not fixtures:
        print("No cached fixtures with probabilities — run data_source_registry.py first.")
        return {"picks": 0}

    print(f"Running {len(variants)} variants x {len(fixtures)} fixtures "
          f"({len({f['source'] for f in fixtures})} sources)...")

    # الاستراتيجيات الخبيرة الواعية بالـ vig (من expert_strategies.py + reports/strategy_intelligence.md)
    try:
        import expert_strategies as ex
        expert_fns = {
            "coinflip_home_premium": ex.coinflip_home_premium,
            "thick_edge_favorite": ex.thick_edge_favorite,
        }
    except Exception:
        expert_fns = {}

    picks: List[dict] = []
    seen = set()
    for f in fixtures:
        hp, ap = f["home_prob"], f["away_prob"]
        # parameter-sweep variants (probability rules)
        for v in variants:
            side = apply_variant(v.get("params", {}), v.get("base", ""), hp, ap)
            if side is None:
                continue
            prob = hp if side == "home" else ap
            odds = 1.0 / max(prob, 0.01)
            strategy = f"{v['name']}__{f['source']}"
            pick = {
                "match_date": f.get("date") or target,
                "sport": _normalize_sport(f.get("sport", "")),
                "league": f.get("league", ""),
                "home": f["home"],
                "away": f["away"],
                "pick": f["home"] if side == "home" else f["away"],
                "source": f["source"],
                "strategy": strategy,
                "model_prob": round(prob, 4),
                "odds_at_prediction": round(odds, 2),
                "confidence": "C",
                "notes": f"{v['name']} via {f['source']} (backtest ROI {v.get('backtest_roi',0):+.1f}%)",
            }
            k = dedupe_key(pick)
            if k not in seen:
                seen.add(k)
                picks.append(pick)

        # expert vig-aware strategies (need raw bookmaker odds)
        ho = 1.0 / max(hp, 0.01)
        ao = 1.0 / max(ap, 0.01)
        for ename, efn in expert_fns.items():
            try:
                r = efn(f["home"], f["away"], ho, ao)
            except Exception:
                r = None
            if not r:
                continue
            r["match_date"] = f.get("date") or target
            r["sport"] = _normalize_sport(f.get("sport", ""))
            r["league"] = f.get("league", "")
            r["home"] = f["home"]
            r["away"] = f["away"]
            r["strategy"] = f"{ename}__{f['source']}"
            k = dedupe_key(r)
            if k not in seen:
                seen.add(k)
                picks.append(r)

    # persist to betting_journal.db
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    recorded = 0
    for p in picks:
        exists = c.execute(
            "SELECT 1 FROM predictions WHERE match_date=? AND home=? AND away=? "
            "AND pick=? AND source=? AND strategy=?",
            (p["match_date"], p["home"], p["away"], p["pick"], p["source"], p["strategy"]),
        ).fetchone()
        if exists:
            continue
        c.execute(
            "INSERT INTO predictions (created_at, match_date, sport, league, home, away, "
            "pick, source, model_prob, odds_at_prediction, stake, kelly_stake, strategy, "
            "confidence, notes) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (datetime.now().isoformat(), p["match_date"], p["sport"], p["league"], p["home"],
             p["away"], p["pick"], p["source"], p["model_prob"], p["odds_at_prediction"],
             0.0, 0.0, p["strategy"], p["confidence"], p["notes"]),
        )
        recorded += 1
    conn.commit()
    conn.close()

    # summary by source x sport
    by_src: Dict[str, int] = {}
    by_sport: Dict[str, int] = {}
    for p in picks:
        by_src[p["source"]] = by_src.get(p["source"], 0) + 1
        by_sport[p["sport"]] = by_sport.get(p["sport"], 0) + 1
    print(f"\nGenerated {len(picks)} picks ({recorded} new).")
    print("By source: " + ", ".join(f"{k}={v}" for k, v in sorted(by_src.items(), key=lambda x: -x[1])))
    print("By sport:  " + ", ".join(f"{k}={v}" for k, v in sorted(by_sport.items(), key=lambda x: -x[1])))
    return {"picks": len(picks), "recorded": recorded, "by_source": by_src, "by_sport": by_sport}


def main() -> int:
    ap = argparse.ArgumentParser(description="Run winning variants across all cached data sources.")
    ap.add_argument("--date", default=None)
    args = ap.parse_args()
    run(target_date=args.date)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
