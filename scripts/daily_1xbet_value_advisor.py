#!/usr/bin/env python3
"""Create a daily 1xBet-aware shortlist with value and stake sizing.

The script ranks already-generated app picks. It does not place bets and does
not log in to 1xBet. A positive stake means "candidate worth manual platform
verification", not a guaranteed outcome.
"""
from __future__ import annotations

import argparse
import csv
import math
import re
import subprocess
import sys
import unicodedata
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

import pandas as pd
import requests

BASE_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = BASE_DIR / "scripts"
REPORTS_DIR = BASE_DIR / "reports"
DEFAULT_MANUAL_1XBET_ODDS = BASE_DIR / "data" / "manual_1xbet_odds.csv"
DEFAULT_MIN_EDGE = 0.015
DEFAULT_PRICE_TARGET_GAP = 10.0
DEFAULT_MAX_1XBET_ODDS_AGE_MIN = 60.0
DEFAULT_ENTRY_LOCKOUT_MIN = 10.0

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

try:
    from one_xbet_status import is_confirmed_1xbet_status
    from sports_strategy_profiles import sport_ids
except Exception:  # pragma: no cover
    def is_confirmed_1xbet_status(value: object) -> bool:
        return str(value or "") in {"AUTO_MATCHED", "PUBLIC_ODDS_CONFIRMED"}

    def sport_ids() -> Dict[str, int]:
        return {"football": 1, "basketball": 3}

try:
    from fill_overrides_1xbet_websearch import (  # type: ignore
        DEFAULT_CACHE,
        SPORT_IDS,
        _best_match,
        _load_cache,
        _save_cache,
        _search,
    )
except Exception:  # pragma: no cover - the advisor still works without 1xBet search.
    DEFAULT_CACHE = BASE_DIR / "data" / "tmp" / "1xbet_daily_search_cache.json"
    SPORT_IDS = {"football": 1, "basketball": 3}
    _best_match = None
    _load_cache = None
    _save_cache = None
    _search = None

SPORT_IDS.update(sport_ids())


def _target_date(value: str) -> date:
    today = date.today()
    raw = (value or "today").strip().lower()
    if raw in {"today", "اليوم"}:
        return today
    if raw in {"tomorrow", "غدا", "غداً"}:
        return today + timedelta(days=1)
    return date.fromisoformat(raw)


def _as_float(value: Any) -> Optional[float]:
    try:
        if value is None or (isinstance(value, float) and math.isnan(value)):
            return None
        out = float(value)
        if math.isnan(out):
            return None
        return out
    except Exception:
        return None


def _norm_key(value: Any) -> str:
    text = "" if value is None else str(value)
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def _manual_odds_key(row: Dict[str, Any]) -> tuple[str, str, str, str, str]:
    return (
        str(row.get("Date") or "").strip()[:10],
        _norm_key(row.get("Sport")),
        _norm_key(row.get("Home")),
        _norm_key(row.get("Away")),
        _norm_key(row.get("Pick")),
    )


def _parse_note_fields(note: Any) -> Dict[str, str]:
    fields: Dict[str, str] = {}
    for part in str(note or "").split(";"):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key and value:
            fields[key] = value
    return fields


def _has_value(value: Any) -> bool:
    if value in (None, ""):
        return False
    try:
        if isinstance(value, float) and math.isnan(value):
            return False
    except Exception:
        pass
    text = str(value).strip()
    return bool(text and text.lower() != "nan")


def _set_default(row: Dict[str, Any], key: str, value: Any) -> None:
    if not _has_value(row.get(key)):
        row[key] = value


def _parse_checked_at(value: Any) -> Optional[datetime]:
    raw = str(value or "").strip()
    if not raw or raw.lower() == "nan":
        return None
    normalized = raw.replace("Z", "+00:00")
    for candidate in (normalized, normalized.replace(" ", "T", 1)):
        try:
            dt = datetime.fromisoformat(candidate)
            return dt.astimezone() if dt.tzinfo is not None else dt.astimezone()
        except Exception:
            pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(raw, fmt).astimezone()
        except Exception:
            pass
    return None


def _odds_age_minutes(value: Any) -> Optional[float]:
    dt = _parse_checked_at(value)
    if dt is None:
        return None
    now = datetime.now(dt.tzinfo)
    return max(0.0, (now - dt).total_seconds() / 60.0)


def _parse_event_start_utc(value: Any) -> Optional[datetime]:
    raw = str(value or "").strip()
    if not raw or raw.lower() == "nan":
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc) if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _run(cmd: List[str], timeout_s: int) -> int:
    proc = subprocess.run(cmd, cwd=BASE_DIR, check=False, timeout=timeout_s)
    return int(proc.returncode)


def _ensure_inputs(
    target: date,
    *,
    refresh_picks: bool,
    football_summary: str = "",
    football_csv: str = "",
    other_csv: str = "",
    other_md: str = "",
) -> Dict[str, str]:
    football_path = Path(football_csv) if football_csv else REPORTS_DIR / f"daily_picks_{target.isoformat()}.csv"
    other_csv_path = Path(other_csv) if other_csv else REPORTS_DIR / f"other_sports_1xbet_candidates_{target.isoformat()}.csv"
    other_md_path = (
        Path(other_md)
        if other_md
        else (
            other_csv_path.with_suffix(".md")
            if other_csv
            else REPORTS_DIR / f"other_sports_1xbet_candidates_{target.isoformat()}.md"
        )
    )
    paths = {
        "football": str(football_path),
        "other": str(other_csv_path),
    }
    py = sys.executable
    if refresh_picks or not football_path.exists():
        cmd = [
            py,
            str(SCRIPTS_DIR / "daily_select.py"),
            "--date",
            target.isoformat(),
            "--out",
            str(football_path),
        ]
        if football_summary:
            cmd.extend(["--summary", football_summary])
        _run(
            cmd,
            timeout_s=300,
        )
    if refresh_picks or not other_csv_path.exists():
        _run(
            [
                py,
                str(SCRIPTS_DIR / "other_sports_1xbet_candidates.py"),
                "--date",
                target.isoformat(),
                "--out-csv",
                str(other_csv_path),
                "--out-md",
                str(other_md_path),
            ],
            timeout_s=240,
        )
    return paths


def _odds_flag(odds: Optional[float]) -> str:
    if odds is None:
        return "NO_ODDS"
    if odds <= 1.05:
        return "VERY_LOW_RETURN"
    if odds <= 1.12:
        return "LOW_RETURN"
    if odds <= 1.55:
        return "NORMAL_FAV"
    if odds <= 2.05:
        return "VALUE_OR_VOLATILE"
    return "HIGH_VOLATILITY"


def _stake_cap(row: Dict[str, Any]) -> float:
    decision = str(row.get("Decision") or "")
    sport = str(row.get("Sport") or "")
    if sport == "football":
        return 0.015
    if decision == "ACCEPT_STRONG":
        return 0.012
    if decision in {"ACCEPT_MEDIUM", "ACCEPT_SECONDARY"}:
        return 0.008
    if decision.startswith("WATCH"):
        return 0.0
    return 0.0


def _apply_manual_1xbet_odds(rows: List[Dict[str, Any]], path: Path) -> int:
    for row in rows:
        row["LocalOdds"] = row.get("PickOdds")
        _set_default(row, "OddsSourceUsed", "LOCAL_SOURCE")
        _set_default(row, "OneXBetManualOdds", None)
        _set_default(row, "OneXBetManualCheckedAt", None)
        _set_default(row, "OneXBetManualSource", None)
        _set_default(row, "OneXBetManualEventId", None)
        _set_default(row, "OneXBetManualCanonicalId", None)
        _set_default(row, "OneXBetManualLeague", None)
        _set_default(row, "OneXBetManualEventDate", None)
        _set_default(row, "OneXBetManualStartUtc", None)
        _set_default(row, "OneXBetManualMatchScore", None)
        _set_default(row, "OneXBetPublicBase", None)
        _set_default(row, "OneXBetManualNote", None)
        _set_default(row, "OneXBetOddsAgeMin", None)
        _set_default(row, "OneXBetOddsFreshness", "LOCAL_OR_NO_1XBET_PRICE")
        _set_default(row, "OneXBetOddsMaxAgeMin", None)
        _set_default(row, "MinutesToStart", None)
        _set_default(row, "EventTimingStatus", "UNKNOWN_START")
        if _has_value(row.get("OneXBetManualOdds")):
            row["OddsSourceUsed"] = "1XBET_MANUAL"
    if not path.exists():
        return 0
    try:
        df = pd.read_csv(path)
    except Exception:
        return 0
    required = {"Date", "Sport", "Home", "Away", "Pick", "OneXBetOdds"}
    if df.empty or not required.issubset(set(df.columns)):
        return 0

    odds_map: Dict[tuple[str, str, str, str, str], Dict[str, Any]] = {}
    for _, rec in df.iterrows():
        odds = _as_float(rec.get("OneXBetOdds"))
        if odds is None or odds <= 1.0:
            continue
        key = _manual_odds_key(rec.to_dict())
        if not all(key):
            continue
        odds_map[key] = {
            "odds": odds,
            "checked_at": rec.get("CheckedAt"),
            "source": rec.get("Source") or "manual_1xbet_odds.csv",
            "note": rec.get("Note"),
            "note_fields": _parse_note_fields(rec.get("Note")),
        }

    applied = 0
    for row in rows:
        hit = odds_map.get(_manual_odds_key(row))
        if not hit:
            continue
        row["OneXBetManualOdds"] = hit["odds"]
        row["OneXBetManualCheckedAt"] = hit["checked_at"]
        row["OneXBetManualSource"] = hit["source"]
        row["OneXBetManualNote"] = hit["note"]
        row["PickOdds"] = hit["odds"]
        row["OddsSourceUsed"] = "1XBET_MANUAL"
        row["OddsFlag"] = _odds_flag(hit["odds"])
        fields = hit["note_fields"]
        row["OneXBetManualEventId"] = fields.get("event_id")
        row["OneXBetManualCanonicalId"] = fields.get("canonical_id")
        row["OneXBetManualLeague"] = fields.get("league")
        row["OneXBetManualEventDate"] = fields.get("event_date")
        row["OneXBetManualStartUtc"] = fields.get("start_utc")
        row["OneXBetManualMatchScore"] = fields.get("match_score")
        row["OneXBetPublicBase"] = fields.get("base")
        applied += 1
    return applied


def _apply_odds_freshness_gate(rows: List[Dict[str, Any]], max_age_min: float) -> None:
    for row in rows:
        row["OneXBetOddsMaxAgeMin"] = max_age_min
        if not row.get("OneXBetManualOdds"):
            row["OneXBetOddsFreshness"] = "LOCAL_OR_NO_1XBET_PRICE"
            continue

        age = _odds_age_minutes(row.get("OneXBetManualCheckedAt"))
        if age is None:
            row["OneXBetOddsAgeMin"] = None
            row["OneXBetOddsFreshness"] = "UNKNOWN_AGE"
        else:
            row["OneXBetOddsAgeMin"] = round(age, 1)
            row["OneXBetOddsFreshness"] = "FRESH" if age <= max_age_min else "STALE"

        if str(row.get("ValueVerdict") or "").startswith("ENTER") and row["OneXBetOddsFreshness"] != "FRESH":
            row["StakePct"] = 0.0
            row["StakeAmount"] = 0.0
            row["ActionVerdict"] = "RECHECK_1XBET_PRICE_BEFORE_ENTRY"
            row["ValueVerdict"] = (
                "RECHECK_STALE_1XBET_ODDS"
                if row["OneXBetOddsFreshness"] == "STALE"
                else "RECHECK_1XBET_ODDS_TIME_UNKNOWN"
            )
            if str(row.get("Decision") or "").startswith("ACCEPT"):
                row["Decision"] = "RECHECK_1XBET_PRICE"


def _apply_event_timing_gate(rows: List[Dict[str, Any]], lockout_min: float) -> None:
    now = datetime.now(timezone.utc)
    for row in rows:
        start = _parse_event_start_utc(row.get("OneXBetStartUtc") or row.get("OneXBetManualStartUtc"))
        if start is None:
            row["MinutesToStart"] = None
            row["EventTimingStatus"] = "UNKNOWN_START"
            continue
        minutes = (start - now).total_seconds() / 60.0
        row["MinutesToStart"] = round(minutes, 1)
        if minutes <= 0:
            row["EventTimingStatus"] = "STARTED_OR_EXPIRED"
        elif minutes <= lockout_min:
            row["EventTimingStatus"] = "CLOSE_TO_START"
        else:
            row["EventTimingStatus"] = "SCHEDULED"

        if str(row.get("ValueVerdict") or "").startswith("ENTER") and row["EventTimingStatus"] != "SCHEDULED":
            row["StakePct"] = 0.0
            row["StakeAmount"] = 0.0
            row["ActionVerdict"] = "RECHECK_EVENT_TIME_BEFORE_ENTRY"
            row["ValueVerdict"] = (
                "RECHECK_EVENT_ALREADY_STARTED"
                if row["EventTimingStatus"] == "STARTED_OR_EXPIRED"
                else "RECHECK_CLOSE_TO_START"
            )
            if str(row.get("Decision") or "").startswith("ACCEPT"):
                row["Decision"] = "RECHECK_EVENT_TIME"


def _apply_entry_readiness(rows: List[Dict[str, Any]], min_edge: float) -> None:
    for row in rows:
        blockers: List[str] = []
        odds = _as_float(row.get("PickOdds"))
        prob = _as_float(row.get("Prob"))
        ev_pct = _as_float(row.get("EVPercent"))
        min_entry = _as_float(row.get("MinEntryOdds"))
        stake = _as_float(row.get("StakeAmount")) or 0.0
        status = str(row.get("OneXBetStatus") or "")
        freshness = str(row.get("OneXBetOddsFreshness") or "")
        timing = str(row.get("EventTimingStatus") or "")
        action = str(row.get("ActionVerdict") or "")
        verdict = str(row.get("ValueVerdict") or "")

        if prob is None or odds is None:
            blockers.append("missing_price_or_probability")
        if ev_pct is not None and ev_pct < min_edge * 100.0:
            blockers.append("ev_below_minimum")
        if odds is not None and min_entry is not None and odds < min_entry:
            blockers.append("price_below_target")
        if freshness == "STALE":
            blockers.append("stale_1xbet_price")
        elif freshness == "UNKNOWN_AGE":
            blockers.append("unknown_1xbet_price_age")
        if not is_confirmed_1xbet_status(status):
            blockers.append("unconfirmed_1xbet_event")
        if timing == "STARTED_OR_EXPIRED":
            blockers.append("event_started_or_expired")
        elif timing == "CLOSE_TO_START":
            blockers.append("event_close_to_start")
        if stake <= 0 and verdict.startswith("ENTER"):
            blockers.append("stake_zero_on_entry")
        if action == "CHECK_STAKE_RULES":
            blockers.append("stake_rules_blocked")

        if verdict.startswith("ENTER") and not blockers:
            readiness = "ENTRY_CANDIDATE_SOURCE_REVIEW"
        elif verdict.startswith("RECHECK"):
            readiness = "RECHECK_PRICE"
        elif action in {"PRICE_TARGET_NEAR", "PRICE_TARGET_WAIT"}:
            readiness = "PRICE_TARGET_ONLY"
        elif verdict.startswith("WATCH"):
            readiness = "WATCH_ONLY"
        else:
            readiness = "NO_ENTRY"

        row["GateBlockers"] = ";".join(dict.fromkeys(blockers)) if blockers else "none"
        row["EntryReadiness"] = readiness


def _compute_value(
    row: Dict[str, Any],
    bankroll: float,
    min_edge: float = DEFAULT_MIN_EDGE,
    price_target_gap: float = DEFAULT_PRICE_TARGET_GAP,
) -> Dict[str, Any]:
    prob = _as_float(row.get("Prob"))
    odds = _as_float(row.get("PickOdds"))
    if prob is None or odds is None or prob <= 0:
        row.update(
            {
                "FairOdds": None,
                "MinEntryOdds": None,
                "PriceGapPct": None,
                "Edge": None,
                "EVPercent": None,
                "KellyFull": 0.0,
                "StakePct": 0.0,
                "StakeAmount": 0.0,
                "ValueVerdict": "NO_VALUE_DATA",
                "ActionVerdict": "MISSING_PRICE_OR_PROB",
            }
        )
        return row
    if odds <= 1.0:
        row.update(
            {
                "FairOdds": round(1.0 / prob, 3),
                "MinEntryOdds": round((1.0 + min_edge) / prob, 3),
                "PriceGapPct": None,
                "Edge": None,
                "EVPercent": None,
                "KellyFull": 0.0,
                "StakePct": 0.0,
                "StakeAmount": 0.0,
                "ValueVerdict": "NO_BET_BAD_ODDS",
                "ActionVerdict": "NO_BET",
            }
        )
        return row

    fair_odds = 1.0 / prob
    min_entry_odds = (1.0 + min_edge) / prob
    price_gap_pct = ((min_entry_odds / odds) - 1.0) * 100.0
    edge = (prob * odds) - 1.0
    kelly_full = edge / (odds - 1.0) if edge > 0 and odds > 1.0 else 0.0
    cap = _stake_cap(row)
    # Quarter Kelly with hard caps. This keeps the recommendation conservative.
    stake_pct = min(max(kelly_full * 0.25, 0.0), cap)
    if edge < min_edge:
        stake_pct = 0.0
    if odds <= 1.05 and edge < 0.03:
        stake_pct = 0.0

    if stake_pct <= 0:
        verdict = "NO_BET_LOW_VALUE" if edge <= 0 else "WATCH_VALUE_TOO_SMALL"
        if price_gap_pct <= 0:
            action = "CHECK_STAKE_RULES"
        elif price_gap_pct <= 3.0:
            action = "PRICE_TARGET_NEAR"
        elif price_gap_pct <= price_target_gap:
            action = "PRICE_TARGET_WAIT"
        else:
            action = "NO_BET_PRICE_TOO_LOW"
    elif edge >= 0.04 and stake_pct >= 0.008:
        verdict = "ENTER_STRONG_VALUE"
        action = "ENTER_NOW_AFTER_SOURCE_CHECK"
    elif edge >= 0.015:
        verdict = "ENTER_CONTROLLED"
        action = "ENTER_NOW_AFTER_SOURCE_CHECK"
    else:
        verdict = "WATCH_SMALL_EDGE"
        action = "CHECK_STAKE_RULES"

    row.update(
        {
            "FairOdds": round(fair_odds, 3),
            "MinEntryOdds": round(min_entry_odds, 3),
            "PriceGapPct": round(price_gap_pct, 2),
            "Edge": round(edge, 5),
            "EVPercent": round(edge * 100.0, 2),
            "KellyFull": round(kelly_full, 5),
            "StakePct": round(stake_pct, 5),
            "StakeAmount": round(bankroll * stake_pct, 3),
            "ValueVerdict": verdict,
            "ActionVerdict": action,
        }
    )
    return row


def _normalize_entry_decision(row: Dict[str, Any]) -> Dict[str, Any]:
    value = str(row.get("ValueVerdict") or "")
    if value.startswith("ENTER"):
        return row
    decision = str(row.get("Decision") or "")
    if value.startswith("NO_BET") or value == "NO_VALUE_DATA":
        if decision == "ACCEPT_FOOTBALL":
            row["Decision"] = "NO_ENTRY_FOOTBALL_VALUE"
        elif decision.startswith("ACCEPT"):
            row["Decision"] = "NO_ENTRY_VALUE"
    elif value.startswith("WATCH") and decision.startswith("ACCEPT"):
        row["Decision"] = "WATCH_VALUE_ONLY"
    return row


def _pick_football_odds(row: Dict[str, Any], pred: str) -> Optional[float]:
    key = {"H": "OddsH", "D": "OddsD", "A": "OddsA"}.get(pred)
    return _as_float(row.get(key)) if key else None


def _load_football(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    try:
        df = pd.read_csv(path)
    except Exception:
        return []
    rows: List[Dict[str, Any]] = []
    for _, r in df.iterrows():
        rec = r.to_dict()
        pred = str(rec.get("Pred") or "").strip()
        home = str(rec.get("Home") or "").strip()
        away = str(rec.get("Away") or "").strip()
        prob = _as_float(rec.get("Conf"))
        odds = _pick_football_odds(rec, pred)
        if not home or not away or prob is None:
            continue
        pick = {"H": home, "A": away, "D": "Draw"}.get(pred, pred)
        rows.append(
            {
                "Sport": "football",
                "Date": rec.get("Date"),
                "League": rec.get("League"),
                "Home": home,
                "Away": away,
                "Pick": pick,
                "Side": pred,
                "Prob": round(float(prob), 6),
                "ProbabilitySource": "football_decision_model",
                "Margin": None,
                "BrainScore": round(float(prob) * 100.0, 2),
                "Grade": "A" if prob >= 0.75 else "B",
                "PickOdds": odds,
                "OddsFlag": _odds_flag(odds),
                "Decision": "ACCEPT_FOOTBALL",
                "Source": str(path.relative_to(BASE_DIR)),
            }
        )
    return rows


def _load_other(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    try:
        df = pd.read_csv(path)
    except Exception:
        return []
    rows = []
    for _, r in df.iterrows():
        rec = r.to_dict()
        for field in [
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
        ]:
            if pd.isna(rec.get(field)):
                rec[field] = ""
        decision = str(rec.get("Decision") or "")
        if decision == "REJECT":
            continue
        rec["PickOdds"] = _as_float(rec.get("PickOdds"))
        rec["OddsFlag"] = rec.get("OddsFlag") or _odds_flag(rec.get("PickOdds"))
        if not rec.get("ProbabilitySource"):
            sport = str(rec.get("Sport") or "").lower()
            if str(rec.get("StrategyGate") or "").upper().startswith("WATCH_ONLY"):
                rec["ProbabilitySource"] = "public_market_watch_strategy"
            else:
                rec["ProbabilitySource"] = "market_consensus_strategy" if sport in {"basketball", "tabletennis"} else "local_model"
        rows.append(rec)
    return rows


def _verify_1xbet(rows: List[Dict[str, Any]], target: date, *, limit_rows: int, timeout_s: float) -> None:
    if _search is None or _best_match is None or _load_cache is None or _save_cache is None:
        for row in rows:
            row["OneXBetStatus"] = "SEARCH_UNAVAILABLE"
        return
    cache_path = Path(DEFAULT_CACHE)
    cache = _load_cache(cache_path)
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0"})
    checked = 0
    for row in rows:
        manual_event_id = row.get("OneXBetManualEventId")
        if manual_event_id:
            row["OneXBetStatus"] = "PUBLIC_ODDS_CONFIRMED"
            row["OneXBetEventId"] = manual_event_id
            row["OneXBetCanonicalId"] = row.get("OneXBetManualCanonicalId")
            row["OneXBetLeague"] = row.get("OneXBetManualLeague")
            row["OneXBetDate"] = row.get("OneXBetManualEventDate")
            row["OneXBetStartUtc"] = row.get("OneXBetManualStartUtc")
            row["OneXBetMatchScore"] = row.get("OneXBetManualMatchScore")
            continue
        sport = str(row.get("Sport") or "").lower()
        sport_id = SPORT_IDS.get(sport)
        if sport_id is None:
            row["OneXBetStatus"] = "NEEDS_MANUAL_PLATFORM_CHECK"
            continue
        if checked >= limit_rows:
            row["OneXBetStatus"] = "NOT_CHECKED_LIMIT"
            continue
        home = str(row.get("Home") or "")
        away = str(row.get("Away") or "")
        best = None
        query = f"{home} {away}"
        events = _search(
            session,
            query,
            limit=25,
            timeout_s=timeout_s,
            retries=0,
            sleep_s=0.02,
            cache=cache,
        )
        candidate = _best_match(home, away, target, events, sport_id, 1)
        if candidate and (best is None or candidate.score > best.score):
            best = candidate
        checked += 1
        if best and best.score >= 4:
            row["OneXBetStatus"] = "AUTO_MATCHED"
            row["OneXBetEventId"] = best.event_id
            row["OneXBetCanonicalId"] = getattr(best, "canonical_id", None)
            row["OneXBetLeague"] = best.league
            row["OneXBetDate"] = best.event_date
            row["OneXBetMatchScore"] = best.score
        else:
            row["OneXBetStatus"] = "NEEDS_MANUAL_PLATFORM_CHECK"
            row["OneXBetEventId"] = None
            row["OneXBetCanonicalId"] = None
            row["OneXBetLeague"] = None
            row["OneXBetDate"] = None
            row["OneXBetMatchScore"] = best.score if best else 0
    _save_cache(cache_path, cache)


def _rank_score(row: Dict[str, Any]) -> float:
    brain = _as_float(row.get("BrainScore")) or 0.0
    edge_pct = _as_float(row.get("EVPercent")) or -25.0
    stake_pct = (_as_float(row.get("StakePct")) or 0.0) * 100.0
    status = str(row.get("OneXBetStatus") or "")
    status_bonus = 8.0 if is_confirmed_1xbet_status(status) else -4.0 if status.startswith("NEEDS") else 0.0
    value = str(row.get("ValueVerdict") or "")
    value_bonus = 10.0 if value.startswith("ENTER") else -8.0 if value.startswith("NO_BET") else -5.0 if value.startswith("RECHECK") else 0.0
    reliability = _as_float(row.get("ReliabilityScore")) or 0.0
    lab_tier = str(row.get("LabTier") or "")
    lab_bonus = 2.5 if lab_tier == "PRIME_WATCH" else 1.5 if lab_tier == "PROMISING_WATCH" else -1.5 if lab_tier == "DEEP_LAB" else 0.0
    return round(brain * 0.44 + edge_pct * 0.34 + stake_pct * 2.0 + status_bonus + value_bonus + reliability * 0.06 + lab_bonus, 4)


def _write_csv(rows: Iterable[Dict[str, Any]], path: Path) -> None:
    rows = list(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "Rank",
        "Sport",
        "Date",
        "League",
        "Home",
        "Away",
        "Pick",
        "Prob",
        "PickOdds",
        "LocalOdds",
        "OneXBetManualOdds",
        "OneXBetManualCheckedAt",
        "OneXBetManualSource",
        "OneXBetManualEventId",
        "OneXBetManualCanonicalId",
        "OneXBetManualLeague",
        "OneXBetManualEventDate",
        "OneXBetManualStartUtc",
        "OneXBetOddsAgeMin",
        "OneXBetOddsFreshness",
        "OneXBetOddsMaxAgeMin",
        "OneXBetStartUtc",
        "MinutesToStart",
        "EventTimingStatus",
        "OddsSourceUsed",
        "FairOdds",
        "MinEntryOdds",
        "PriceGapPct",
        "EVPercent",
        "StakePct",
        "StakeAmount",
        "ActionVerdict",
        "EntryReadiness",
        "GateBlockers",
        "BrainScore",
        "ProbabilitySource",
        "Decision",
        "ValueVerdict",
        "OneXBetStatus",
        "OneXBetEventId",
        "OneXBetCanonicalId",
        "OneXBetLeague",
        "RankScore",
        "OddsFlag",
        "Source",
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
    cleaned_rows = []
    for row in rows:
        cleaned: Dict[str, Any] = {}
        for key, value in row.items():
            cleaned[key] = "" if pd.isna(value) else value
        cleaned_rows.append(cleaned)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(cleaned_rows)


def _table(rows: List[Dict[str, Any]], limit: int) -> List[str]:
    lines = [
        "| # | Sport | Match | Pick | Prob | Odds | Target | EV% | Stake | 1xBet | Fresh | Action | Verdict |",
        "| ---: | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- | --- | --- | --- |",
    ]
    for row in rows[:limit]:
        odds = row.get("PickOdds")
        odds_s = "" if odds is None or pd.isna(odds) else f"{float(odds):.2f}"
        target = row.get("MinEntryOdds")
        target_s = "" if target is None or pd.isna(target) else f"{float(target):.2f}"
        lines.append(
            f"| {row.get('Rank')} | {row.get('Sport')} | {row.get('Home')} vs {row.get('Away')} | "
            f"{row.get('Pick')} | {float(row.get('Prob') or 0):.3f} | {odds_s} | {target_s} | "
            f"{float(row.get('EVPercent') or 0):.2f} | {float(row.get('StakeAmount') or 0):.3f} | "
            f"{row.get('OneXBetStatus')} | {row.get('OneXBetOddsFreshness')} | {row.get('ActionVerdict')} | {row.get('ValueVerdict')} |"
        )
    if not rows:
        lines.append("| - | - | - | - | - | - | - | - | - | - | - | - | - |")
    return lines


def _write_md(
    rows: List[Dict[str, Any]],
    target: date,
    bankroll: float,
    top: int,
    path: Path,
    *,
    manual_odds_applied: int,
    min_edge: float,
    max_odds_age_min: float,
) -> None:
    enter = [r for r in rows if str(r.get("ValueVerdict") or "").startswith("ENTER")]
    recheck = [r for r in rows if str(r.get("ValueVerdict") or "").startswith("RECHECK")]
    price_targets = [
        r
        for r in rows
        if str(r.get("ActionVerdict") or "") in {"PRICE_TARGET_NEAR", "PRICE_TARGET_WAIT"}
    ]
    no_bet = [
        r
        for r in rows
        if str(r.get("ValueVerdict") or "").startswith("NO_BET")
        or str(r.get("ValueVerdict") or "") == "NO_VALUE_DATA"
    ]
    manual = [r for r in rows if str(r.get("OneXBetStatus") or "").startswith("NEEDS")]
    stale_odds = [r for r in rows if str(r.get("OneXBetOddsFreshness") or "") == "STALE"]
    blocker_counts: Counter[str] = Counter()
    for row in rows:
        for blocker in str(row.get("GateBlockers") or "").split(";"):
            if blocker and blocker != "none":
                blocker_counts[blocker] += 1
    blocker_lines = [f"- {name}: {count}" for name, count in blocker_counts.most_common(12)]
    if not blocker_lines:
        blocker_lines = ["- none: 0"]
    lines = [
        "# Daily 1xBet value advisor",
        f"- Date: {target.isoformat()}",
        f"- Bankroll/base units: {bankroll:g}",
        f"- Candidates analyzed: {len(rows)}",
        f"- Enter candidates: {len(enter)}",
        f"- Recheck blocked candidates: {len(recheck)}",
        f"- Price-target candidates: {len(price_targets)}",
        f"- Need manual 1xBet check: {len(manual)}",
        f"- Manual 1xBet odds applied: {manual_odds_applied}",
        f"- Stale 1xBet prices: {len(stale_odds)}",
        f"- Minimum edge required: {min_edge * 100:.2f}%",
        f"- Max accepted 1xBet price age: {max_odds_age_min:g} minutes",
        "",
        "## Best candidates",
        *_table(enter, top),
        "",
        "## Blocked until 1xBet price is refreshed",
        *_table(recheck, min(top, 20)),
        "",
        "## Accepted only if 1xBet reaches target price",
        *_table(price_targets, min(top, 25)),
        "",
        "## Good probability but no value",
        *_table(no_bet, min(top, 20)),
        "",
        "## Manual platform check queue",
        *_table(manual, min(top, 20)),
        "",
        "## Gate blocker summary",
        *blocker_lines,
        "",
        "## Rules",
        "- Stake is fractional Kelly capped by sport and confidence.",
        "- A zero stake means the probability may be high, but the price is not worth entering.",
        "- Target is the minimum 1xBet odds required to clear the configured EV edge.",
        "- If you add current 1xBet prices to data/manual_1xbet_odds.csv, this report recalculates EV from those prices.",
        f"- Any 1xBet price older than {max_odds_age_min:g} minutes cannot be an entry until refreshed.",
        "- If 1xBet odds differ from local odds, rerun mentally with the displayed 1xBet price before entry.",
        "- This report is a decision aid, not a guarantee.",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _source_review_rows(rows: List[Dict[str, Any]], top: int) -> List[Dict[str, Any]]:
    selected: List[Dict[str, Any]] = []
    seen = set()

    def add_matching(predicate: Callable[[Dict[str, Any]], bool]) -> None:
        for row in rows:
            key = (row.get("Sport"), row.get("Home"), row.get("Away"), row.get("Pick"))
            if key in seen or not predicate(row):
                continue
            seen.add(key)
            selected.append(row)
            if len(selected) >= max(top, 1):
                return

    add_matching(lambda r: str(r.get("ValueVerdict") or "").startswith("ENTER"))
    add_matching(lambda r: str(r.get("ActionVerdict") or "") == "PRICE_TARGET_NEAR")
    add_matching(lambda r: str(r.get("ActionVerdict") or "") == "PRICE_TARGET_WAIT")
    add_matching(lambda r: str(r.get("OneXBetStatus") or "").startswith("NEEDS"))
    add_matching(lambda r: str(r.get("ValueVerdict") or "").startswith("WATCH"))
    add_matching(lambda r: True)
    return selected[: max(top, 1)]


def _write_source_review_md(rows: List[Dict[str, Any]], target: date, top: int, path: Path) -> None:
    review_rows = _source_review_rows(rows, top)
    lines = [
        "# 1xBet source review queue",
        f"- Date: {target.isoformat()}",
        f"- Candidates queued: {len(review_rows)}",
        "- Default state: MANUAL_CHECK until current external sources are reviewed.",
        "",
        "## Decision gates",
        "- Confirm exact event, start time, market, and current price on 1xBet.",
        "- Reject or refresh any stale 1xBet price before considering entry.",
        "- Recompute value against the current 1xBet price. If EV is negative, mark NO_BET.",
        "- Use at least two independent non-model sources for news, injuries, lineups, schedule, and recent form.",
        "- Use odds movement or market comparison when available without account creation or blocked access.",
        "- Use weather/venue checks for outdoor sports where conditions can materially change the edge.",
        "- If a critical source is blocked, inconsistent, or missing, keep MANUAL_CHECK instead of ENTER.",
        "",
    ]
    if not review_rows:
        lines.extend(["## Queue", "No candidates available for source review."])
    for row in review_rows:
        match = f"{row.get('Home')} vs {row.get('Away')}"
        lines.extend(
            [
                f"## {row.get('Rank')}. {match}",
                f"- Sport: {row.get('Sport')}",
                f"- League: {row.get('League')}",
                f"- Pick: {row.get('Pick')}",
                f"- Model probability: {float(row.get('Prob') or 0):.3f}",
                f"- Probability source: {row.get('ProbabilitySource')}",
                f"- Odds used: {row.get('PickOdds')}",
                f"- Local/source odds before manual override: {row.get('LocalOdds')}",
                f"- Minimum 1xBet odds for entry: {row.get('MinEntryOdds')}",
                f"- Price gap to target %: {row.get('PriceGapPct')}",
                f"- Odds source used: {row.get('OddsSourceUsed')}",
                f"- 1xBet price checked at: {row.get('OneXBetManualCheckedAt')}",
                f"- 1xBet price age minutes: {row.get('OneXBetOddsAgeMin')}",
                f"- 1xBet price freshness: {row.get('OneXBetOddsFreshness')}",
                f"- 1xBet event id: {row.get('OneXBetEventId')}",
                f"- 1xBet canonical id: {row.get('OneXBetCanonicalId')}",
                f"- 1xBet start UTC: {row.get('OneXBetStartUtc')}",
                f"- Minutes to start: {row.get('MinutesToStart')}",
                f"- Event timing status: {row.get('EventTimingStatus')}",
                f"- Fair odds: {row.get('FairOdds')}",
                f"- EV% on odds used: {row.get('EVPercent')}",
                f"- Suggested stake before source review: {row.get('StakeAmount')}",
                f"- Entry readiness: {row.get('EntryReadiness')}",
                f"- Gate blockers: {row.get('GateBlockers')}",
                f"- 1xBet auto status: {row.get('OneXBetStatus')}",
                f"- Local data source: {row.get('Source')}",
                "",
                "```text",
                "1xBet match/market/current odds:",
                "Official source:",
                "News/injuries/lineups source:",
                "Independent stats source:",
                "Odds movement/comparison source:",
                "Weather/venue source if relevant:",
                "Conflicts or missing data:",
                "Final decision: ENTER / NO_BET / MANUAL_CHECK",
                "Reason:",
                "```",
                "",
            ]
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Daily 1xBet value/stake advisor.")
    parser.add_argument("--date", default="today", help="today, tomorrow, or YYYY-MM-DD")
    parser.add_argument("--bankroll", type=float, default=100.0, help="Base bankroll/units used for stake amount.")
    parser.add_argument("--top", type=int, default=12)
    parser.add_argument("--refresh-picks", action="store_true")
    parser.add_argument("--no-verify-1xbet", action="store_true")
    parser.add_argument("--verify-limit", type=int, default=12)
    parser.add_argument("--one-x-timeout", type=float, default=3.0)
    parser.add_argument("--min-edge", type=float, default=DEFAULT_MIN_EDGE, help="Minimum EV edge required for entry, e.g. 0.015.")
    parser.add_argument("--price-target-gap", type=float, default=DEFAULT_PRICE_TARGET_GAP, help="Max percent gap to show in the 1xBet target-price watchlist.")
    parser.add_argument("--max-1xbet-odds-age-min", type=float, default=DEFAULT_MAX_1XBET_ODDS_AGE_MIN, help="Block entry if confirmed 1xBet odds are older than this.")
    parser.add_argument("--entry-lockout-min", type=float, default=DEFAULT_ENTRY_LOCKOUT_MIN, help="Block entry if the event starts within this many minutes.")
    parser.add_argument("--manual-1xbet-odds", default=str(DEFAULT_MANUAL_1XBET_ODDS))
    parser.add_argument("--football-summary", default="", help="Optional summary CSV to pass into daily_select.py.")
    parser.add_argument("--football-picks-csv", default="", help="Optional path for generated football picks input.")
    parser.add_argument("--other-picks-csv", default="", help="Optional path for generated other-sports picks input.")
    parser.add_argument("--other-picks-md", default="", help="Optional path for generated other-sports markdown summary.")
    parser.add_argument("--out-csv", default="")
    parser.add_argument("--out-md", default="")
    parser.add_argument("--out-source-review", default="")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    target = _target_date(args.date)
    paths = _ensure_inputs(
        target,
        refresh_picks=args.refresh_picks,
        football_summary=args.football_summary,
        football_csv=args.football_picks_csv,
        other_csv=args.other_picks_csv,
        other_md=args.other_picks_md,
    )
    rows = _load_football(Path(paths["football"])) + _load_other(Path(paths["other"]))
    manual_odds_applied = _apply_manual_1xbet_odds(rows, Path(args.manual_1xbet_odds))
    rows = [_normalize_entry_decision(_compute_value(r, args.bankroll, args.min_edge, args.price_target_gap)) for r in rows]
    _apply_odds_freshness_gate(rows, args.max_1xbet_odds_age_min)
    _apply_event_timing_gate(rows, args.entry_lockout_min)
    rows = [r for r in rows if str(r.get("Decision") or "") != "REJECT"]
    rows.sort(
        key=lambda r: (
            str(r.get("ValueVerdict") or "").startswith("ENTER"),
            _as_float(r.get("EVPercent")) or -99.0,
            _as_float(r.get("BrainScore")) or 0.0,
        ),
        reverse=True,
    )
    if not args.no_verify_1xbet:
        _verify_1xbet(rows, target, limit_rows=args.verify_limit, timeout_s=args.one_x_timeout)
    else:
        for row in rows:
            row["OneXBetStatus"] = "NOT_CHECKED"
    _apply_entry_readiness(rows, args.min_edge)
    for row in rows:
        row["RankScore"] = _rank_score(row)
    rows.sort(
        key=lambda r: (
            str(r.get("ValueVerdict") or "").startswith("ENTER"),
            is_confirmed_1xbet_status(r.get("OneXBetStatus")),
            _as_float(r.get("RankScore")) or -999.0,
        ),
        reverse=True,
    )
    for idx, row in enumerate(rows, start=1):
        row["Rank"] = idx

    out_csv = Path(args.out_csv) if args.out_csv else REPORTS_DIR / f"daily_1xbet_value_advisor_{target.isoformat()}.csv"
    out_md = Path(args.out_md) if args.out_md else REPORTS_DIR / f"daily_1xbet_value_advisor_{target.isoformat()}.md"
    out_source_review = (
        Path(args.out_source_review)
        if args.out_source_review
        else REPORTS_DIR / f"1xbet_source_review_queue_{target.isoformat()}.md"
    )
    _write_csv(rows, out_csv)
    _write_md(
        rows,
        target,
        args.bankroll,
        args.top,
        out_md,
        manual_odds_applied=manual_odds_applied,
        min_edge=args.min_edge,
        max_odds_age_min=args.max_1xbet_odds_age_min,
    )
    _write_source_review_md(rows, target, args.top, out_source_review)
    print(f"Wrote {out_csv}")
    print(f"Wrote {out_md}")
    print(f"Wrote {out_source_review}")
    print(f"enter={sum(1 for r in rows if str(r.get('ValueVerdict') or '').startswith('ENTER'))} total={len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
