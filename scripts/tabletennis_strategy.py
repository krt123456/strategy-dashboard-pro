#!/usr/bin/env python3
"""Table tennis strategy backtest using BetExplorer results + odds.

Two-way markets (no draw). Builds ELO + form + rest features and
applies strict filters for high accuracy.
"""
from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Dict, List, Tuple, Any, Optional

import pandas as pd
import unicodedata
import yaml


HOME_ADV_ELO = 0.0
BASE_ELO = 1500.0
ELO_K = 22.0
FORM_WINDOW = 6


@dataclass
class TeamForm:
    points: float
    gd: float
    gf: float
    ga: float
    games: int


def _implied_probs(odd_h: float | None, odd_a: float | None) -> Tuple[float | None, float | None]:
    if not odd_h or not odd_a:
        return None, None
    ph = 1.0 / odd_h if odd_h > 0 else None
    pa = 1.0 / odd_a if odd_a > 0 else None
    if ph is None or pa is None:
        return None, None
    total = ph + pa
    if total <= 0:
        return None, None
    return ph / total, pa / total


def _elo_prob(elo_diff: float) -> float:
    return 1.0 / (1.0 + 10 ** (-elo_diff / 400.0))


def _update_elo(elo_home: float, elo_away: float, home_goals: int, away_goals: int) -> Tuple[float, float]:
    diff = elo_home - elo_away
    expected_home = 1.0 / (1.0 + 10 ** (-diff / 400.0))
    score_home = 1.0 if home_goals > away_goals else 0.0
    mov = abs(home_goals - away_goals)
    mov_mult = (2.2 / (abs(diff) * 0.001 + 2.2)) * (1.0 + mov / 6.0)
    k = ELO_K * mov_mult
    new_home = elo_home + k * (score_home - expected_home)
    new_away = elo_away - k * (score_home - expected_home)
    return new_home, new_away


def _form_stats(history: List[Tuple[int, int, int]], window: int = FORM_WINDOW) -> TeamForm:
    recent = history[-window:]
    if not recent:
        return TeamForm(0.0, 0.0, 0.0, 0.0, 0)
    pts = 0.0
    gd = 0.0
    gf = 0.0
    ga = 0.0
    for gf_i, ga_i, outcome in recent:
        gf += gf_i
        ga += ga_i
        gd += gf_i - ga_i
        pts += outcome
    return TeamForm(pts, gd, gf, ga, len(recent))


def _load_league_map(path: Path) -> Dict[str, str]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    out: Dict[str, str] = {}
    for entry in cfg.get("lists", {}).get("betexplorer_tabletennis", []):
        code = str(entry.get("code") or "").strip()
        name = str(entry.get("name") or "").strip()
        if code:
            out[code] = name or code
    return out


def _load_league_rules(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _norm_league(name: str) -> str:
    name = name.strip()
    if not name:
        return ""
    name = unicodedata.normalize("NFKD", name)
    name = "".join(ch for ch in name if not unicodedata.combining(ch))
    name = name.lower()
    name = re.sub(r"\s+", " ", name).strip()
    return name


def _norm_player(name: str) -> str:
    name = name.strip()
    if not name:
        return ""
    name = unicodedata.normalize("NFKD", name)
    name = "".join(ch for ch in name if not unicodedata.combining(ch))
    name = name.lower()
    name = re.sub(r"[^a-z0-9]+", "", name)
    return name


def _load_player_rules(path: Path) -> Dict[str, Any]:
    if not path or not path.exists():
        return {"drop": [], "strict": []}
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {"drop": [], "strict": []}


def _league_thresholds(
    league: str,
    base: Dict[str, float | int],
    rules: Dict[str, Any],
) -> Tuple[Dict[str, float | int], bool]:
    if not rules:
        return base, False

    blacklist = set(_norm_league(str(x)) for x in rules.get("blacklist", []) if x)
    overrides_raw = rules.get("overrides", {}) or {}
    overrides = { _norm_league(str(k)): v for k, v in overrides_raw.items() }
    strict = rules.get("blacklist_strict", {}) or {}
    drop_blacklisted = bool(rules.get("drop_blacklisted", False))

    thresholds = dict(base)
    league_key = _norm_league(league)
    is_blacklisted = league_key in blacklist

    if is_blacklisted:
        for key, value in strict.items():
            if value is None:
                continue
            if key in thresholds:
                thresholds[key] = max(thresholds[key], value)
            else:
                thresholds[key] = value
        if drop_blacklisted:
            thresholds["drop"] = 1
    elif league_key in overrides:
        for key, value in overrides.get(league_key, {}).items():
            if value is None:
                continue
            thresholds[key] = value

    return thresholds, is_blacklisted


def _load_matches(data_dir: Path, league_map: Dict[str, str]) -> pd.DataFrame:
    rows: List[pd.DataFrame] = []
    for path in sorted(data_dir.glob("*.csv")):
        code = path.stem
        try:
            df = pd.read_csv(path)
        except Exception:
            continue
        df["Code"] = code
        if "League" in df.columns and df["League"].notna().any():
            df["League"] = df["League"].astype(str)
        else:
            df["League"] = league_map.get(code, code)
        rows.append(df)
    if not rows:
        return pd.DataFrame()
    df = pd.concat(rows, ignore_index=True)
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    df = df.dropna(subset=["Date", "HomeTeam", "AwayTeam"])
    df["FTHG"] = pd.to_numeric(df["FTHG"], errors="coerce")
    df["FTAG"] = pd.to_numeric(df["FTAG"], errors="coerce")
    if "AvgH" in df.columns:
        df["AvgH"] = pd.to_numeric(df["AvgH"], errors="coerce")
    else:
        df["AvgH"] = None
    if "AvgA" in df.columns:
        df["AvgA"] = pd.to_numeric(df["AvgA"], errors="coerce")
    else:
        df["AvgA"] = None
    if "Season" not in df.columns:
        df["Season"] = df["Date"].dt.year.astype(str)
    return df.sort_values("Date").reset_index(drop=True)


def _actual_result(home_goals: float | None, away_goals: float | None) -> str | None:
    if home_goals is None or away_goals is None:
        return None
    return "H" if home_goals > away_goals else "A"


def build_predictions(
    df: pd.DataFrame,
    *,
    min_games: int = 6,
    min_prob: float = 0.64,
    min_prob_margin: float = 0.12,
    min_edge: float = 0.02,
    max_rest_disadv: int = 2,
    weight_market: float = 0.75,
    min_form_diff: float = 0.0,
    min_gd_diff: float = 0.0,
    player_acc_min: float | None = None,
    player_min_picks: int = 20,
    player_strict_prob_boost: float = 0.05,
    player_strict_margin_boost: float = 0.03,
    league_rules: Dict[str, Any] | None = None,
    player_rules: Dict[str, Any] | None = None,
) -> pd.DataFrame:
    if df.empty:
        return df

    elo: Dict[str, float] = {}
    last_date: Dict[str, date] = {}
    form_hist: Dict[str, List[Tuple[int, int, int]]] = {}
    season_games: Dict[Tuple[str, str], int] = {}
    player_stats: Dict[str, Dict[str, float]] = {}

    rows = []

    base_thresholds = {
        "min_games": min_games,
        "min_prob": min_prob,
        "min_prob_margin": min_prob_margin,
        "min_edge": min_edge,
        "max_rest_disadv": max_rest_disadv,
        "min_form_diff": min_form_diff,
        "min_gd_diff": min_gd_diff,
    }

    # Player-specific rules (drop/strict) from external YAML
    player_rules = player_rules or {}
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
            prob_boost = player_strict_prob_boost
            margin_boost = player_strict_margin_boost
        else:
            name = _norm_player(str(item.get("name", "")))
            league_key = _norm_league(str(item.get("league", "")))
            prob_boost = float(item.get("prob_boost", player_strict_prob_boost) or player_strict_prob_boost)
            margin_boost = float(item.get("margin_boost", player_strict_margin_boost) or player_strict_margin_boost)
        if not name:
            continue
        if league_key:
            strict_by_league.setdefault(league_key, {})[name] = (prob_boost, margin_boost)
        else:
            strict_global[name] = (prob_boost, margin_boost)

    for _, row in df.iterrows():
        match_date = row["Date"].date()
        home = str(row["HomeTeam"]).strip()
        away = str(row["AwayTeam"]).strip()
        league = str(row.get("League", ""))
        league_key = _norm_league(league)
        home_key = _norm_player(home)
        away_key = _norm_player(away)
        season = str(row.get("Season", ""))

        elo_home = elo.get(home, BASE_ELO)
        elo_away = elo.get(away, BASE_ELO)

        rest_home = (match_date - last_date[home]).days if home in last_date else None
        rest_away = (match_date - last_date[away]).days if away in last_date else None
        rest_diff = rest_home - rest_away if rest_home is not None and rest_away is not None else None

        form_home = _form_stats(form_hist.get(home, []))
        form_away = _form_stats(form_hist.get(away, []))
        form_pts_diff = (form_home.points / form_home.games - form_away.points / form_away.games) if form_home.games and form_away.games else None
        gd_diff = (form_home.gd / form_home.games - form_away.gd / form_away.games) if form_home.games and form_away.games else None

        elo_diff = (elo_home + HOME_ADV_ELO) - elo_away
        elo_home_prob = _elo_prob(elo_diff)

        m_ph, m_pa = _implied_probs(row.get("AvgH"), row.get("AvgA"))
        if m_ph is None:
            prob_home = elo_home_prob
            market_probs = (None, None)
        else:
            market_probs = (m_ph, m_pa)
            prob_home = weight_market * m_ph + (1.0 - weight_market) * elo_home_prob

        prob_away = 1.0 - prob_home

        if prob_home >= prob_away:
            pred = "H"
            prob = prob_home
            implied = market_probs[0]
        else:
            pred = "A"
            prob = prob_away
            implied = market_probs[1]

        model_odd_h = round(1.0 / prob_home, 3) if prob_home > 0 else None
        model_odd_a = round(1.0 / prob_away, 3) if prob_away > 0 else None

        prob_margin = abs(prob_home - prob_away)
        edge = None
        if implied is not None:
            edge = prob - implied

        games_home = season_games.get((season, home), 0)
        games_away = season_games.get((season, away), 0)
        thresholds, is_blacklisted = _league_thresholds(league, base_thresholds, league_rules or {})

        force_drop = False
        if home_key in drop_global or away_key in drop_global:
            force_drop = True
        if league_key and league_key in drop_by_league:
            if home_key in drop_by_league[league_key] or away_key in drop_by_league[league_key]:
                force_drop = True
        if thresholds.get("drop"):
            qualifies = False
            actual = _actual_result(row.get("FTHG"), row.get("FTAG"))
            correct = None
            if actual is not None:
                pred = "H" if prob_home >= prob_away else "A"
                correct = int(pred == actual)
            else:
                pred = "H" if prob_home >= prob_away else "A"
            prob = prob_home if pred == "H" else prob_away
            score = round(prob * 100.0, 1)
            rows.append(
                {
                    "Date": row["Date"].date().isoformat(),
                    "League": league,
                    "Code": row.get("Code", ""),
                    "HomeTeam": home,
                    "AwayTeam": away,
                    "Pred": pred,
                    "Prob": round(prob, 4),
                    "ModelProbHome": round(prob_home, 4),
                    "ModelProbAway": round(prob_away, 4),
                    "ModelOddHome": model_odd_h,
                    "ModelOddAway": model_odd_a,
                    "ProbMargin": round(prob_margin, 4),
                    "Edge": round(edge, 4) if edge is not None else None,
                    "RestDiff": rest_diff,
                    "FormPtsDiff": round(form_pts_diff, 3) if form_pts_diff is not None else None,
                    "GDDiff": round(gd_diff, 3) if gd_diff is not None else None,
                    "Score": score,
                    "Actual": actual,
                    "Correct": correct,
                    "Qualifies": int(qualifies),
                    "Blacklisted": int(is_blacklisted),
                }
            )
            continue

        # player-level strictness for historically noisy players
        eff_min_prob = float(thresholds.get("min_prob", min_prob))
        eff_min_margin = float(thresholds.get("min_prob_margin", min_prob_margin))
        extra_prob_boost = 0.0
        extra_margin_boost = 0.0
        # explicit per-player strict rules (global or league-specific)
        for name_key in (home_key, away_key):
            if name_key in strict_global:
                p_boost, m_boost = strict_global[name_key]
                extra_prob_boost = max(extra_prob_boost, p_boost)
                extra_margin_boost = max(extra_margin_boost, m_boost)
            if league_key and league_key in strict_by_league and name_key in strict_by_league[league_key]:
                p_boost, m_boost = strict_by_league[league_key][name_key]
                extra_prob_boost = max(extra_prob_boost, p_boost)
                extra_margin_boost = max(extra_margin_boost, m_boost)
        if player_acc_min is not None:
            strict_player = False
            for name in (home, away):
                stats = player_stats.get(name)
                if not stats:
                    continue
                if stats["picks"] >= player_min_picks:
                    acc = stats["correct"] / max(1.0, stats["picks"])
                    if acc < player_acc_min:
                        strict_player = True
                        break
            if strict_player:
                eff_min_prob = max(eff_min_prob, eff_min_prob + player_strict_prob_boost)
                eff_min_margin = max(eff_min_margin, eff_min_margin + player_strict_margin_boost)

        if extra_prob_boost or extra_margin_boost:
            eff_min_prob = max(eff_min_prob, eff_min_prob + extra_prob_boost)
            eff_min_margin = max(eff_min_margin, eff_min_margin + extra_margin_boost)

        min_games_ok = min(games_home, games_away) >= int(thresholds.get("min_games", min_games))

        rest_ok = True
        if rest_diff is not None:
            max_rest_local = int(thresholds.get("max_rest_disadv", max_rest_disadv))
            if pred == "H" and rest_diff < -max_rest_local:
                rest_ok = False
            if pred == "A" and rest_diff > max_rest_local:
                rest_ok = False

        edge_ok = True
        if edge is not None:
            edge_ok = edge >= float(thresholds.get("min_edge", min_edge))

        form_ok = True
        min_form_local = float(thresholds.get("min_form_diff", min_form_diff))
        if form_pts_diff is not None and min_form_local > 0:
            if pred == "H" and form_pts_diff < min_form_local:
                form_ok = False
            if pred == "A" and form_pts_diff > -min_form_local:
                form_ok = False

        gd_ok = True
        min_gd_local = float(thresholds.get("min_gd_diff", min_gd_diff))
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
            and edge_ok
            and form_ok
            and gd_ok
        )
        if force_drop:
            qualifies = False

        actual = _actual_result(row.get("FTHG"), row.get("FTAG"))
        correct = None
        if actual is not None:
            correct = int(pred == actual)
            for name in (home, away):
                stats = player_stats.setdefault(name, {"picks": 0.0, "correct": 0.0})
                stats["picks"] += 1.0
                stats["correct"] += float(correct)

        score = round(prob * 100.0, 1)

        rows.append(
            {
                "Date": row["Date"].date().isoformat(),
                "League": league,
                "Code": row.get("Code", ""),
                "HomeTeam": home,
                "AwayTeam": away,
                "Pred": pred,
                "Prob": round(prob, 4),
                "ModelProbHome": round(prob_home, 4),
                "ModelProbAway": round(prob_away, 4),
                "ModelOddHome": model_odd_h,
                "ModelOddAway": model_odd_a,
                "ProbMargin": round(prob_margin, 4),
                "Edge": round(edge, 4) if edge is not None else None,
                "RestDiff": rest_diff,
                "FormPtsDiff": round(form_pts_diff, 3) if form_pts_diff is not None else None,
                "GDDiff": round(gd_diff, 3) if gd_diff is not None else None,
                "Score": score,
                "Actual": actual,
                "Correct": correct,
                "Qualifies": int(qualifies),
                "Blacklisted": int(is_blacklisted),
            }
        )

        if actual is not None:
            new_home, new_away = _update_elo(elo_home, elo_away, int(row["FTHG"]), int(row["FTAG"]))
            elo[home] = new_home
            elo[away] = new_away
            last_date[home] = match_date
            last_date[away] = match_date
            season_games[(season, home)] = games_home + 1
            season_games[(season, away)] = games_away + 1

            outcome_home = 1 if actual == "H" else 0
            outcome_away = 1 if actual == "A" else 0
            form_hist.setdefault(home, []).append((int(row["FTHG"]), int(row["FTAG"]), outcome_home))
            form_hist.setdefault(away, []).append((int(row["FTAG"]), int(row["FTHG"]), outcome_away))

    return pd.DataFrame(rows)


def summarize(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    picks = df[df["Qualifies"] == 1].copy()
    if picks.empty:
        return pd.DataFrame()
    summary = (
        picks.groupby("League")
        .agg(Picks=("Qualifies", "size"), Correct=("Correct", "sum"))
        .reset_index()
    )
    summary["Wrong"] = summary["Picks"] - summary["Correct"]
    summary["Accuracy"] = (summary["Correct"] / summary["Picks"]).round(4)
    return summary.sort_values("Accuracy", ascending=False)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default="data/raw/tabletennis_betexplorer")
    ap.add_argument("--league-list", default="data/betexplorer_tabletennis_selected.yaml")
    ap.add_argument("--league-rules", default=None)
    ap.add_argument("--out-picks", default="reports/tabletennis_strategy_picks.csv")
    ap.add_argument("--out-summary", default="reports/tabletennis_strategy_summary.csv")
    ap.add_argument("--min-games", type=int, default=6)
    ap.add_argument("--min-prob", type=float, default=0.64)
    ap.add_argument("--min-prob-margin", type=float, default=0.12)
    ap.add_argument("--min-edge", type=float, default=0.02)
    ap.add_argument("--max-rest-disadv", type=int, default=2)
    ap.add_argument("--weight-market", type=float, default=0.75)
    ap.add_argument("--min-form-diff", type=float, default=0.0)
    ap.add_argument("--min-gd-diff", type=float, default=0.0)
    ap.add_argument("--player-acc-min", type=float, default=None)
    ap.add_argument("--player-min-picks", type=int, default=20)
    ap.add_argument("--player-strict-prob-boost", type=float, default=0.05)
    ap.add_argument("--player-strict-margin-boost", type=float, default=0.03)
    ap.add_argument("--player-rules", default=None)
    ap.add_argument("--start-date", default=None, help="Filter matches on/after YYYY-MM-DD")
    ap.add_argument("--end-date", default=None, help="Filter matches on/before YYYY-MM-DD")
    args = ap.parse_args()

    league_map = _load_league_map(Path(args.league_list))
    df = _load_matches(Path(args.data_dir), league_map)
    if df.empty:
        print("No table tennis data found. Download results first.")
        return 1
    if args.start_date:
        df = df[df["Date"] >= pd.to_datetime(args.start_date)]
    if args.end_date:
        df = df[df["Date"] <= pd.to_datetime(args.end_date)]
    if df.empty:
        print("No table tennis data found in the selected date range.")
        return 1

    picks = build_predictions(
        df,
        min_games=args.min_games,
        min_prob=args.min_prob,
        min_prob_margin=args.min_prob_margin,
        min_edge=args.min_edge,
        max_rest_disadv=args.max_rest_disadv,
        weight_market=args.weight_market,
        min_form_diff=args.min_form_diff,
        min_gd_diff=args.min_gd_diff,
        player_acc_min=args.player_acc_min,
        player_min_picks=args.player_min_picks,
        player_strict_prob_boost=args.player_strict_prob_boost,
        player_strict_margin_boost=args.player_strict_margin_boost,
        league_rules=_load_league_rules(Path(args.league_rules)) if args.league_rules else None,
        player_rules=_load_player_rules(Path(args.player_rules)) if args.player_rules else None,
    )

    out_picks = Path(args.out_picks)
    out_picks.parent.mkdir(parents=True, exist_ok=True)
    picks.to_csv(out_picks, index=False)

    summary = summarize(picks)
    if not summary.empty:
        out_summary = Path(args.out_summary)
        out_summary.parent.mkdir(parents=True, exist_ok=True)
        summary.to_csv(out_summary, index=False)
        print(summary.head(20).to_string(index=False))
    else:
        print("No qualifying picks with current thresholds.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
