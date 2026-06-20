#!/usr/bin/env python3
"""Shared participant-name quality checks for sport candidate pipelines."""
from __future__ import annotations

import re
import unicodedata
from typing import Any, List

_GENERIC_COMPACT_NAMES = {
    "home",
    "away",
    "team",
    "team1",
    "team2",
    "teama",
    "teamb",
    "player",
    "player1",
    "player2",
    "playera",
    "playerb",
    "competitor",
    "competitor1",
    "competitor2",
    "participant",
    "participant1",
    "participant2",
    "tbd",
    "tba",
    "unknown",
    "na",
    "none",
    "null",
}

_GENERIC_TEXT_RE = re.compile(
    r"^(home|away|team\s*[12ab]?|player\s*[12ab]?|competitor\s*[12ab]?|"
    r"participant\s*[12ab]?|tbd|tba|to\s+be\s+(decided|announced)|"
    r"unknown|n/?a|none|null)$",
    re.IGNORECASE,
)


def compact_name(value: Any) -> str:
    text = "" if value is None else str(value)
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def normalized_text(value: Any) -> str:
    text = "" if value is None else str(value)
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", text.strip().lower())


def is_placeholder_participant(value: Any) -> bool:
    raw = str(value or "").strip()
    if not raw:
        return False
    text = normalized_text(raw)
    compact = compact_name(raw)
    if compact and compact in _GENERIC_COMPACT_NAMES:
        return True
    return bool(text and _GENERIC_TEXT_RE.match(text))


def participant_quality_flags(home: Any, away: Any) -> List[str]:
    flags: List[str] = []
    home_raw = str(home or "").strip()
    away_raw = str(away or "").strip()
    home_key = compact_name(home_raw)
    away_key = compact_name(away_raw)

    if not home_raw or not away_raw:
        flags.append("missing_participant_name")
    if is_placeholder_participant(home_raw) or is_placeholder_participant(away_raw):
        flags.append("placeholder_participant_name")
    if home_key and away_key and home_key == away_key:
        flags.append("identical_participants")
    return flags


def has_bad_participant_pair(home: Any, away: Any) -> bool:
    return bool(participant_quality_flags(home, away))
