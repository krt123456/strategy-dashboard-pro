#!/usr/bin/env python3
"""Apply the primary strategy across all leagues we can fetch and report results."""
from __future__ import annotations

import argparse
import ast
import time
from pathlib import Path
from typing import Dict, Any, List, Tuple

import pandas as pd
import yaml

from run_all_european_enhanced import (
    fetch_csv,
    normalize_main,
    normalize_extra,
    parse_last_date,
    pick_odds_cols,
    build_match_features,
    evaluate_strategies,
    season_code_to_str,
    normalize_team_name,
)
from daily_select import match_qualifies
from betexplorer_utils import download_league_csv


ADV_THRESHOLDS = {
    "ShotsDiff": 6.6,
    "SOTDiff": 3.2,
    "GDDiff": 2.2,
    "FormPtsDiff": 1.6,
}

PRIMARY_FORM_PTS_MIN_TIGHT = -0.6
PRIMARY_GD_MAX = 1.9
PRIMARY_HARDENED_CODES = {"E0", "D2"}
PRIMARY_HARDENED_CONF_MIN = 0.80
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


def load_yaml(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_optional_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def parse_params(raw) -> Dict[str, Any] | None:
    if raw is None:
        return None
    if isinstance(raw, dict):
        return raw
    try:
        return ast.literal_eval(str(raw))
    except Exception:
        return None


def normalize_new(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    if "HG" in df.columns and "AG" in df.columns:
        df = df.rename(columns={"HG": "FTHG", "AG": "FTAG"})
    if "Home" in df.columns and "Away" in df.columns:
        df = df.rename(columns={"Home": "HomeTeam", "Away": "AwayTeam"})
    return df


def load_team_geo(path: Path) -> Dict[str, Dict[str, float]]:
    if not path.exists():
        return {}
    df = pd.read_csv(path)
    out: Dict[str, Dict[str, float]] = {}
    for _, row in df.iterrows():
        name = str(row.get("TeamName", "")).strip()
        if not name:
            continue
        key = normalize_team_name(name)
        lat = pd.to_numeric(row.get("Latitude"), errors="coerce")
        lon = pd.to_numeric(row.get("Longitude"), errors="coerce")
        cap = pd.to_numeric(row.get("Capacity"), errors="coerce")
        payload: Dict[str, float] = {}
        if pd.notna(lat) and pd.notna(lon):
            payload["lat"] = float(lat)
            payload["lon"] = float(lon)
        if pd.notna(cap):
            payload["capacity"] = float(cap)
        if payload:
            out[key] = payload
    return out


def oriented_adv(df: pd.DataFrame, col: str) -> pd.Series:
    if col not in df.columns:
        return pd.Series(pd.NA, index=df.index, dtype="Float64")
    values = pd.to_numeric(df[col], errors="coerce")
    return values.where(df["Pred"] == "H").fillna((-values).where(df["Pred"] == "A"))


def adv_count_q75(df: pd.DataFrame) -> pd.Series:
    counts = pd.Series(0, index=df.index, dtype="int64")
    for col, thr in ADV_THRESHOLDS.items():
        if col not in df.columns:
            continue
        adv = oriented_adv(df, col)
        counts += (adv >= thr).fillna(False).astype(int)
    return counts


def apply_strategy(qualifying: pd.DataFrame, code: str | None = None) -> Tuple[pd.DataFrame, pd.DataFrame]:
    if qualifying.empty:
        return qualifying, qualifying
    adv_count = adv_count_q75(qualifying)
    base_mask = ~((qualifying["ProbD"] >= 0.22) & (adv_count >= 2))
    base = qualifying[base_mask].copy()
    primary = base.copy()
    if "IsTransferWindow" in primary.columns:
        tw_mask = primary["IsTransferWindow"] == 1
        tw_ok = (
            primary["FormPtsDiff"].notna()
            & (primary["FormPtsDiff"] >= PRIMARY_FORM_PTS_MIN_TIGHT)
            & primary["GDDiff"].notna()
            & (primary["GDDiff"] <= PRIMARY_GD_MAX)
        )
        primary = primary[~tw_mask | tw_ok].copy()

        league_code = (code or "").strip()
        if not league_code:
            if "Code" in primary.columns:
                league_code = str(primary["Code"].iloc[0])
            elif "LeagueCode" in primary.columns:
                league_code = str(primary["LeagueCode"].iloc[0])

        if league_code in PRIMARY_HARDENED_CODES:
            if "Conf" in primary.columns:
                tw_mask = primary["IsTransferWindow"] == 1
                primary = primary[~tw_mask | (primary["Conf"] >= PRIMARY_HARDENED_CONF_MIN)].copy()
            else:
                primary = primary[primary["IsTransferWindow"] != 1].copy()
    return base, primary


def summarize(
    name: str,
    code: str,
    source: str,
    feat: pd.DataFrame,
    qualifying: pd.DataFrame,
    base: pd.DataFrame,
    primary: pd.DataFrame,
    last_date: str,
    params: Dict[str, Any] | None,
) -> Dict[str, Any]:
    def pack(label: str, df: pd.DataFrame, denom: int):
        if df.empty:
            return {
                f"{label}_picks": 0,
                f"{label}_correct": 0,
                f"{label}_wrong": 0,
                f"{label}_acc": None,
                f"{label}_cov": 0.0,
            }
        correct = (df["Pred"] == df["Actual"]).sum()
        wrong = len(df) - correct
        acc = correct / len(df) if len(df) else None
        cov = len(df) / denom if denom else 0.0
        return {
            f"{label}_picks": len(df),
            f"{label}_correct": int(correct),
            f"{label}_wrong": int(wrong),
            f"{label}_acc": acc,
            f"{label}_cov": cov,
        }

    row = {
        "Code": code,
        "League": name,
        "Source": source,
        "Matches": len(feat),
        "LastDate": last_date,
        "Status": "ok" if len(feat) else "No finished matches",
        "Params": params,
    }
    row.update(pack("qualifying", qualifying, len(feat)))
    row.update(pack("base96", base, len(qualifying)))
    row.update(pack("primary", primary, len(qualifying)))
    return row


def build_league_entries(season_code: str, lists_cfg: dict) -> List[Dict[str, Any]]:
    base_main = "https://www.football-data.co.uk/mmz4281"
    base_extra = "https://www.football-data.co.uk/new"
    season_str = season_code_to_str(season_code)

    main_leagues = {
        "E0": "England Premier League",
        "E1": "England Championship",
        "E2": "England League One",
        "E3": "England League Two",
        "EC": "England Conference",
        "SC0": "Scotland Premier League",
        "SC1": "Scotland Division 1",
        "SC2": "Scotland Division 2",
        "SC3": "Scotland Division 3",
        "D1": "Germany Bundesliga 1",
        "D2": "Germany Bundesliga 2",
        "I1": "Italy Serie A",
        "I2": "Italy Serie B",
        "SP1": "Spain La Liga",
        "SP2": "Spain Segunda",
        "F1": "France Ligue 1",
        "F2": "France Ligue 2",
        "N1": "Netherlands Eredivisie",
        "B1": "Belgium Pro League",
        "P1": "Portugal Primeira",
        "T1": "Turkey Super Lig",
        "G1": "Greece Super League",
    }
    extra_leagues = {
        "AUT": "Austria Bundesliga",
        "DNK": "Denmark Superliga",
        "FIN": "Finland Veikkausliiga",
        "IRL": "Ireland Premier Division",
        "NOR": "Norway Eliteserien",
        "POL": "Poland Ekstraklasa",
        "ROU": "Romania Liga 1",
        "RUS": "Russia Premier League",
        "SWE": "Sweden Allsvenskan",
        "SWZ": "Switzerland Super League",
    }

    entries: List[Dict[str, Any]] = []
    europe_codes = lists_cfg.get("lists", {}).get("active_supported_europe", [])
    for code in europe_codes:
        code = str(code)
        if code in main_leagues:
            entries.append(
                {
                    "code": code,
                    "name": main_leagues[code],
                    "source": "main",
                    "season_label": season_code,
                    "url": f"{base_main}/{season_code}/{code}.csv",
                }
            )
        elif code in extra_leagues:
            entries.append(
                {
                    "code": code,
                    "name": extra_leagues[code],
                    "source": "extra",
                    "season_label": season_str,
                    "url": f"{base_extra}/{code}.csv",
                }
            )

    for entry in lists_cfg.get("lists", {}).get("football_data_new", []):
        code = str(entry.get("code", "")).strip()
        url = str(entry.get("url", "")).strip()
        name = str(entry.get("name", code)).strip()
        if not code or not url:
            continue
        entries.append(
            {
                "code": code,
                "name": name,
                "source": "new",
                "season_label": "all",
                "url": url,
            }
        )
    return entries


def build_betexplorer_entries(cfg: dict) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    for entry in cfg.get("lists", {}).get("betexplorer", []):
        code = str(entry.get("code", "")).strip()
        url = str(entry.get("url", "")).strip()
        name = str(entry.get("name", code)).strip()
        if not code or not url:
            continue
        entries.append(
            {
                "code": code,
                "name": name,
                "source": "betexplorer",
                "season_label": "all",
                "url": url,
            }
        )
    return entries


def flatten_platform_list(cfg: dict) -> List[Dict[str, str]]:
    out = []
    platform = cfg.get("lists", {}).get("platform_final", {})
    for region, groups in platform.items():
        for group, items in groups.items():
            for item in items:
                out.append({"Region": region, "Group": group, "Competition": str(item)})
    return out


def normalize_name(name: str) -> str:
    return "".join(ch for ch in name.lower() if ch.isalnum())


def build_alias_code_map() -> Dict[str, str]:
    # Map platform competition names -> known summary codes
    return {
        normalize_name("Scotland Premiership"): "SC0",
        normalize_name("Scotland Championship"): "SC1",
        normalize_name("Scotland League One"): "SC2",
        normalize_name("Scotland League Two"): "SC3",
        normalize_name("England National League"): "EC",
        normalize_name("Germany Bundesliga"): "D1",
        normalize_name("Germany 2. Bundesliga"): "D2",
        normalize_name("Spain Segunda Division"): "SP2",
        normalize_name("Portugal Primeira Liga"): "P1",
        normalize_name("Belgium Jupiler League"): "B1",
        normalize_name("Switzerland Super League"): "SWZ",
        normalize_name("Switzerland Challenge League"): "SWZ",
    }


def _is_stale(path: Path, max_age_hours: float) -> bool:
    if not path.exists():
        return True
    if max_age_hours <= 0:
        return True
    age_s = time.time() - path.stat().st_mtime
    return age_s > max_age_hours * 3600


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", default="2526")
    ap.add_argument("--lists", default="data/league_search_lists.yaml")
    ap.add_argument("--team-stadiums", default="data/processed/team_stadiums.csv")
    ap.add_argument("--external", default="data/processed/external_features.csv")
    ap.add_argument("--europe-summary", default="reports/europe_summary_enhanced.csv")
    ap.add_argument("--global-summary", default="reports/global_fd_summary_enhanced.csv")
    ap.add_argument("--betexplorer-list", default="data/betexplorer_leagues.yaml")
    ap.add_argument("--betexplorer-seasons", type=int, default=0)
    ap.add_argument("--min-acc", type=float, default=0.90)
    ap.add_argument("--min-picks", type=int, default=5)
    ap.add_argument("--force-download", action="store_true")
    ap.add_argument("--max-age-hours", type=float, default=6.0)
    ap.add_argument("--out", default="reports/primary_strategy_all_competitions.csv")
    args = ap.parse_args()

    cfg = load_yaml(Path(args.lists))
    entries = build_league_entries(str(args.season), cfg)
    bet_cfg = load_optional_yaml(Path(args.betexplorer_list))
    entries.extend(build_betexplorer_entries(bet_cfg))
    # Disable football-data sources entirely; rely only on BetExplorer.
    entries = [entry for entry in entries if entry.get("source") == "betexplorer"]

    europe_params: Dict[str, Dict[str, Any]] = {}
    global_params: Dict[str, Dict[str, Any]] = {}

    europe_path = Path(args.europe_summary)
    if europe_path.exists():
        summary = pd.read_csv(europe_path)
        for _, row in summary.iterrows():
            if str(row.get("Status")) == "ok":
                params = parse_params(row.get("Params"))
                if params:
                    europe_params[str(row.get("Code"))] = params

    global_path = Path(args.global_summary)
    if global_path.exists():
        summary = pd.read_csv(global_path)
        for _, row in summary.iterrows():
            if str(row.get("Status")) == "ok":
                params = parse_params(row.get("Params"))
                if params:
                    global_params[str(row.get("Code"))] = params

    team_geo = load_team_geo(Path(args.team_stadiums))
    ext_df = None
    external_path = Path(args.external)
    if external_path.exists():
        try:
            ext_df = pd.read_csv(external_path, encoding="utf-8-sig")
        except Exception:
            ext_df = None

    raw_dir = Path("data/raw/football_data")
    bet_raw_dir = Path("data/raw/betexplorer")
    raw_dir.mkdir(parents=True, exist_ok=True)
    bet_raw_dir.mkdir(parents=True, exist_ok=True)
    report_dir = Path("reports")
    report_dir.mkdir(parents=True, exist_ok=True)

    summary_rows: List[Dict[str, Any]] = []

    for entry in entries:
        code = entry["code"]
        name = entry["name"]
        url = entry["url"]
        source = entry["source"]
        season_label = entry["season_label"]

        if code in EXCLUDED_CODES:
            summary_rows.append(
                {
                    "Code": code,
                    "League": name,
                    "Source": source,
                    "Status": "excluded_high_risk",
                }
            )
            continue
        if code in PRIMARY_EXCLUDED_CODES:
            summary_rows.append(
                {
                    "Code": code,
                    "League": name,
                    "Source": source,
                    "Status": "excluded_primary",
                }
            )
            continue
        if is_cup_competition(name):
            summary_rows.append(
                {
                    "Code": code,
                    "League": name,
                    "Source": source,
                    "Status": "excluded_cup",
                }
            )
            continue

        if source == "betexplorer":
            dest = bet_raw_dir / f"{code}_{season_label}.csv"
            stale = _is_stale(dest, args.max_age_hours)
            if (args.force_download or stale) and dest.exists():
                dest.unlink()
            if args.force_download or stale or not dest.exists() or dest.stat().st_size == 0:
                ok = download_league_csv(url, dest, max_seasons=args.betexplorer_seasons)
                if not ok:
                    summary_rows.append({"Code": code, "League": name, "Source": source, "Status": "missing"})
                    continue
        else:
            dest = raw_dir / f"{code}_{season_label}.csv"
            stale = _is_stale(dest, args.max_age_hours)
            if (args.force_download or stale) and dest.exists():
                dest.unlink()
            if not fetch_csv(url, dest):
                summary_rows.append({"Code": code, "League": name, "Source": source, "Status": "missing"})
                continue

        df = pd.read_csv(dest, encoding="utf-8-sig")
        if source == "main":
            df = normalize_main(df)
        elif source == "extra":
            df = normalize_extra(df, season_code_to_str(str(args.season)))
        elif source == "betexplorer":
            df = normalize_new(df)
        else:
            df = normalize_new(df)

        last_date = parse_last_date(df)
        odds_cols = pick_odds_cols(df)
        if not odds_cols:
            summary_rows.append({"Code": code, "League": name, "Source": source, "Status": "No odds columns"})
            continue
        for col in odds_cols:
            df[col] = pd.to_numeric(df[col], errors="coerce")

        feat = build_match_features(df, odds_cols, window=5, team_geo=team_geo, external_features=ext_df)
        if feat.empty:
            summary_rows.append({"Code": code, "League": name, "Source": source, "Status": "No finished matches"})
            continue

        params = None
        if source in {"main", "extra"}:
            params = europe_params.get(code)
        elif source == "new":
            params = global_params.get(code)

        if params is None:
            thresholds = [0.60, 0.65, 0.70, 0.75]
            thresholds_ext = [0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70, 0.75]
            res = evaluate_strategies(feat, thresholds, target_acc=0.90, extended=False, allow_draws=False)
            best = res.get("best")
            if (best is None) or (best["coverage"] < 0.06):
                res_ext = evaluate_strategies(feat, thresholds_ext, target_acc=0.90, extended=True, allow_draws=True)
                best_ext = res_ext.get("best")
                if best_ext and (
                    best is None
                    or best_ext["coverage"] > best["coverage"]
                    or (best_ext["coverage"] == best["coverage"] and best_ext["accuracy"] > best["accuracy"])
                ):
                    best = best_ext
            if best:
                params = best.get("params")

        if not params:
            summary_rows.append({"Code": code, "League": name, "Source": source, "Status": "No strategy params"})
            continue
        qualifying = feat[feat.apply(lambda r: match_qualifies(r, params), axis=1)].copy()
        base, primary = apply_strategy(qualifying, code)
        if code in BASE96_EXCLUDED_CODES:
            base = base.iloc[0:0].copy()
        summary_rows.append(summarize(name, code, source, feat, qualifying, base, primary, last_date, params))

    summary_df = pd.DataFrame(summary_rows)
    out_csv = Path(args.out)
    summary_df.to_csv(out_csv, index=False)

    supported = summary_df[
        (summary_df["Status"] == "ok")
        & summary_df["primary_acc"].notna()
        & (summary_df["primary_acc"] >= args.min_acc)
        & (summary_df["primary_picks"] >= args.min_picks)
    ].copy()
    supported.sort_values(["primary_acc", "primary_picks"], ascending=False, inplace=True)
    supported_csv = report_dir / "primary_strategy_supported_over90.csv"
    supported.to_csv(supported_csv, index=False)

    supported_md = report_dir / "primary_strategy_supported_over90.md"
    lines = [
        "# Primary strategy (Base96 + transfer window tighten: FormPtsDiff >= -0.6, GDDiff <= 1.9; E0/D2 transfer window Conf >= 0.80) — supported competitions"
    ]
    if supported.empty:
        lines.append("- None")
    else:
        for _, row in supported.iterrows():
            lines.append(
                f"- [{row['Code']}] {row['League']} — Acc {row['primary_acc']*100:.2f}% | Picks {int(row['primary_picks'])} | Cov {row['primary_cov']*100:.1f}%"
            )
    supported_md.write_text("\n".join(lines), encoding="utf-8")

    # Coverage report against platform list (best-effort name matching)
    platform_items = flatten_platform_list(cfg)
    name_map = {}
    for _, row in summary_df.iterrows():
        key = normalize_name(str(row["League"]))
        name_map[key] = row

    alias_codes = build_alias_code_map()

    platform_rows = []
    for item in platform_items:
        norm = normalize_name(item["Competition"])
        matched = name_map.get(norm)
        if matched is None:
            alias_code = alias_codes.get(norm)
            if alias_code:
                matched = summary_df[summary_df["Code"] == alias_code].head(1)
                if not matched.empty:
                    matched = matched.iloc[0]
                else:
                    matched = None
        if matched is None:
            platform_rows.append(
                {
                    **item,
                    "Status": "no_data_source",
                    "Code": "",
                    "Accuracy": "",
                    "Picks": "",
                }
            )
        else:
            platform_rows.append(
                {
                    **item,
                    "Status": matched.get("Status"),
                    "Code": matched.get("Code"),
                    "Accuracy": round(float(matched.get("primary_acc", 0)) * 100, 2) if matched.get("primary_acc") else "",
                    "Picks": matched.get("primary_picks", ""),
                }
            )

    platform_df = pd.DataFrame(platform_rows)
    platform_csv = report_dir / "primary_strategy_platform_coverage.csv"
    platform_df.to_csv(platform_csv, index=False)

    print(f"saved: {out_csv}")
    print(f"saved: {supported_csv}")
    print(f"saved: {platform_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
