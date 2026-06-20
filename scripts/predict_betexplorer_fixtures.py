#!/usr/bin/env python3
"""Predict fixtures for a target date using BetExplorer fixtures + historical results."""
from __future__ import annotations

import argparse
import re
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
import yaml

from betexplorer_utils import download_league_csv
from run_all_european_enhanced import (
    build_match_features,
    build_pre_match_features,
    evaluate_strategies,
)
from daily_select import match_qualifies
from apply_primary_strategy_all import (
    apply_strategy,
    EXCLUDED_CODES,
    PRIMARY_EXCLUDED_CODES,
    is_cup_competition,
)

try:
    import requests  # type: ignore
except Exception as exc:  # pragma: no cover
    raise SystemExit("Missing dependency: requests. Install with: pip install requests") from exc


UA = "Mozilla/5.0 (compatible; BetExplorerScraper/1.0)"
OPEN_ODDS_COLS = ("AvgH", "AvgD", "AvgA")


def _get(url: str, timeout_s: int = 45, retries: int = 2, sleep_s: float = 0.8) -> str | None:
    for attempt in range(retries + 1):
        try:
            resp = requests.get(url, headers={"User-Agent": UA}, timeout=timeout_s)
            if resp.status_code != 200:
                return None
            return resp.text
        except requests.RequestException:
            if attempt >= retries:
                return None
            time.sleep(sleep_s)
    return None


def _parse_date(text: str) -> str | None:
    text = text.strip()
    today = date.today()
    if not text or text == "&nbsp;":
        return None
    if text.lower().startswith("today"):
        return today.isoformat()
    if text.lower().startswith("tomorrow"):
        return (today + timedelta(days=1)).isoformat()
    # strip time
    text = text.split(" ")[0]
    m = re.match(r"(\d{2})\.(\d{2})\.(\d{4})", text)
    if m:
        d, mn, y = map(int, m.groups())
        return date(y, mn, d).isoformat()
    m = re.match(r"(\d{2})\.(\d{2})\.", text)
    if m:
        d, mn = map(int, m.groups())
        return date(today.year, mn, d).isoformat()
    return None


def parse_fixtures_page(html: str) -> List[Dict[str, str]]:
    rows = re.findall(r"<tr>(.*?)</tr>", html, re.DOTALL)
    fixtures: List[Dict[str, str]] = []
    last_date: Optional[str] = None
    for row in rows:
        if "in-match" not in row:
            continue
        odds = re.findall(r'data-odd="([0-9.]+)"', row)
        if len(odds) < 3:
            continue
        date_match = re.search(r'table-main__datetime\">([^<]+)<', row)
        date_iso = None
        if date_match:
            date_iso = _parse_date(date_match.group(1))
        if not date_iso:
            date_iso = last_date
        if not date_iso:
            continue
        last_date = date_iso

        link_match = re.search(r'class="in-match"[^>]*>(.*?)</a>', row, re.DOTALL)
        if not link_match:
            continue
        link_html = link_match.group(1)
        spans = re.findall(r"<span[^>]*>(.*?)</span>", link_html, re.DOTALL)
        teams = [re.sub(r"<[^>]+>", "", s).strip() for s in spans if re.sub(r"<[^>]+>", "", s).strip()]
        if len(teams) < 2:
            continue

        fixtures.append(
            {
                "Date": date_iso,
                "HomeTeam": teams[0],
                "AwayTeam": teams[1],
                "FTHG": pd.NA,
                "FTAG": pd.NA,
                "AvgH": float(odds[0]),
                "AvgD": float(odds[1]),
                "AvgA": float(odds[2]),
            }
        )
    return fixtures


def fetch_fixtures(url: str, target_date: str) -> List[Dict[str, str]]:
    if not url.endswith("/"):
        url += "/"
    if not url.endswith("fixtures/"):
        url += "fixtures/"
    html = _get(url)
    if not html:
        return []
    fixtures = parse_fixtures_page(html)
    return [f for f in fixtures if f.get("Date") == target_date]


def load_yaml(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def normalize_results(df: pd.DataFrame) -> pd.DataFrame:
    # BetExplorer CSVs already have HomeTeam/AwayTeam + scores.
    if "Home" in df.columns and "Away" in df.columns:
        df = df.rename(columns={"Home": "HomeTeam", "Away": "AwayTeam"})
    if "HG" in df.columns and "AG" in df.columns:
        df = df.rename(columns={"HG": "FTHG", "AG": "FTAG"})
    return df


def resolve_params(feat: pd.DataFrame) -> Optional[Dict[str, float]]:
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
    if not best:
        return None
    return best.get("params")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True, help="Target date in YYYY-MM-DD")
    ap.add_argument("--betexplorer-list", default="data/betexplorer_leagues.yaml")
    ap.add_argument("--betexplorer-seasons", type=int, default=1)
    ap.add_argument("--out", default="")
    ap.add_argument("--sleep", type=float, default=0.2)
    args = ap.parse_args()

    target_date = args.date
    cfg = load_yaml(Path(args.betexplorer_list))
    leagues = cfg.get("lists", {}).get("betexplorer", [])
    if not leagues:
        print("No BetExplorer leagues configured.")
        return 1

    raw_dir = Path("data/raw/betexplorer")
    raw_dir.mkdir(parents=True, exist_ok=True)
    report_dir = Path("reports")
    report_dir.mkdir(parents=True, exist_ok=True)

    picks: List[Dict[str, str]] = []
    checked = 0
    with_fixtures = 0

    for entry in leagues:
        code = entry.get("code")
        name = entry.get("name")
        url = entry.get("url")
        if not code or not name or not url:
            continue
        checked += 1
        if code in EXCLUDED_CODES or code in PRIMARY_EXCLUDED_CODES or is_cup_competition(name):
            continue
        fixtures = fetch_fixtures(url, target_date)
        if not fixtures:
            continue
        with_fixtures += 1
        time.sleep(args.sleep)

        # ensure historical results
        hist_path = raw_dir / f"{code}_current.csv"
        if not hist_path.exists() or hist_path.stat().st_size == 0:
            ok = download_league_csv(url, hist_path, max_seasons=args.betexplorer_seasons)
            if not ok:
                continue

        df_hist = pd.read_csv(hist_path)
        df_hist = normalize_results(df_hist)
        if df_hist.empty:
            continue

        # compute params from history
        feat_hist = build_match_features(df_hist, OPEN_ODDS_COLS, window=5, team_geo=None, external_features=None)
        if feat_hist.empty:
            continue
        params = resolve_params(feat_hist)
        if not params:
            continue

        # build pre-match features using history + fixtures
        df_fix = pd.DataFrame(fixtures)
        df_combined = pd.concat([df_hist, df_fix], ignore_index=True, sort=False)
        feat_all = build_pre_match_features(df_combined, OPEN_ODDS_COLS, window=5, team_geo=None, external_features=None)
        feat_all["DateOnly"] = pd.to_datetime(feat_all["Date"], errors="coerce").dt.date.astype(str)
        day = feat_all[(feat_all["DateOnly"] == target_date) & feat_all["FTHG"].isna() & feat_all["FTAG"].isna()].copy()
        if day.empty:
            continue

        qualifying = day[day.apply(lambda r: match_qualifies(r, params), axis=1)].copy()
        if qualifying.empty:
            continue
        _, primary = apply_strategy(qualifying, code)
        if primary.empty:
            continue

        for _, r in primary.iterrows():
            picks.append(
                {
                    "Date": target_date,
                    "League": name,
                    "Code": code,
                    "Home": r.get("HomeTeam"),
                    "Away": r.get("AwayTeam"),
                    "Pred": r.get("Pred"),
                    "Conf": r.get("Conf"),
                    "ProbMargin": r.get("ProbMargin"),
                }
            )

    out_csv = Path(args.out) if args.out else report_dir / f"betexplorer_picks_{target_date}.csv"
    pd.DataFrame(picks).to_csv(out_csv, index=False)
    out_md = out_csv.with_suffix(".md")
    lines = [
        f"# BetExplorer picks ({target_date})",
        f"- Leagues checked: {checked}",
        f"- Leagues with fixtures: {with_fixtures}",
        f"- Picks: {len(picks)}",
    ]
    if picks:
        for p in picks:
            lines.append(
                f"- [{p['Code']}] {p['League']} — {p['Home']} vs {p['Away']} — Pred {p['Pred']} — Conf {p['Conf']}"
            )
    else:
        lines.append("- No picks")
    out_md.write_text("\n".join(lines), encoding="utf-8")

    print(f"saved: {out_csv}")
    print(f"saved: {out_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
