#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from statistics import mean, median, pstdev
from typing import Any, Dict, Iterable, List, Optional, Tuple


def _safe_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> Optional[int]:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _load_matches(matches_dir: Path) -> Dict[int, Dict[str, Any]]:
    out: Dict[int, Dict[str, Any]] = {}
    for path in sorted(matches_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(data, list):
            continue
        for item in data:
            match_id = item.get("id")
            if match_id is None:
                continue
            out[int(match_id)] = item
    return out


def _extract_scores(score_obj: Any) -> Optional[int]:
    if not isinstance(score_obj, dict):
        return None
    current = _safe_int(score_obj.get("current"))
    if current is not None:
        return current
    display = score_obj.get("display")
    return _safe_int(display)


def _extract_full_time_odds(odds_payload: Any) -> List[Dict[str, Any]]:
    data = odds_payload.get("data", odds_payload)
    if not isinstance(data, list):
        return []
    rows: List[Dict[str, Any]] = []
    for entry in data:
        periods = entry.get("periods") or []
        for period in periods:
            if str(period.get("period_type")).lower() != "full time":
                continue
            for odd in period.get("odds", []):
                row = {
                    "home": _safe_float(odd.get("home")),
                    "away": _safe_float(odd.get("away")),
                    "home_movement": _safe_float(odd.get("home_movement")),
                    "away_movement": _safe_float(odd.get("away_movement")),
                    "payout": _safe_float(odd.get("payout")),
                    "bookmaker": odd.get("bookmaker_name"),
                }
                if row["home"] is not None and row["away"] is not None:
                    rows.append(row)
    return rows


def _calc_implied_prob(home: float, away: float) -> Tuple[float, float, float]:
    ph = 1.0 / home if home > 0 else 0.0
    pa = 1.0 / away if away > 0 else 0.0
    total = ph + pa
    if total <= 0:
        return 0.0, 0.0, 0.0
    return ph / total, pa / total, total


def _aggregate_odds(odds_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not odds_rows:
        return {}
    homes = [r["home"] for r in odds_rows if r["home"] is not None]
    aways = [r["away"] for r in odds_rows if r["away"] is not None]
    if not homes or not aways:
        return {}

    implied_probs = []
    overrounds = []
    for r in odds_rows:
        ph, pa, total = _calc_implied_prob(r["home"], r["away"])
        implied_probs.append((ph, pa))
        overrounds.append(total)

    home_probs = [p[0] for p in implied_probs]
    away_probs = [p[1] for p in implied_probs]

    def _stat(values: List[float]) -> Dict[str, float]:
        if not values:
            return {"avg": 0.0, "med": 0.0, "std": 0.0, "min": 0.0, "max": 0.0}
        return {
            "avg": mean(values),
            "med": median(values),
            "std": pstdev(values) if len(values) > 1 else 0.0,
            "min": min(values),
            "max": max(values),
        }

    home_stats = _stat(homes)
    away_stats = _stat(aways)
    prob_home_stats = _stat(home_probs)
    prob_away_stats = _stat(away_probs)

    payout_vals = [r["payout"] for r in odds_rows if r.get("payout") is not None]
    move_home = [abs(r["home_movement"]) for r in odds_rows if r.get("home_movement") is not None]
    move_away = [abs(r["away_movement"]) for r in odds_rows if r.get("away_movement") is not None]

    return {
        "bookmaker_count": len(odds_rows),
        "avg_home_odds": round(home_stats["avg"], 4),
        "avg_away_odds": round(away_stats["avg"], 4),
        "med_home_odds": round(home_stats["med"], 4),
        "med_away_odds": round(away_stats["med"], 4),
        "best_home_odds": round(home_stats["max"], 4),
        "best_away_odds": round(away_stats["max"], 4),
        "std_home_odds": round(home_stats["std"], 4),
        "std_away_odds": round(away_stats["std"], 4),
        "consensus_home_prob": round(prob_home_stats["avg"], 4),
        "consensus_away_prob": round(prob_away_stats["avg"], 4),
        "prob_dispersion": round(max(prob_home_stats["std"], prob_away_stats["std"]), 4),
        "overround_avg": round(mean(overrounds), 4) if overrounds else 0.0,
        "avg_payout": round(mean(payout_vals), 4) if payout_vals else None,
        "avg_move_abs": round(mean(move_home + move_away), 4) if (move_home or move_away) else None,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--matches-dir", default="data/raw/sportdevs_tabletennis/matches")
    ap.add_argument("--odds-dir", default="data/raw/sportdevs_tabletennis/odds_full_time")
    ap.add_argument("--out", default="data/processed/sportdevs_tabletennis_dataset.csv")
    args = ap.parse_args()

    matches = _load_matches(Path(args.matches_dir))
    odds_dir = Path(args.odds_dir)
    rows: List[Dict[str, Any]] = []

    for odds_path in sorted(odds_dir.glob("*.json")):
        match_id = _safe_int(odds_path.stem)
        if match_id is None or match_id not in matches:
            continue
        try:
            odds_payload = json.loads(odds_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        odds_rows = _extract_full_time_odds(odds_payload)
        agg = _aggregate_odds(odds_rows)
        if not agg:
            continue

        match = matches[match_id]
        home = match.get("home_team_name")
        away = match.get("away_team_name")
        league = match.get("league_name")
        start_time = match.get("start_time")
        status = match.get("status_type")

        home_score = _extract_scores(match.get("home_team_score"))
        away_score = _extract_scores(match.get("away_team_score"))

        actual = None
        if home_score is not None and away_score is not None:
            actual = "H" if home_score > away_score else "A"

        rows.append(
            {
                "match_id": match_id,
                "start_time": start_time,
                "league": league,
                "home": home,
                "away": away,
                "status": status,
                "home_score": home_score,
                "away_score": away_score,
                "actual": actual,
                **agg,
            }
        )

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    import pandas as pd

    df = pd.DataFrame(rows)
    df.to_csv(out_path, index=False)
    print(f"Wrote {len(df)} rows to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
