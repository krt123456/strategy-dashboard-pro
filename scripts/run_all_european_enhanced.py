#!/usr/bin/env python3
"""Enhanced selective strategy across European leagues.

Adds optional filters:
- Referee draw rate (from past matches)
- Rest advantage alignment (from past matches)

Chooses, per league, the strategy with highest coverage that meets target accuracy.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import List, Tuple, Dict, Any, Optional

try:
    import pandas as pd  # type: ignore
except Exception:
    print("Missing dependency: pandas. Install with: pip install pandas", file=sys.stderr)
    raise

try:
    import requests  # type: ignore
except Exception:
    print("Missing dependency: requests. Install with: pip install requests", file=sys.stderr)
    raise


def normalize_team_name(name: str) -> str:
    import re

    s = str(name).lower()
    s = re.sub(r"\(.*?\)", "", s)
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    s = re.sub(r"\b(fc|cf|afc|sc|ac|ss|as|the)\b", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _parse_date_series(series: pd.Series) -> pd.Series:
    raw = series.astype(str).str.strip()
    raw = raw.str.replace(r"[\u2010\u2011\u2012\u2013\u2014\u2212]", "-", regex=True)
    parsed = pd.Series(index=raw.index, dtype="datetime64[ns]")
    mask_ymd = raw.str.match(r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}")
    mask_dmy = raw.str.match(r"^\d{1,2}[./-]\d{1,2}[./-]\d{2,4}")
    if mask_ymd.any():
        parsed.loc[mask_ymd] = pd.to_datetime(raw[mask_ymd], errors="coerce")
    if mask_dmy.any():
        parsed.loc[mask_dmy] = pd.to_datetime(raw[mask_dmy], dayfirst=True, errors="coerce")
    remaining = parsed.isna()
    if remaining.any():
        import warnings

        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", message="Parsing dates in", category=UserWarning)
            warnings.filterwarnings("ignore", message="Could not infer format", category=UserWarning)
            parsed.loc[remaining] = pd.to_datetime(raw[remaining], dayfirst=True, errors="coerce")
    return parsed


def season_code_to_str(season_code: str) -> str:
    if len(season_code) != 4 or not season_code.isdigit():
        return season_code
    start = int("20" + season_code[:2])
    end = int("20" + season_code[2:])
    return f"{start}/{end}"


def fetch_csv(url: str, dest: Path, *, force: bool = False) -> bool:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not force and dest.exists() and dest.stat().st_size > 0:
        return True
    headers = {"User-Agent": "Mozilla/5.0 (compatible; FootballData/1.0)"}
    resp = None
    for attempt in range(3):
        try:
            resp = requests.get(url, timeout=30, headers=headers)
            if resp.status_code == 200 and resp.content.strip():
                break
        except requests.RequestException:
            resp = None
        if attempt < 2:
            time.sleep(1.5 * (attempt + 1))
    if resp is None or resp.status_code != 200 or not resp.content.strip():
        return False
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    tmp.write_bytes(resp.content)
    tmp.replace(dest)
    return True


def pick_odds_cols(df: pd.DataFrame) -> Tuple[str, str, str] | None:
    candidates = [
        ("AvgH", "AvgD", "AvgA"),
        ("B365H", "B365D", "B365A"),
        ("AvgCH", "AvgCD", "AvgCA"),
        ("B365CH", "B365CD", "B365CA"),
        ("B365CH", "B365CD", "B36CA"),
        ("PSCH", "PSCD", "PSCA"),
        ("MaxCH", "MaxCD", "MaxCA"),
        ("MaxH", "MaxD", "MaxA"),
    ]
    for cols in candidates:
        if all(c in df.columns for c in cols):
            return cols  # type: ignore[return-value]
    return None


def normalize_main(df: pd.DataFrame) -> pd.DataFrame:
    return df


def normalize_extra(df: pd.DataFrame, season_str: str) -> pd.DataFrame:
    if "Season" in df.columns:
        seasons = df["Season"].astype(str)
        filtered = df[seasons == season_str].copy()
        if filtered.empty:
            # Fallback to most recent season (calendar-year leagues often use "YYYY").
            def season_rank(value: str) -> int:
                import re

                nums = [int(x) for x in re.findall(r"\d{2,4}", value)]
                return nums[-1] if nums else -1

            available = seasons.dropna().tolist()
            if available:
                latest = max(available, key=season_rank)
                df = df[seasons == latest].copy()
            else:
                df = filtered
        else:
            df = filtered
    if "HG" in df.columns and "AG" in df.columns:
        df = df.rename(columns={"HG": "FTHG", "AG": "FTAG"})
    if "Home" in df.columns:
        df = df.rename(columns={"Home": "HomeTeam", "Away": "AwayTeam"})
    return df


def parse_last_date(df: pd.DataFrame) -> str:
    if "Date" not in df.columns:
        return ""
    dates = _parse_date_series(df["Date"])
    if dates.isna().all():
        return ""
    return dates.max().date().isoformat()


def league_context(df: pd.DataFrame) -> Dict[str, float]:
    # basic league characteristics (crowd/home effect proxy)
    out: Dict[str, float] = {}
    if df.empty:
        return out
    df = df.dropna(subset=["FTHG", "FTAG"]).copy()
    total = len(df)
    if total == 0:
        return out
    out["home_win_rate"] = float((df["FTHG"] > df["FTAG"]).mean())
    out["draw_rate"] = float((df["FTHG"] == df["FTAG"]).mean())
    out["away_win_rate"] = float((df["FTHG"] < df["FTAG"]).mean())
    out["avg_goals"] = float((df["FTHG"] + df["FTAG"]).mean())
    if "HY" in df.columns and "AY" in df.columns:
        out["avg_yellow"] = float((df["HY"] + df["AY"]).mean())
    if "HR" in df.columns and "AR" in df.columns:
        out["avg_red"] = float((df["HR"] + df["AR"]).mean())
    if "HS" in df.columns and "AS" in df.columns:
        out["avg_shots"] = float((df["HS"] + df["AS"]).mean())
    if "HST" in df.columns and "AST" in df.columns:
        out["avg_sot"] = float((df["HST"] + df["AST"]).mean())
    return out


def _build_external_map(external_features: Optional[pd.DataFrame]) -> Dict[Tuple[str, str, Any], Dict[str, Any]] | None:
    if external_features is None or external_features.empty:
        return None
    ext = external_features.copy()
    if "Date" not in ext.columns or "Team" not in ext.columns:
        return None
    ext["Date"] = _parse_date_series(ext["Date"]).dt.date
    ext = ext.dropna(subset=["Date", "Team"]).copy()
    out: Dict[Tuple[str, str, Any], Dict[str, Any]] = {}
    for _, r in ext.iterrows():
        team = normalize_team_name(str(r.get("Team", "")))
        opp = normalize_team_name(str(r.get("Opp", ""))) if "Opp" in ext.columns else ""
        date = r.get("Date")
        if not team or not date:
            continue
        payload = {
            "xg": r.get("XG"),
            "xga": r.get("XGA"),
            "inj": r.get("Injuries"),
            "lineup": r.get("Lineup"),
            "susp": r.get("Suspensions"),
            "temp": r.get("WeatherTemp"),
            "wind": r.get("WeatherWind"),
            "rain": r.get("WeatherRain"),
            "humidity": r.get("WeatherHumidity"),
        }
        out[(team, opp, date)] = payload
        if not opp:
            out[(team, "", date)] = payload
    return out


def add_season_phase_features(df: pd.DataFrame) -> pd.DataFrame:
    if "Date" not in df.columns:
        return df
    dates = pd.to_datetime(df["Date"], errors="coerce")
    if dates.isna().all():
        return df

    if "Season" in df.columns:
        season_key = df["Season"].astype(str)
    else:
        season_key = pd.Series(pd.NA, index=df.index, dtype="object")

    # Fill missing season keys using July-June heuristic.
    inferred = pd.Series(pd.NA, index=df.index, dtype="object")
    year = dates.dt.year
    month = dates.dt.month
    start_year = year.where(month >= 7, year - 1)
    inferred = (
        start_year.astype("Int64").astype(str)
        + "/"
        + (start_year + 1).astype("Int64").astype(str)
    )
    season_key = season_key.where(season_key.notna(), inferred)

    df["SeasonKey"] = season_key
    df["SeasonStart"] = df.groupby("SeasonKey")["Date"].transform("min")
    df["SeasonEnd"] = df.groupby("SeasonKey")["Date"].transform("max")
    df["SeasonDay"] = (df["Date"] - df["SeasonStart"]).dt.days
    df["SeasonLength"] = (df["SeasonEnd"] - df["SeasonStart"]).dt.days
    df["SeasonProgress"] = df["SeasonDay"] / df["SeasonLength"].replace(0, pd.NA)
    df["SeasonMatchNo"] = df.groupby("SeasonKey").cumcount() + 1
    df["SeasonMatches"] = df.groupby("SeasonKey")["Date"].transform("count")
    df["SeasonMatchPct"] = df["SeasonMatchNo"] / df["SeasonMatches"]
    df["IsEarlySeason"] = (df["SeasonMatchNo"] <= 5).astype(int)
    df["IsLateSeason"] = (df["SeasonMatchNo"] >= df["SeasonMatches"] - 4).astype(int)

    df["Month"] = pd.to_datetime(df["Date"], errors="coerce").dt.month
    df["IsTransferWindow"] = df["Month"].isin([1, 7, 8]).astype(int)
    summer_close = pd.to_datetime(df["Date"].dt.year.astype(str) + "-08-31", errors="coerce")
    winter_close = pd.to_datetime(df["Date"].dt.year.astype(str) + "-01-31", errors="coerce")
    df["DaysAfterSummerClose"] = (df["Date"] - summer_close).dt.days
    df["DaysAfterWinterClose"] = (df["Date"] - winter_close).dt.days
    df["IsPostSummerWindow"] = df["DaysAfterSummerClose"].between(0, 30).astype(int)
    df["IsPostWinterWindow"] = df["DaysAfterWinterClose"].between(0, 30).astype(int)

    return df


def build_match_features(
    df: pd.DataFrame,
    odds_cols: Tuple[str, str, str],
    window: int,
    team_geo: Dict[str, Dict[str, float]] | None = None,
    external_features: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    df = df.copy()
    df = df.dropna(subset=["Date", "FTHG", "FTAG"] + list(odds_cols)).copy()
    df["Date"] = _parse_date_series(df["Date"])
    df = df.dropna(subset=["Date"]).sort_values("Date").reset_index(drop=True)

    raw_h = 1.0 / df[odds_cols[0]]
    raw_d = 1.0 / df[odds_cols[1]]
    raw_a = 1.0 / df[odds_cols[2]]
    p_h = raw_h.copy()
    p_d = raw_d.copy()
    p_a = raw_a.copy()
    s = p_h + p_d + p_a
    p_h /= s
    p_d /= s
    p_a /= s

    probs = pd.DataFrame({"H": p_h, "D": p_d, "A": p_a})
    df["Pred"] = probs.apply(lambda r: r.idxmax() if r.notna().any() else None, axis=1)
    df["Conf"] = probs.max(axis=1)
    df["ProbD"] = p_d
    df["ProbSecond"] = probs.apply(lambda r: r.nlargest(2).iloc[-1], axis=1)
    df["ProbMargin"] = df["Conf"] - df["ProbSecond"]
    raw_probs = pd.DataFrame({"H": raw_h, "D": raw_d, "A": raw_a})
    df["RawPred"] = raw_probs.apply(lambda r: r.idxmax() if r.notna().any() else None, axis=1)
    df["RawConf"] = raw_probs.max(axis=1)
    df["RawSecond"] = raw_probs.apply(lambda r: r.nlargest(2).iloc[-1], axis=1)
    df["RawMargin"] = df["RawConf"] - df["RawSecond"]
    df["Actual"] = df.apply(lambda r: "H" if r.FTHG > r.FTAG else ("A" if r.FTHG < r.FTAG else "D"), axis=1)

    # Referee draw rate (rolling)
    if "Referee" in df.columns:
        ref_stats: Dict[str, Dict[str, int]] = {}
        league_draws: List[int] = []
        ref_draw_rates = []
        for _, r in df.iterrows():
            ldr = (sum(league_draws) / len(league_draws)) if league_draws else 0.30
            ref = r["Referee"]
            if ref in ref_stats and ref_stats[ref]["n"] > 0:
                rdr = ref_stats[ref]["draws"] / ref_stats[ref]["n"]
            else:
                rdr = ldr
            ref_draw_rates.append(rdr)

            # update stats
            if ref not in ref_stats:
                ref_stats[ref] = {"n": 0, "draws": 0}
            ref_stats[ref]["n"] += 1
            if r.FTHG == r.FTAG:
                ref_stats[ref]["draws"] += 1
            league_draws.append(1 if r.FTHG == r.FTAG else 0)

        df["RefDrawRate"] = ref_draw_rates
    else:
        df["RefDrawRate"] = 0.30

    # external map
    ext_map = _build_external_map(external_features)

    def ext_lookup(team: str, opp: str, date_val):
        if not ext_map or date_val is None:
            return {}
        key = (normalize_team_name(team), normalize_team_name(opp), date_val)
        if key in ext_map:
            return ext_map[key]
        key2 = (normalize_team_name(team), "", date_val)
        return ext_map.get(key2, {})

    # Rest days difference + rolling form stats + travel/capacity
    last_date: Dict[str, pd.Timestamp] = {}
    team_hist: Dict[str, List[Dict[str, float]]] = {}
    rest_diff = []
    form_pts_diff = []
    gd_diff = []
    sot_diff = []
    shots_diff = []
    shot_share_diff = []
    shot_acc_diff = []
    shgd_diff = []
    card_diff = []
    home_away_pts_diff = []
    gf_diff = []
    ga_diff = []
    xg_diff = []
    xga_diff = []
    inj_diff = []
    lineup_diff = []
    susp_diff = []
    weather_temp = []
    weather_wind = []
    weather_rain = []
    weather_humidity = []
    travel_km = []
    cap_home = []

    def haversine_km(lat1, lon1, lat2, lon2):
        import math

        r = 6371.0
        phi1 = math.radians(lat1)
        phi2 = math.radians(lat2)
        dphi = math.radians(lat2 - lat1)
        dl = math.radians(lon2 - lon1)
        a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dl / 2) ** 2
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
        return r * c

    def rolling_mean(vals: List[float]) -> float | None:
        if len(vals) < window:
            return None
        return float(sum(vals[-window:]) / window)

    home_hist_pts: Dict[str, List[float]] = {}
    away_hist_pts: Dict[str, List[float]] = {}

    for _, r in df.iterrows():
        home = r["HomeTeam"] if "HomeTeam" in df.columns else r.get("Home", "")
        away = r["AwayTeam"] if "AwayTeam" in df.columns else r.get("Away", "")

        # rest
        rest_h = (r["Date"] - last_date[home]).days if home in last_date else None
        rest_a = (r["Date"] - last_date[away]).days if away in last_date else None
        if rest_h is None or rest_a is None:
            rest_diff.append(0)
        else:
            rest_diff.append(rest_h - rest_a)
        last_date[home] = r["Date"]
        last_date[away] = r["Date"]

        # form stats (before updating history)
        if home not in team_hist:
            team_hist[home] = []
        if away not in team_hist:
            team_hist[away] = []
        if home not in home_hist_pts:
            home_hist_pts[home] = []
        if away not in away_hist_pts:
            away_hist_pts[away] = []

        def extract_mean(team: str, key: str) -> float | None:
            vals = [m[key] for m in team_hist[team] if m.get(key) is not None]
            return rolling_mean(vals) if vals else None

        h_pts = extract_mean(home, "pts")
        a_pts = extract_mean(away, "pts")
        h_gd = extract_mean(home, "gd")
        a_gd = extract_mean(away, "gd")
        h_sot = extract_mean(home, "sot")
        a_sot = extract_mean(away, "sot")
        h_shots = extract_mean(home, "shots")
        a_shots = extract_mean(away, "shots")
        h_shot_share = extract_mean(home, "shot_share")
        a_shot_share = extract_mean(away, "shot_share")
        h_shot_acc = extract_mean(home, "shot_acc")
        a_shot_acc = extract_mean(away, "shot_acc")
        h_shgd = extract_mean(home, "shgd")
        a_shgd = extract_mean(away, "shgd")
        h_card = extract_mean(home, "cards")
        a_card = extract_mean(away, "cards")
        h_gf = extract_mean(home, "gf")
        a_gf = extract_mean(away, "gf")
        h_ga = extract_mean(home, "ga")
        a_ga = extract_mean(away, "ga")
        h_xg = extract_mean(home, "xg")
        a_xg = extract_mean(away, "xg")
        h_xga = extract_mean(home, "xga")
        a_xga = extract_mean(away, "xga")
        h_inj = extract_mean(home, "inj")
        a_inj = extract_mean(away, "inj")
        h_lineup = extract_mean(home, "lineup")
        a_lineup = extract_mean(away, "lineup")
        h_susp = extract_mean(home, "susp")
        a_susp = extract_mean(away, "susp")

        form_pts_diff.append(h_pts - a_pts if h_pts is not None and a_pts is not None else None)
        gd_diff.append(h_gd - a_gd if h_gd is not None and a_gd is not None else None)
        sot_diff.append(h_sot - a_sot if h_sot is not None and a_sot is not None else None)
        shots_diff.append(h_shots - a_shots if h_shots is not None and a_shots is not None else None)
        shot_share_diff.append(h_shot_share - a_shot_share if h_shot_share is not None and a_shot_share is not None else None)
        shot_acc_diff.append(h_shot_acc - a_shot_acc if h_shot_acc is not None and a_shot_acc is not None else None)
        shgd_diff.append(h_shgd - a_shgd if h_shgd is not None and a_shgd is not None else None)
        card_diff.append(h_card - a_card if h_card is not None and a_card is not None else None)
        gf_diff.append(h_gf - a_gf if h_gf is not None and a_gf is not None else None)
        ga_diff.append(h_ga - a_ga if h_ga is not None and a_ga is not None else None)
        xg_diff.append(h_xg - a_xg if h_xg is not None and a_xg is not None else None)
        xga_diff.append(h_xga - a_xga if h_xga is not None and a_xga is not None else None)
        inj_diff.append(h_inj - a_inj if h_inj is not None and a_inj is not None else None)
        lineup_diff.append(h_lineup - a_lineup if h_lineup is not None and a_lineup is not None else None)
        susp_diff.append(h_susp - a_susp if h_susp is not None and a_susp is not None else None)

        ext_h = ext_lookup(home, away, r["Date"].date())
        ext_a = ext_lookup(away, home, r["Date"].date())

        def pick_weather(key: str):
            v = ext_h.get(key)
            if v is None:
                v = ext_a.get(key)
            return v

        weather_temp.append(pick_weather("temp"))
        weather_wind.append(pick_weather("wind"))
        weather_rain.append(pick_weather("rain"))
        weather_humidity.append(pick_weather("humidity"))

        # home vs away form (crowd influence proxy)
        h_home_pts = rolling_mean(home_hist_pts[home])
        a_away_pts = rolling_mean(away_hist_pts[away])
        if h_home_pts is not None and a_away_pts is not None:
            home_away_pts_diff.append(h_home_pts - a_away_pts)
        else:
            home_away_pts_diff.append(None)

        # update team history with current match
        # points
        if r.FTHG > r.FTAG:
            h_pts_now, a_pts_now = 3.0, 0.0
        elif r.FTHG < r.FTAG:
            h_pts_now, a_pts_now = 0.0, 3.0
        else:
            h_pts_now, a_pts_now = 1.0, 1.0
        h_gd_now = float(r.FTHG - r.FTAG)
        a_gd_now = float(r.FTAG - r.FTHG)
        h_sot_now = float(r["HST"]) if "HST" in df.columns and pd.notna(r.get("HST")) else None
        a_sot_now = float(r["AST"]) if "AST" in df.columns and pd.notna(r.get("AST")) else None
        h_shots_now = float(r["HS"]) if "HS" in df.columns and pd.notna(r.get("HS")) else None
        a_shots_now = float(r["AS"]) if "AS" in df.columns and pd.notna(r.get("AS")) else None
        h_shot_share_now = None
        a_shot_share_now = None
        if h_shots_now is not None and a_shots_now is not None and (h_shots_now + a_shots_now) > 0:
            total_shots = h_shots_now + a_shots_now
            h_shot_share_now = h_shots_now / total_shots
            a_shot_share_now = a_shots_now / total_shots
        h_shot_acc_now = None
        a_shot_acc_now = None
        if h_shots_now is not None and h_shots_now > 0 and h_sot_now is not None:
            h_shot_acc_now = h_sot_now / h_shots_now
        if a_shots_now is not None and a_shots_now > 0 and a_sot_now is not None:
            a_shot_acc_now = a_sot_now / a_shots_now
        h_shgd_now = None
        a_shgd_now = None
        if pd.notna(r.get("HTHG")) and pd.notna(r.get("HTAG")):
            h_shgd_now = float((r.FTHG - r.FTAG) - (r.HTHG - r.HTAG))
            a_shgd_now = -h_shgd_now
        h_cards_now = None
        a_cards_now = None
        if "HY" in df.columns and "HR" in df.columns and pd.notna(r.get("HY")) and pd.notna(r.get("HR")):
            h_cards_now = float(r["HY"] + r["HR"])
        if "AY" in df.columns and "AR" in df.columns and pd.notna(r.get("AY")) and pd.notna(r.get("AR")):
            a_cards_now = float(r["AY"] + r["AR"])

        ext_h = ext_lookup(home, away, r["Date"].date())
        ext_a = ext_lookup(away, home, r["Date"].date())

        team_hist[home].append(
            {
                "pts": h_pts_now,
                "gd": h_gd_now,
                "sot": h_sot_now,
                "shots": h_shots_now,
                "shot_share": h_shot_share_now,
                "shot_acc": h_shot_acc_now,
                "shgd": h_shgd_now,
                "cards": h_cards_now,
                "gf": float(r.FTHG),
                "ga": float(r.FTAG),
                "xg": ext_h.get("xg"),
                "xga": ext_h.get("xga"),
                "inj": ext_h.get("inj"),
                "lineup": ext_h.get("lineup"),
                "susp": ext_h.get("susp"),
            }
        )
        team_hist[away].append(
            {
                "pts": a_pts_now,
                "gd": a_gd_now,
                "sot": a_sot_now,
                "shots": a_shots_now,
                "shot_share": a_shot_share_now,
                "shot_acc": a_shot_acc_now,
                "shgd": a_shgd_now,
                "cards": a_cards_now,
                "gf": float(r.FTAG),
                "ga": float(r.FTHG),
                "xg": ext_a.get("xg"),
                "xga": ext_a.get("xga"),
                "inj": ext_a.get("inj"),
                "lineup": ext_a.get("lineup"),
                "susp": ext_a.get("susp"),
            }
        )
        home_hist_pts[home].append(h_pts_now)
        away_hist_pts[away].append(a_pts_now)

        # travel + capacity
        if team_geo:
            h = team_geo.get(normalize_team_name(home))
            a = team_geo.get(normalize_team_name(away))
        else:
            h = None
            a = None
        cap_home.append(h.get("capacity") if h else None)
        if h and a and all(k in h for k in ["lat", "lon"]) and all(k in a for k in ["lat", "lon"]):
            travel_km.append(haversine_km(a["lat"], a["lon"], h["lat"], h["lon"]))
        else:
            travel_km.append(None)

    df["RestDiff"] = rest_diff
    df["FormPtsDiff"] = form_pts_diff
    df["GDDiff"] = gd_diff
    df["SOTDiff"] = sot_diff
    df["ShotsDiff"] = shots_diff
    df["ShotShareDiff"] = shot_share_diff
    df["ShotAccDiff"] = shot_acc_diff
    df["SHGDDiff"] = shgd_diff
    df["CardDiff"] = card_diff
    df["HomeAwayPtsDiff"] = home_away_pts_diff
    df["GFDiff"] = gf_diff
    df["GADiff"] = ga_diff
    df["XGDiff"] = xg_diff
    df["XGADiff"] = xga_diff
    df["InjuryDiff"] = inj_diff
    df["LineupDiff"] = lineup_diff
    df["SuspDiff"] = susp_diff
    df["WeatherTemp"] = weather_temp
    df["WeatherWind"] = weather_wind
    df["WeatherRain"] = weather_rain
    df["WeatherHumidity"] = weather_humidity
    df["TravelKm"] = travel_km
    df["CapHome"] = cap_home
    df["AbsFormPtsDiff"] = pd.to_numeric(df["FormPtsDiff"], errors="coerce").abs()
    df["AbsGDDiff"] = pd.to_numeric(df["GDDiff"], errors="coerce").abs()
    df["AbsSOTDiff"] = pd.to_numeric(df["SOTDiff"], errors="coerce").abs()
    df["AbsShotsDiff"] = pd.to_numeric(df["ShotsDiff"], errors="coerce").abs()
    df["AbsShotShareDiff"] = pd.to_numeric(df["ShotShareDiff"], errors="coerce").abs()
    df["AbsShotAccDiff"] = pd.to_numeric(df["ShotAccDiff"], errors="coerce").abs()
    df["AbsSHGDDiff"] = pd.to_numeric(df["SHGDDiff"], errors="coerce").abs()
    df["AbsGFDiff"] = pd.to_numeric(df["GFDiff"], errors="coerce").abs()
    df["AbsGADiff"] = pd.to_numeric(df["GADiff"], errors="coerce").abs()
    df["AbsSuspDiff"] = pd.to_numeric(df["SuspDiff"], errors="coerce").abs()
    df["AbsXGDiff"] = pd.to_numeric(df["XGDiff"], errors="coerce").abs()
    df["AbsXGADiff"] = pd.to_numeric(df["XGADiff"], errors="coerce").abs()
    df["AbsInjuryDiff"] = pd.to_numeric(df["InjuryDiff"], errors="coerce").abs()
    df["AbsLineupDiff"] = pd.to_numeric(df["LineupDiff"], errors="coerce").abs()
    df["AbsRestDiff"] = pd.to_numeric(df["RestDiff"], errors="coerce").abs()
    df["AbsHomeAwayPtsDiff"] = pd.to_numeric(df["HomeAwayPtsDiff"], errors="coerce").abs()

    df = add_season_phase_features(df)
    return df


def build_pre_match_features(
    df: pd.DataFrame,
    odds_cols: Tuple[str, str, str],
    window: int,
    team_geo: Dict[str, Dict[str, float]] | None = None,
    external_features: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    # Computes rolling features for both finished and upcoming matches (no leakage).
    df = df.copy()
    df["Date"] = _parse_date_series(df["Date"])
    df = df.dropna(subset=["Date"]).sort_values("Date").reset_index(drop=True)

    raw_h = 1.0 / df[odds_cols[0]]
    raw_d = 1.0 / df[odds_cols[1]]
    raw_a = 1.0 / df[odds_cols[2]]
    s = raw_h + raw_d + raw_a
    p_h = raw_h / s
    p_d = raw_d / s
    p_a = raw_a / s

    probs = pd.DataFrame({"H": p_h, "D": p_d, "A": p_a})
    df["Pred"] = probs.apply(lambda r: r.idxmax() if r.notna().any() else None, axis=1)
    df["Conf"] = probs.max(axis=1)
    df["ProbD"] = p_d

    def second_prob(row):
        vals = [v for v in row.values if pd.notna(v)]
        if len(vals) < 2:
            return None
        vals.sort()
        return vals[-2]

    df["ProbSecond"] = probs.apply(second_prob, axis=1)
    df["ProbMargin"] = df["Conf"] - df["ProbSecond"]

    raw_probs = pd.DataFrame({"H": raw_h, "D": raw_d, "A": raw_a})
    df["RawPred"] = raw_probs.apply(lambda r: r.idxmax() if r.notna().any() else None, axis=1)
    df["RawConf"] = raw_probs.max(axis=1)
    df["RawSecond"] = raw_probs.apply(second_prob, axis=1)
    df["RawMargin"] = df["RawConf"] - df["RawSecond"]

    # clear predictions when odds are missing
    missing_odds = probs.isna().all(axis=1)
    df.loc[missing_odds, ["Pred", "Conf", "ProbD", "ProbSecond", "ProbMargin", "RawPred", "RawConf", "RawSecond", "RawMargin"]] = None

    # Referee draw rate (rolling, update only on finished matches)
    ref_stats: Dict[str, Dict[str, int]] = {}
    league_draws: List[int] = []
    ref_draw_rates = []

    # external map
    ext_map = _build_external_map(external_features)

    def ext_lookup(team: str, opp: str, date_val):
        if not ext_map or date_val is None:
            return {}
        key = (normalize_team_name(team), normalize_team_name(opp), date_val)
        if key in ext_map:
            return ext_map[key]
        key2 = (normalize_team_name(team), "", date_val)
        return ext_map.get(key2, {})

    last_date: Dict[str, pd.Timestamp] = {}
    team_hist: Dict[str, List[Dict[str, float]]] = {}
    home_hist_pts: Dict[str, List[float]] = {}
    away_hist_pts: Dict[str, List[float]] = {}

    rest_diff = []
    form_pts_diff = []
    gd_diff = []
    sot_diff = []
    shots_diff = []
    shot_share_diff = []
    shot_acc_diff = []
    shgd_diff = []
    card_diff = []
    home_away_pts_diff = []
    gf_diff = []
    ga_diff = []
    xg_diff = []
    xga_diff = []
    inj_diff = []
    lineup_diff = []
    susp_diff = []
    weather_temp = []
    weather_wind = []
    weather_rain = []
    weather_humidity = []
    travel_km = []
    cap_home = []

    def haversine_km(lat1, lon1, lat2, lon2):
        import math

        r = 6371.0
        phi1 = math.radians(lat1)
        phi2 = math.radians(lat2)
        dphi = math.radians(lat2 - lat1)
        dl = math.radians(lon2 - lon1)
        a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dl / 2) ** 2
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
        return r * c

    def rolling_mean(vals: List[float]) -> float | None:
        if len(vals) < window:
            return None
        return float(sum(vals[-window:]) / window)

    for _, r in df.iterrows():
        home = r["HomeTeam"] if "HomeTeam" in df.columns else r.get("Home", "")
        away = r["AwayTeam"] if "AwayTeam" in df.columns else r.get("Away", "")
        finished = pd.notna(r.get("FTHG")) and pd.notna(r.get("FTAG"))

        ext_h = ext_lookup(home, away, r["Date"].date())
        ext_a = ext_lookup(away, home, r["Date"].date())

        # referee draw rate
        ldr = (sum(league_draws) / len(league_draws)) if league_draws else 0.30
        ref = r.get("Referee")
        if pd.notna(ref) and ref in ref_stats and ref_stats[ref]["n"] > 0:
            rdr = ref_stats[ref]["draws"] / ref_stats[ref]["n"]
        else:
            rdr = ldr
        ref_draw_rates.append(rdr)

        # rest
        rest_h = (r["Date"] - last_date[home]).days if home in last_date else None
        rest_a = (r["Date"] - last_date[away]).days if away in last_date else None
        if rest_h is None or rest_a is None:
            rest_diff.append(0)
        else:
            rest_diff.append(rest_h - rest_a)

        # form stats (before updating history)
        if home not in team_hist:
            team_hist[home] = []
        if away not in team_hist:
            team_hist[away] = []
        if home not in home_hist_pts:
            home_hist_pts[home] = []
        if away not in away_hist_pts:
            away_hist_pts[away] = []

        def extract_mean(team: str, key: str) -> float | None:
            vals = [m[key] for m in team_hist[team] if m.get(key) is not None]
            return rolling_mean(vals) if vals else None

        h_pts = extract_mean(home, "pts")
        a_pts = extract_mean(away, "pts")
        h_gd = extract_mean(home, "gd")
        a_gd = extract_mean(away, "gd")
        h_sot = extract_mean(home, "sot")
        a_sot = extract_mean(away, "sot")
        h_shots = extract_mean(home, "shots")
        a_shots = extract_mean(away, "shots")
        h_shot_share = extract_mean(home, "shot_share")
        a_shot_share = extract_mean(away, "shot_share")
        h_shot_acc = extract_mean(home, "shot_acc")
        a_shot_acc = extract_mean(away, "shot_acc")
        h_shgd = extract_mean(home, "shgd")
        a_shgd = extract_mean(away, "shgd")
        h_card = extract_mean(home, "cards")
        a_card = extract_mean(away, "cards")
        h_gf = extract_mean(home, "gf")
        a_gf = extract_mean(away, "gf")
        h_ga = extract_mean(home, "ga")
        a_ga = extract_mean(away, "ga")
        h_xg = extract_mean(home, "xg")
        a_xg = extract_mean(away, "xg")
        h_xga = extract_mean(home, "xga")
        a_xga = extract_mean(away, "xga")
        h_inj = extract_mean(home, "inj")
        a_inj = extract_mean(away, "inj")
        h_lineup = extract_mean(home, "lineup")
        a_lineup = extract_mean(away, "lineup")
        h_susp = extract_mean(home, "susp")
        a_susp = extract_mean(away, "susp")

        form_pts_diff.append(h_pts - a_pts if h_pts is not None and a_pts is not None else None)
        gd_diff.append(h_gd - a_gd if h_gd is not None and a_gd is not None else None)
        sot_diff.append(h_sot - a_sot if h_sot is not None and a_sot is not None else None)
        shots_diff.append(h_shots - a_shots if h_shots is not None and a_shots is not None else None)
        shot_share_diff.append(h_shot_share - a_shot_share if h_shot_share is not None and a_shot_share is not None else None)
        shot_acc_diff.append(h_shot_acc - a_shot_acc if h_shot_acc is not None and a_shot_acc is not None else None)
        shgd_diff.append(h_shgd - a_shgd if h_shgd is not None and a_shgd is not None else None)
        card_diff.append(h_card - a_card if h_card is not None and a_card is not None else None)
        gf_diff.append(h_gf - a_gf if h_gf is not None and a_gf is not None else None)
        ga_diff.append(h_ga - a_ga if h_ga is not None and a_ga is not None else None)
        xg_diff.append(h_xg - a_xg if h_xg is not None and a_xg is not None else None)
        xga_diff.append(h_xga - a_xga if h_xga is not None and a_xga is not None else None)
        inj_diff.append(h_inj - a_inj if h_inj is not None and a_inj is not None else None)
        lineup_diff.append(h_lineup - a_lineup if h_lineup is not None and a_lineup is not None else None)
        susp_diff.append(h_susp - a_susp if h_susp is not None and a_susp is not None else None)

        def pick_weather(key: str):
            v = ext_h.get(key)
            if v is None:
                v = ext_a.get(key)
            return v

        weather_temp.append(pick_weather("temp"))
        weather_wind.append(pick_weather("wind"))
        weather_rain.append(pick_weather("rain"))
        weather_humidity.append(pick_weather("humidity"))

        # home vs away form (crowd influence proxy)
        h_home_pts = rolling_mean(home_hist_pts[home])
        a_away_pts = rolling_mean(away_hist_pts[away])
        if h_home_pts is not None and a_away_pts is not None:
            home_away_pts_diff.append(h_home_pts - a_away_pts)
        else:
            home_away_pts_diff.append(None)

        # update team history only if finished
        if finished:
            if r.FTHG > r.FTAG:
                h_pts_now, a_pts_now = 3.0, 0.0
            elif r.FTHG < r.FTAG:
                h_pts_now, a_pts_now = 0.0, 3.0
            else:
                h_pts_now, a_pts_now = 1.0, 1.0
            h_gd_now = float(r.FTHG - r.FTAG)
            a_gd_now = float(r.FTAG - r.FTHG)
            h_sot_now = float(r["HST"]) if "HST" in df.columns and pd.notna(r.get("HST")) else None
            a_sot_now = float(r["AST"]) if "AST" in df.columns and pd.notna(r.get("AST")) else None
            h_shots_now = float(r["HS"]) if "HS" in df.columns and pd.notna(r.get("HS")) else None
            a_shots_now = float(r["AS"]) if "AS" in df.columns and pd.notna(r.get("AS")) else None
            h_shot_share_now = None
            a_shot_share_now = None
            if h_shots_now is not None and a_shots_now is not None and (h_shots_now + a_shots_now) > 0:
                total_shots = h_shots_now + a_shots_now
                h_shot_share_now = h_shots_now / total_shots
                a_shot_share_now = a_shots_now / total_shots
            h_shot_acc_now = None
            a_shot_acc_now = None
            if h_shots_now is not None and h_shots_now > 0 and h_sot_now is not None:
                h_shot_acc_now = h_sot_now / h_shots_now
            if a_shots_now is not None and a_shots_now > 0 and a_sot_now is not None:
                a_shot_acc_now = a_sot_now / a_shots_now
            h_shgd_now = None
            a_shgd_now = None
            if pd.notna(r.get("HTHG")) and pd.notna(r.get("HTAG")):
                h_shgd_now = float((r.FTHG - r.FTAG) - (r.HTHG - r.HTAG))
                a_shgd_now = -h_shgd_now
            h_cards_now = None
            a_cards_now = None
            if "HY" in df.columns and "HR" in df.columns and pd.notna(r.get("HY")) and pd.notna(r.get("HR")):
                h_cards_now = float(r["HY"] + r["HR"])
            if "AY" in df.columns and "AR" in df.columns and pd.notna(r.get("AY")) and pd.notna(r.get("AR")):
                a_cards_now = float(r["AY"] + r["AR"])

            team_hist[home].append(
                {
                    "pts": h_pts_now,
                    "gd": h_gd_now,
                    "sot": h_sot_now,
                    "shots": h_shots_now,
                    "shot_share": h_shot_share_now,
                    "shot_acc": h_shot_acc_now,
                    "shgd": h_shgd_now,
                    "cards": h_cards_now,
                    "gf": float(r.FTHG),
                    "ga": float(r.FTAG),
                    "xg": ext_h.get("xg"),
                    "xga": ext_h.get("xga"),
                    "inj": ext_h.get("inj"),
                    "lineup": ext_h.get("lineup"),
                    "susp": ext_h.get("susp"),
                }
            )
            team_hist[away].append(
                {
                    "pts": a_pts_now,
                    "gd": a_gd_now,
                    "sot": a_sot_now,
                    "shots": a_shots_now,
                    "shot_share": a_shot_share_now,
                    "shot_acc": a_shot_acc_now,
                    "shgd": a_shgd_now,
                    "cards": a_cards_now,
                    "gf": float(r.FTAG),
                    "ga": float(r.FTHG),
                    "xg": ext_a.get("xg"),
                    "xga": ext_a.get("xga"),
                    "inj": ext_a.get("inj"),
                    "lineup": ext_a.get("lineup"),
                    "susp": ext_a.get("susp"),
                }
            )
            home_hist_pts[home].append(h_pts_now)
            away_hist_pts[away].append(a_pts_now)
            last_date[home] = r["Date"]
            last_date[away] = r["Date"]

            if pd.notna(ref):
                if ref not in ref_stats:
                    ref_stats[ref] = {"n": 0, "draws": 0}
                ref_stats[ref]["n"] += 1
                if r.FTHG == r.FTAG:
                    ref_stats[ref]["draws"] += 1
                league_draws.append(1 if r.FTHG == r.FTAG else 0)


        # travel + capacity
        if team_geo:
            h = team_geo.get(normalize_team_name(home))
            a = team_geo.get(normalize_team_name(away))
        else:
            h = None
            a = None
        cap_home.append(h.get("capacity") if h else None)
        if h and a and all(k in h for k in ["lat", "lon"]) and all(k in a for k in ["lat", "lon"]):
            travel_km.append(haversine_km(a["lat"], a["lon"], h["lat"], h["lon"]))
        else:
            travel_km.append(None)

    df["RefDrawRate"] = ref_draw_rates
    df["RestDiff"] = rest_diff
    df["FormPtsDiff"] = form_pts_diff
    df["GDDiff"] = gd_diff
    df["SOTDiff"] = sot_diff
    df["ShotsDiff"] = shots_diff
    df["ShotShareDiff"] = shot_share_diff
    df["ShotAccDiff"] = shot_acc_diff
    df["SHGDDiff"] = shgd_diff
    df["CardDiff"] = card_diff
    df["HomeAwayPtsDiff"] = home_away_pts_diff
    df["GFDiff"] = gf_diff
    df["GADiff"] = ga_diff
    df["XGDiff"] = xg_diff
    df["XGADiff"] = xga_diff
    df["InjuryDiff"] = inj_diff
    df["LineupDiff"] = lineup_diff
    df["SuspDiff"] = susp_diff
    df["WeatherTemp"] = weather_temp
    df["WeatherWind"] = weather_wind
    df["WeatherRain"] = weather_rain
    df["WeatherHumidity"] = weather_humidity
    df["TravelKm"] = travel_km
    df["CapHome"] = cap_home
    df["AbsFormPtsDiff"] = pd.to_numeric(df["FormPtsDiff"], errors="coerce").abs()
    df["AbsGDDiff"] = pd.to_numeric(df["GDDiff"], errors="coerce").abs()
    df["AbsSOTDiff"] = pd.to_numeric(df["SOTDiff"], errors="coerce").abs()
    df["AbsShotsDiff"] = pd.to_numeric(df["ShotsDiff"], errors="coerce").abs()
    df["AbsShotShareDiff"] = pd.to_numeric(df["ShotShareDiff"], errors="coerce").abs()
    df["AbsShotAccDiff"] = pd.to_numeric(df["ShotAccDiff"], errors="coerce").abs()
    df["AbsSHGDDiff"] = pd.to_numeric(df["SHGDDiff"], errors="coerce").abs()
    df["AbsGFDiff"] = pd.to_numeric(df["GFDiff"], errors="coerce").abs()
    df["AbsGADiff"] = pd.to_numeric(df["GADiff"], errors="coerce").abs()
    df["AbsSuspDiff"] = pd.to_numeric(df["SuspDiff"], errors="coerce").abs()
    df["AbsXGDiff"] = pd.to_numeric(df["XGDiff"], errors="coerce").abs()
    df["AbsXGADiff"] = pd.to_numeric(df["XGADiff"], errors="coerce").abs()
    df["AbsInjuryDiff"] = pd.to_numeric(df["InjuryDiff"], errors="coerce").abs()
    df["AbsLineupDiff"] = pd.to_numeric(df["LineupDiff"], errors="coerce").abs()
    df["AbsRestDiff"] = pd.to_numeric(df["RestDiff"], errors="coerce").abs()
    df["AbsHomeAwayPtsDiff"] = pd.to_numeric(df["HomeAwayPtsDiff"], errors="coerce").abs()

    df = add_season_phase_features(df)
    return df


def evaluate_strategies(
    df: pd.DataFrame,
    thresholds: List[float],
    target_acc: float,
    extended: bool = False,
    allow_draws: bool = False,
) -> Dict[str, Any]:
    # Strategy grid (compact): base thresholds + optional filters and their pairs
    if extended:
        ref_max_list = [0.40, 0.35, 0.30]
        rest_min_list = [0, 1]
        form_min_list = [0.3, 0.5]
        gd_min_list = [0.3, 0.5]
        sot_min_list = [0.5, 1.0]
        shots_min_list = [1.0, 2.0]
        shot_share_min_list = [0.03, 0.05]
        shot_acc_min_list = [0.03, 0.05]
        shgd_min_list = [0.1, 0.2]
        xg_min_list = [0.2, 0.4]
        xga_min_list = [0.2, 0.4]
        injury_min_list = [0.2, 0.4]
        susp_min_list = [0.2, 0.4]
        lineup_min_list = [0.05, 0.1]
        travel_min_list = [400, 600]
        cap_min_list = [25000, 35000]
        home_adv_min_list = [0.2, 0.3]
        draw_prob_max_list = [0.30, 0.28]
        draw_prob_min_list = [0.30, 0.32]
        margin_min_list = [0.06, 0.08]
        gf_min_list = [0.3]
        ga_min_list = [0.2]
        conf_draw_list = [0.30, 0.32, 0.34]
        draw_balance_pts_max_list = [0.3, 0.5]
        draw_balance_gd_max_list = [0.3, 0.5]
        draw_balance_sot_max_list = [1.0]
        draw_balance_rest_max_list = [1]
        draw_balance_homeaway_max_list = [0.3]
        raw_conf_min_list = [0.60, 0.62, 0.65]
    else:
        ref_max_list = [0.35]
        rest_min_list = [0, 1]
        form_min_list = [0.4]
        gd_min_list = [0.4]
        sot_min_list = [1.0]
        shots_min_list: List[float] = []
        shot_share_min_list: List[float] = []
        shot_acc_min_list: List[float] = []
        shgd_min_list: List[float] = []
        xg_min_list: List[float] = []
        xga_min_list: List[float] = []
        injury_min_list: List[float] = []
        susp_min_list: List[float] = []
        lineup_min_list: List[float] = []
        travel_min_list = [600]
        cap_min_list = [30000]
        home_adv_min_list = [0.3]
        draw_prob_max_list = [0.30]
        draw_prob_min_list: List[float] = []
        margin_min_list = [0.06]
        gf_min_list: List[float] = []
        ga_min_list: List[float] = []
        conf_draw_list: List[float] = []
        draw_balance_pts_max_list: List[float] = []
        draw_balance_gd_max_list: List[float] = []
        draw_balance_sot_max_list: List[float] = []
        draw_balance_rest_max_list: List[float] = []
        draw_balance_homeaway_max_list: List[float] = []
        raw_conf_min_list: List[float] = []

    # reduce grids when data is missing
    if df["SOTDiff"].isna().all():
        sot_min_list = []
    if df["ShotsDiff"].isna().all():
        shots_min_list = []
    if df["ShotShareDiff"].isna().all():
        shot_share_min_list = []
    if df["ShotAccDiff"].isna().all():
        shot_acc_min_list = []
    if df["SHGDDiff"].isna().all():
        shgd_min_list = []
    if df["XGDiff"].isna().all():
        xg_min_list = []
    if df["XGADiff"].isna().all():
        xga_min_list = []
    if df["InjuryDiff"].isna().all():
        injury_min_list = []
    if df["SuspDiff"].isna().all():
        susp_min_list = []
    if df["LineupDiff"].isna().all():
        lineup_min_list = []
    if df["HomeAwayPtsDiff"].isna().all():
        home_adv_min_list = []
    if df["TravelKm"].isna().all():
        travel_min_list = []
    if df["CapHome"].isna().all():
        cap_min_list = []
    if df["GFDiff"].isna().all():
        gf_min_list = []
    if df["GADiff"].isna().all():
        ga_min_list = []
    if df["AbsSOTDiff"].isna().all():
        draw_balance_sot_max_list = []
    if df["AbsHomeAwayPtsDiff"].isna().all():
        draw_balance_homeaway_max_list = []

    if not allow_draws:
        conf_draw_list = []
        draw_prob_min_list = []
        draw_balance_pts_max_list = []
        draw_balance_gd_max_list = []
        draw_balance_sot_max_list = []
        draw_balance_rest_max_list = []
        draw_balance_homeaway_max_list = []

    best = None

    def consider(strategy: str, params: Dict[str, Any], idx_mask):
        nonlocal best
        if idx_mask.sum() == 0:
            return
        acc = (df.loc[idx_mask, "Pred"] == df.loc[idx_mask, "Actual"]).mean()
        cov = idx_mask.mean()
        if acc < target_acc:
            return
        if best is None or cov > best["coverage"] or (cov == best["coverage"] and acc > best["accuracy"]):
            best = {
                "strategy": strategy,
                "params": params,
                "coverage": cov,
                "accuracy": acc,
                "n": int(idx_mask.sum()),
            }

    conf_draw_iter = ([None] + conf_draw_list) if conf_draw_list else [None]
    raw_conf_iter = ([None] + raw_conf_min_list) if raw_conf_min_list else [None]
    for conf_home in thresholds:
        for conf_away in thresholds:
            for conf_draw in conf_draw_iter:
                for raw_conf in raw_conf_iter:
                    base_idx = (
                        ((df["Pred"] == "H") & (df["Conf"] >= conf_home)) |
                        ((df["Pred"] == "A") & (df["Conf"] >= conf_away))
                    )
                    base_params: Dict[str, Any] = {"conf_home": conf_home, "conf_away": conf_away}
                    if conf_draw is not None:
                        base_idx = base_idx | ((df["Pred"] == "D") & (df["Conf"] >= conf_draw))
                        base_params = {**base_params, "conf_draw": conf_draw}
                    if raw_conf is not None:
                        raw_idx = (
                            (df["Pred"] != "D") &
                            (df["RawPred"] == df["Pred"]) &
                            (df["RawConf"] >= raw_conf)
                        )
                        base_idx = base_idx | raw_idx
                        base_params = {**base_params, "raw_conf_min": raw_conf}

                    consider("grid", base_params, base_idx)

                    filters: List[Tuple[Any, Dict[str, Any]]] = []

                for dmax in draw_prob_max_list:
                    mask = (df["Pred"] == "D") | (df["ProbD"] <= dmax)
                    filters.append((mask, {"draw_prob_max": dmax}))
                for dmin in draw_prob_min_list:
                    mask = (df["Pred"] != "D") | (df["ProbD"] >= dmin)
                    filters.append((mask, {"draw_prob_min": dmin}))
            for mmin in margin_min_list:
                mask = df["ProbMargin"] >= mmin
                filters.append((mask, {"prob_margin_min": mmin}))
            for rmax in ref_max_list:
                mask = df["RefDrawRate"] <= rmax
                filters.append((mask, {"ref_draw_max": rmax}))
            for rmin in rest_min_list:
                mask = (
                    (df["Pred"] == "D") |
                    ((df["Pred"] == "H") & (df["RestDiff"] >= rmin)) |
                    ((df["Pred"] == "A") & (df["RestDiff"] <= -rmin))
                )
                filters.append((mask, {"rest_min": rmin}))
            for fmin in form_min_list:
                mask = (
                    (df["Pred"] == "D") |
                    ((df["Pred"] == "H") & (df["FormPtsDiff"] >= fmin)) |
                    ((df["Pred"] == "A") & (df["FormPtsDiff"] <= -fmin))
                )
                filters.append((mask, {"form_pts_min": fmin}))
            for gfmin in gf_min_list:
                mask = (
                    (df["Pred"] == "D") |
                    ((df["Pred"] == "H") & (df["GFDiff"] >= gfmin)) |
                    ((df["Pred"] == "A") & (df["GFDiff"] <= -gfmin))
                )
                filters.append((mask, {"gf_min": gfmin}))
            for gamin in ga_min_list:
                mask = (
                    (df["Pred"] == "D") |
                    ((df["Pred"] == "H") & (df["GADiff"] <= -gamin)) |
                    ((df["Pred"] == "A") & (df["GADiff"] >= gamin))
                )
                filters.append((mask, {"ga_min": gamin}))
            for gdmin in gd_min_list:
                mask = (
                    (df["Pred"] == "D") |
                    ((df["Pred"] == "H") & (df["GDDiff"] >= gdmin)) |
                    ((df["Pred"] == "A") & (df["GDDiff"] <= -gdmin))
                )
                filters.append((mask, {"gd_min": gdmin}))
            for sotmin in sot_min_list:
                mask = (
                    (df["Pred"] == "D") |
                    ((df["Pred"] == "H") & (df["SOTDiff"] >= sotmin)) |
                    ((df["Pred"] == "A") & (df["SOTDiff"] <= -sotmin))
                )
                filters.append((mask, {"sot_min": sotmin}))
            for shmin in shots_min_list:
                mask = (
                    (df["Pred"] == "D") |
                    ((df["Pred"] == "H") & (df["ShotsDiff"] >= shmin)) |
                    ((df["Pred"] == "A") & (df["ShotsDiff"] <= -shmin))
                )
                filters.append((mask, {"shots_min": shmin}))
            for ssmin in shot_share_min_list:
                mask = (
                    (df["Pred"] == "D") |
                    ((df["Pred"] == "H") & (df["ShotShareDiff"] >= ssmin)) |
                    ((df["Pred"] == "A") & (df["ShotShareDiff"] <= -ssmin))
                )
                filters.append((mask, {"shot_share_min": ssmin}))
            for samin in shot_acc_min_list:
                mask = (
                    (df["Pred"] == "D") |
                    ((df["Pred"] == "H") & (df["ShotAccDiff"] >= samin)) |
                    ((df["Pred"] == "A") & (df["ShotAccDiff"] <= -samin))
                )
                filters.append((mask, {"shot_acc_min": samin}))
            for shgmin in shgd_min_list:
                mask = (
                    (df["Pred"] == "D") |
                    ((df["Pred"] == "H") & (df["SHGDDiff"] >= shgmin)) |
                    ((df["Pred"] == "A") & (df["SHGDDiff"] <= -shgmin))
                )
                filters.append((mask, {"shgd_min": shgmin}))
            for xgmin in xg_min_list:
                mask = (
                    (df["Pred"] == "D") |
                    ((df["Pred"] == "H") & (df["XGDiff"] >= xgmin)) |
                    ((df["Pred"] == "A") & (df["XGDiff"] <= -xgmin))
                )
                filters.append((mask, {"xg_min": xgmin}))
            for xgamin in xga_min_list:
                mask = (
                    (df["Pred"] == "D") |
                    ((df["Pred"] == "H") & (df["XGADiff"] <= -xgamin)) |
                    ((df["Pred"] == "A") & (df["XGADiff"] >= xgamin))
                )
                filters.append((mask, {"xga_min": xgamin}))
            for injmin in injury_min_list:
                mask = (
                    (df["Pred"] == "D") |
                    ((df["Pred"] == "H") & (df["InjuryDiff"] <= -injmin)) |
                    ((df["Pred"] == "A") & (df["InjuryDiff"] >= injmin))
                )
                filters.append((mask, {"injury_min": injmin}))
            for smin in susp_min_list:
                mask = (
                    (df["Pred"] == "D") |
                    ((df["Pred"] == "H") & (df["SuspDiff"] <= -smin)) |
                    ((df["Pred"] == "A") & (df["SuspDiff"] >= smin))
                )
                filters.append((mask, {"susp_min": smin}))
            for lmin in lineup_min_list:
                mask = (
                    (df["Pred"] == "D") |
                    ((df["Pred"] == "H") & (df["LineupDiff"] >= lmin)) |
                    ((df["Pred"] == "A") & (df["LineupDiff"] <= -lmin))
                )
                filters.append((mask, {"lineup_min": lmin}))
            for hav in home_adv_min_list:
                mask = (
                    (df["Pred"] == "D") |
                    ((df["Pred"] == "H") & df["HomeAwayPtsDiff"].notna() & (df["HomeAwayPtsDiff"] >= hav)) |
                    ((df["Pred"] == "A") & df["HomeAwayPtsDiff"].notna() & (df["HomeAwayPtsDiff"] <= -hav))
                )
                filters.append((mask, {"home_adv_min": hav}))
            for tmin in travel_min_list:
                mask = (
                    (df["Pred"] == "D") |
                    ((df["Pred"] == "H") & df["TravelKm"].notna() & (df["TravelKm"] >= tmin)) |
                    (df["Pred"] == "A")
                )
                filters.append((mask, {"travel_min_km": tmin}))
            for cmin in cap_min_list:
                mask = (
                    (df["Pred"] == "D") |
                    ((df["Pred"] == "H") & df["CapHome"].notna() & (df["CapHome"] >= cmin)) |
                    (df["Pred"] == "A")
                )
                filters.append((mask, {"cap_home_min": cmin}))
            for dbp in draw_balance_pts_max_list:
                mask = (df["Pred"] != "D") | (df["AbsFormPtsDiff"] <= dbp)
                filters.append((mask, {"draw_pts_max": dbp}))
            for dbg in draw_balance_gd_max_list:
                mask = (df["Pred"] != "D") | (df["AbsGDDiff"] <= dbg)
                filters.append((mask, {"draw_gd_max": dbg}))
            for dbs in draw_balance_sot_max_list:
                mask = (df["Pred"] != "D") | (df["AbsSOTDiff"] <= dbs)
                filters.append((mask, {"draw_sot_max": dbs}))
            for dbr in draw_balance_rest_max_list:
                mask = (df["Pred"] != "D") | (df["AbsRestDiff"] <= dbr)
                filters.append((mask, {"draw_rest_max": dbr}))
            for dbh in draw_balance_homeaway_max_list:
                mask = (df["Pred"] != "D") | (df["AbsHomeAwayPtsDiff"] <= dbh)
                filters.append((mask, {"draw_homeaway_max": dbh}))

            # single filter
            for mask, p in filters:
                consider("grid", {**base_params, **p}, base_idx & mask)

            # pairs of filters (limited for speed)
            for i in range(len(filters)):
                mask_i, p_i = filters[i]
                for j in range(i + 1, len(filters)):
                    mask_j, p_j = filters[j]
                    consider("grid", {**base_params, **p_i, **p_j}, base_idx & mask_i & mask_j)

    return {"best": best}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", default="2526")
    ap.add_argument("--out", default="reports/europe_summary_enhanced.md")
    ap.add_argument("--thresholds", default="")
    ap.add_argument("--codes", default="")
    ap.add_argument("--min-coverage", type=float, default=0.06)
    ap.add_argument("--target-acc", type=float, default=0.90)
    ap.add_argument("--window", type=int, default=5)
    ap.add_argument("--team-stadiums", default="data/processed/team_stadiums.csv")
    ap.add_argument("--external", default="data/processed/external_features.csv")
    ap.add_argument("--force-refresh", action="store_true", help="Refresh existing football-data CSVs instead of trusting cached files.")
    ap.add_argument("--summary-csv", default="", help="Optional CSV output path for the summary rows.")
    args = ap.parse_args()

    season_code = str(args.season)
    season_str = season_code_to_str(season_code)
    if args.thresholds:
        thresholds = [float(x) for x in args.thresholds.split(",") if x]
    else:
        thresholds = [0.60, 0.65, 0.70, 0.75]
    thresholds_ext = [0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70, 0.75]
    codes_filter = {c.strip() for c in args.codes.split(",") if c.strip()}

    base_main = "https://www.football-data.co.uk/mmz4281"
    base_extra = "https://www.football-data.co.uk/new"

    main_leagues = [
        ("E0", "England Premier League"),
        ("E1", "England Championship"),
        ("E2", "England League One"),
        ("E3", "England League Two"),
        ("EC", "England Conference"),
        ("SC0", "Scotland Premier League"),
        ("SC1", "Scotland Division 1"),
        ("SC2", "Scotland Division 2"),
        ("SC3", "Scotland Division 3"),
        ("D1", "Germany Bundesliga 1"),
        ("D2", "Germany Bundesliga 2"),
        ("I1", "Italy Serie A"),
        ("I2", "Italy Serie B"),
        ("SP1", "Spain La Liga"),
        ("SP2", "Spain Segunda"),
        ("F1", "France Ligue 1"),
        ("F2", "France Ligue 2"),
        ("N1", "Netherlands Eredivisie"),
        ("B1", "Belgium Pro League"),
        ("P1", "Portugal Primeira"),
        ("T1", "Turkey Super Lig"),
        ("G1", "Greece Super League"),
    ]

    extra_leagues = [
        ("AUT", "Austria Bundesliga"),
        ("DNK", "Denmark Superliga"),
        ("FIN", "Finland Veikkausliiga"),
        ("IRL", "Ireland Premier Division"),
        ("NOR", "Norway Eliteserien"),
        ("POL", "Poland Ekstraklasa"),
        ("ROU", "Romania Liga 1"),
        ("RUS", "Russia Premier League"),
        ("SWE", "Sweden Allsvenskan"),
        ("SWZ", "Switzerland Super League"),
    ]

    raw_dir = Path("data/raw/football_data")
    report_dir = Path("reports")
    report_dir.mkdir(parents=True, exist_ok=True)

    # Load team geo mapping if available
    team_geo: Dict[str, Dict[str, float]] = {}
    team_stadiums_path = Path(args.team_stadiums)
    if team_stadiums_path.exists():
        tdf = pd.read_csv(team_stadiums_path)
        for _, r in tdf.iterrows():
            name = str(r.get("TeamName", "")).strip()
            if not name:
                continue
            key = normalize_team_name(name)
            lat = pd.to_numeric(r.get("Latitude"), errors="coerce")
            lon = pd.to_numeric(r.get("Longitude"), errors="coerce")
            cap = pd.to_numeric(r.get("Capacity"), errors="coerce")
            if pd.notna(lat) and pd.notna(lon):
                team_geo[key] = {
                    "lat": float(lat),
                    "lon": float(lon),
                    "capacity": float(cap) if pd.notna(cap) else None,
                }
            elif pd.notna(cap):
                team_geo[key] = {"capacity": float(cap)}

    ext_df = None
    external_path = Path(args.external)
    if external_path.exists():
        try:
            ext_df = pd.read_csv(external_path, encoding="utf-8-sig")
        except Exception:
            ext_df = None

    summary_rows = []

    def process(df: pd.DataFrame, code: str, name: str, source: str, season_label: str, last_date: str):
        odds_cols = pick_odds_cols(df)
        if not odds_cols:
            summary_rows.append({"Code": code, "League": name, "Source": source, "Status": "No odds columns"})
            return

        ctx = league_context(df)
        feat = build_match_features(df, odds_cols, args.window, team_geo, external_features=ext_df)
        if feat.empty:
            summary_rows.append({"Code": code, "League": name, "Source": source, "Status": "No finished matches"})
            return

        res = evaluate_strategies(feat, thresholds, args.target_acc, extended=False, allow_draws=False)
        best = res.get("best")
        extended_used = False
        if (best is None) or (best["coverage"] < args.min_coverage):
            res_ext = evaluate_strategies(feat, thresholds_ext, args.target_acc, extended=True, allow_draws=True)
            best_ext = res_ext.get("best")
            if best_ext and (best is None or best_ext["coverage"] > best["coverage"] or (best_ext["coverage"] == best["coverage"] and best_ext["accuracy"] > best["accuracy"])):
                best = best_ext
                extended_used = True
        if not best:
            summary_rows.append({"Code": code, "League": name, "Source": source, "Status": "No strategy ≥ target"})
            return

        # report
        report_path = report_dir / f"selective_{code}_enhanced.md"
        lines = []
        lines.append("# تقييم استراتيجية انتقائية محسّنة")
        lines.append(f"- الدوري: {name} ({code})")
        lines.append(f"- الموسم: {season_label}")
        lines.append(f"- عدد المباريات المكتملة: {len(feat)}")
        if last_date:
            lines.append(f"- آخر تاريخ في البيانات: {last_date}")
        lines.append(f"- أعمدة الاحتمالات المستخدمة: {odds_cols}")
        if ctx:
            lines.append("")
            lines.append("## خصائص الدوري (مؤشرات عامة)")
            if "home_win_rate" in ctx:
                lines.append(f"- نسبة فوز صاحب الأرض: {ctx['home_win_rate']*100:.1f}%")
            if "draw_rate" in ctx:
                lines.append(f"- نسبة التعادل: {ctx['draw_rate']*100:.1f}%")
            if "away_win_rate" in ctx:
                lines.append(f"- نسبة فوز الضيف: {ctx['away_win_rate']*100:.1f}%")
            if "avg_goals" in ctx:
                lines.append(f"- متوسط الأهداف/مباراة: {ctx['avg_goals']:.2f}")
            if "avg_yellow" in ctx:
                lines.append(f"- متوسط البطاقات الصفراء/مباراة: {ctx['avg_yellow']:.2f}")
            if "avg_red" in ctx:
                lines.append(f"- متوسط البطاقات الحمراء/مباراة: {ctx['avg_red']:.2f}")
            if "avg_shots" in ctx:
                lines.append(f"- متوسط التسديدات/مباراة: {ctx['avg_shots']:.2f}")
            if "avg_sot" in ctx:
                lines.append(f"- متوسط التسديدات على المرمى/مباراة: {ctx['avg_sot']:.2f}")
        lines.append("")
        lines.append(f"## الاستراتيجية المختارة (أعلى تغطية مع دقة ≥{args.target_acc*100:.0f}%)")
        lines.append(f"- النوع: {best['strategy']}")
        if extended_used:
            lines.append("- ملاحظة: تم استخدام بحث موسّع لأن التغطية كانت منخفضة")
        lines.append(f"- المعلمات: {best['params']}")
        lines.append(f"- التغطية: {best['coverage']*100:.1f}%")
        lines.append(f"- الدقة: {best['accuracy']*100:.1f}%")
        lines.append(f"- عدد المباريات: {best['n']}")
        report_path.write_text("\n".join(lines), encoding="utf-8")

        summary_rows.append(
            {
                "Code": code,
                "League": name,
                "Source": source,
                "Status": "ok",
                "Matches": len(feat),
                "LastDate": last_date,
                "Strategy": best["strategy"],
                "Params": best["params"],
                "Coverage": best["coverage"] * 100,
                "Accuracy": best["accuracy"] * 100,
                "HomeWinRate": ctx.get("home_win_rate", ""),
                "DrawRate": ctx.get("draw_rate", ""),
                "AwayWinRate": ctx.get("away_win_rate", ""),
                "AvgGoals": ctx.get("avg_goals", ""),
            }
        )

    # main leagues
    for code, name in main_leagues:
        if codes_filter and code not in codes_filter:
            continue
        url = f"{base_main}/{season_code}/{code}.csv"
        dest = raw_dir / f"{code}_{season_code}.csv"
        ok = fetch_csv(url, dest, force=args.force_refresh)
        if not ok:
            summary_rows.append({"Code": code, "League": name, "Source": "main", "Status": "missing"})
            continue
        df = pd.read_csv(dest, encoding="utf-8-sig")
        df = normalize_main(df)
        last_date = parse_last_date(df)
        process(df, code, name, "main", season_code, last_date)

    # extra leagues
    for code, name in extra_leagues:
        if codes_filter and code not in codes_filter:
            continue
        url = f"{base_extra}/{code}.csv"
        dest = raw_dir / f"{code}_all.csv"
        ok = fetch_csv(url, dest, force=args.force_refresh)
        if not ok:
            summary_rows.append({"Code": code, "League": name, "Source": "extra", "Status": "missing"})
            continue
        df = pd.read_csv(dest, encoding="utf-8-sig")
        df = normalize_extra(df, season_str)
        last_date = parse_last_date(df)
        process(df, code, name, "extra", season_str, last_date)

    summary_df = pd.DataFrame(summary_rows)
    summary_csv = Path(args.summary_csv) if args.summary_csv else report_dir / "europe_summary_enhanced.csv"
    summary_csv.parent.mkdir(parents=True, exist_ok=True)
    summary_df.to_csv(summary_csv, index=False)

    summary_md = Path(args.out)
    lines = []
    lines.append("# ملخص الاستراتيجية المحسّنة لجميع الدوريات الأوروبية")
    lines.append(f"- الموسم: {season_code} (للدوريات الرئيسية) / {season_str} (للدوريات الإضافية)")
    lines.append(f"- هدف الدقة: ≥{args.target_acc*100:.0f}% (اختيار أعلى تغطية يحقق الهدف)")
    lines.append("")
    lines.append("| الكود | الدوري | المصدر | الحالة | مباريات | آخر تاريخ | الاستراتيجية | التغطية | الدقة |")
    lines.append("|---|---|---|---|---:|---|---|---:|---:|")
    for _, r in summary_df.iterrows():
        lines.append(
            f"| {r.get('Code','')} | {r.get('League','')} | {r.get('Source','')} | {r.get('Status','')} | {r.get('Matches','')} | {r.get('LastDate','')} | {r.get('Strategy','')} | {r.get('Coverage','')} | {r.get('Accuracy','')} |"
        )

    summary_md.write_text("\n".join(lines), encoding="utf-8")

    print(f"saved: {summary_csv}")
    print(f"saved: {summary_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
