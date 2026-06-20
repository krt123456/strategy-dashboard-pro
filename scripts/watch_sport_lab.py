#!/usr/bin/env python3
"""Shared lab calibration for non-core public-watch sports.

The goal is not to promote these sports directly into live entry decisions.
Instead, this module:
1. Splits watch sports into more specific strategy variants.
2. Calibrates raw public-market probabilities with volatility/memory penalties.
3. Exposes the same logic to live shortlist generation and historical replay.
"""
from __future__ import annotations

import csv
import math
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Tuple

from sports_strategy_profiles import get_profile, normalize_sport_key

BASE_DIR = Path(__file__).resolve().parents[1]
MEMORY_CSV = BASE_DIR / "data" / "prediction_result_memory.csv"

VARIANT_RULES: Dict[str, Dict[str, Any]] = {
    "tennis_prime": {
        "label": "TENNIS_PRIME",
        "prob_penalty": 0.0,
        "margin_penalty": 0.0,
        "prob_floor_delta": 0.0,
        "margin_floor_delta": 0.0,
        "odds_soft_cap": 1.45,
        "note": "main draw singles path",
        "force_exclude": False,
    },
    "tennis_doubles": {
        "label": "TENNIS_DOUBLES_DEEP_LAB",
        "prob_penalty": 0.035,
        "margin_penalty": 0.04,
        "prob_floor_delta": 0.03,
        "margin_floor_delta": 0.04,
        "odds_soft_cap": 1.40,
        "note": "pair chemistry / serve-return variance",
        "force_exclude": False,
    },
    "tennis_qualification": {
        "label": "TENNIS_HIGH_VARIANCE_QUAL",
        "prob_penalty": 0.025,
        "margin_penalty": 0.03,
        "prob_floor_delta": 0.015,
        "margin_floor_delta": 0.03,
        "odds_soft_cap": 1.42,
        "note": "qualification draw variance",
        "force_exclude": False,
    },
    "tennis_utr": {
        "label": "TENNIS_MICRO_TOUR",
        "prob_penalty": 0.05,
        "margin_penalty": 0.05,
        "prob_floor_delta": 0.03,
        "margin_floor_delta": 0.05,
        "odds_soft_cap": 1.38,
        "note": "UTR/private tour variance",
        "force_exclude": True,
    },
    "baseball_college": {
        "label": "BASEBALL_DEEP_LAB_COLLEGE",
        "prob_penalty": 0.04,
        "margin_penalty": 0.04,
        "prob_floor_delta": 0.03,
        "margin_floor_delta": 0.04,
        "odds_soft_cap": 1.55,
        "note": "college rotation volatility",
        "force_exclude": False,
    },
    "baseball_pro": {
        "label": "BASEBALL_DEEP_LAB_PRO",
        "prob_penalty": 0.025,
        "margin_penalty": 0.03,
        "prob_floor_delta": 0.02,
        "margin_floor_delta": 0.03,
        "odds_soft_cap": 1.58,
        "note": "pitcher/lineup/weather dependency",
        "force_exclude": False,
    },
    "hockey_core": {
        "label": "HOCKEY_GOALIE_MONITOR",
        "prob_penalty": 0.015,
        "margin_penalty": 0.02,
        "prob_floor_delta": 0.0,
        "margin_floor_delta": 0.02,
        "odds_soft_cap": 1.75,
        "note": "goalie/rest context required",
        "force_exclude": False,
    },
    "handball_core": {
        "label": "HANDBALL_SELECTED_LEAGUES",
        "prob_penalty": 0.012,
        "margin_penalty": 0.02,
        "prob_floor_delta": 0.0,
        "margin_floor_delta": 0.02,
        "odds_soft_cap": 1.48,
        "note": "selected-league watch only",
        "force_exclude": False,
    },
    "volleyball_core": {
        "label": "VOLLEYBALL_SET_MONITOR",
        "prob_penalty": 0.012,
        "margin_penalty": 0.025,
        "prob_floor_delta": 0.0,
        "margin_floor_delta": 0.025,
        "odds_soft_cap": 1.48,
        "note": "set volatility watch",
        "force_exclude": False,
    },
    "cricket_core": {
        "label": "CRICKET_POST_TOSS_ONLY",
        "prob_penalty": 0.018,
        "margin_penalty": 0.03,
        "prob_floor_delta": 0.0,
        "margin_floor_delta": 0.03,
        "odds_soft_cap": 1.50,
        "note": "toss/lineup dependency",
        "force_exclude": False,
    },
    "americanfootball_core": {
        "label": "AMFOOTBALL_QB_MONITOR",
        "prob_penalty": 0.018,
        "margin_penalty": 0.025,
        "prob_floor_delta": 0.0,
        "margin_floor_delta": 0.025,
        "odds_soft_cap": 1.65,
        "note": "QB/injury/weather dependency",
        "force_exclude": False,
    },
    "futsal_core": {
        "label": "FUTSAL_GOAL_VOLATILITY",
        "prob_penalty": 0.018,
        "margin_penalty": 0.03,
        "prob_floor_delta": 0.0,
        "margin_floor_delta": 0.03,
        "odds_soft_cap": 1.48,
        "note": "goal volatility watch",
        "force_exclude": False,
    },
    "darts_core": {
        "label": "DARTS_FORMAT_MONITOR",
        "prob_penalty": 0.015,
        "margin_penalty": 0.025,
        "prob_floor_delta": 0.0,
        "margin_floor_delta": 0.025,
        "odds_soft_cap": 1.45,
        "note": "short-format variance",
        "force_exclude": False,
    },
    "snooker_core": {
        "label": "SNOOKER_FORMAT_MONITOR",
        "prob_penalty": 0.02,
        "margin_penalty": 0.03,
        "prob_floor_delta": 0.0,
        "margin_floor_delta": 0.03,
        "odds_soft_cap": 1.45,
        "note": "frame-format variance",
        "force_exclude": False,
    },
}


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, float(value)))


def _as_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        out = float(value)
        if math.isnan(out):
            return None
        return out
    except Exception:
        return None


def canonical_watch_variant(sport: object, league: object) -> str:
    sport_key = normalize_sport_key(sport)
    league_text = str(league or "").lower()
    if sport_key == "tennis":
        if "utr" in league_text:
            return "tennis_utr"
        if "doubles" in league_text:
            return "tennis_doubles"
        if "qualification" in league_text or re.search(r"\bqual\b", league_text):
            return "tennis_qualification"
        return "tennis_prime"
    if sport_key == "baseball":
        if "ncaa" in league_text or "college" in league_text:
            return "baseball_college"
        return "baseball_pro"
    return f"{sport_key}_core"


@lru_cache(maxsize=1)
def watch_memory_by_sport() -> Dict[str, Dict[str, float]]:
    by_sport: Dict[str, list[int]] = {}
    if not MEMORY_CSV.exists():
        return {}
    with MEMORY_CSV.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if str(row.get("ProbabilitySource") or "") != "public_market_watch_strategy":
                continue
            outcome = str(row.get("PickOutcome") or "")
            if outcome not in {"CORRECT", "WRONG"}:
                continue
            sport = normalize_sport_key(row.get("Sport"))
            if not sport:
                continue
            bucket = by_sport.setdefault(sport, [0, 0])
            bucket[0] += 1
            if outcome == "CORRECT":
                bucket[1] += 1
    out: Dict[str, Dict[str, float]] = {}
    for sport, (sample, correct) in by_sport.items():
        out[sport] = {
            "sample": float(sample),
            "correct": float(correct),
            "accuracy": round(correct / sample, 6) if sample else 0.0,
        }
    return out


@lru_cache(maxsize=1)
def watch_memory_by_variant() -> Dict[Tuple[str, str], Dict[str, float]]:
    by_variant: Dict[Tuple[str, str], list[int]] = {}
    if not MEMORY_CSV.exists():
        return {}
    with MEMORY_CSV.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if str(row.get("ProbabilitySource") or "") != "public_market_watch_strategy":
                continue
            outcome = str(row.get("PickOutcome") or "")
            if outcome not in {"CORRECT", "WRONG"}:
                continue
            sport = normalize_sport_key(row.get("Sport"))
            variant = canonical_watch_variant(sport, row.get("League"))
            if not sport or not variant:
                continue
            bucket = by_variant.setdefault((sport, variant), [0, 0])
            bucket[0] += 1
            if outcome == "CORRECT":
                bucket[1] += 1
    out: Dict[Tuple[str, str], Dict[str, float]] = {}
    for key, (sample, correct) in by_variant.items():
        out[key] = {
            "sample": float(sample),
            "correct": float(correct),
            "accuracy": round(correct / sample, 6) if sample else 0.0,
        }
    return out


def _memory_snapshot(sport_key: str, variant: str) -> Dict[str, float]:
    variant_row = watch_memory_by_variant().get((sport_key, variant))
    if variant_row:
        return variant_row
    return watch_memory_by_sport().get(sport_key, {"sample": 0.0, "correct": 0.0, "accuracy": 0.0})


def evaluate_public_watch_candidate(
    *,
    sport: object,
    league: object,
    base_prob: float,
    odds: float | None,
    margin: float | None = None,
) -> Dict[str, Any]:
    sport_key = normalize_sport_key(sport)
    profile = get_profile(sport_key) or {}
    variant = canonical_watch_variant(sport_key, league)
    rules = VARIANT_RULES.get(variant, {})
    memory = _memory_snapshot(sport_key, variant)
    sample = int(memory.get("sample", 0.0))
    accuracy = float(memory.get("accuracy", 0.0))
    min_prob = _as_float(profile.get("min_prob")) or 0.60
    min_margin = _as_float(profile.get("min_margin")) or 0.10
    prob_penalty = float(rules.get("prob_penalty", 0.0))
    margin_penalty = float(rules.get("margin_penalty", 0.0))
    soft_cap = _as_float(rules.get("odds_soft_cap"))
    notes = [str(rules.get("note") or "").strip()]

    if sample >= 6:
        if accuracy < 0.60:
            prob_penalty += 0.035
            margin_penalty += 0.03
            notes.append("weak realized memory")
        elif accuracy < 0.68:
            prob_penalty += 0.015
            margin_penalty += 0.015
            notes.append("watch-only memory needs caution")
        elif accuracy >= 0.75:
            prob_penalty = max(0.0, prob_penalty - 0.005)
            notes.append("strong realized memory")
    elif sample < 3:
        prob_penalty += 0.008
        margin_penalty += 0.01
        notes.append("sample still exploratory")

    if odds is not None and soft_cap is not None and odds > soft_cap:
        prob_penalty += 0.008
        margin_penalty += 0.01
        notes.append(f"odds_above_soft_cap_{soft_cap:.2f}")

    calibrated_prob = _clamp(base_prob - prob_penalty, 0.501, 0.999)
    calibrated_margin = None if margin is None else max(0.0, float(margin) - margin_penalty)
    dynamic_min_prob = min(0.90, max(min_prob, min_prob + float(rules.get("prob_floor_delta", 0.0))))
    dynamic_min_margin = min(0.60, max(min_margin, min_margin + float(rules.get("margin_floor_delta", 0.0))))

    include = calibrated_prob >= dynamic_min_prob
    if calibrated_margin is not None:
        include = include and calibrated_margin >= dynamic_min_margin
    if rules.get("force_exclude"):
        include = False
        notes.append("forced deep-lab exclude")
    if sport_key == "baseball" and sample >= 6 and accuracy < 0.60:
        include = False
        notes.append("baseball remains deep-lab until context repair")
    if variant == "tennis_doubles":
        if sample < 8:
            include = False
            notes.append("doubles path still below sample floor")
        elif accuracy < 0.65:
            include = False
            notes.append("doubles path under live accuracy floor")
    if variant == "tennis_qualification" and sample >= 3 and accuracy < 0.50:
        include = False
        notes.append("qualification path under live floor")

    accuracy_score = accuracy * 100.0 if sample else 56.0
    sample_score = min(100.0, sample * 10.0)
    prob_score = _clamp(((calibrated_prob - 0.50) / 0.20) * 100.0, 0.0, 100.0)
    reliability = round(_clamp(prob_score * 0.45 + accuracy_score * 0.35 + sample_score * 0.20, 0.0, 100.0), 2)

    if not include:
        if "baseball" in variant or variant in {"tennis_utr", "tennis_doubles"}:
            lab_tier = "DEEP_LAB"
        elif sample < 3:
            lab_tier = "EXPLORATORY_BLOCKED"
        else:
            lab_tier = "WATCH_BLOCKED"
    elif sport_key == "tennis" and variant == "tennis_prime" and sample >= 10 and accuracy >= 0.80:
        lab_tier = "PRIME_WATCH"
    elif sample >= 6 and accuracy >= 0.68:
        lab_tier = "PROMISING_WATCH"
    elif sample < 3:
        lab_tier = "EXPLORATORY_WATCH"
    else:
        lab_tier = "STANDARD_WATCH"

    return {
        "sport_key": sport_key,
        "strategy_variant": variant,
        "strategy_variant_label": str(rules.get("label") or variant.upper()),
        "lab_tier": lab_tier,
        "include": include,
        "calibrated_prob": round(calibrated_prob, 6),
        "calibrated_margin": round(calibrated_margin, 6) if calibrated_margin is not None else None,
        "dynamic_min_prob": round(dynamic_min_prob, 6),
        "dynamic_min_margin": round(dynamic_min_margin, 6),
        "reliability_score": reliability,
        "memory_sample": sample,
        "memory_accuracy": round(accuracy, 6) if sample else 0.0,
        "prob_penalty": round(prob_penalty, 6),
        "margin_penalty": round(margin_penalty, 6),
        "notes": "; ".join(item for item in notes if item),
    }
