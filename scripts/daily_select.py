#!/usr/bin/env python3
"""Select daily matches across leagues using learned per-league strategies."""
from __future__ import annotations

import argparse
import ast
from datetime import date as dt_date
from pathlib import Path
from typing import Dict, Any, List

import pandas as pd

from run_all_european_enhanced import (
    normalize_main,
    normalize_extra,
    season_code_to_str,
    pick_odds_cols,
    build_pre_match_features,
)


def parse_params(raw) -> Dict[str, Any] | None:
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return None
    if isinstance(raw, dict):
        return raw
    try:
        return ast.literal_eval(str(raw))
    except Exception:
        return None


def get_val(row, col):
    v = row.get(col)
    if v is None or pd.isna(v):
        return None
    return float(v)


ADV_THRESHOLDS = {
    "AdvShots": 6.6,
    "AdvSOT": 3.2,
    "AdvGD": 2.2,
    "AdvFormPts": 1.6,
}

PRIMARY_FORM_PTS_MIN = -1.2
PRIMARY_FORM_PTS_MIN_MID = -3.2
PRIMARY_FORM_PTS_MIN_TIGHT = -0.6
PRIMARY_GD_MAX = 1.9
PRIMARY_ABS_REST_DIFF_MIN = 0.5
PRIMARY_ABS_REST_DIFF_MAX = 5.0
PRIMARY_PROB_MARGIN_MIN = 0.30
PRIMARY_PROB_MARGIN_MIN_MID = 0.22
PRIMARY_PROB_MARGIN_MIN_TIGHT = 0.32
PRIMARY_CONF_MIN_IF_NO_ODDS = 0.785
PRIMARY_CONF_MIN_IF_NO_ODDS_MID = 0.68
PRIMARY_CONF_MIN_IF_NO_ODDS_TIGHT = 0.80
OPEN_ODDS_COLS = ("AvgH", "PSH", "B365H", "MaxH")
EXCLUDED_CODES = {
    "NOR",  # Norway Eliteserien
    "BE_SPAIN_PRIMERA_RFEF_GROUP_2",
    "BE_SPAIN_SEGUNDA_RFEF_GROUP_3",
    "ROU",  # Romania Liga 1
}
BASE96_EXCLUDED_CODES = {
    "CHN",  # China Super League (low sample + poor accuracy in current season)
}
PRIMARY_EXCLUDED_CODES = {
    "BE_SPAIN_TERCERA_RFEF_GROUP_11",
}
PRIMARY_HARDENED_CODES = {"E0", "D2"}
PRIMARY_HARDENED_CONF_MIN = 0.80
PRIMARY_MONTHLY_ACC_MIN = 0.90
PRIMARY_MONTHS_BACK = 3
MAX_HISTORY_STALENESS_DAYS = 30
DRAW_TRAP_SCOTLAND_CODES = {"SC2", "SC3"}
DRAW_TRAP_LOW_FAV_ODDS_MAX = 1.60
DRAW_TRAP_SCOTLAND_PICK_ODDS_MAX = 1.45
DRAW_TRAP_SCOTLAND_PROB_D_MIN = 0.21
DRAW_TRAP_SCOTLAND_IMPLIED_D_MIN = 0.23
DRAW_TRAP_SERIE_B_PROB_D_MIN = 0.24
DRAW_TRAP_SERIE_B_IMPLIED_D_MIN = 0.25
DRAW_TRAP_GENERIC_PROB_D_MIN = 0.24

# Global draw-risk gate: reject H/A when ProbD > threshold AND match is tight (low GDDiff)
DRAW_RISK_PROB_D_THRESHOLD = 0.22
DRAW_RISK_GD_DIFF_MAX = 0.5

CUP_TOKENS = {
    " cup",
    "cup",
    "trophy",
    "beker",
    "coppa",
    "copa",
    "coupe",
    "pokal",
    "pokalen",
    "pohar",
    "taça",
    "taca",
    "super cup",
    "supercup",
}


def is_cup_competition(name: str) -> bool:
    return any(tok in str(name).lower() for tok in CUP_TOKENS)


def match_qualifies(row, params: Dict[str, Any]) -> bool:
    pred = row.get("Pred")
    conf = get_val(row, "Conf")
    if pred is None or conf is None:
        return False

    conf_home = params.get("conf_home")
    conf_away = params.get("conf_away")
    conf_draw = params.get("conf_draw")
    raw_conf_min = params.get("raw_conf_min")

    qual = False
    if pred == "H" and conf_home is not None and conf >= conf_home:
        qual = True
    if pred == "A" and conf_away is not None and conf >= conf_away:
        qual = True
    if pred == "D" and conf_draw is not None and conf >= conf_draw:
        qual = True

    if raw_conf_min is not None and pred != "D":
        raw_pred = row.get("RawPred")
        raw_conf = get_val(row, "RawConf")
        if raw_pred == pred and raw_conf is not None and raw_conf >= raw_conf_min:
            qual = qual or True

    if not qual:
        return False

    def check_diff(col, thr, invert=False):
        v = get_val(row, col)
        if v is None:
            return False
        if pred == "H":
            return v <= -thr if invert else v >= thr
        if pred == "A":
            return v >= thr if invert else v <= -thr
        return True

    # filters
    if "ref_draw_max" in params:
        v = get_val(row, "RefDrawRate")
        if v is None or v > params["ref_draw_max"]:
            return False
    if "rest_min" in params:
        if pred != "D" and not check_diff("RestDiff", params["rest_min"], invert=False):
            return False
    if "form_pts_min" in params:
        if pred != "D" and not check_diff("FormPtsDiff", params["form_pts_min"], invert=False):
            return False
    if "gd_min" in params:
        if pred != "D" and not check_diff("GDDiff", params["gd_min"], invert=False):
            return False
    if "sot_min" in params:
        if pred != "D" and not check_diff("SOTDiff", params["sot_min"], invert=False):
            return False
    if "shots_min" in params:
        if pred != "D" and not check_diff("ShotsDiff", params["shots_min"], invert=False):
            return False
    if "shot_share_min" in params:
        if pred != "D" and not check_diff("ShotShareDiff", params["shot_share_min"], invert=False):
            return False
    if "shot_acc_min" in params:
        if pred != "D" and not check_diff("ShotAccDiff", params["shot_acc_min"], invert=False):
            return False
    if "shgd_min" in params:
        if pred != "D" and not check_diff("SHGDDiff", params["shgd_min"], invert=False):
            return False
    if "xg_min" in params:
        if pred != "D" and not check_diff("XGDiff", params["xg_min"], invert=False):
            return False
    if "xga_min" in params:
        if pred != "D" and not check_diff("XGADiff", params["xga_min"], invert=True):
            return False
    if "injury_min" in params:
        if pred != "D" and not check_diff("InjuryDiff", params["injury_min"], invert=True):
            return False
    if "susp_min" in params:
        if pred != "D" and not check_diff("SuspDiff", params["susp_min"], invert=True):
            return False
    if "lineup_min" in params:
        if pred != "D" and not check_diff("LineupDiff", params["lineup_min"], invert=False):
            return False
    if "home_adv_min" in params:
        if pred != "D" and not check_diff("HomeAwayPtsDiff", params["home_adv_min"], invert=False):
            return False
    if "gf_min" in params:
        if pred != "D" and not check_diff("GFDiff", params["gf_min"], invert=False):
            return False
    if "ga_min" in params:
        if pred != "D" and not check_diff("GADiff", params["ga_min"], invert=True):
            return False
    if "prob_margin_min" in params:
        v = get_val(row, "ProbMargin")
        if v is None or v < params["prob_margin_min"]:
            return False
    if "weather_rain_max" in params:
        v = get_val(row, "WeatherRain")
        if v is None or v > params["weather_rain_max"]:
            return False
    if "weather_wind_max" in params:
        v = get_val(row, "WeatherWind")
        if v is None or v > params["weather_wind_max"]:
            return False
    if "weather_temp_min" in params:
        v = get_val(row, "WeatherTemp")
        if v is None or v < params["weather_temp_min"]:
            return False
    if "weather_temp_max" in params:
        v = get_val(row, "WeatherTemp")
        if v is None or v > params["weather_temp_max"]:
            return False
    if "weather_humidity_max" in params:
        v = get_val(row, "WeatherHumidity")
        if v is None or v > params["weather_humidity_max"]:
            return False
    if "draw_prob_max" in params:
        v = get_val(row, "ProbD")
        if pred != "D" and (v is None or v > params["draw_prob_max"]):
            return False
    if "draw_prob_min" in params:
        v = get_val(row, "ProbD")
        if pred == "D" and (v is None or v < params["draw_prob_min"]):
            return False
    if "travel_min_km" in params:
        if pred == "H":
            v = get_val(row, "TravelKm")
            if v is None or v < params["travel_min_km"]:
                return False
    if "cap_home_min" in params:
        if pred == "H":
            v = get_val(row, "CapHome")
            if v is None or v < params["cap_home_min"]:
                return False

    # draw balance filters
    if pred == "D":
        if "draw_pts_max" in params:
            v = get_val(row, "AbsFormPtsDiff")
            if v is None or v > params["draw_pts_max"]:
                return False
        if "draw_gd_max" in params:
            v = get_val(row, "AbsGDDiff")
            if v is None or v > params["draw_gd_max"]:
                return False
        if "draw_sot_max" in params:
            v = get_val(row, "AbsSOTDiff")
            if v is None or v > params["draw_sot_max"]:
                return False
        if "draw_rest_max" in params:
            v = get_val(row, "AbsRestDiff")
            if v is None or v > params["draw_rest_max"]:
                return False
        if "draw_homeaway_max" in params:
            v = get_val(row, "AbsHomeAwayPtsDiff")
            if v is None or v > params["draw_homeaway_max"]:
                return False

    return True


def oriented_adv(row, col):
    pred = row.get("Pred")
    v = get_val(row, col)
    if v is None or pred is None:
        return None
    if pred == "H":
        return v
    if pred == "A":
        return -v
    return None


def adv_count_q75(row) -> int:
    diff_map = {
        "AdvShots": "ShotsDiff",
        "AdvSOT": "SOTDiff",
        "AdvGD": "GDDiff",
        "AdvFormPts": "FormPtsDiff",
    }
    count = 0
    for col, thr in ADV_THRESHOLDS.items():
        base_col = diff_map.get(col)
        v = oriented_adv(row, base_col) if base_col else None
        if v is not None and v >= thr:
            count += 1
    return count


def passes_base96(row) -> bool:
    prob_d = get_val(row, "ProbD")
    if prob_d is None:
        return False
    adv_count = adv_count_q75(row)
    if prob_d >= 0.22 and adv_count >= 2:
        return False
    return True


def passes_primary(row) -> bool:
    if not passes_base96(row):
        return False
    is_transfer = get_val(row, "IsTransferWindow") == 1
    if is_transfer:
        form_pts = get_val(row, "FormPtsDiff")
        if form_pts is None or form_pts < PRIMARY_FORM_PTS_MIN_TIGHT:
            return False
        gd = get_val(row, "GDDiff")
        if gd is None or gd > PRIMARY_GD_MAX:
            return False
        code = str(row.get("Code") or row.get("LeagueCode") or "")
        if code in PRIMARY_HARDENED_CODES:
            conf = get_val(row, "Conf")
            if conf is None or conf < PRIMARY_HARDENED_CONF_MIN:
                return False
    return True


def _pick_odds_from_daily_row(row) -> float | None:
    pred = row.get("Pred")
    key = {"H": "OddsH", "D": "OddsD", "A": "OddsA"}.get(pred)
    return get_val(row, key) if key else None


def draw_trap_reject_reason(row) -> str | None:
    pred = row.get("Pred")
    if pred in {None, "D"}:
        return None
    prob_d = get_val(row, "ProbD")
    pick_odds = _pick_odds_from_daily_row(row)
    draw_odds = get_val(row, "OddsD")
    if prob_d is None:
        return "draw_prob_missing"
    draw_implied = (1.0 / draw_odds) if draw_odds and draw_odds > 1.0 else None
    code = str(row.get("Code") or "")

    if (
        code in DRAW_TRAP_SCOTLAND_CODES
        and pick_odds is not None
        and draw_implied is not None
        and pick_odds <= DRAW_TRAP_SCOTLAND_PICK_ODDS_MAX
        and prob_d >= DRAW_TRAP_SCOTLAND_PROB_D_MIN
        and draw_implied >= DRAW_TRAP_SCOTLAND_IMPLIED_D_MIN
    ):
        return "draw_trap_scotland_low_price"

    if (
        code == "I2"
        and pick_odds is not None
        and draw_implied is not None
        and pick_odds <= DRAW_TRAP_LOW_FAV_ODDS_MAX
        and prob_d >= DRAW_TRAP_SERIE_B_PROB_D_MIN
        and draw_implied >= DRAW_TRAP_SERIE_B_IMPLIED_D_MIN
    ):
        return "draw_trap_serie_b_low_price"

    if (
        pick_odds is not None
        and pick_odds <= DRAW_TRAP_SCOTLAND_PICK_ODDS_MAX
        and prob_d >= DRAW_TRAP_GENERIC_PROB_D_MIN
    ):
        return "draw_trap_generic_low_price"

    return None


def draw_risk_reject_reason(row) -> str | None:
    """Global draw-risk gate: reject H/A when ProbD is high AND match is tight (low GDDiff)."""
    pred = row.get("Pred")
    if pred in {None, "D"}:
        return None
    prob_d = get_val(row, "ProbD")
    gd_diff = get_val(row, "GDDiff")
    if prob_d is None or gd_diff is None:
        return None
    abs_gd = abs(gd_diff)
    if prob_d >= DRAW_RISK_PROB_D_THRESHOLD and abs_gd <= DRAW_RISK_GD_DIFF_MAX:
        return f"draw_risk_probD_{prob_d:.3f}_gdDiff_{abs_gd:.2f}"
    return None


def primary_reject_reason(row) -> str | None:
    draw_trap_reason = draw_trap_reject_reason(row)
    if draw_trap_reason:
        return draw_trap_reason
    if not passes_base96(row):
        return "base96_failed"
    is_transfer = get_val(row, "IsTransferWindow") == 1
    if is_transfer:
        form_pts = get_val(row, "FormPtsDiff")
        if form_pts is None:
            return "form_pts_missing"
        if form_pts < PRIMARY_FORM_PTS_MIN_TIGHT:
            return f"form_pts<{PRIMARY_FORM_PTS_MIN_TIGHT}"
        gd = get_val(row, "GDDiff")
        if gd is None:
            return "gd_missing"
        if gd > PRIMARY_GD_MAX:
            return f"gd>{PRIMARY_GD_MAX}"
        code = str(row.get("Code") or row.get("LeagueCode") or "")
        if code in PRIMARY_HARDENED_CODES:
            conf = get_val(row, "Conf")
            if conf is None:
                return "conf_missing"
            if conf < PRIMARY_HARDENED_CONF_MIN:
                return f"conf<{PRIMARY_HARDENED_CONF_MIN}_hardening"
    return None


def _month_keys(last_date: pd.Timestamp, months_back: int) -> List[pd.Period]:
    if last_date is None or pd.isna(last_date):
        return []
    period = last_date.to_period("M")
    return [period - i for i in range(months_back)]


def last_finished_match_date(df: pd.DataFrame) -> dt_date | None:
    if df.empty or "Date" not in df.columns:
        return None
    finished = df
    if "FTHG" in df.columns and "FTAG" in df.columns:
        finished = df[df["FTHG"].notna() & df["FTAG"].notna()]
    dates = pd.to_datetime(finished.get("Date"), errors="coerce", dayfirst=True)
    if not dates.notna().any():
        return None
    return dates.max().date()


def league_fails_monthly_accuracy(
    feat: pd.DataFrame,
    params: Dict[str, Any],
    code: str,
    min_acc: float = PRIMARY_MONTHLY_ACC_MIN,
    months_back: int = PRIMARY_MONTHS_BACK,
) -> bool:
    if feat.empty:
        return False
    hist = feat[feat["FTHG"].notna() & feat["FTAG"].notna()].copy()
    if hist.empty:
        return False
    hist["Month"] = pd.to_datetime(hist["Date"], errors="coerce").dt.to_period("M")
    last_date = pd.to_datetime(hist["Date"], errors="coerce").max()
    months = _month_keys(last_date, months_back)
    if not months:
        return False
    window_df = hist[hist["Month"].isin(months)].copy()
    if window_df.empty:
        return False
    window_df["Code"] = code

    qualifying = window_df[window_df.apply(lambda r: match_qualifies(r, params), axis=1)].copy()
    if qualifying.empty:
        return False
    picks = qualifying[qualifying.apply(passes_primary, axis=1)].copy()
    if picks.empty:
        return False
    if "Actual" not in picks.columns:
        picks = picks.copy()
        picks["Actual"] = picks.apply(
            lambda r: "H" if r.FTHG > r.FTAG else ("A" if r.FTHG < r.FTAG else "D"),
            axis=1,
        )
    acc = (picks["Pred"] == picks["Actual"]).mean()
    return acc < min_acc


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="")
    ap.add_argument("--season", default="2526")
    ap.add_argument("--summary", default="reports/europe_summary_enhanced.csv")
    ap.add_argument("--external", default="data/processed/external_features.csv")
    ap.add_argument("--fixtures", default="data/raw/football_data/fixtures.csv")
    ap.add_argument("--codes", default="")
    ap.add_argument("--out", default="")
    ap.add_argument("--max-history-staleness-days", type=int, default=MAX_HISTORY_STALENESS_DAYS)
    args = ap.parse_args()

    target_date = dt_date.today().isoformat() if not args.date else args.date
    target_date_obj = dt_date.fromisoformat(target_date)
    season_code = str(args.season)
    season_str = season_code_to_str(season_code)

    summary_path = Path(args.summary)
    if not summary_path.exists():
        print(f"Missing summary file: {summary_path}")
        return 1
    summary = pd.read_csv(summary_path)

    codes_filter = {c.strip() for c in args.codes.split(",") if c.strip()}

    ext_df = None
    external_path = Path(args.external)
    if external_path.exists():
        try:
            ext_df = pd.read_csv(external_path, encoding="utf-8-sig")
        except Exception:
            ext_df = None

    fixtures_df = None
    fixtures_path = Path(args.fixtures)
    if fixtures_path.exists():
        try:
            fixtures_df = pd.read_csv(fixtures_path, encoding="utf-8-sig")
        except Exception:
            fixtures_df = None
    if fixtures_df is not None and "Div" not in fixtures_df.columns:
        if "Code" in fixtures_df.columns:
            fixtures_df = fixtures_df.rename(columns={"Code": "Div"})
        elif "LeagueCode" in fixtures_df.columns:
            fixtures_df = fixtures_df.rename(columns={"LeagueCode": "Div"})

    picks_all: List[Dict[str, Any]] = []
    safety_rejected: List[Dict[str, Any]] = []
    stale_skipped: List[Dict[str, Any]] = []
    for _, row in summary.iterrows():
        if str(row.get("Status")) != "ok":
            continue
        code = str(row.get("Code"))
        league = str(row.get("League"))

        if code in EXCLUDED_CODES:
            continue
        if code in PRIMARY_EXCLUDED_CODES or is_cup_competition(league):
            continue
        if codes_filter and code not in codes_filter:
            continue
        params = parse_params(row.get("Params"))
        if not params:
            continue

        raw_dir = Path("data/raw/football_data")
        path_main = raw_dir / f"{code}_{season_code}.csv"
        path_all = raw_dir / f"{code}_all.csv"
        if path_main.exists():
            df_hist = pd.read_csv(path_main, encoding="utf-8-sig")
            df_hist = normalize_main(df_hist)
        elif path_all.exists():
            df_hist = pd.read_csv(path_all, encoding="utf-8-sig")
            df_hist = normalize_extra(df_hist, season_str)
        else:
            continue

        last_hist_date = last_finished_match_date(df_hist)
        if last_hist_date is None:
            stale_skipped.append(
                {
                    "Code": code,
                    "League": league,
                    "Reason": "no_finished_history_date",
                }
            )
            continue
        stale_days = (target_date_obj - last_hist_date).days
        if stale_days > args.max_history_staleness_days:
            stale_skipped.append(
                {
                    "Code": code,
                    "League": league,
                    "LastHistoryDate": last_hist_date.isoformat(),
                    "StaleDays": stale_days,
                    "Reason": f"history_stale>{args.max_history_staleness_days}d",
                }
            )
            continue

        df_fix = None
        if fixtures_df is not None and "Div" in fixtures_df.columns:
            df_fix = fixtures_df[fixtures_df["Div"].astype(str) == code].copy()
            if not df_fix.empty:
                df_fix = df_fix.rename(columns={"Div": "LeagueCode"})
                # ensure required columns exist
                for col in ["FTHG", "FTAG"]:
                    if col not in df_fix.columns:
                        df_fix[col] = pd.NA
        if df_fix is not None and not df_fix.empty:
            # merge historical with fixtures for rolling features
            if "Date" in df_fix.columns:
                df_fix["Date"] = pd.to_datetime(df_fix["Date"], errors="coerce", dayfirst=True)
            df = pd.concat([df_hist, df_fix], ignore_index=True, sort=False)
        else:
            df = df_hist

        if df.empty or "Date" not in df.columns:
            continue

        # pick odds columns prioritizing fixture odds if present
        odds_cols = None
        if df_fix is not None and not df_fix.empty:
            candidates = [
                ("AvgH", "AvgD", "AvgA"),
                ("B365H", "B365D", "B365A"),
                ("AvgCH", "AvgCD", "AvgCA"),
                ("B365CH", "B365CD", "B365CA"),
                ("PSCH", "PSCD", "PSCA"),
                ("MaxCH", "MaxCD", "MaxCA"),
                ("MaxH", "MaxD", "MaxA"),
                ("BFDH", "BFDD", "BFDA"),
            ]
            for cols in candidates:
                if all(c in df.columns for c in cols):
                    # at least one fixture row has odds
                    if df_fix[list(cols)].notna().any(axis=1).any():
                        odds_cols = cols
                        break
        if odds_cols is None:
            odds_cols = pick_odds_cols(df)
        if not odds_cols:
            continue

        feat = build_pre_match_features(df, odds_cols, window=5, team_geo=None, external_features=ext_df)
        if league_fails_monthly_accuracy(feat, params, code):
            continue
        feat["DateOnly"] = pd.to_datetime(feat["Date"], errors="coerce").dt.date.astype(str)
        day = feat[(feat["DateOnly"] == target_date) & feat["FTHG"].isna() & feat["FTAG"].isna()].copy()
        if day.empty:
            continue

        for _, r in day.iterrows():
            if match_qualifies(r, params):
                adv_count = adv_count_q75(r)
                pick = {
                    "Date": target_date,
                    "League": league,
                    "Code": code,
                    "Home": r.get("HomeTeam"),
                    "Away": r.get("AwayTeam"),
                    "Pred": r.get("Pred"),
                    "Conf": r.get("Conf"),
                    "ProbD": r.get("ProbD"),
                    "FormPtsDiff": r.get("FormPtsDiff"),
                    "GDDiff": r.get("GDDiff"),
                    "GADiff": r.get("GADiff"),
                    "AdvCount_Q75": adv_count,
                    "OddsH": r.get(odds_cols[0]),
                    "OddsD": r.get(odds_cols[1]),
                    "OddsA": r.get(odds_cols[2]),
                    "Params": params,
                }
                draw_trap_reason = draw_trap_reject_reason(pick)
                if draw_trap_reason:
                    pick["SafetyRejectReason"] = draw_trap_reason
                    safety_rejected.append(pick)
                    continue
                draw_risk_reason = draw_risk_reject_reason(pick)
                if draw_risk_reason:
                    pick["SafetyRejectReason"] = draw_risk_reason
                    safety_rejected.append(pick)
                    continue
                picks_all.append(pick)

    def is_safety_rejected(p):
        return draw_trap_reject_reason(p) is not None or draw_risk_reject_reason(p) is not None

    picks_base = [
        p
        for p in picks_all
        if not is_safety_rejected(p) and passes_base96(p) and p.get("Code") not in BASE96_EXCLUDED_CODES
    ]
    picks_primary = [p for p in picks_all if not is_safety_rejected(p) and passes_primary(p)]

    out_csv = Path(args.out) if args.out else Path(f"reports/daily_picks_{target_date}.csv")
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    primary_csv = out_csv
    base_csv = out_csv.with_name(f"{out_csv.stem}_base96{out_csv.suffix}")

    pd.DataFrame(picks_primary).to_csv(primary_csv, index=False)
    pd.DataFrame(picks_base).to_csv(base_csv, index=False)

    # combined markdown summary
    out_md = out_csv.with_suffix(".md")
    lines = []
    lines.append(f"# Daily picks ({target_date})")
    lines.append(f"- Qualifying pool before safety: {len(picks_all) + len(safety_rejected)}")
    lines.append(f"- Qualifying pool after safety: {len(picks_all)}")
    draw_trap_count = sum(1 for p in safety_rejected if p.get("SafetyRejectReason", "").startswith("draw_trap"))
    draw_risk_count = sum(1 for p in safety_rejected if p.get("SafetyRejectReason", "").startswith("draw_risk"))
    lines.append(f"- Draw-trap safety rejections: {draw_trap_count}")
    lines.append(f"- Draw-risk gate rejections: {draw_risk_count}")
    lines.append(f"- Leagues skipped for stale history: {len(stale_skipped)}")
    lines.append(
        f"- Primary (Base96 + transfer window tighten: FormPtsDiff >= {PRIMARY_FORM_PTS_MIN_TIGHT}, GDDiff <= {PRIMARY_GD_MAX}; E0/D2 transfer window Conf >= {PRIMARY_HARDENED_CONF_MIN}; monthly leagues <{PRIMARY_MONTHLY_ACC_MIN*100:.0f}% removed): {len(picks_primary)}"
    )
    lines.append(f"- Base96: {len(picks_base)}")

    def add_picks_section(title: str, picks: List[Dict[str, Any]]):
        lines.append("")
        lines.append(f"## {title}")
        if not picks:
            lines.append("- No picks")
            return
        by_league = pd.DataFrame(picks).groupby("League").size().sort_values(ascending=False)
        lines.append("### Picks by league")
        for league, n in by_league.items():
            lines.append(f"- {league}: {int(n)}")
        lines.append("")
        lines.append("### Picks")
        for p in picks:
            conf = p.get("Conf")
            conf_str = f"{conf:.2f}" if conf is not None and not pd.isna(conf) else "n/a"
            lines.append(f"- [{p['Code']}] {p['Home']} vs {p['Away']} | Pred: {p['Pred']} | Conf: {conf_str}")

    add_picks_section("Primary picks", picks_primary)
    add_picks_section("Base96 picks", picks_base)

    lines.append("")
    lines.append("## Draw-trap safety rejections")
    draw_trap_rejected = [p for p in safety_rejected if p.get("SafetyRejectReason", "").startswith("draw_trap")]
    if not draw_trap_rejected:
        lines.append("- None")
    else:
        for p in draw_trap_rejected:
            conf = p.get("Conf")
            conf_str = f"{conf:.2f}" if conf is not None and not pd.isna(conf) else "n/a"
            prob_d = p.get("ProbD")
            prob_d_str = f"{prob_d:.3f}" if prob_d is not None and not pd.isna(prob_d) else "n/a"
            lines.append(
                f"- [{p['Code']}] {p['Home']} vs {p['Away']} | Pred: {p['Pred']} | Conf: {conf_str} | ProbD: {prob_d_str} | Reason: {p.get('SafetyRejectReason')}"
            )

    lines.append("")
    lines.append("## Draw-risk gate rejections")
    draw_risk_rejected = [p for p in safety_rejected if p.get("SafetyRejectReason", "").startswith("draw_risk")]
    if not draw_risk_rejected:
        lines.append("- None")
    else:
        for p in draw_risk_rejected:
            conf = p.get("Conf")
            conf_str = f"{conf:.2f}" if conf is not None and not pd.isna(conf) else "n/a"
            prob_d = p.get("ProbD")
            prob_d_str = f"{prob_d:.3f}" if prob_d is not None and not pd.isna(prob_d) else "n/a"
            gd_diff = p.get("GDDiff")
            gd_diff_str = f"{gd_diff:.2f}" if gd_diff is not None and not pd.isna(gd_diff) else "n/a"
            lines.append(
                f"- [{p['Code']}] {p['Home']} vs {p['Away']} | Pred: {p['Pred']} | Conf: {conf_str} | ProbD: {prob_d_str} | GDDiff: {gd_diff_str} | Reason: {p.get('SafetyRejectReason')}"
            )

    lines.append("")
    lines.append("## Stale-history league skips")
    if not stale_skipped:
        lines.append("- None")
    else:
        for item in stale_skipped:
            lines.append(
                f"- [{item.get('Code')}] {item.get('League')} | LastHistoryDate: {item.get('LastHistoryDate', 'n/a')} | StaleDays: {item.get('StaleDays', 'n/a')} | Reason: {item.get('Reason')}"
            )

    primary_only = {(p["Code"], p["Home"], p["Away"]) for p in picks_primary}
    base_only = [p for p in picks_base if (p["Code"], p["Home"], p["Away"]) not in primary_only]
    lines.append("")
    lines.append("## Base96 picks excluded by primary")
    if not base_only:
        lines.append("- None")
    else:
        for p in base_only:
            reason = primary_reject_reason(p)
            conf = p.get("Conf")
            conf_str = f"{conf:.2f}" if conf is not None and not pd.isna(conf) else "n/a"
            form_pts = p.get("FormPtsDiff")
            form_str = f"{form_pts:.2f}" if form_pts is not None and not pd.isna(form_pts) else "n/a"
            lines.append(
                f"- [{p['Code']}] {p['Home']} vs {p['Away']} | Pred: {p['Pred']} | Conf: {conf_str} | FormPtsDiff: {form_str} | Reason: {reason}"
            )

    out_md.write_text("\n".join(lines), encoding="utf-8")
    print(f"saved: {primary_csv}")
    print(f"saved: {base_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
