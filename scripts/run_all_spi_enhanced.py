#!/usr/bin/env python3
"""Evaluate club competitions using FiveThirtyEight SPI data (global, non-Europe ready)."""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Dict, List, Tuple, Any

try:
    import pandas as pd  # type: ignore
except Exception:
    print("Missing dependency: pandas. Install with: pip install pandas", file=sys.stderr)
    raise

try:
    import yaml  # type: ignore
except Exception:
    print("Missing dependency: PyYAML. Install with: pip install pyyaml", file=sys.stderr)
    raise

from run_all_european_enhanced import build_match_features, evaluate_strategies, normalize_team_name


ADJ_MAP = {
    "argentine": "argentina",
    "australian": "australia",
    "brazilian": "brazil",
    "chilean": "chile",
    "chinese": "china",
    "colombian": "colombia",
    "danish": "denmark",
    "dutch": "netherlands",
    "english": "england",
    "french": "france",
    "german": "germany",
    "greek": "greece",
    "italian": "italy",
    "japanese": "japan",
    "korean": "korea",
    "mexican": "mexico",
    "norwegian": "norway",
    "polish": "poland",
    "portuguese": "portugal",
    "romanian": "romania",
    "russian": "russia",
    "scottish": "scotland",
    "spanish": "spain",
    "swedish": "sweden",
    "swiss": "switzerland",
    "turkish": "turkey",
    "uruguayan": "uruguay",
    "venezuelan": "venezuela",
}

STOP_WORDS = {
    "the",
    "league",
    "division",
    "liga",
    "cup",
    "super",
    "premier",
    "professional",
    "national",
    "club",
    "clubs",
    "first",
    "second",
    "third",
    "de",
    "del",
    "da",
    "do",
    "of",
}

ALIASES = {
    "USA MLS": ["MLS", "Major League Soccer", "US Major League Soccer"],
    "USA USL Championship": ["USL Championship", "United Soccer League"],
    "Mexico Liga MX": ["Liga MX", "Mexican Primera Division"],
    "Canada Premier League": ["Canadian Premier League"],
    "Brazil Serie A": ["Brazilian Serie A", "Campeonato Brasileiro Serie A", "Brasileirao"],
    "Brazil Serie B": ["Brazilian Serie B", "Campeonato Brasileiro Serie B"],
    "Argentina Primera Division": ["Argentine Primera Division", "Liga Profesional", "Superliga"],
    "Argentina Primera Nacional": ["Primera Nacional", "Argentina Nacional B"],
    "Colombia Primera A": ["Colombian Primera A", "Categoria Primera A"],
    "Colombia Primera B": ["Colombian Primera B", "Categoria Primera B"],
    "Chile Primera Division": ["Chilean Primera Division", "Campeonato Nacional"],
    "Peru Primera Division": ["Peruvian Primera Division", "Liga 1 Peru"],
    "Ecuador LigaPro": ["LigaPro Serie A", "Ecuador Serie A"],
    "Japan J1 League": ["J1 League", "Japanese J League"],
    "Japan J2 League": ["J2 League", "Japanese J2 League"],
    "Japan J3 League": ["J3 League", "Japanese J3 League"],
    "South Korea K League 1": ["K League 1", "K-League 1"],
    "South Korea K League 2": ["K League 2", "K-League 2"],
    "China Super League": ["Chinese Super League"],
    "Australia A-League": ["A-League", "Australian A-League"],
    "Saudi Pro League": ["Saudi Professional League"],
    "UAE Pro League": ["Arabian Gulf League", "UAE Pro League"],
    "Iran Pro League": ["Persian Gulf Pro League", "Iranian Pro League"],
    "CONCACAF Champions Cup": ["CONCACAF Champions League"],
    "Copa Libertadores": ["CONMEBOL Libertadores"],
    "Copa Sudamericana": ["CONMEBOL Sudamericana"],
    "Recopa Sudamericana": ["CONMEBOL Recopa"],
    "AFC Champions League": ["AFC Champions League Elite", "AFC Champions League"],
    "UEFA Champions League": ["UEFA Champions League"],
    "UEFA Europa League": ["UEFA Europa League"],
    "UEFA Europa Conference League": ["UEFA Europa Conference League"],
}


def load_yaml(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def normalize_text(value: str) -> str:
    text = value.lower().strip()
    text = text.replace("&", "and")
    for src, dst in ADJ_MAP.items():
        text = text.replace(src, dst)
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def token_set(value: str) -> set[str]:
    tokens = normalize_text(value).split()
    tokens = [t for t in tokens if t and t not in STOP_WORDS]
    return set(tokens)


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def pick_col(df: pd.DataFrame, candidates: List[str]) -> str | None:
    for col in candidates:
        if col in df.columns:
            return col
    return None


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


def flatten_block(block: Any) -> List[str]:
    out: List[str] = []
    if isinstance(block, list):
        out.extend([str(x) for x in block if x])
    elif isinstance(block, dict):
        for val in block.values():
            out.extend(flatten_block(val))
    return out


def flatten_targets(lists: dict, continents: List[str], include_europe: bool) -> List[str]:
    targets: List[str] = []
    platform = lists.get("lists", {}).get("platform_final", {})
    if platform:
        if include_europe or "europe" in continents:
            targets.extend(flatten_block(platform.get("europe", {})))
        non_europe = platform.get("non_europe", {})
        for key in continents:
            if key == "europe":
                continue
            targets.extend(flatten_block(non_europe.get(key, {})))
        return sorted({t for t in targets if t})

    expansion = lists.get("lists", {}).get("expansion_targets", {})
    for key in continents:
        block = expansion.get(key, {})
        targets.extend(flatten_block(block))
    if include_europe:
        targets.extend(flatten_block(expansion.get("europe", {})))
    return sorted({t for t in targets if t})


def build_mapping(targets: List[str], spi_leagues: List[str]) -> Tuple[Dict[str, List[str]], List[str], Dict[str, List[str]]]:
    spi_norm = {}
    for league in spi_leagues:
        spi_norm.setdefault(normalize_text(league), []).append(league)

    missing = []
    ambiguous: Dict[str, List[str]] = {}
    mapping: Dict[str, List[str]] = {}
    for target in targets:
        candidates: List[str] = []
        norm = normalize_text(target)
        if norm in spi_norm:
            candidates.extend(spi_norm[norm])
        for alias in ALIASES.get(target, []):
            alias_norm = normalize_text(alias)
            if alias_norm in spi_norm:
                candidates.extend(spi_norm[alias_norm])
        candidates = sorted({c for c in candidates})
        if not candidates:
            t_tokens = token_set(target)
            scored = []
            for league in spi_leagues:
                score = jaccard(t_tokens, token_set(league))
                if score >= 0.6:
                    scored.append((score, league))
            if scored:
                scored.sort(reverse=True)
                best_score = scored[0][0]
                best = [name for score, name in scored if score == best_score]
                if len(best) == 1:
                    candidates = best
                else:
                    ambiguous[target] = best
                    continue
        if not candidates:
            missing.append(target)
        else:
            mapping[target] = candidates
    return mapping, missing, ambiguous


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lists", default="data/league_search_lists.yaml")
    ap.add_argument("--spi", default="data/raw/fivethirtyeight/spi_matches.csv")
    ap.add_argument("--out", default="reports/spi_summary_enhanced.csv")
    ap.add_argument("--team-stadiums", default="data/processed/team_stadiums.csv")
    ap.add_argument("--continents", default="asia,north_america,south_america,oceania,intercontinental")
    ap.add_argument("--include-europe", action="store_true")
    ap.add_argument("--target-acc", type=float, default=0.90)
    ap.add_argument("--min-coverage", type=float, default=0.06)
    ap.add_argument("--window", type=int, default=5)
    ap.add_argument("--list-leagues", action="store_true")
    args = ap.parse_args()

    lists = load_yaml(Path(args.lists))
    target_continents = [c.strip() for c in args.continents.split(",") if c.strip()]
    targets = flatten_targets(lists, target_continents, args.include_europe)

    spi_path = Path(args.spi)
    if not spi_path.exists():
        print(f"Missing SPI dataset: {spi_path}", file=sys.stderr)
        return 1

    df = pd.read_csv(spi_path)
    if df.empty:
        print("SPI dataset is empty.", file=sys.stderr)
        return 1

    league_col = pick_col(df, ["league", "league_name", "competition"])
    if league_col is None:
        print("Missing league column in SPI data.", file=sys.stderr)
        return 1

    if args.list_leagues:
        leagues = sorted({str(x) for x in df[league_col].dropna().unique()})
        for name in leagues:
            print(name)
        return 0

    spi_leagues = sorted({str(x) for x in df[league_col].dropna().unique()})
    mapping, missing, ambiguous = build_mapping(targets, spi_leagues)

    date_col = pick_col(df, ["date", "Date", "match_date"])
    team1_col = pick_col(df, ["team1", "home_team", "home", "HomeTeam"])
    team2_col = pick_col(df, ["team2", "away_team", "away", "AwayTeam"])
    score1_col = pick_col(df, ["score1", "home_score", "score_home", "FTHG"])
    score2_col = pick_col(df, ["score2", "away_score", "score_away", "FTAG"])
    prob1_col = pick_col(df, ["prob1", "prob_home", "probh", "home_win_prob"])
    prob2_col = pick_col(df, ["prob2", "prob_away", "proba", "away_win_prob"])
    probd_col = pick_col(df, ["probtie", "prob_draw", "probd", "draw_prob"])

    required = [date_col, team1_col, team2_col, score1_col, score2_col, prob1_col, prob2_col, probd_col]
    if any(col is None for col in required):
        print("Missing required SPI columns. Found: " + ", ".join(df.columns), file=sys.stderr)
        return 1

    team_geo = load_team_geo(Path(args.team_stadiums))

    thresholds = [0.60, 0.65, 0.70, 0.75]
    thresholds_ext = [0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70, 0.75]

    def choose_best(feat: pd.DataFrame) -> Tuple[Dict[str, Any] | None, bool]:
        res = evaluate_strategies(feat, thresholds, args.target_acc, extended=False, allow_draws=False)
        best = res.get("best")
        extended_used = False
        if (best is None) or (best["coverage"] < args.min_coverage):
            res_ext = evaluate_strategies(feat, thresholds_ext, args.target_acc, extended=True, allow_draws=True)
            best_ext = res_ext.get("best")
            if best_ext and (
                best is None
                or best_ext["coverage"] > best["coverage"]
                or (best_ext["coverage"] == best["coverage"] and best_ext["accuracy"] > best["accuracy"])
            ):
                best = best_ext
                extended_used = True
        return best, extended_used

    summary_rows = []
    report_dir = Path("reports")
    report_dir.mkdir(parents=True, exist_ok=True)

    for target, spi_names in mapping.items():
        for spi_name in spi_names:
            sub = df[df[league_col] == spi_name].copy()
            if sub.empty:
                summary_rows.append({"Target": target, "SpiLeague": spi_name, "Status": "missing"})
                continue

            sub = sub.rename(
                columns={
                    date_col: "Date",
                    team1_col: "HomeTeam",
                    team2_col: "AwayTeam",
                    score1_col: "FTHG",
                    score2_col: "FTAG",
                }
            )

            for col in ["FTHG", "FTAG", prob1_col, prob2_col, probd_col]:
                sub[col] = pd.to_numeric(sub[col], errors="coerce")
            sub["Date"] = pd.to_datetime(sub["Date"], errors="coerce")

            sub["AvgH"] = sub[prob1_col].apply(lambda v: (1.0 / v) if pd.notna(v) and v > 0 else None)
            sub["AvgD"] = sub[probd_col].apply(lambda v: (1.0 / v) if pd.notna(v) and v > 0 else None)
            sub["AvgA"] = sub[prob2_col].apply(lambda v: (1.0 / v) if pd.notna(v) and v > 0 else None)

            last_date = sub["Date"].dropna().max()
            last_date_str = last_date.date().isoformat() if pd.notna(last_date) else ""

            feat = build_match_features(sub, ("AvgH", "AvgD", "AvgA"), args.window, team_geo, external_features=None)
            if feat.empty:
                summary_rows.append({"Target": target, "SpiLeague": spi_name, "Status": "No finished matches"})
                continue

            best, extended_used = choose_best(feat)
            if not best:
                summary_rows.append({"Target": target, "SpiLeague": spi_name, "Status": "No strategy >= target"})
                continue

            summary_rows.append(
                {
                    "Target": target,
                    "SpiLeague": spi_name,
                    "Status": "ok",
                    "Matches": len(feat),
                    "LastDate": last_date_str,
                    "Strategy": best["strategy"],
                    "Params": best["params"],
                    "Coverage": best["coverage"] * 100,
                    "Accuracy": best["accuracy"] * 100,
                    "Extended": extended_used,
                }
            )

    summary_df = pd.DataFrame(summary_rows)
    summary_df.to_csv(args.out, index=False)

    # mapping report
    mapping_md = report_dir / "spi_league_mapping.md"
    lines = ["# SPI league mapping"]
    lines.append("")
    lines.append("## Matched targets")
    for target, spi_names in sorted(mapping.items()):
        joined = ", ".join(spi_names)
        lines.append(f"- {target} -> {joined}")
    lines.append("")
    lines.append("## Missing targets")
    for target in sorted(missing):
        lines.append(f"- {target}")
    lines.append("")
    lines.append("## Ambiguous targets")
    for target, names in sorted(ambiguous.items()):
        lines.append(f"- {target} -> {', '.join(names)}")
    mapping_md.write_text("\n".join(lines), encoding="utf-8")

    # filtered list
    over90 = summary_df[(summary_df["Status"] == "ok") & (summary_df["Accuracy"] >= 90.0)].copy()
    over90.sort_values(["Accuracy", "Coverage"], ascending=False, inplace=True)
    over90_csv = report_dir / "spi_supported_over90.csv"
    over90.to_csv(over90_csv, index=False)

    over90_md = report_dir / "spi_supported_over90.md"
    lines = ["# SPI competitions >= 90% accuracy"]
    if over90.empty:
        lines.append("- None")
    else:
        for _, row in over90.iterrows():
            lines.append(
                f"- {row['Target']} (SPI: {row['SpiLeague']}) — "
                f"Acc {row['Accuracy']:.2f}% | Cov {row['Coverage']:.2f}%"
            )
    over90_md.write_text("\n".join(lines), encoding="utf-8")

    print(f"saved: {args.out}")
    print(f"saved: {over90_csv}")
    print(f"saved: {mapping_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
