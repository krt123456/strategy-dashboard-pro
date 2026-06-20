#!/usr/bin/env python3
"""Build a focused non-football shortlist for user-selected 1xBet matches.

The script intentionally uses only locally collected market/model data. It does
not place bets and it does not create accounts. The output is a ranked worklist:
high-confidence candidates first, then secondary/watch/reject rows.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import subprocess
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urlencode

import pandas as pd

from sport_name_quality import has_bad_participant_pair

BASE_DIR = Path(__file__).resolve().parent.parent
BASKETBALL_CURRENT = BASE_DIR / "data" / "basketball_betexplorer_current.csv"
BASKETBALL_FIXTURES_DIR = BASE_DIR / "data" / "raw" / "betexplorer_basketball_fixtures"
TABLETENNIS_FUTURE = BASE_DIR / "reports" / "tabletennis_future_picks.csv"
TABLETENNIS_ODDS_DIR = BASE_DIR / "data" / "raw" / "oddsapi_tabletennis_future" / "odds"
HANDBALL_SUMMARY = BASE_DIR / "reports" / "handball_strategy_summary.csv"
HOCKEY_FIXTURES_ALL = BASE_DIR / "data" / "raw" / "betexplorer_hockey_fixtures_all.csv"
LINEFEED_HISTORY = BASE_DIR / "data" / "one_xbet_linefeed_history.csv"
PUBLIC_1XBET_BASES = ["https://q1ayxwi7tuwrn.bar", "https://1xbet.com"]
try:
    from sports_strategy_profiles import public_watch_configs
    PUBLIC_WATCH_SPORTS = public_watch_configs()
except Exception:  # pragma: no cover
    PUBLIC_WATCH_SPORTS = {
        "hockey": {"id": 2, "label": "hockey", "min_prob": 0.58, "min_margin": 0.08, "min_odds": 1.45, "max_odds": 2.05, "haircut": 0.035, "decision": "WATCH_HOCKEY_GOALIE_REST_REQUIRED", "strategy_gate": "WATCH_ONLY"},
        "tennis": {"id": 4, "label": "tennis", "min_prob": 0.64, "min_margin": 0.14, "min_odds": 1.25, "max_odds": 1.65, "haircut": 0.050, "decision": "WATCH_TENNIS_MODEL_REQUIRED", "strategy_gate": "WATCH_ONLY"},
        "handball": {"id": 8, "label": "handball", "min_prob": 0.62, "min_margin": 0.12, "min_odds": 1.35, "max_odds": 1.85, "haircut": 0.050, "decision": "WATCH_HANDBALL_RETUNE", "strategy_gate": "WATCH_ONLY"},
    }
try:
    from watch_sport_lab import evaluate_public_watch_candidate
except Exception:  # pragma: no cover
    evaluate_public_watch_candidate = None
PUBLIC_STATUS_NOTES: List[str] = []


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, float(value)))


def _norm(value: Any) -> str:
    text = "" if value is None else str(value)
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def _as_float(value: Any) -> Optional[float]:
    try:
        if value is None or (isinstance(value, float) and math.isnan(value)):
            return None
        f = float(value)
        if math.isnan(f):
            return None
        return f
    except Exception:
        return None


def _score_basketball(prob: float, margin: float) -> float:
    prob_score = _clamp(((float(prob) - 0.5) / 0.5) * 100.0)
    margin_score = _clamp(min(max(float(margin), 0.0) / 0.3, 1.0) * 100.0)
    return _clamp(prob_score * 0.7 + margin_score * 0.3)


def _score_tabletennis(prob: float, margin: float) -> float:
    prob_score = _clamp(((float(prob) - 0.5) / 0.5) * 100.0)
    margin_score = _clamp(min(max(float(margin), 0.0) / 0.3, 1.0) * 100.0)
    return _clamp(prob_score * 0.65 + margin_score * 0.35)


def _grade(score: float) -> str:
    if score >= 90:
        return "A+"
    if score >= 82:
        return "A"
    if score >= 74:
        return "B+"
    if score >= 64:
        return "B"
    if score >= 54:
        return "C"
    return "D"


def _odds_flag(odds: Optional[float]) -> str:
    if odds is None:
        return "NO_LOCAL_ODDS"
    if odds <= 1.05:
        return "VERY_LOW_RETURN"
    if odds <= 1.12:
        return "LOW_RETURN"
    if odds <= 1.55:
        return "NORMAL_FAV"
    if odds <= 2.05:
        return "VALUE_OR_VOLATILE"
    return "HIGH_VOLATILITY"


def _decision_basketball(score: float, prob: float, margin: float, odds: Optional[float]) -> str:
    if odds is None:
        return "WATCH_NO_LOCAL_ODDS" if score >= 74 and prob >= 0.72 else "REJECT"
    if score >= 80 and prob >= 0.78 and margin >= 0.28:
        return "ACCEPT_STRONG"
    if score >= 70 and prob >= 0.72 and margin >= 0.20:
        return "ACCEPT_MEDIUM"
    if score >= 60 and prob >= 0.66 and margin >= 0.14:
        return "WATCH"
    if score >= 50 and prob >= 0.60:
        return "PAPER_TRADE"
    return "REJECT"


def _decision_tabletennis(score: float, prob: float, margin: float, odds: Optional[float]) -> str:
    if prob >= 0.70 and margin >= 0.35 and (odds is None or odds <= 1.55):
        return "ACCEPT_SECONDARY"
    if prob >= 0.68 and margin >= 0.30 and (odds is None or odds <= 1.70):
        return "WATCH_SECONDARY"
    return "REJECT"


def _score_market_watch(prob: float, margin: float, odds: float, min_odds: float, max_odds: float) -> float:
    prob_score = _clamp(((prob - 0.50) / 0.30) * 100.0)
    margin_score = _clamp((margin / 0.25) * 100.0)
    mid = (min_odds + max_odds) / 2.0
    half_width = max((max_odds - min_odds) / 2.0, 0.01)
    odds_score = _clamp(100.0 - abs(odds - mid) / half_width * 45.0)
    return _clamp(prob_score * 0.45 + margin_score * 0.35 + odds_score * 0.20)


def _public_bases() -> List[str]:
    configured = os.environ.get("ONE_XBET_PUBLIC_BASE_URLS") or os.environ.get("ONE_XBET_PUBLIC_BASE_URL") or ""
    out = [item.strip().rstrip("/") for item in configured.split(",") if item.strip()]
    for base in PUBLIC_1XBET_BASES:
        if base not in out:
            out.append(base)
    return out


def _curl_json(base_url: str, path: str, params: Dict[str, Any], timeout_s: int) -> Dict[str, Any]:
    url = f"{base_url}{path}?{urlencode(params)}"
    cmd = [
        "curl",
        "-sS",
        "-L",
        "-A",
        "Mozilla/5.0",
        "--connect-timeout",
        str(max(2, min(timeout_s, 8))),
        "--max-time",
        str(timeout_s),
        url,
    ]
    proc = subprocess.run(cmd, check=False, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or f"curl rc={proc.returncode}").strip())
    return json.loads(proc.stdout)


def _public_linefeed_events(sport_id: int, *, count: int = 40, timeout_s: int = 6) -> tuple[List[Dict[str, Any]], str]:
    last_error: Optional[Exception] = None
    for base in _public_bases():
        try:
            payload = _curl_json(
                base,
                "/service-api/LineFeed/Get1x2_VZip",
                {"sports": sport_id, "count": count, "lng": "en", "mode": 1},
                timeout_s,
            )
            if payload.get("Success") is True:
                return [item for item in payload.get("Value") or [] if isinstance(item, dict)], base
        except Exception as exc:
            last_error = exc
            continue
    raise RuntimeError(str(last_error or "1xBet public linefeed failed"))


def _event_date(value: Any) -> Optional[date]:
    try:
        return pd.to_datetime(int(value), unit="s", utc=True).date()
    except Exception:
        return None


def _event_start_utc(value: Any) -> str:
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc).isoformat(timespec="seconds")
    except Exception:
        return ""


def _main_market_prices(event: Dict[str, Any]) -> Dict[int, float]:
    prices: Dict[int, float] = {}
    for odd in event.get("E") or []:
        if not isinstance(odd, dict) or odd.get("G") != 1:
            continue
        try:
            t = int(odd.get("T"))
            c = float(odd.get("C"))
        except Exception:
            continue
        if c > 1.0 and t in {1, 2, 3}:
            prices[t] = c
    return prices


def _public_market_watch_rows(target: date) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    checked_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    for _, cfg in PUBLIC_WATCH_SPORTS.items():
        sport = str(cfg["label"])
        display = str(cfg.get("display") or sport.title())
        try:
            events, base_url = _public_linefeed_events(int(cfg["id"]), timeout_s=6)
        except Exception as exc:
            PUBLIC_STATUS_NOTES.append(f"- {display} 1xBet public watch: unavailable ({str(exc)[:120]}).")
            continue
        sport_rows = 0
        for event in events:
            if _event_date(event.get("S")) != target:
                continue
            prices = _main_market_prices(event)
            if not prices or 1 not in prices or 3 not in prices:
                continue
            inv = {k: 1.0 / v for k, v in prices.items() if v > 1.0}
            total = sum(inv.values())
            if total <= 0:
                continue
            fair = {k: v / total for k, v in inv.items()}
            home_fair = fair.get(1, 0.0)
            away_fair = fair.get(3, 0.0)
            side_t = 1 if home_fair >= away_fair else 3
            side = "home" if side_t == 1 else "away"
            odds = prices[side_t]
            raw_prob = fair[side_t]
            next_prob = max(v for k, v in fair.items() if k != side_t)
            margin = raw_prob - next_prob
            base_prob = max(0.501, raw_prob - float(cfg["haircut"]))
            min_odds = float(cfg["min_odds"])
            max_odds = float(cfg["max_odds"])
            if raw_prob < float(cfg["min_prob"]) or margin < float(cfg["min_margin"]) or not (min_odds <= odds <= max_odds):
                continue
            home = str(event.get("O1E") or event.get("O1") or "").strip()
            away = str(event.get("O2E") or event.get("O2") or "").strip()
            if not home or not away or has_bad_participant_pair(home, away):
                continue
            league = event.get("LE") or event.get("L") or ""
            lab = (
                evaluate_public_watch_candidate(
                    sport=sport,
                    league=league,
                    base_prob=base_prob,
                    odds=odds,
                    margin=margin,
                )
                if evaluate_public_watch_candidate is not None
                else {
                    "include": True,
                    "strategy_variant": f"{sport}_core",
                    "strategy_variant_label": f"{sport.upper()}_CORE",
                    "lab_tier": "STANDARD_WATCH",
                    "calibrated_prob": round(base_prob, 6),
                    "calibrated_margin": round(margin, 6),
                    "dynamic_min_prob": float(cfg["min_prob"]),
                    "dynamic_min_margin": float(cfg["min_margin"]),
                    "reliability_score": 60.0,
                    "memory_sample": 0,
                    "memory_accuracy": 0.0,
                    "prob_penalty": 0.0,
                    "margin_penalty": 0.0,
                    "notes": "fallback_no_lab_calibration",
                }
            )
            prob = float(lab["calibrated_prob"])
            calibrated_margin = float(lab["calibrated_margin"]) if lab.get("calibrated_margin") is not None else margin
            if not lab.get("include"):
                continue
            score = _score_market_watch(prob, calibrated_margin, odds, min_odds, max_odds)
            rows.append(
                {
                    "Sport": sport,
                    "Date": target.isoformat(),
                    "League": league,
                    "Home": home,
                    "Away": away,
                    "Pick": home if side == "home" else away,
                    "Side": side,
                    "Prob": round(prob, 6),
                    "Margin": round(calibrated_margin, 6),
                    "RawProb": round(base_prob, 6),
                    "RawMargin": round(margin, 6),
                    "BrainScore": round(score, 2),
                    "Grade": _grade(score),
                    "PickOdds": odds,
                    "OddsFlag": _odds_flag(odds),
                    "Decision": str(cfg["decision"]),
                    "Source": f"1xbet_public_linefeed_sport_{cfg['id']}",
                    "OneXBetManualOdds": odds,
                    "OneXBetManualCheckedAt": checked_at,
                    "OneXBetManualSource": "1XBET_PUBLIC_LINEFEED",
                    "OneXBetManualEventId": event.get("I") or "",
                    "OneXBetManualCanonicalId": event.get("CI") or "",
                    "OneXBetManualLeague": league,
                    "OneXBetManualEventDate": target.isoformat(),
                    "OneXBetManualStartUtc": _event_start_utc(event.get("S")),
                    "OneXBetManualMatchScore": 8,
                    "OneXBetPublicBase": base_url,
                    "OneXBetManualNote": (
                        f"api=Get1x2_VZip; sport_id={cfg['id']}; "
                        f"event_id={event.get('I') or ''}; canonical_id={event.get('CI') or ''}"
                    ),
                    "StrategyGate": str(cfg.get("strategy_gate") or "WATCH_ONLY: public market signal only; needs local model/backtest/source review before entry."),
                    "StrategyVariant": str(lab.get("strategy_variant") or ""),
                    "StrategyVariantLabel": str(lab.get("strategy_variant_label") or ""),
                    "LabTier": str(lab.get("lab_tier") or ""),
                    "ReliabilityScore": lab.get("reliability_score") or "",
                    "VariantMemorySample": lab.get("memory_sample") or "",
                    "VariantMemoryAccuracy": lab.get("memory_accuracy") or "",
                    "VariantProbPenalty": lab.get("prob_penalty") or "",
                    "VariantMarginPenalty": lab.get("margin_penalty") or "",
                    "DynamicMinProb": lab.get("dynamic_min_prob") or "",
                    "DynamicMinMargin": lab.get("dynamic_min_margin") or "",
                    "LabNotes": str(lab.get("notes") or ""),
                }
            )
            sport_rows += 1
            if sport_rows >= 12:
                break
        PUBLIC_STATUS_NOTES.append(f"- {display} 1xBet public watch rows: {sport_rows}.")
    rows.sort(key=lambda r: (r["BrainScore"], r["Prob"]), reverse=True)
    return rows


def _append_linefeed_history(rows: Iterable[Dict[str, Any]], path: Path = LINEFEED_HISTORY) -> int:
    fields = [
        "SnapshotAt",
        "Date",
        "Sport",
        "League",
        "Home",
        "Away",
        "Pick",
        "Side",
        "OneXBetOdds",
        "EventId",
        "CanonicalId",
        "StartUtc",
        "CheckedAt",
        "Source",
        "PublicBase",
    ]
    history_rows: List[Dict[str, Any]] = []
    snapshot_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    for row in rows:
        if not str(row.get("Source") or "").startswith("1xbet_public_linefeed"):
            continue
        if not row.get("OneXBetManualEventId") or not row.get("OneXBetManualOdds"):
            continue
        history_rows.append(
            {
                "SnapshotAt": snapshot_at,
                "Date": row.get("Date"),
                "Sport": row.get("Sport"),
                "League": row.get("League"),
                "Home": row.get("Home"),
                "Away": row.get("Away"),
                "Pick": row.get("Pick"),
                "Side": row.get("Side"),
                "OneXBetOdds": row.get("OneXBetManualOdds") or row.get("PickOdds"),
                "EventId": row.get("OneXBetManualEventId"),
                "CanonicalId": row.get("OneXBetManualCanonicalId"),
                "StartUtc": row.get("OneXBetManualStartUtc"),
                "CheckedAt": row.get("OneXBetManualCheckedAt"),
                "Source": row.get("Source"),
                "PublicBase": row.get("OneXBetPublicBase"),
            }
        )
    if not history_rows:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not path.exists() or path.stat().st_size == 0
    with path.open("a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        if write_header:
            writer.writeheader()
        writer.writerows(history_rows)
    return len(history_rows)


@dataclass
class FixtureOdds:
    home_odds: Optional[float]
    away_odds: Optional[float]
    source_file: str


def _load_basketball_fixture_odds(target: date) -> Dict[tuple[str, str], FixtureOdds]:
    odds: Dict[tuple[str, str], FixtureOdds] = {}
    if not BASKETBALL_FIXTURES_DIR.exists():
        return odds
    for path in sorted(BASKETBALL_FIXTURES_DIR.glob("*.csv")):
        try:
            df = pd.read_csv(path)
        except Exception:
            continue
        if df.empty or "Date" not in df.columns:
            continue
        dates = pd.to_datetime(df["Date"], errors="coerce").dt.date
        for _, row in df[dates == target].iterrows():
            home = row.get("HomeTeam")
            away = row.get("AwayTeam")
            key = (_norm(home), _norm(away))
            if not key[0] or not key[1]:
                continue
            odds[key] = FixtureOdds(
                home_odds=_as_float(row.get("OddH") or row.get("Odd1")),
                away_odds=_as_float(row.get("OddA") or row.get("Odd2")),
                source_file=str(path.relative_to(BASE_DIR)),
            )
    return odds


def _load_tabletennis_odds(target: date) -> Dict[tuple[str, str], FixtureOdds]:
    odds: Dict[tuple[str, str], FixtureOdds] = {}
    if not TABLETENNIS_ODDS_DIR.exists():
        return odds
    for path in sorted(TABLETENNIS_ODDS_DIR.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(payload, dict) or not payload:
            continue
        event_date = pd.to_datetime(payload.get("date"), errors="coerce")
        if pd.isna(event_date) or event_date.date() != target:
            continue
        home = payload.get("home")
        away = payload.get("away")
        key = (_norm(home), _norm(away))
        if not key[0] or not key[1]:
            continue
        home_odds = None
        away_odds = None
        for markets in (payload.get("bookmakers") or {}).values():
            if not isinstance(markets, list):
                continue
            for market in markets:
                if market.get("name") != "ML":
                    continue
                items = market.get("odds") or []
                if items:
                    home_odds = _as_float(items[0].get("home"))
                    away_odds = _as_float(items[0].get("away"))
                    break
            if home_odds is not None or away_odds is not None:
                break
        odds[key] = FixtureOdds(home_odds=home_odds, away_odds=away_odds, source_file=str(path.relative_to(BASE_DIR)))
    return odds


def _basketball_rows(target: date) -> List[Dict[str, Any]]:
    if not BASKETBALL_CURRENT.exists():
        return []
    df = pd.read_csv(BASKETBALL_CURRENT, low_memory=False)
    if df.empty:
        return []
    df["GAME_DATE_EST"] = pd.to_datetime(df["GAME_DATE_EST"], errors="coerce").dt.date
    day = df[df["GAME_DATE_EST"] == target].copy()
    if "STATUS" in day.columns:
        day = day[day["STATUS"].astype(str).str.lower().eq("not_started")]
    if "accepted" in day.columns:
        day = day[pd.to_numeric(day["accepted"], errors="coerce").fillna(0).astype(int).eq(1)]
    odds_map = _load_basketball_fixture_odds(target)
    rows: List[Dict[str, Any]] = []
    for _, row in day.iterrows():
        home = str(row.get("HOME_TEAM_NAME") or "").strip()
        away = str(row.get("VISITOR_TEAM_NAME") or "").strip()
        if has_bad_participant_pair(home, away):
            continue
        ph = _as_float(row.get("MARKET_PROB_home"))
        pa = _as_float(row.get("MARKET_PROB_away"))
        if ph is None or pa is None or (ph <= 0 and pa <= 0):
            continue
        pred_side = "home" if ph >= pa else "away"
        pred = home if pred_side == "home" else away
        prob = max(ph, pa)
        margin = _as_float(row.get("prob_margin"))
        if margin is None:
            margin = abs(ph - pa)
        score = _score_basketball(prob, margin)
        fixture = odds_map.get((_norm(home), _norm(away)))
        pick_odds = None
        odds_source = ""
        if fixture:
            pick_odds = fixture.home_odds if pred_side == "home" else fixture.away_odds
            odds_source = fixture.source_file
        rows.append(
            {
                "Sport": "basketball",
                "Date": target.isoformat(),
                "League": row.get("league", ""),
                "Home": home,
                "Away": away,
                "Pick": pred,
                "Side": pred_side,
                "Prob": round(prob, 6),
                "Margin": round(float(margin), 6),
                "BrainScore": round(score, 2),
                "Grade": _grade(score),
                "PickOdds": pick_odds,
                "OddsFlag": _odds_flag(pick_odds),
                "Decision": _decision_basketball(score, prob, float(margin), pick_odds),
                "Source": odds_source or "basketball_betexplorer_current.csv",
            }
        )
    rows.sort(key=lambda r: (r["Decision"].startswith("ACCEPT"), r["BrainScore"], r["Prob"]), reverse=True)
    return rows


def _tabletennis_rows(target: date) -> List[Dict[str, Any]]:
    if not TABLETENNIS_FUTURE.exists():
        return []
    df = pd.read_csv(TABLETENNIS_FUTURE)
    if df.empty or "Date" not in df.columns:
        return []
    dates = pd.to_datetime(df["Date"], errors="coerce").dt.date
    day = df[dates == target].copy()
    odds_map = _load_tabletennis_odds(target)
    rows: List[Dict[str, Any]] = []
    for _, row in day.iterrows():
        home = str(row.get("Home") or "").strip()
        away = str(row.get("Away") or "").strip()
        pred = str(row.get("Pred") or "").strip()
        if has_bad_participant_pair(home, away):
            continue
        prob = _as_float(row.get("Prob"))
        margin = _as_float(row.get("Margin"))
        if prob is None or margin is None:
            continue
        pred_side = "home" if _norm(pred) == _norm(home) else "away"
        score = _score_tabletennis(prob, margin)
        fixture = odds_map.get((_norm(home), _norm(away)))
        pick_odds = None
        odds_source = ""
        if fixture:
            pick_odds = fixture.home_odds if pred_side == "home" else fixture.away_odds
            odds_source = fixture.source_file
        rows.append(
            {
                "Sport": "tabletennis",
                "Date": target.isoformat(),
                "League": row.get("League", ""),
                "Home": home,
                "Away": away,
                "Pick": pred,
                "Side": pred_side,
                "Prob": round(prob, 6),
                "Margin": round(margin, 6),
                "BrainScore": round(score, 2),
                "Grade": _grade(score),
                "PickOdds": pick_odds,
                "OddsFlag": _odds_flag(pick_odds),
                "Decision": _decision_tabletennis(score, prob, margin, pick_odds),
                "Source": odds_source or "tabletennis_future_picks.csv",
            }
        )
    rows.sort(key=lambda r: (r["Decision"].startswith("ACCEPT"), r["BrainScore"], r["Prob"]), reverse=True)
    return rows


def _status_rows(target: date) -> List[str]:
    lines: List[str] = []
    if HOCKEY_FIXTURES_ALL.exists():
        try:
            h = pd.read_csv(HOCKEY_FIXTURES_ALL)
            hd = pd.to_datetime(h.get("Date"), errors="coerce").dt.date
            lines.append(f"- Hockey local fixtures for {target.isoformat()}: {int((hd == target).sum())}.")
        except Exception:
            lines.append("- Hockey local fixtures: unreadable.")
    else:
        lines.append("- Hockey local fixtures: missing.")
    if HANDBALL_SUMMARY.exists():
        try:
            hb = pd.read_csv(HANDBALL_SUMMARY)
            parts = []
            for _, r in hb.iterrows():
                parts.append(f"{r.get('League')} {r.get('Picks')} picks/{float(r.get('Accuracy', 0.0)):.2%}")
            lines.append("- Handball model health: " + "; ".join(parts) + ".")
        except Exception:
            lines.append("- Handball model health: unreadable.")
    else:
        lines.append("- Handball model health: missing.")
    lines.append("- Public watch sports are strategy-lab rows unless they also have a validated local model.")
    lines.extend(PUBLIC_STATUS_NOTES)
    return lines


def _write_csv(rows: Iterable[Dict[str, Any]], out_csv: Path) -> None:
    rows = list(rows)
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "Sport",
        "Date",
        "League",
        "Home",
        "Away",
        "Pick",
        "Side",
        "Prob",
        "Margin",
        "BrainScore",
        "Grade",
        "PickOdds",
        "OddsFlag",
        "Decision",
        "Source",
        "OneXBetManualOdds",
        "OneXBetManualCheckedAt",
        "OneXBetManualSource",
        "OneXBetManualEventId",
        "OneXBetManualCanonicalId",
        "OneXBetManualLeague",
        "OneXBetManualEventDate",
        "OneXBetManualStartUtc",
        "OneXBetManualMatchScore",
        "OneXBetPublicBase",
        "OneXBetManualNote",
        "StrategyGate",
        "RawProb",
        "RawMargin",
        "StrategyVariant",
        "StrategyVariantLabel",
        "LabTier",
        "ReliabilityScore",
        "VariantMemorySample",
        "VariantMemoryAccuracy",
        "VariantProbPenalty",
        "VariantMarginPenalty",
        "DynamicMinProb",
        "DynamicMinMargin",
        "LabNotes",
    ]
    with out_csv.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _write_md(rows: List[Dict[str, Any]], target: date, out_md: Path) -> None:
    out_md.parent.mkdir(parents=True, exist_ok=True)
    accepts = [r for r in rows if str(r["Decision"]).startswith("ACCEPT")]
    watch = [r for r in rows if str(r["Decision"]).startswith("WATCH")]
    rejected = [r for r in rows if r["Decision"] == "REJECT"]

    def table(items: List[Dict[str, Any]], limit: int = 30) -> List[str]:
        out = [
            "| Sport | League | Match | Pick | Prob | Odds | Score | Tier | Variant | Decision |",
            "| --- | --- | --- | --- | ---: | ---: | ---: | --- | --- | --- |",
        ]
        for r in items[:limit]:
            odds = "" if r.get("PickOdds") is None else f"{float(r['PickOdds']):.2f}"
            display = {
                "LabTier": r.get("LabTier") or "LOCAL_MODEL",
                "StrategyVariantLabel": r.get("StrategyVariantLabel") or "LOCAL_MODEL",
                **r,
                "Odds": odds,
            }
            out.append(
                "| {Sport} | {League} | {Home} vs {Away} | {Pick} | {Prob:.3f} | {Odds} | {BrainScore:.2f} | {LabTier} | {StrategyVariantLabel} | {Decision} |".format(
                    **display
                )
            )
        if not items:
            out.append("| - | - | - | - | - | - | - | - | - | - |")
        return out

    lines = [
        "# Other sports 1xBet candidate shortlist",
        f"- Date: {target.isoformat()}",
        f"- Total candidates: {len(rows)}",
        f"- Accepted: {len(accepts)}",
        f"- Watch: {len(watch)}",
        f"- Rejected/low-priority: {len(rejected)}",
        "",
        "## Accepted first",
        *table(accepts, limit=35),
        "",
        "## Watch only",
        *table(watch, limit=25),
        "",
        "## Sport coverage notes",
        *_status_rows(target),
        "",
        "## Method",
        "- Basketball uses local accepted BetExplorer current rows, market probability, probability margin, and local fixture odds.",
        "- Table tennis uses the generated future picks plus local ML odds where available.",
        "- Tennis, hockey, handball, volleyball, baseball, cricket, American football, futsal, darts, and snooker rows are public 1xBet market-watch signals only. They are not entry picks until local backtests and source gates are completed.",
        "- Public-watch rows now pass through a lab calibration layer that splits high-variance paths such as tennis qualification/UTR and college baseball away from stronger prime-watch paths.",
        "- Very low odds are not removed automatically, but they are marked so the operator can avoid low-return risk.",
        "- This is a ranking tool, not a guarantee.",
    ]
    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Rank non-football 1xBet-style candidates from local data.")
    parser.add_argument("--date", required=True, help="Target date YYYY-MM-DD.")
    parser.add_argument("--out-csv", default="")
    parser.add_argument("--out-md", default="")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    target = pd.to_datetime(args.date, errors="raise").date()
    rows = _basketball_rows(target) + _tabletennis_rows(target) + _public_market_watch_rows(target)
    rows.sort(key=lambda r: (r["Decision"].startswith("ACCEPT"), r["BrainScore"], r["Prob"]), reverse=True)
    out_csv = Path(args.out_csv) if args.out_csv else BASE_DIR / "reports" / f"other_sports_1xbet_candidates_{target.isoformat()}.csv"
    out_md = Path(args.out_md) if args.out_md else BASE_DIR / "reports" / f"other_sports_1xbet_candidates_{target.isoformat()}.md"
    _write_csv(rows, out_csv)
    _write_md(rows, target, out_md)
    linefeed_history_rows = _append_linefeed_history(rows)
    print(f"Wrote {out_csv}")
    print(f"Wrote {out_md}")
    print(f"linefeed_history_rows={linefeed_history_rows}")
    print(f"accepted={sum(1 for r in rows if str(r['Decision']).startswith('ACCEPT'))} total={len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
