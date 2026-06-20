#!/usr/bin/env python3
"""Fetch match-day weather from Open-Meteo (no API key) and write weather.csv."""
from __future__ import annotations

import argparse
import json
import time
from datetime import date as dt_date
from pathlib import Path
from difflib import SequenceMatcher
from typing import Dict, List, Tuple

import pandas as pd
import requests

from run_all_european_enhanced import normalize_main, normalize_extra, season_code_to_str, normalize_team_name


def load_team_geo(path: Path) -> Dict[str, Tuple[float, float]]:
    if not path.exists():
        return {}
    df = pd.read_csv(path, encoding="utf-8-sig")
    out: Dict[str, Tuple[float, float]] = {}
    for _, r in df.iterrows():
        name = str(r.get("TeamName", "")).strip()
        if not name:
            continue
        lat = r.get("Latitude")
        lon = r.get("Longitude")
        if pd.notna(lat) and pd.notna(lon):
            out[normalize_team_name(name)] = (float(lat), float(lon))
    return out


def resolve_team_geo(team: str, team_geo: Dict[str, Tuple[float, float]], aliases: Dict[str, str]) -> Tuple[float, float] | None:
    key = normalize_team_name(team)
    if key in team_geo:
        return team_geo[key]
    if key in aliases and aliases[key] in team_geo:
        return team_geo[aliases[key]]
    best_key = None
    best_score = 0.0
    for cand in team_geo.keys():
        score = SequenceMatcher(None, key, cand).ratio()
        if score > best_score:
            best_score = score
            best_key = cand
    if best_key and best_score >= 0.85:
        aliases[key] = best_key
        return team_geo[best_key]
    return None


def fetch_weather(lat: float, lon: float, day: str, cache: Dict[str, dict]) -> tuple[dict | None, bool]:
    key = f"{day}|{lat:.4f},{lon:.4f}"
    if key in cache:
        return cache[key], False
    day_dt = dt_date.fromisoformat(day)
    today = dt_date.today()
    if day_dt <= today:
        base = "https://archive-api.open-meteo.com/v1/archive"
    else:
        base = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": day,
        "end_date": day,
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum,wind_speed_10m_max,relative_humidity_2m_max",
        "timezone": "UTC",
        "windspeed_unit": "kmh",
    }
    try:
        resp = requests.get(base, params=params, timeout=20)
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return None, False
    daily = data.get("daily", {})
    if not daily:
        return None, False
    temp_max = daily.get("temperature_2m_max", [None])[0]
    temp_min = daily.get("temperature_2m_min", [None])[0]
    rain = daily.get("precipitation_sum", [None])[0]
    wind = daily.get("wind_speed_10m_max", [None])[0]
    humidity = daily.get("relative_humidity_2m_max", [None])[0]
    if temp_max is not None and temp_min is not None:
        temp = (float(temp_max) + float(temp_min)) / 2.0
    else:
        temp = None
    payload = {
        "WeatherTemp": temp,
        "WeatherWind": wind,
        "WeatherRain": rain,
        "WeatherHumidity": humidity,
    }
    cache[key] = payload
    return payload, True


def load_matches(path: Path, season_str: str) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_csv(path, encoding="utf-8-sig")
    if "Season" in df.columns or "HG" in df.columns:
        df = normalize_extra(df, season_str)
    else:
        df = normalize_main(df)
    if "Date" not in df.columns:
        return pd.DataFrame()
    df["Date"] = pd.to_datetime(df["Date"], dayfirst=True, errors="coerce").dt.date
    return df.dropna(subset=["Date"]).copy()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", default="2526")
    ap.add_argument("--codes", default="")
    ap.add_argument("--start", default="")
    ap.add_argument("--end", default="")
    ap.add_argument("--out", default="data/external/weather.csv")
    ap.add_argument("--cache", default="data/external/weather_cache.json")
    ap.add_argument("--aliases", default="data/processed/team_aliases.json")
    ap.add_argument("--sleep", type=float, default=0.2)
    args = ap.parse_args()

    season_code = str(args.season)
    season_str = season_code_to_str(season_code)
    codes_filter = {c.strip() for c in args.codes.split(",") if c.strip()}
    start_date = dt_date.fromisoformat(args.start) if args.start else None
    end_date = dt_date.fromisoformat(args.end) if args.end else None

    team_geo = load_team_geo(Path("data/processed/team_stadiums.csv"))
    if not team_geo:
        print("Missing team stadium coordinates.")
        return 1

    alias_path = Path(args.aliases)
    if alias_path.exists():
        try:
            aliases = json.loads(alias_path.read_text(encoding="utf-8"))
        except Exception:
            aliases = {}
    else:
        aliases = {}

    raw_dir = Path("data/raw/football_data")
    rows: List[dict] = []

    # load cache
    cache_path = Path(args.cache)
    if cache_path.exists():
        try:
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
        except Exception:
            cache = {}
    else:
        cache = {}

    # infer league files
    files = sorted(raw_dir.glob(f"*_{season_code}.csv"))
    for path in files:
        code = path.name.split("_")[0]
        if codes_filter and code not in codes_filter:
            continue
        df = load_matches(path, season_str)
        if df.empty:
            continue
        if start_date or end_date:
            if start_date:
                df = df[df["Date"] >= start_date]
            if end_date:
                df = df[df["Date"] <= end_date]
        if df.empty:
            continue
        for _, r in df.iterrows():
            day = r["Date"].isoformat()
            home = str(r.get("HomeTeam", "")).strip()
            away = str(r.get("AwayTeam", "")).strip()
            if not home or not away:
                continue
            coords = resolve_team_geo(home, team_geo, aliases)
            if not coords:
                continue
            lat, lon = coords
            w, fetched = fetch_weather(lat, lon, day, cache)
            if not w:
                continue
            rows.append(
                {
                    "Date": day,
                    "League": code,
                    "Team": home,
                    "Opp": away,
                    **w,
                }
            )
            rows.append(
                {
                    "Date": day,
                    "League": code,
                    "Team": away,
                    "Opp": home,
                    **w,
                }
            )
            if fetched and args.sleep:
                time.sleep(args.sleep)

    if not rows:
        print("No weather rows produced.")
        return 1

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df_out = pd.DataFrame(rows).drop_duplicates(subset=["Date", "League", "Team"])
    df_out.to_csv(out_path, index=False)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(cache), encoding="utf-8")
    alias_path.parent.mkdir(parents=True, exist_ok=True)
    alias_path.write_text(json.dumps(aliases), encoding="utf-8")
    print(f"saved: {out_path}")
    print(f"saved: {cache_path}")
    print(f"saved: {alias_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
