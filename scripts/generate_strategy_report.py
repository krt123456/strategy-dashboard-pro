#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from app import engine


def _safe_float(value: Any) -> float | None:
    try:
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return None
        return float(value)
    except Exception:
        return None


def _load_json(path: Path) -> Dict[str, Any]:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return {}


def _football_rows(base_dir: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    path = base_dir / "reports" / "current_season_per_league_primary_base96.csv"
    if not path.exists():
        return rows
    df = pd.read_csv(path)
    for _, r in df.iterrows():
        league = str(r.get("League") or "")
        if not league:
            continue
        params = {
            "primary_picks": _safe_float(r.get("Primary")),
            "primary_correct": _safe_float(r.get("Correct")),
            "primary_wrong": _safe_float(r.get("Wrong")),
            "primary_acc": _safe_float(r.get("Acc")),
            "base96_picks": _safe_float(r.get("Base96")),
            "base96_correct": _safe_float(r.get("BaseCorrect")),
            "base96_wrong": _safe_float(r.get("BaseWrong")),
            "base96_acc": _safe_float(r.get("BaseAcc")),
            "code": r.get("Code"),
        }
        rows.append(
            {
                "Sport": "football",
                "League": league,
                "Strategy": "primary/base96",
                "Picks": params.get("primary_picks"),
                "Accuracy": params.get("primary_acc"),
                "Coverage": None,
                "Params": json.dumps(params, ensure_ascii=False),
                "TeamNotes": "",
                "Source": str(path),
            }
        )
    return rows


def _basketball_rows(base_dir: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    backtest = engine.BASKETBALL_BACKTEST
    if not backtest.exists():
        return rows
    df_all = pd.read_csv(backtest, low_memory=False)
    if df_all.empty or "GAME_DATE_EST" not in df_all.columns:
        return rows
    df_all["GAME_DATE_EST"] = pd.to_datetime(df_all["GAME_DATE_EST"], errors="coerce").dt.date
    end = df_all["GAME_DATE_EST"].max() or date.today()
    relaxed_map = engine._build_basketball_relaxed_strict_map(df_all, end)
    margin_floor_map = engine._build_basketball_margin_floor_map(df_all, end)
    team_err_map = engine._build_basketball_team_error_by_league(df_all, end)
    meta_static = engine._load_basketball_meta_relation()
    meta_dynamic = engine._build_basketball_dynamic_meta(df_all, end)

    leagues = sorted(df_all["league"].dropna().unique())
    for league in leagues:
        league_key = str(league)
        team_info = team_err_map.get(league_key, {})
        threshold = team_info.get("threshold")
        teams = team_info.get("teams") or {}
        risky = [t for t, s in teams.items() if s.get("error_rate", 0) > (threshold or 0)]
        meta_cfg = engine._merge_basketball_meta_configs(
            meta_static.get(league_key) if meta_static else None,
            meta_dynamic.get(league_key) if meta_dynamic else None,
        )
        params = {
            "relaxed_strict": relaxed_map.get(league_key),
            "margin_floor": margin_floor_map.get(league_key),
            "team_risk_threshold": threshold,
            "team_risk_extra_margin": engine.BASKETBALL_TEAM_ERR_EXTRA_MARGIN,
            "team_risk_min_games": engine.BASKETBALL_TEAM_ERR_MIN_GAMES,
            "zero_wrong_days": engine.BASKETBALL_RECENT_ZERO_DAYS,
            "meta_config": meta_cfg,
        }
        rows.append(
            {
                "Sport": "basketball",
                "League": league_key,
                "Strategy": "relaxed_strict+margin+team_risk",
                "Picks": None,
                "Accuracy": None,
                "Coverage": None,
                "Params": json.dumps(params, ensure_ascii=False),
                "TeamNotes": "; ".join(sorted(risky)),
                "Source": str(backtest),
            }
        )
    return rows


def _hockey_rows(base_dir: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    best_path = None
    for cand in engine.HOCKEY_BEST_CANDIDATES:
        if cand.exists():
            best_path = cand
            break
    if best_path is None:
        for cand in engine.HOCKEY_BEST_REG_CANDIDATES:
            if cand.exists():
                best_path = cand
                break
    if best_path is None:
        return rows
    payload = _load_json(best_path)
    leagues = payload.get("leagues") or {}
    blacklist_payload = _load_json(engine.HOCKEY_TEAM_BLACKLIST)
    blacklist_leagues = (blacklist_payload.get("leagues") or {}) if isinstance(blacklist_payload, dict) else {}
    for league_id, info in leagues.items():
        params = {
            "market": info.get("market"),
            "strategy": info.get("strategy"),
            "threshold": info.get("threshold"),
            "params": info.get("params"),
        }
        team_notes = ""
        bl = blacklist_leagues.get(str(league_id))
        if isinstance(bl, dict):
            team_notes = "; ".join(sorted(bl.get("blacklist") or []))
        rows.append(
            {
                "Sport": "hockey",
                "League": str(info.get("league_id") or league_id),
                "Strategy": str(info.get("strategy") or ""),
                "Picks": info.get("picks"),
                "Accuracy": info.get("accuracy"),
                "Coverage": None,
                "Params": json.dumps(params, ensure_ascii=False),
                "TeamNotes": team_notes,
                "Source": str(best_path),
            }
        )
    return rows


def _tennis_rows(base_dir: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    path = engine.TENNIS_FILTERS_SGODDS if engine.TENNIS_FILTERS_SGODDS.exists() else engine.TENNIS_FILTERS_SPORTRADAR
    if not path.exists():
        return rows
    df = pd.read_csv(path, low_memory=False)
    if df.empty:
        return rows
    df = df[df.get("picks", 0).fillna(0) > 0]
    for _, r in df.iterrows():
        league = str(r.get("tournament") or "")
        if not league:
            continue
        params = {
            "min_elo_edge": _safe_float(r.get("min_elo_edge")),
            "min_recent_edge": _safe_float(r.get("min_recent_edge")),
            "max_rest_disadv": _safe_float(r.get("max_rest_disadv")),
            "min_games_edge": _safe_float(r.get("min_games_edge")),
            "match_mode": r.get("match_mode"),
            "surface": r.get("surface"),
            "event_type": r.get("event_type"),
            "is_qualifying": r.get("is_qualifying"),
            "is_regular": r.get("is_regular"),
        }
        rows.append(
            {
                "Sport": "tennis",
                "League": league,
                "Strategy": str(r.get("status") or "filter"),
                "Picks": _safe_float(r.get("picks")),
                "Accuracy": _safe_float(r.get("accuracy")),
                "Coverage": _safe_float(r.get("coverage")),
                "Params": json.dumps(params, ensure_ascii=False),
                "TeamNotes": "",
                "Source": str(path),
            }
        )
    return rows


def main() -> None:
    base_dir = Path(__file__).resolve().parent.parent
    rows: List[Dict[str, Any]] = []
    rows.extend(_football_rows(base_dir))
    rows.extend(_basketball_rows(base_dir))
    rows.extend(_hockey_rows(base_dir))
    rows.extend(_tennis_rows(base_dir))

    out = pd.DataFrame(rows)
    out = out.sort_values(["Sport", "League"], kind="stable") if not out.empty else out
    out_path = base_dir / "reports" / "strategy_master_report.csv"
    out.to_csv(out_path, index=False)
    print(f"saved {out_path} (rows: {len(out)})")


if __name__ == "__main__":
    main()
