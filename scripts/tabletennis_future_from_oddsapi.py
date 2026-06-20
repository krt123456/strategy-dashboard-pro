#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pandas as pd

from tabletennis_strategy import (
    _elo_prob,
    _form_stats,
    _league_thresholds,
    _load_league_map,
    _load_league_rules,
    _load_matches,
    _load_player_rules,
    _norm_league,
    _norm_player,
    _update_elo,
    BASE_ELO,
)


def _load_events(events_dir: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if not events_dir.exists():
        return rows
    for path in sorted(events_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(data, list):
            continue
        rows.extend(data)
    return rows


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return pd.to_datetime(value, errors="coerce").date()
    except Exception:
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--events-dir", default="data/raw/oddsapi_tabletennis_future/events")
    ap.add_argument("--data-dir", default="data/raw/tabletennis_scoretennis")
    ap.add_argument("--league-list", default="data/scoretennis_tabletennis_selected.yaml")
    ap.add_argument("--league-rules", default="data/tabletennis_elite_rules_recent4m.yaml")
    ap.add_argument("--player-rules", default="data/tabletennis_player_rules_recent4m.yaml")
    ap.add_argument("--start-date", required=True)
    ap.add_argument("--end-date", required=True)
    ap.add_argument("--odds-dir", default="data/raw/oddsapi_tabletennis_future/odds")
    ap.add_argument("--min-games", type=int, default=1)
    ap.add_argument("--min-prob", type=float, default=0.6)
    ap.add_argument("--min-prob-margin", type=float, default=0.05)
    ap.add_argument("--max-rest-disadv", type=int, default=4)
    ap.add_argument("--min-form-diff", type=float, default=0.0)
    ap.add_argument("--min-gd-diff", type=float, default=0.0)
    ap.add_argument("--out", default="reports/tabletennis_future_picks.csv")
    args = ap.parse_args()

    start = pd.to_datetime(args.start_date, errors="coerce").date()
    end = pd.to_datetime(args.end_date, errors="coerce").date()

    league_map = _load_league_map(Path(args.league_list))
    df = _load_matches(Path(args.data_dir), league_map)
    if df.empty:
        print("No historical table tennis data found.")
        return 1

    rules = _load_league_rules(Path(args.league_rules))
    player_rules = _load_player_rules(Path(args.player_rules))

    # Build player states from history.
    elo: Dict[str, float] = {}
    last_date: Dict[str, date] = {}
    form_hist: Dict[str, List[Tuple[int, int, int]]] = {}
    games_total: Dict[str, int] = {}

    for _, row in df.iterrows():
        match_date = row["Date"].date()
        home = str(row["HomeTeam"]).strip()
        away = str(row["AwayTeam"]).strip()
        home_elo = elo.get(home, BASE_ELO)
        away_elo = elo.get(away, BASE_ELO)
        if row.get("FTHG") is None or row.get("FTAG") is None:
            continue
        try:
            hg = int(row["FTHG"])
            ag = int(row["FTAG"])
        except Exception:
            continue
        new_home, new_away = _update_elo(home_elo, away_elo, hg, ag)
        elo[home] = new_home
        elo[away] = new_away
        last_date[home] = match_date
        last_date[away] = match_date
        games_total[home] = games_total.get(home, 0) + 1
        games_total[away] = games_total.get(away, 0) + 1
        outcome_home = 1 if hg > ag else 0
        outcome_away = 1 if ag > hg else 0
        form_hist.setdefault(home, []).append((hg, ag, outcome_home))
        form_hist.setdefault(away, []).append((ag, hg, outcome_away))

    # Player rules
    drop_global: set[str] = set()
    drop_by_league: Dict[str, set[str]] = {}
    strict_global: Dict[str, Tuple[float, float]] = {}
    strict_by_league: Dict[str, Dict[str, Tuple[float, float]]] = {}
    for item in player_rules.get("drop", []) or []:
        if isinstance(item, str):
            name = _norm_player(item)
            league_key = ""
        else:
            name = _norm_player(str(item.get("name", "")))
            league_key = _norm_league(str(item.get("league", "")))
        if not name:
            continue
        if league_key:
            drop_by_league.setdefault(league_key, set()).add(name)
        else:
            drop_global.add(name)
    for item in player_rules.get("strict", []) or []:
        if isinstance(item, str):
            name = _norm_player(item)
            league_key = ""
            prob_boost = 0.05
            margin_boost = 0.03
        else:
            name = _norm_player(str(item.get("name", "")))
            league_key = _norm_league(str(item.get("league", "")))
            prob_boost = float(item.get("prob_boost", 0.05) or 0.05)
            margin_boost = float(item.get("margin_boost", 0.03) or 0.03)
        if not name:
            continue
        if league_key:
            strict_by_league.setdefault(league_key, {})[name] = (prob_boost, margin_boost)
        else:
            strict_global[name] = (prob_boost, margin_boost)

    base_thresholds = {
        "min_games": args.min_games,
        "min_prob": args.min_prob,
        "min_prob_margin": args.min_prob_margin,
        "max_rest_disadv": args.max_rest_disadv,
        "min_form_diff": args.min_form_diff,
        "min_gd_diff": args.min_gd_diff,
    }

    events = _load_events(Path(args.events_dir))
    odds_dir = Path(args.odds_dir)

    def _extract_ml(odds_payload: Dict[str, Any]) -> Dict[str, float] | None:
        bookmakers = odds_payload.get("bookmakers", {})
        if not bookmakers:
            return None
        for book_name, markets in bookmakers.items():
            for market in markets:
                if market.get("name") != "ML":
                    continue
                odds_list = market.get("odds", [])
                if not odds_list:
                    continue
                ml = odds_list[0]
                try:
                    home = float(ml.get("home"))
                    away = float(ml.get("away"))
                except Exception:
                    return None
                return {"home": home, "away": away}
        return None

    def _implied_probs(home: float, away: float) -> tuple[float, float]:
        ph = 1.0 / home if home > 0 else 0.0
        pa = 1.0 / away if away > 0 else 0.0
        total = ph + pa
        if total <= 0:
            return 0.0, 0.0
        return ph / total, pa / total

    odds_probs: Dict[int, Tuple[float, float]] = {}
    if odds_dir.exists():
        for path in odds_dir.glob("*.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            event_id = payload.get("id")
            if event_id is None:
                continue
            ml = _extract_ml(payload)
            if not ml:
                continue
            home_p, away_p = _implied_probs(ml["home"], ml["away"])
            odds_probs[int(event_id)] = (home_p, away_p)
    rows: List[Dict[str, Any]] = []
    seen_events: set[int] = set()
    for event in events:
        if str(event.get("status")).lower() != "pending":
            continue
        event_date = _parse_date(event.get("date"))
        if event_date is None:
            continue
        if event_date < start or event_date > end:
            continue
        event_id = event.get("id")
        if event_id is not None:
            if int(event_id) in seen_events:
                continue
            seen_events.add(int(event_id))
        league = (event.get("league") or {}).get("name") or ""
        if not league:
            continue
        home = str(event.get("home") or "").strip()
        away = str(event.get("away") or "").strip()
        if not home or not away:
            continue

        league_key = _norm_league(league)
        home_key = _norm_player(home)
        away_key = _norm_player(away)

        thresholds, is_blacklisted = _league_thresholds(league, base_thresholds, rules)
        if thresholds.get("drop"):
            # For future-only odds-based picks, allow blacklisted leagues but tighten thresholds modestly.
            thresholds["min_prob"] = max(float(thresholds.get("min_prob", args.min_prob)), 0.62)
            thresholds["min_prob_margin"] = max(float(thresholds.get("min_prob_margin", args.min_prob_margin)), 0.07)

        if home_key in drop_global or away_key in drop_global:
            continue
        if league_key and league_key in drop_by_league:
            if home_key in drop_by_league[league_key] or away_key in drop_by_league[league_key]:
                continue

        prob_home = None
        prob_away = None
        event_id = event.get("id")
        if event_id is not None and int(event_id) in odds_probs:
            prob_home, prob_away = odds_probs[int(event_id)]
        if prob_home is None or prob_away is None:
            elo_home = elo.get(home, BASE_ELO)
            elo_away = elo.get(away, BASE_ELO)
            prob_home = _elo_prob(elo_home - elo_away)
            prob_away = 1.0 - prob_home
        pred = "H" if prob_home >= prob_away else "A"
        prob = prob_home if pred == "H" else prob_away
        prob_margin = abs(prob_home - prob_away)

        # Rest and form
        rest_diff = None
        if home in last_date and away in last_date:
            rest_diff = (event_date - last_date[home]).days - (event_date - last_date[away]).days
        form_home = _form_stats(form_hist.get(home, []))
        form_away = _form_stats(form_hist.get(away, []))
        form_pts_diff = None
        gd_diff = None
        if form_home.games and form_away.games:
            form_pts_diff = (form_home.points / form_home.games) - (form_away.points / form_away.games)
            gd_diff = (form_home.gd / form_home.games) - (form_away.gd / form_away.games)

        # Player strict boosts
        eff_min_prob = float(thresholds.get("min_prob", args.min_prob))
        eff_min_margin = float(thresholds.get("min_prob_margin", args.min_prob_margin))
        extra_prob_boost = 0.0
        extra_margin_boost = 0.0
        for name_key in (home_key, away_key):
            if name_key in strict_global:
                p_boost, m_boost = strict_global[name_key]
                extra_prob_boost = max(extra_prob_boost, p_boost)
                extra_margin_boost = max(extra_margin_boost, m_boost)
            if league_key and league_key in strict_by_league and name_key in strict_by_league[league_key]:
                p_boost, m_boost = strict_by_league[league_key][name_key]
                extra_prob_boost = max(extra_prob_boost, p_boost)
                extra_margin_boost = max(extra_margin_boost, m_boost)
        if extra_prob_boost or extra_margin_boost:
            eff_min_prob = max(eff_min_prob, eff_min_prob + extra_prob_boost)
            eff_min_margin = max(eff_min_margin, eff_min_margin + extra_margin_boost)

        min_games_required = int(thresholds.get("min_games", 0))
        if event_id is not None and int(event_id) in odds_probs:
            min_games_required = 0
        min_games_ok = min(games_total.get(home, 0), games_total.get(away, 0)) >= min_games_required
        rest_ok = True
        if rest_diff is not None:
            max_rest_local = int(thresholds.get("max_rest_disadv", args.max_rest_disadv))
            if pred == "H" and rest_diff < -max_rest_local:
                rest_ok = False
            if pred == "A" and rest_diff > max_rest_local:
                rest_ok = False
        form_ok = True
        min_form_local = float(thresholds.get("min_form_diff", 0.0))
        if form_pts_diff is not None and min_form_local > 0:
            if pred == "H" and form_pts_diff < min_form_local:
                form_ok = False
            if pred == "A" and form_pts_diff > -min_form_local:
                form_ok = False
        gd_ok = True
        min_gd_local = float(thresholds.get("min_gd_diff", 0.0))
        if gd_diff is not None and min_gd_local > 0:
            if pred == "H" and gd_diff < min_gd_local:
                gd_ok = False
            if pred == "A" and gd_diff > -min_gd_local:
                gd_ok = False

        qualifies = (
            min_games_ok
            and prob >= eff_min_prob
            and prob_margin >= eff_min_margin
            and rest_ok
            and form_ok
            and gd_ok
        )

        if not qualifies:
            continue

        pred_team = home if pred == "H" else away
        rows.append(
            {
                "Date": event_date.isoformat(),
                "League": league,
                "Home": home,
                "Away": away,
                "Pred": pred_team,
                "Prob": round(prob, 4),
                "Margin": round(prob_margin, 4),
                "Result": "pending",
            }
        )

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_df = pd.DataFrame(rows)
    if not out_df.empty and {"Date", "League", "Home", "Away", "Pred"}.issubset(out_df.columns):
        out_df["Date"] = pd.to_datetime(out_df["Date"], errors="coerce").dt.date
        out_df = out_df.drop_duplicates(subset=["Date", "League", "Home", "Away", "Pred"])
    out_df.to_csv(out_path, index=False)
    print(f"Wrote {len(out_df)} picks to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
