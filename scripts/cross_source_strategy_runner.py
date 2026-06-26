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

# Nova steam-move threshold: odds shortening (vs opening snapshot) that counts as a
# steam move (sharp-money signal). Retroactively validated at +22-26% ROI (unique matches).
STEAM_THR = 0.03

# Result-source gate (2026-06-26): SKIP sports/leagues with no accessible result source.
# Do not generate predictions that can never be graded. Evidence: futsal 1%, hockey-RHL 0%
# resolution. Config: data/result_source_gate.json. Re-enable by editing the config once a
# source is wired (API_SPORTS_KEY for hockey/futsal; scores24 resolver for table tennis).
try:
    _gate_cfg = json.loads((PROJECT_DIR / "data" / "result_source_gate.json").read_text("utf-8"))
    UNSOURCED_SPORTS = set(s.lower().replace(" ", "") for s in _gate_cfg.get("unsourced_sports", []))
    UNSOURCED_LEAGUE_SUBS = [s.lower() for s in _gate_cfg.get("unsourced_league_substrings", [])]
except Exception:
    UNSOURCED_SPORTS = set()
    UNSOURCED_LEAGUE_SUBS = []


def _is_unsourced(sport: str, league: str) -> bool:
    if (sport or "").lower().replace(" ", "") in UNSOURCED_SPORTS:
        return True
    lg = (league or "").lower()
    return any(sub in lg for sub in UNSOURCED_LEAGUE_SUBS)


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
            "deep_seek_1": ex.deep_seek_1,
            "deep_seek_2": ex.deep_seek_2,
            "deep_seek_3": ex.mid_odds_home,
            "deep_seek_4": ex.baseball_home_specialist,
            "deep_seek_5": ex.safe_odds_floor,
            "deep_seek_6": ex.deep_seek_6_tt_away,
            "deep_seek_7": ex.deep_seek_7_baseball_compound,
            "deep_seek_8": ex.deep_seek_8_tennis_hybrid,
            "deep_seek_9": ex.deep_seek_9_football_away,
            "deep_seek_10": ex.deep_seek_10_hybrid_auto,
            "deep_seek_11": ex.deep_seek_11_multifilter,
            "nova_fade_favorite": ex.nova_fade_favorite,
            "nova_sweet_spot": ex.nova_sweet_spot,
            "nova_underdog": ex.nova_underdog,
            "nova_pickem": ex.nova_pickem,
            "nova_volley_home": ex.nova_volley_home,
            "nova_baseball_away": ex.nova_baseball_away,
            "nova_fade_fav_v2": ex.nova_fade_fav_v2,
            "nova_baseball_away_v2": ex.nova_baseball_away_v2,
        }
    except Exception:
        expert_fns = {}

    # مكتبة النسخ المُطوّرة يومياً (append-only) — كل نسخة خبيرة تتنافس حيّاً
    try:
        import daily_strategy_evolution as dse
        import version_engine as ve
        library = dse.get_active_versions()
    except Exception:
        library = []
        ve = None

    picks: List[dict] = []
    seen = set()
    skipped_unsourced = 0
    for f in fixtures:
        if _is_unsourced(f.get("sport", ""), f.get("league", "")):
            skipped_unsourced += 1
            continue
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
                r = efn(f["home"], f["away"], ho, ao, f.get("sport", ""))
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

        # Nova steam-move: حافة مستقلة من حركة odds (sharp money) — مُتحقَّق بأثر رجعي +22% ROI
        hm = f.get("home_move"); am = f.get("away_move")
        ho_real = f.get("home_odds"); ao_real = f.get("away_odds")
        if hm is not None and am is not None and ho_real and ao_real:
            _side = _odds = _mv = None
            if hm <= -STEAM_THR and hm <= am:
                _side, _odds, _mv = "home", ho_real, hm
            elif am <= -STEAM_THR and am < hm:
                _side, _odds, _mv = "away", ao_real, am
            if _side:
                _prob = 1.0 / _odds
                r = {"pick": f["home"] if _side == "home" else f["away"],
                     "model_prob": round(_prob, 4),
                     "odds_at_prediction": round(_odds, 2),
                     "source": f["source"],
                     "strategy": f"nova_steam_{_side}__{f['source']}",
                     "confidence": "B",
                     "notes": f"nova steam {_side} move{_mv*100:.0f}% @{_odds:.2f}"}
                r["match_date"] = f.get("date") or target
                r["sport"] = _normalize_sport(f.get("sport", ""))
                r["league"] = f.get("league", "")
                r["home"] = f["home"]; r["away"] = f["away"]
                k = dedupe_key(r)
                if k not in seen:
                    seen.add(k); picks.append(r)

        # مكتبة النسخ الخبيرة المُلحقة يومياً (لا تُحذف، تنمو فقط)
        if ve is not None:
            for ver in library:
                try:
                    r = ve.apply_version(ver, f["home"], f["away"], hp, ap, ho, ao)
                except Exception:
                    r = None
                if not r:
                    continue
                r["match_date"] = f.get("date") or target
                r["sport"] = _normalize_sport(f.get("sport", ""))
                r["league"] = f.get("league", "")
                r["home"] = f["home"]
                r["away"] = f["away"]
                r["strategy"] = f"{ver['name']}__{f['source']}"
                k = dedupe_key(r)
                if k not in seen:
                    seen.add(k)
                    picks.append(r)

    # persist to betting_journal.db
    batch = datetime.utcnow().strftime("%Y-%m-%d_%H")  # جولة التحديث (لتحليل توقيت التوقع)
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
            "confidence, notes, batch) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (datetime.now().isoformat(), p["match_date"], p["sport"], p["league"], p["home"],
             p["away"], p["pick"], p["source"], p["model_prob"], p["odds_at_prediction"],
             0.0, 0.0, p["strategy"], p["confidence"], p["notes"], batch),
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
    print(f"\nGenerated {len(picks)} picks ({recorded} new)." + (f" [skipped {skipped_unsourced} unsourced fixtures]" if skipped_unsourced else ""))
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
