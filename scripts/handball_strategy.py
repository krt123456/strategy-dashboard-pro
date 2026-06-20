#!/usr/bin/env python3
"""Handball strategy backtest using BetExplorer results + 1X2 odds.

Builds pre-match features (ELO, form, rest, goal diff) and applies
strict filters to maximize accuracy.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Dict, List, Tuple

import pandas as pd
import yaml


HOME_ADV_ELO = 60.0
BASE_ELO = 1500.0
ELO_K = 20.0
FORM_WINDOW = 5
REST_SEASON_GAP_DAYS = 45


@dataclass
class TeamForm:
    points: float
    gd: float
    gf: float
    ga: float
    games: int


def _implied_probs(odd_h: float | None, odd_d: float | None, odd_a: float | None) -> Tuple[float | None, float | None, float | None]:
    if not odd_h or not odd_d or not odd_a:
        return None, None, None
    ph = 1.0 / odd_h if odd_h > 0 else None
    pd_ = 1.0 / odd_d if odd_d > 0 else None
    pa = 1.0 / odd_a if odd_a > 0 else None
    if ph is None or pd_ is None or pa is None:
        return None, None, None
    total = ph + pd_ + pa
    if total <= 0:
        return None, None, None
    return ph / total, pd_ / total, pa / total


def _elo_prob(elo_diff: float, draw_base: float) -> Tuple[float, float, float]:
    p_home_raw = 1.0 / (1.0 + 10 ** (-elo_diff / 400.0))
    p_away_raw = 1.0 - p_home_raw
    p_draw = max(0.05, min(0.35, draw_base - abs(elo_diff) / 2000.0))
    p_home = p_home_raw * (1.0 - p_draw)
    p_away = p_away_raw * (1.0 - p_draw)
    return p_home, p_draw, p_away


def _update_elo(elo_home: float, elo_away: float, home_goals: int, away_goals: int) -> Tuple[float, float]:
    diff = elo_home - elo_away
    expected_home = 1.0 / (1.0 + 10 ** (-diff / 400.0))
    score_home = 0.5
    if home_goals > away_goals:
        score_home = 1.0
    elif home_goals < away_goals:
        score_home = 0.0
    mov = abs(home_goals - away_goals)
    mov_mult = (2.2 / (abs(diff) * 0.001 + 2.2)) * (1.0 + mov / 25.0)
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
    for entry in cfg.get("lists", {}).get("betexplorer_handball", []):
        code = str(entry.get("code") or "").strip()
        name = str(entry.get("name") or "").strip()
        if code:
            out[code] = name or code
    return out


def _load_matches(data_dir: Path, league_map: Dict[str, str]) -> pd.DataFrame:
    rows: List[pd.DataFrame] = []
    for path in sorted(data_dir.glob("*.csv")):
        code = path.stem
        try:
            df = pd.read_csv(path)
        except Exception:
            continue
        df["Code"] = code
        df["League"] = league_map.get(code, code)
        rows.append(df)
    if not rows:
        return pd.DataFrame()
    df = pd.concat(rows, ignore_index=True)
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    df = df.dropna(subset=["Date", "HomeTeam", "AwayTeam"])
    df["FTHG"] = pd.to_numeric(df["FTHG"], errors="coerce")
    df["FTAG"] = pd.to_numeric(df["FTAG"], errors="coerce")
    df["AvgH"] = pd.to_numeric(df["AvgH"], errors="coerce")
    df["AvgD"] = pd.to_numeric(df["AvgD"], errors="coerce")
    df["AvgA"] = pd.to_numeric(df["AvgA"], errors="coerce")
    return df.sort_values("Date").reset_index(drop=True)


def _actual_result(home_goals: float | None, away_goals: float | None) -> str | None:
    if home_goals is None or away_goals is None:
        return None
    if home_goals > away_goals:
        return "H"
    if home_goals < away_goals:
        return "A"
    return "D"


def build_predictions(
    df: pd.DataFrame,
    *,
    min_games: int = 3,
    min_prob: float = 0.58,
    min_prob_margin: float = 0.08,
    min_edge: float = 0.01,
    max_rest_disadv: int = 3,
    weight_market: float = 0.7,
    include_draws: bool = False,
    require_odds: bool = True,
) -> pd.DataFrame:
    if df.empty:
        return df

    elo: Dict[Tuple[str, str, str], float] = {}
    last_date: Dict[Tuple[str, str, str], date] = {}
    form_hist: Dict[Tuple[str, str, str], List[Tuple[int, int, int]]] = {}
    season_games: Dict[Tuple[str, str, str], int] = {}
    league_draw_hist: Dict[Tuple[str, str], List[int]] = {}

    rows = []

    for _, row in df.iterrows():
        match_date = row["Date"].date()
        home = str(row["HomeTeam"]).strip()
        away = str(row["AwayTeam"]).strip()
        league = str(row.get("League", ""))
        season = str(row.get("Season", ""))

        team_home_key = (league, season, home)
        team_away_key = (league, season, away)

        elo_home = elo.get(team_home_key, BASE_ELO)
        elo_away = elo.get(team_away_key, BASE_ELO)

        rest_home = (match_date - last_date[team_home_key]).days if team_home_key in last_date else None
        rest_away = (match_date - last_date[team_away_key]).days if team_away_key in last_date else None
        if rest_home is not None and rest_home > REST_SEASON_GAP_DAYS:
            rest_home = None
        if rest_away is not None and rest_away > REST_SEASON_GAP_DAYS:
            rest_away = None
        rest_diff = rest_home - rest_away if rest_home is not None and rest_away is not None else None

        form_home = _form_stats(form_hist.get(team_home_key, []))
        form_away = _form_stats(form_hist.get(team_away_key, []))
        form_pts_diff = (form_home.points / form_home.games - form_away.points / form_away.games) if form_home.games and form_away.games else None
        gd_diff = (form_home.gd / form_home.games - form_away.gd / form_away.games) if form_home.games and form_away.games else None

        league_key = (league, season)
        league_draws = league_draw_hist.get(league_key, [])
        draw_base = sum(league_draws) / len(league_draws) if len(league_draws) >= 20 else 0.2

        elo_diff = (elo_home + HOME_ADV_ELO) - elo_away
        elo_probs = _elo_prob(elo_diff, draw_base)

        m_ph, m_pd, m_pa = _implied_probs(row.get("AvgH"), row.get("AvgD"), row.get("AvgA"))
        market_probs = (m_ph, m_pd, m_pa) if m_ph is not None else (None, None, None)
        if m_ph is None:
            blend_probs = elo_probs
        else:
            blend_probs = tuple(weight_market * m + (1.0 - weight_market) * e for m, e in zip(market_probs, elo_probs))

        labels = ["H", "D", "A"]
        best_idx = int(max(range(3), key=lambda i: blend_probs[i]))
        sorted_probs = sorted(blend_probs, reverse=True)
        prob = blend_probs[best_idx]
        prob_margin = prob - sorted_probs[1]
        pred = labels[best_idx]

        edge = None
        if market_probs[best_idx] is not None:
            edge = prob - market_probs[best_idx]

        games_home = season_games.get((league, season, home), 0)
        games_away = season_games.get((league, season, away), 0)
        min_games_ok = min(games_home, games_away) >= min_games

        rest_ok = True
        if rest_diff is not None and pred != "D":
            if pred == "H" and rest_diff < -max_rest_disadv:
                rest_ok = False
            if pred == "A" and rest_diff > max_rest_disadv:
                rest_ok = False

        edge_ok = True
        if edge is not None and pred != "D":
            edge_ok = edge >= min_edge

        draw_ok = include_draws or pred != "D"
        odds_ok = (m_ph is not None) if require_odds else True

        qualifies = (
            min_games_ok
            and draw_ok
            and prob >= min_prob
            and prob_margin >= min_prob_margin
            and rest_ok
            and edge_ok
            and odds_ok
        )

        actual = _actual_result(row.get("FTHG"), row.get("FTAG"))
        correct = None
        if actual is not None:
            correct = int(pred == actual)

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
                "ProbMargin": round(prob_margin, 4),
                "Edge": round(edge, 4) if edge is not None else None,
                "RestDiff": rest_diff,
                "FormPtsDiff": round(form_pts_diff, 3) if form_pts_diff is not None else None,
                "GDDiff": round(gd_diff, 3) if gd_diff is not None else None,
                "Score": score,
                "Actual": actual,
                "Correct": correct,
                "Qualifies": int(qualifies),
            }
        )

        # update state after processing
        if actual is not None:
            new_home, new_away = _update_elo(elo_home, elo_away, int(row["FTHG"]), int(row["FTAG"]))
            elo[(league, season, home)] = new_home
            elo[(league, season, away)] = new_away
            last_date[(league, season, home)] = match_date
            last_date[(league, season, away)] = match_date
            season_games[(league, season, home)] = games_home + 1
            season_games[(league, season, away)] = games_away + 1

            outcome_home = 1 if actual == "H" else 0 if actual == "A" else 0.5
            outcome_away = 1 if actual == "A" else 0 if actual == "H" else 0.5
            form_hist.setdefault((league, season, home), []).append((int(row["FTHG"]), int(row["FTAG"]), outcome_home))
            form_hist.setdefault((league, season, away), []).append((int(row["FTAG"]), int(row["FTHG"]), outcome_away))
            league_draw_hist.setdefault(league_key, []).append(1 if actual == "D" else 0)

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
    ap.add_argument("--data-dir", default="data/raw/handball_betexplorer")
    ap.add_argument("--league-list", default="data/betexplorer_handball_selected.yaml")
    ap.add_argument("--out-picks", default="reports/handball_strategy_picks.csv")
    ap.add_argument("--out-summary", default="reports/handball_strategy_summary.csv")
    ap.add_argument("--min-games", type=int, default=3)
    ap.add_argument("--min-prob", type=float, default=0.58)
    ap.add_argument("--min-prob-margin", type=float, default=0.08)
    ap.add_argument("--min-edge", type=float, default=0.01)
    ap.add_argument("--max-rest-disadv", type=int, default=3)
    ap.add_argument("--weight-market", type=float, default=0.7)
    ap.add_argument("--include-draws", action="store_true")
    ap.add_argument("--allow-missing-odds", action="store_true")
    args = ap.parse_args()

    league_map = _load_league_map(Path(args.league_list))
    df = _load_matches(Path(args.data_dir), league_map)
    if df.empty:
        print("No handball data found. Download results first.")
        return 1

    picks = build_predictions(
        df,
        min_games=args.min_games,
        min_prob=args.min_prob,
        min_prob_margin=args.min_prob_margin,
        min_edge=args.min_edge,
        max_rest_disadv=args.max_rest_disadv,
        weight_market=args.weight_market,
        include_draws=args.include_draws,
        require_odds=not args.allow_missing_odds,
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
