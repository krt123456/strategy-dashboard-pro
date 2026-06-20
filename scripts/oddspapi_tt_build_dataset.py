#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
import unicodedata
from typing import Any, Dict, Iterable, List, Optional, Tuple
import pandas as pd


def _safe_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _iter_payloads(path: Path) -> Iterable[Dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        if "fixtureId" in data or "id" in data:
            return [data]
        if "fixtures" in data and isinstance(data["fixtures"], list):
            return data["fixtures"]
        if "data" in data and isinstance(data["data"], list):
            return data["data"]
    return []


def _iter_player_entries(players: Any) -> Iterable[Dict[str, Any]]:
    if isinstance(players, dict):
        for entry in players.values():
            if isinstance(entry, dict):
                yield entry
    elif isinstance(players, list):
        for entry in players:
            if isinstance(entry, dict):
                yield entry


def _pick_price(entries: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not entries:
        return None
    # Prefer active entries if available; otherwise use the last entry.
    for entry in entries:
        if entry.get("active") is True:
            return entry
    return entries[-1]


def _extract_moneyline(odds_payload: Dict[str, Any], bookmaker: Optional[str]) -> Optional[Dict[str, Any]]:
    bookmakers = odds_payload.get("bookmakerOdds") or odds_payload.get("bookmakers") or {}
    if not isinstance(bookmakers, dict) or not bookmakers:
        return None
    if bookmaker and bookmaker in bookmakers:
        book_key = bookmaker
    else:
        book_key = next(iter(bookmakers.keys()))
    book = bookmakers.get(book_key, {})
    markets = book.get("markets", {}) if isinstance(book, dict) else {}
    if not isinstance(markets, dict):
        return None

    home_price = None
    away_price = None
    for market in markets.values():
        outcomes = market.get("outcomes", {})
        if not isinstance(outcomes, dict):
            continue
        # Collect generic outcome prices in case bookmakerOutcomeId doesn't use home/away labels.
        fallback_prices = []
        temp_home = None
        temp_away = None
        for outcome_key, outcome in outcomes.items():
            players = outcome.get("players", {})
            entries = list(_iter_player_entries(players))
            choice = _pick_price(entries)
            if not choice:
                continue
            outcome_id = str(
                choice.get("bookmakerOutcomeId")
                or outcome.get("bookmakerOutcomeId")
                or ""
            ).lower()
            price = _safe_float(choice.get("price"))
            if price is None:
                continue
            if outcome_id == "home":
                temp_home = price
            elif outcome_id == "away":
                temp_away = price
            else:
                fallback_prices.append((str(outcome_key), price))
        if temp_home is None or temp_away is None:
            if len(fallback_prices) == 2:
                # Use sorted outcome keys as a stable proxy for participant order.
                fallback_prices.sort(key=lambda x: x[0])
                if temp_home is None:
                    temp_home = fallback_prices[0][1]
                if temp_away is None:
                    temp_away = fallback_prices[1][1]
        if temp_home is not None and temp_away is not None:
            home_price = temp_home
            away_price = temp_away
            break
    if home_price is None or away_price is None:
        return None
    return {"bookmaker": book_key, "home_odds": home_price, "away_odds": away_price}


def _implied_probs(home: float, away: float) -> Dict[str, float]:
    ph = 1.0 / home if home > 0 else 0.0
    pa = 1.0 / away if away > 0 else 0.0
    total = ph + pa
    if total <= 0:
        return {"home": 0.0, "away": 0.0, "overround": 0.0}
    return {"home": ph / total, "away": pa / total, "overround": total}


def _load_scores(scores_dir: Path) -> Dict[str, Dict[str, Any]]:
    scores: Dict[str, Dict[str, Any]] = {}
    if not scores_dir.exists():
        return scores
    for path in scores_dir.glob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        fixture_id = str(payload.get("fixtureId") or payload.get("id") or path.stem)
        scores[fixture_id] = payload
    return scores


def _norm_name(name: str) -> str:
    name = (name or "").strip()
    if not name:
        return ""
    name = unicodedata.normalize("NFKD", name)
    name = "".join(ch for ch in name if not unicodedata.combining(ch))
    name = re.sub(r"[^a-z0-9]+", "", name.lower())
    return name


def _load_results_csv(path: Path) -> Dict[Tuple[str, str, str], Tuple[float, float]]:
    if not path.exists():
        return {}
    try:
        df = pd.read_csv(path)
    except Exception:
        return {}
    if "Date" not in df.columns or "HomeTeam" not in df.columns or "AwayTeam" not in df.columns:
        return {}
    out: Dict[Tuple[str, str, str], Tuple[float, float]] = {}
    for _, row in df.iterrows():
        date_val = str(row.get("Date", "")).split(" ")[0]
        home = _norm_name(str(row.get("HomeTeam", "")))
        away = _norm_name(str(row.get("AwayTeam", "")))
        try:
            fthg = float(row.get("FTHG"))
            ftag = float(row.get("FTAG"))
        except Exception:
            continue
        if date_val and home and away:
            out[(date_val, home, away)] = (fthg, ftag)
    return out


def _extract_final_scores(payload: Dict[str, Any]) -> Tuple[Optional[float], Optional[float]]:
    # Try common top-level fields first.
    p1 = _safe_float(payload.get("participant1Score"))
    p2 = _safe_float(payload.get("participant2Score"))
    if p1 is not None and p2 is not None:
        return p1, p2
    scores = payload.get("scores")
    if not isinstance(scores, dict) or not scores:
        return None, None
    if "0" in scores:
        entry = scores.get("0") or {}
        return _safe_float(entry.get("participant1Score")), _safe_float(entry.get("participant2Score"))
    # Fallback to highest period key.
    best_key = None
    for key in scores.keys():
        if best_key is None or str(key) > str(best_key):
            best_key = key
    entry = scores.get(best_key) or {}
    return _safe_float(entry.get("participant1Score")), _safe_float(entry.get("participant2Score"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--odds-dir", default="data/raw/oddspapi_tabletennis/odds")
    ap.add_argument("--odds-by-tournaments-dir", default="data/raw/oddspapi_tabletennis/odds_by_tournaments")
    ap.add_argument("--scores-dir", default="data/raw/oddspapi_tabletennis/scores")
    ap.add_argument("--bookmaker", default=None)
    ap.add_argument("--results-csv", default=None, help="Optional CSV with results to avoid scores API")
    ap.add_argument("--out", default="data/processed/oddspapi_tabletennis_dataset.csv")
    args = ap.parse_args()

    odds_dir = Path(args.odds_dir)
    odds_by_tournaments_dir = Path(args.odds_by_tournaments_dir)
    scores_map = _load_scores(Path(args.scores_dir))
    results_map = _load_results_csv(Path(args.results_csv)) if args.results_csv else {}

    rows: List[Dict[str, Any]] = []

    sources = []
    if odds_dir.exists():
        sources.extend(sorted(odds_dir.glob("*.json")))
    if odds_by_tournaments_dir.exists():
        sources.extend(sorted(odds_by_tournaments_dir.glob("*.json")))

    if not sources:
        print("No odds data found.")
        return 1

    seen = set()
    for path in sources:
        for payload in _iter_payloads(path):
            fixture_id = str(payload.get("fixtureId") or payload.get("id") or "")
            if not fixture_id or fixture_id in seen:
                continue
            ml = _extract_moneyline(payload, args.bookmaker)
            if not ml:
                continue
            probs = _implied_probs(ml["home_odds"], ml["away_odds"])
            scores_payload = scores_map.get(fixture_id, {})
            p1_score, p2_score = _extract_final_scores(scores_payload)
            if (p1_score is None or p2_score is None) and results_map:
                date_key = str(payload.get("startTime") or payload.get("date") or "").split("T")[0]
                home_name = _norm_name(str(payload.get("participant1Name") or payload.get("home") or ""))
                away_name = _norm_name(str(payload.get("participant2Name") or payload.get("away") or ""))
                if date_key and home_name and away_name:
                    match = results_map.get((date_key, home_name, away_name))
                    if match:
                        p1_score, p2_score = match
                    else:
                        rev = results_map.get((date_key, away_name, home_name))
                        if rev:
                            # swap scores if names reversed
                            p2_score, p1_score = rev
            actual = None
            if p1_score is not None and p2_score is not None:
                actual = "H" if p1_score > p2_score else "A"

            rows.append(
                {
                    "fixture_id": fixture_id,
                    "start_time": payload.get("startTime") or payload.get("date"),
                    "tournament": payload.get("tournamentName") or payload.get("tournamentSlug"),
                    "category": payload.get("categoryName") or payload.get("categorySlug"),
                    "home": payload.get("participant1Name") or payload.get("home"),
                    "away": payload.get("participant2Name") or payload.get("away"),
                    "status_id": payload.get("statusId"),
                    "bookmaker": ml["bookmaker"],
                    "home_odds": ml["home_odds"],
                    "away_odds": ml["away_odds"],
                    "home_prob": probs["home"],
                    "away_prob": probs["away"],
                    "overround": probs["overround"],
                    "p1_score": p1_score,
                    "p2_score": p2_score,
                    "actual": actual,
                }
            )
            seen.add(fixture_id)

    if not rows:
        print("No usable odds rows found.")
        return 1

    import pandas as pd

    df = pd.DataFrame(rows)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_path, index=False)
    print(f"Wrote {len(df)} rows to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
