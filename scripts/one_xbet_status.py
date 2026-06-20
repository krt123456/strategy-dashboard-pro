"""Shared helpers for interpreting 1xBet confirmation statuses."""
from __future__ import annotations

CONFIRMED_1XBET_STATUSES = {
    "AUTO_MATCHED",
    "PUBLIC_ODDS_CONFIRMED",
    "1XBET_PUBLIC_API",
    "1XBET_PUBLIC_LINEFEED",
    "1XBET_LINEFEED_SNAPSHOT",
    "1XBET_PUBLIC_LINEFEED_CONFIRMED",
    "1XBET_LINEFEED_SNAPSHOT_CONFIRMED",
}


def is_confirmed_1xbet_status(value: object) -> bool:
    status = str(value or "").strip().upper()
    if status in CONFIRMED_1XBET_STATUSES:
        return True
    if status.startswith("1XBET_") and "CONFIRMED" in status:
        return True
    return False
