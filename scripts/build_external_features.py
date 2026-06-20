#!/usr/bin/env python3
"""Build a unified external features file for xG / injuries / lineups / suspensions / weather.

Expected input schemas (CSV):
- xg_matches.csv: Date, League, Season, HomeTeam, AwayTeam, HomeXG, AwayXG
- injuries.csv: Date, League, Team, Injuries
- lineups.csv: Date, League, Team, Lineup
 - suspensions.csv: Date, League, Team, Suspensions
 - weather.csv: Date, League, Team, Opp, WeatherTemp, WeatherWind, WeatherRain, WeatherHumidity
"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Optional

import pandas as pd


def read_csv(path: str) -> Optional[pd.DataFrame]:
    p = Path(path)
    if not p.exists():
        return None
    return pd.read_csv(p, encoding="utf-8-sig")


def normalize_cols(df: pd.DataFrame, mapping: dict) -> pd.DataFrame:
    cols = {c: c for c in df.columns}
    for src, dst in mapping.items():
        for c in list(cols.keys()):
            if c.lower() == src.lower():
                cols[c] = dst
    return df.rename(columns=cols)


def build_xg_long(df: pd.DataFrame) -> pd.DataFrame:
    df = normalize_cols(
        df,
        {
            "date": "Date",
            "league": "League",
            "season": "Season",
            "hometeam": "HomeTeam",
            "awayteam": "AwayTeam",
            "homexg": "HomeXG",
            "awayxg": "AwayXG",
        },
    )
    needed = {"Date", "HomeTeam", "AwayTeam", "HomeXG", "AwayXG"}
    if not needed.issubset(set(df.columns)):
        return pd.DataFrame()
    df["Date"] = pd.to_datetime(df["Date"], dayfirst=True, errors="coerce")
    rows = []
    for _, r in df.iterrows():
        if pd.isna(r.get("Date")):
            continue
        home = r.get("HomeTeam")
        away = r.get("AwayTeam")
        if not home or not away:
            continue
        league = r.get("League")
        rows.append(
            {
                "Date": r["Date"].date().isoformat(),
                "League": league,
                "Team": home,
                "Opp": away,
                "XG": r.get("HomeXG"),
                "XGA": r.get("AwayXG"),
            }
        )
        rows.append(
            {
                "Date": r["Date"].date().isoformat(),
                "League": league,
                "Team": away,
                "Opp": home,
                "XG": r.get("AwayXG"),
                "XGA": r.get("HomeXG"),
            }
        )
    return pd.DataFrame(rows)


def build_simple(df: pd.DataFrame, value_col: str) -> pd.DataFrame:
    df = normalize_cols(
        df,
        {
            "date": "Date",
            "league": "League",
            "team": "Team",
            "opp": "Opp",
            value_col.lower(): value_col,
        },
    )
    if not {"Date", "Team", value_col}.issubset(set(df.columns)):
        return pd.DataFrame()
    df["Date"] = pd.to_datetime(df["Date"], dayfirst=True, errors="coerce").dt.date
    if "Opp" not in df.columns:
        df["Opp"] = ""
    return df[["Date", "League", "Team", "Opp", value_col]].copy()


def build_weather(df: pd.DataFrame) -> pd.DataFrame:
    df = normalize_cols(
        df,
        {
            "date": "Date",
            "league": "League",
            "team": "Team",
            "opp": "Opp",
            "weathertemp": "WeatherTemp",
            "weatherwind": "WeatherWind",
            "weatherrain": "WeatherRain",
            "weatherhumidity": "WeatherHumidity",
            "temp": "WeatherTemp",
            "wind": "WeatherWind",
            "rain": "WeatherRain",
            "humidity": "WeatherHumidity",
        },
    )
    if not {"Date", "Team"}.issubset(set(df.columns)):
        return pd.DataFrame()
    df["Date"] = pd.to_datetime(df["Date"], dayfirst=True, errors="coerce").dt.date
    if "Opp" not in df.columns:
        df["Opp"] = ""
    cols = ["Date", "League", "Team", "Opp"]
    for col in ["WeatherTemp", "WeatherWind", "WeatherRain", "WeatherHumidity"]:
        if col in df.columns:
            cols.append(col)
    return df[cols].copy()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--xg", default="data/external/xg_matches.csv")
    ap.add_argument("--injuries", default="data/external/injuries.csv")
    ap.add_argument("--lineups", default="data/external/lineups.csv")
    ap.add_argument("--suspensions", default="data/external/suspensions.csv")
    ap.add_argument("--weather", default="data/external/weather.csv")
    ap.add_argument("--out", default="data/processed/external_features.csv")
    args = ap.parse_args()

    xg_df = read_csv(args.xg)
    inj_df = read_csv(args.injuries)
    lin_df = read_csv(args.lineups)
    susp_df = read_csv(args.suspensions)
    weather_df = read_csv(args.weather)

    xg_long = build_xg_long(xg_df) if xg_df is not None else pd.DataFrame()
    inj = build_simple(inj_df, "Injuries") if inj_df is not None else pd.DataFrame()
    lin = build_simple(lin_df, "Lineup") if lin_df is not None else pd.DataFrame()
    susp = build_simple(susp_df, "Suspensions") if susp_df is not None else pd.DataFrame()
    weather = build_weather(weather_df) if weather_df is not None else pd.DataFrame()

    if xg_long.empty and inj.empty and lin.empty and susp.empty and weather.empty:
        print("No external data found. Place files in data/external/ and re-run.")
        return 1

    if not xg_long.empty:
        base = xg_long
    else:
        # build base from injuries/lineups/suspensions/weather
        frames = [df for df in [inj, lin, susp, weather] if not df.empty]
        base = pd.concat(frames, ignore_index=True).drop_duplicates(subset=["Date", "League", "Team", "Opp"])
        base["XG"] = None
        base["XGA"] = None

    if not inj.empty and "Injuries" not in base.columns:
        inj_merge = inj.drop(columns=["Opp"]) if "Opp" in inj.columns else inj
        base = base.merge(inj_merge, on=["Date", "League", "Team"], how="left", suffixes=("", "_inj"))
    if not lin.empty and "Lineup" not in base.columns:
        lin_merge = lin.drop(columns=["Opp"]) if "Opp" in lin.columns else lin
        base = base.merge(lin_merge, on=["Date", "League", "Team"], how="left", suffixes=("", "_lin"))
    if not susp.empty and "Suspensions" not in base.columns:
        susp_merge = susp.drop(columns=["Opp"]) if "Opp" in susp.columns else susp
        base = base.merge(susp_merge, on=["Date", "League", "Team"], how="left", suffixes=("", "_susp"))
    weather_cols = {"WeatherTemp", "WeatherWind", "WeatherRain", "WeatherHumidity"}
    if not weather.empty and not weather_cols.issubset(set(base.columns)):
        weather_merge = weather.drop(columns=["Opp"]) if "Opp" in weather.columns else weather
        base = base.merge(weather_merge, on=["Date", "League", "Team"], how="left", suffixes=("", "_w"))

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    base.to_csv(out_path, index=False)
    print(f"saved: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
