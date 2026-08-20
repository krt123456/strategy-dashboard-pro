#!/usr/bin/env python3
"""Build the professional bilingual strategy-monitoring PWA from the database.

Home = best-bet hero + full strategy monitor (bankroll from $100, 4% flat stake,
record, days, trust score 0-100, risk badge, streak). Tap a strategy -> its
matches with the bet-on team highlighted. ⋮ menu sorts/ filters. History tab.
Auto-rebuilt daily so it tracks every GitHub update and new strategy.
"""
from __future__ import annotations

import csv
import json
import math
import sqlite3
import unicodedata
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Mapping, Optional

import build_telegram_portfolio as portfolio

PROJECT_DIR = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_DIR / "data" / "betting_journal.db"
OUT_DIR = PROJECT_DIR / "dashboard"
LINEFEED_PATH = PROJECT_DIR / "data" / "one_xbet_linefeed_snapshot.csv"
LOCK_DIR = PROJECT_DIR / "reports" / "locked_forecasts"
BACKTEST_PATH = PROJECT_DIR / "reports" / "publication_backtest_2026-08-20.json"
BACKTEST_FALLBACK_PATH = PROJECT_DIR / "reports" / "publication_backtest.json"
FLAT_STAKE = 4.0  # 4% of $100 bankroll per bet

# استراتيجيات معطّلة (تشخيص خبير: anti-edges / نموذج بميزات وهمية) — تُعرض كـ"مُقصاة" للشفافية
DISABLED_BASES = {"aggressive", "balanced", "conservative", "lightgbm_calibrated"}


def _f(v):
    try:
        return float(v) if v not in (None, "") else 0.0
    except Exception:
        return 0.0


def _risk(avg_odds: float) -> str:
    if avg_odds <= 0:
        return "?"
    if avg_odds < 1.6:
        return "safe"
    if avg_odds < 2.3:
        return "balanced"
    return "bold"


def _trust(wins: int, bets: int, roi: float, days: int) -> int:
    if bets == 0:
        return 0
    wr = wins / bets
    wr_pts = min(wr, 0.85) / 0.85 * 35
    sample_pts = min(bets / 50, 1) * 25
    roi_pts = max(-1, min(roi / 40, 1)) * 25
    if roi < 0:
        roi_pts = max(-15, roi / 10)  # negative ROI penalizes but not catastrophically
    days_pts = min(days / 14, 1) * 15
    return max(0, min(100, int(wr_pts + sample_pts + roi_pts + days_pts)))


def _rationale(strategy: str, odds: float, prob: float) -> dict:
    """One-line AR/EN explanation for why a pick was made."""
    base = strategy.split("__")[0].split("_v")[0]
    r = {
        "market_strong": ("توافق السوق على مرشّح قوي", "Strong market consensus favorite"),
        "pure_elo": ("نموذج ELO يفضّل هذا الفريق", "ELO model favors this side"),
        "market_extreme": ("مرشّح متطّرف جداً (ثقة عالية)", "Extreme favorite, high confidence"),
        "clear_favorite": ("هامش واضح لصالح هذا الفريق", "Clear favorite by margin"),
        "away_dominant": ("الفريق الضيف هو الأقوى", "Dominant away side"),
        "coinflip_home_premium": ("ميزة الأرض في مباراة متقاربة", "Home edge in a coinflip"),
        "contrarian_home_coinflip": ("ميزة الأرض المسعّرة ناقصاً", "Underpriced home advantage"),
        "home_market_favorite": ("المضيف + السوق يراه مرشّحاً", "Home + market favorite"),
        "underdog_value": ("قيمة في الخارج المهمَّش", "Value in the underdog"),
        "trapfree_favorite": ("مرشّح واضح بتجنّب فخّ الأسعار", "Clear fav, avoids odds trap"),
        "elo_coinflip_combo": ("ELO + مباراة متقاربة (تأكيد مزدوج)", "ELO + coinflip double-confirm"),
        "vig_aware_value": ("حافة تتجاوز عمولة الـ bookmaker", "Edge exceeds the vig"),
        "thick_edge_favorite": ("حافة سميكة vig-resistant", "Thick vig-resistant edge"),
        "nova_steam_home": ("حركة odds صاعدة على المضيف = رهان الأذكياء", "Steam move on home = sharp money"),
        "nova_steam_away": ("حركة odds صاعدة على الضيف = رهان الأذكياء", "Steam move on away = sharp money"),
        "nova_baseball_away": ("ميزة الضيف المُسكّرة في البيسبول (+13%)", "Underpriced away edge in baseball (+13%)"),
        "nova_volley_home": ("ثبات المضيف القوي في الطائرة", "Strong-home consistency in volleyball"),
        "nova_sweet_spot": ("شريحة السعر الذهبية 2.0-2.5", "Golden odds zone 2.0-2.5"),
        "nova_pickem": ("مركز even-money (أقل أثر عمولة)", "Even-money center (minimal vig)"),
        "nova_underdog": ("قيمة الكلب المتوسط", "Mid-underdog value"),
        "nova_fade_favorite": ("مكافحة المفضّل المُبالغ تسعيره", "Fading the overpriced favorite"),
    }.get(base, ("إشارة استراتيجية موجبة", "Positive strategy signal"))
    return {"ar": r[0], "en": r[1]}


def _normalise(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return "".join(character for character in text if character.isalnum())


def _finite_decimal(value: Any) -> Optional[Decimal]:
    try:
        number = Decimal(str(value).strip().replace(",", "."))
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() else None


def _parse_aware_timestamp(value: Any) -> Optional[datetime]:
    raw = str(value or "").strip()
    if not raw or ("T" not in raw and " " not in raw):
        return None
    if raw.endswith(("Z", "z")):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    try:
        return parsed.astimezone(timezone.utc)
    except (OverflowError, ValueError):
        return None


def _ui_pick(
    *,
    sport: str,
    league: str,
    home: str,
    away: str,
    pick: str,
    odds: Decimal,
    probability: Optional[Decimal],
    start_utc: datetime,
    event_id: str = "",
    official_lock: bool,
) -> dict:
    price = float(odds)
    prob = float(probability) if probability is not None else 0.0
    rationale = _rationale(portfolio.LEGACY_STRATEGY, price, prob)
    return {
        "sport": sport,
        "league": league,
        "home": home,
        "away": away,
        "pick": pick,
        "odds": round(price, 3),
        "strategy": portfolio.LEGACY_STRATEGY,
        "source": portfolio.XBET_SOURCE,
        "real": True,
        "approved": True,
        "official_lock": official_lock,
        "event_id": event_id,
        "pay10": round(10 * price, 2),
        "rat_ar": rationale["ar"],
        "rat_en": rationale["en"],
        "status": "upcoming",
        "start_utc": start_utc.isoformat(timespec="seconds").replace("+00:00", "Z"),
    }


def _official_lock_picks(path: Path, target: date, now_utc: datetime) -> tuple[str, list[dict]]:
    """Return ``(state, picks)`` where state is missing, valid, or invalid.

    A present but malformed lock never falls back to a mutable database query.
    That distinction is intentional: an invalid official artifact is a reason
    to publish nothing until an operator fixes it.
    """

    try:
        if not path.exists():
            return "missing", []
        if not path.is_file() or path.stat().st_size == 0:
            return "invalid", []
    except OSError:
        return "invalid", []
    required = set(portfolio.LOCK_FIELDS)
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            headers = {str(item or "").strip() for item in (reader.fieldnames or [])}
            if not required.issubset(headers):
                return "invalid", []
            rows = list(reader)
    except (OSError, csv.Error, UnicodeError):
        return "invalid", []
    if len(rows) > portfolio.MAX_ALLOWED_PICKS:
        return "invalid", []

    ranked: list[tuple[int, dict]] = []
    ranks: set[int] = set()
    prediction_ids: set[int] = set()
    identities: set[tuple[str, ...]] = set()
    lock_instants: set[datetime] = set()
    for row in rows:
        try:
            rank = int(str(row.get("Rank") or "").strip())
            prediction_id = int(str(row.get("PredictionId") or "").strip())
        except ValueError:
            return "invalid", []
        if not 1 <= rank <= portfolio.MAX_ALLOWED_PICKS or rank in ranks:
            return "invalid", []
        if prediction_id <= 0 or prediction_id in prediction_ids:
            return "invalid", []

        sport = str(row.get("Sport") or "").strip()
        league = str(row.get("League") or "").strip()
        home = str(row.get("Home") or "").strip()
        away = str(row.get("Away") or "").strip()
        pick = str(row.get("Pick") or "").strip()
        event_id = str(row.get("EventId") or "").strip()
        odds = _finite_decimal(row.get("OddsAtPrediction"))
        probability = _finite_decimal(row.get("Prob"))
        start = _parse_aware_timestamp(row.get("StartUtc"))
        locked_at = _parse_aware_timestamp(row.get("LockedAt"))
        captured_at = _parse_aware_timestamp(row.get("OddsCapturedAt"))
        if not all((sport, home, away, pick)) or start is None or odds is None:
            return "invalid", []
        if locked_at is None or captured_at is None:
            return "invalid", []
        if (start - locked_at).total_seconds() < 3600 or (start - captured_at).total_seconds() < 3600:
            return "invalid", []
        if probability is not None and not Decimal("0") <= probability <= Decimal("1"):
            return "invalid", []
        if not portfolio.MIN_ODDS <= odds <= portfolio.MAX_ODDS:
            return "invalid", []
        if _normalise(pick) not in {_normalise(home), _normalise(away)}:
            return "invalid", []
        if start.astimezone(portfolio.BERLIN_TZ).date() != target:
            return "invalid", []
        if str(row.get("ForecastDate") or "").strip() != target.isoformat():
            return "invalid", []
        if str(row.get("StrategyGate") or "").strip() != portfolio.LEGACY_STRATEGY:
            return "invalid", []
        if str(row.get("Source") or "").strip() != portfolio.XBET_SOURCE:
            return "invalid", []
        if str(row.get("OfficialEntry") or "").strip().casefold() != "yes":
            return "invalid", []
        if str(row.get("FinalDecision") or "").strip() != "APPROVED_FOR_PUBLICATION":
            return "invalid", []
        if str(row.get("ForecastPurpose") or "").strip() != "HYPOTHETICAL_PAPER_SIMULATION":
            return "invalid", []

        identity = (
            _normalise(sport),
            f"event:{_normalise(event_id)}" if event_id else _normalise(home),
            "" if event_id else _normalise(away),
            "" if event_id else start.isoformat(),
            _normalise(pick),
        )
        if identity in identities:
            return "invalid", []
        identities.add(identity)
        ranks.add(rank)
        prediction_ids.add(prediction_id)
        lock_instants.add(locked_at)

        # The lock stays immutable, but a fixture that has started is no
        # longer actionable and must disappear from the public dashboard.
        if start <= now_utc:
            continue
        ranked.append(
            (
                rank,
                _ui_pick(
                    sport=sport,
                    league=league,
                    home=home,
                    away=away,
                    pick=pick,
                    odds=odds,
                    probability=probability,
                    start_utc=start,
                    event_id=event_id,
                    official_lock=True,
                ),
            )
        )
    if ranks != set(range(1, len(rows) + 1)) or len(lock_instants) > 1:
        return "invalid", []
    return "valid", [pick for _, pick in sorted(ranked, key=lambda item: item[0])]


def _dynamic_picks(db_path: Path, linefeed_path: Path, target: date, now_utc: datetime) -> list[dict]:
    try:
        candidates, _, _ = portfolio.select_daily_candidates(
            db_path,
            target,
            linefeed_csv=linefeed_path,
            min_lead_minutes=60,
            max_picks=portfolio.MAX_ALLOWED_PICKS,
            now_utc=now_utc,
        )
    except (portfolio.PortfolioBuildError, sqlite3.Error, OSError, UnicodeError, csv.Error, ValueError):
        return []
    return [
        _ui_pick(
            sport=item.sport,
            league=item.league,
            home=item.home,
            away=item.away,
            pick=item.pick,
            odds=item.odds or Decimal("0"),
            probability=item.probability,
            start_utc=item.start_utc,
            event_id=item.event_id,
            official_lock=False,
        )
        for item in candidates
    ]


def _backtest_candidates(explicit: Optional[Path]) -> list[Path]:
    if explicit is not None:
        return [explicit]
    candidates = [BACKTEST_PATH, BACKTEST_FALLBACK_PATH]
    candidates.extend(sorted((PROJECT_DIR / "reports").glob("publication_backtest_*.json"), reverse=True))
    return list(dict.fromkeys(candidates))


def _audited_strategy(explicit_path: Optional[Path] = None) -> Optional[dict]:
    payload: Optional[Mapping[str, Any]] = None
    for path in _backtest_candidates(explicit_path):
        if not path.exists() or not path.is_file():
            continue
        try:
            candidate = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if isinstance(candidate, Mapping):
            payload = candidate
            break
    if payload is None:
        return None
    decision = payload.get("decision")
    legacy = payload.get("legacy_strategy")
    if not isinstance(decision, Mapping) or not isinstance(legacy, Mapping):
        return None
    if payload.get("report_type") != "publication_backtest_audited_summary":
        return None
    if decision.get("strategy") != portfolio.LEGACY_STRATEGY or decision.get("strategy_status") != "legacy_active":
        return None
    metrics = legacy.get("complete_cutoff_metrics")
    if not isinstance(metrics, Mapping):
        return None
    try:
        bets = int(metrics["bets"])
        wins = int(metrics["wins"])
        losses = int(metrics["losses"])
        avg_odds = float(metrics["average_odds"])
        pnl = float(metrics["pnl_units"])
        roi = float(metrics["roi_pct"])
    except (KeyError, TypeError, ValueError):
        return None
    if bets < 0 or wins < 0 or losses < 0 or wins + losses != bets:
        return None
    if not all(math.isfinite(value) for value in (avg_odds, pnl, roi)) or avg_odds <= 0:
        return None

    days = 0
    methodology = payload.get("methodology")
    snapshot = payload.get("snapshot")
    if isinstance(methodology, Mapping) and isinstance(snapshot, Mapping):
        try:
            first_day = date.fromisoformat(str(snapshot["match_date_min"]))
            last_day = date.fromisoformat(str(methodology["automatic_cutoff"]))
            days = max(0, (last_day - first_day).days + 1)
        except (KeyError, TypeError, ValueError):
            days = 0
    return {
        "name": portfolio.LEGACY_STRATEGY,
        "source": portfolio.XBET_SOURCE,
        "bets": bets,
        "wins": wins,
        "losses": losses,
        "bankroll": round(100.0 + pnl, 2),
        "profit": round(pnl, 2),
        "roi": round(roi, 2),
        "days": days,
        "avg_odds": round(avg_odds, 2),
        "trust": _trust(wins, bets, roi, days),
        "streak": 0,
        "streak_kind": "w",
        "risk": _risk(avg_odds),
        "tier": "winner" if bets >= 15 and pnl > 0 else "neutral",
        "spark": [],
        "disabled": False,
        "backtest": True,
        "backtest_no_guarantee": True,
    }


def gather(
    today: str,
    *,
    now_utc: Optional[datetime] = None,
    db_path: Optional[Path] = None,
    linefeed_path: Optional[Path] = None,
    lock_path: Optional[Path] = None,
    backtest_path: Optional[Path] = None,
) -> dict:
    target = date.fromisoformat(today)
    now = now_utc or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("now_utc must be timezone-aware")
    now = now.astimezone(timezone.utc)
    database = db_path or DB_PATH
    linefeed = linefeed_path or LINEFEED_PATH
    official_lock = lock_path or LOCK_DIR / f"forecast_lock_{target.isoformat()}.csv"

    lock_state, today_picks = _official_lock_picks(official_lock, target, now)
    if lock_state == "missing":
        today_picks = _dynamic_picks(database, linefeed, target, now)
        selection_source = "dynamic_legacy_policy" if today_picks else "none"
    elif lock_state == "valid":
        selection_source = "official_lock"
    else:
        # A corrupt/unauthorised lock is never bypassed by dynamic picks.
        today_picks = []
        selection_source = "invalid_lock_fail_closed"

    strategy = _audited_strategy(backtest_path)
    strategies = [strategy] if strategy is not None else []
    best = dict(today_picks[0]) if today_picks and today_picks[0].get("approved") else None
    if best is not None:
        best["trust"] = strategy["trust"] if strategy is not None else 0

    base = portfolio.LEGACY_STRATEGY.split("__")[0]
    compact = [
        {
            "h": pick["home"], "a": pick["away"], "p": pick["pick"], "o": pick["odds"],
            "s": pick["sport"], "lg": (pick["league"] or "")[:18], "pay": pick["pay10"],
        }
        for pick in today_picks
    ]
    today_by_base = {base: compact} if compact else {}
    if strategy is not None:
        strategy["today_bets"] = len(today_picks)

    total_bets = strategy["bets"] if strategy is not None else 0
    total_wins = strategy["wins"] if strategy is not None else 0
    total_profit = strategy["profit"] if strategy is not None else 0.0
    winrate = round(total_wins / total_bets * 100, 1) if total_bets else 0.0
    return {
        "today": target.isoformat(),
        "generated": now.isoformat(timespec="seconds"),
        "selection_source": selection_source,
        "performance_basis": "audited_backtest_no_guarantee" if strategy is not None else "unavailable",
        "headline": {
            "strategies": len(strategies),
            "bets": total_bets,
            "wins": total_wins,
            "profit": total_profit,
            "winrate": winrate,
        },
        "best": best,
        "strategies": strategies,
        "strat_map": {item["name"]: item for item in strategies},
        "today_by_base": today_by_base,
        "matches": {portfolio.LEGACY_STRATEGY: []} if strategy is not None else {},
        "picks": today_picks,
        "awaiting_result": 0,
    }


HTML = r"""<!DOCTYPE html>
<html lang="ar" dir="rtl"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<meta name="theme-color" content="#0b1220"><meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<title>استراتيجي برو</title><link rel="manifest" href="manifest.json">
<style>
/* ── Clean modern design system (2026-06-27): calmer palette, more whitespace,
      simpler cards, clear hierarchy. Winners surfaced, losers tucked away. ── */
:root{--bg:#0b1120;--surface:#151c2e;--surface2:#1c2640;--line:#243049;
--txt:#f1f5fb;--mut:#94a3bd;--faint:#5e6b85;
--green:#34d399;--green-dim:#10894f;--red:#f87171;--gold:#fbbf24;--accent:#60a5fa}
*{box-sizing:border-box;margin:0;padding:0;-webkit-tap-highlight-color:transparent}
body{font-family:'Segoe UI',system-ui,-apple-system,sans-serif;background:var(--bg);color:var(--txt);
line-height:1.55;padding-bottom:72px;font-size:15px}
.wrap{max-width:640px;margin:0 auto;padding:14px 14px 0}
/* header */
.hd{display:flex;align-items:center;justify-content:space-between;padding:14px 16px;
background:rgba(11,17,32,.85);backdrop-filter:blur(10px);position:sticky;top:0;z-index:30;border-bottom:1px solid var(--line)}
.hd h1{font-size:18px;font-weight:800;letter-spacing:-.3px}
.hd h1 b{color:var(--accent)}
.hd .r{display:flex;gap:8px;align-items:center}
.iconbtn{background:transparent;border:1px solid var(--line);color:var(--mut);width:36px;height:36px;
border-radius:11px;display:flex;align-items:center;justify-content:center;cursor:pointer;font-size:16px;font-weight:700;transition:.15s}
.iconbtn:active{background:var(--surface2);color:var(--txt)}
/* summary strip */
.stats{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin:16px 0 8px}
.stat{background:var(--surface);border-radius:14px;padding:14px 8px;text-align:center}
.stat .v{font-size:21px;font-weight:800;letter-spacing:-.5px}.stat .l{font-size:11px;color:var(--mut);margin-top:3px}
/* section heading */
.sec{font-size:13px;font-weight:700;color:var(--mut);margin:26px 4px 12px;text-transform:none;
display:flex;align-items:center;justify-content:space-between;letter-spacing:.2px}
.sec .cnt{font-size:12px;color:var(--faint);font-weight:600}
/* hero best bet */
.hero{background:linear-gradient(150deg,#10271d,#0f1a2c);border:1px solid var(--green-dim);border-radius:20px;
padding:18px;margin:6px 0 4px;box-shadow:0 6px 24px rgba(16,137,79,.12)}
.hero .lbl{font-size:12px;color:var(--green);font-weight:700;display:flex;align-items:center;gap:6px;margin-bottom:12px}
.hero .mtch{font-size:15px;font-weight:600;color:var(--mut);margin-bottom:14px;line-height:1.4}
.hero .row{display:flex;justify-content:space-between;align-items:center;gap:12px}
.hero .pk{font-size:18px;font-weight:800;color:var(--txt)}
.hero .od{background:var(--green);color:#06281a;padding:8px 18px;border-radius:12px;font-weight:800;font-size:20px;min-width:74px;text-align:center}
.hero .why{font-size:12px;color:var(--mut);margin-top:14px;padding-top:13px;border-top:1px solid rgba(255,255,255,.06);line-height:1.5}
/* pick card — minimal */
.pick{background:var(--surface);border-radius:16px;padding:15px 16px;margin:10px 0;transition:.15s}
.pick:active{background:var(--surface2)}
.pick .teams{display:flex;align-items:center;gap:8px;font-size:15px;flex-wrap:wrap}
.pick .me{font-weight:800;color:var(--green)}
.pick .vs{color:var(--faint);font-size:13px}.pick .ot{color:var(--mut);font-weight:500}
.pick .od{margin-inline-start:auto;background:var(--surface2);color:var(--gold);padding:5px 12px;border-radius:10px;font-weight:800;font-size:15px}
.pick .meta{display:flex;justify-content:space-between;align-items:center;margin-top:11px;font-size:12px;color:var(--mut)}
.pick .tag{font-size:11px;color:var(--faint)}
.pick .pay b{color:var(--green)}
.pick .why{font-size:12px;color:var(--accent);margin-top:9px}
/* strategy card — clean */
.scard{background:var(--surface);border-radius:16px;padding:15px 16px;margin:10px 0;cursor:pointer;transition:.15s;border-inline-start:3px solid transparent}
.scard:active{background:var(--surface2)}
.scard.win{border-inline-start-color:var(--green)}
.scard.lose{opacity:.62}
.scard .top{display:flex;justify-content:space-between;align-items:center;gap:10px}
.scard .nm{font-weight:700;font-size:14.5px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.scard .tier{font-size:10px;padding:2px 8px;border-radius:20px;font-weight:700;white-space:nowrap}
.tier.win{background:rgba(52,211,153,.14);color:var(--green)}
.tier.lose{background:rgba(248,113,113,.12);color:var(--red)}
.tier.cut{background:rgba(248,113,113,.12);color:var(--red)}
.scard .mid{display:flex;align-items:baseline;gap:10px;margin-top:12px}
.bank{font-size:24px;font-weight:800;letter-spacing:-.5px}
.bank.up{color:var(--green)}.bank.dn{color:var(--red)}
.scard .rec{font-size:13px;color:var(--mut)}.scard .rec b{color:var(--txt);font-weight:700}
.spark{height:30px;width:100%;margin-top:12px;display:block}
.scard .foot{display:flex;justify-content:space-between;align-items:center;margin-top:10px;font-size:11.5px;color:var(--faint)}
.scard .today{color:var(--gold);font-weight:600}
/* match history row */
.mrow{display:flex;align-items:center;gap:10px;padding:13px 0;border-bottom:1px solid var(--line)}
.mrow:last-child{border:none}
.mrow .res{width:8px;height:8px;border-radius:50%;flex-shrink:0}
.mrow .res.w{background:var(--green)}.mrow .res.l{background:var(--red)}
.mrow .info{flex:1;min-width:0}
.mrow .tm{font-size:13.5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.mrow .me{font-weight:700;color:var(--txt)}.mrow .ot{color:var(--mut)}
.mrow .sub{font-size:11px;color:var(--faint);margin-top:2px}
.mrow .sc{font-weight:800;font-size:14px;white-space:nowrap}
.mrow .od2{font-size:12px;color:var(--mut);min-width:38px;text-align:end}
/* detail header */
.back{display:inline-flex;align-items:center;gap:6px;cursor:pointer;color:var(--accent);font-weight:600;font-size:14px;padding:10px 0}
.dhead{background:var(--surface);border-radius:18px;padding:18px;margin-bottom:6px}
.dhead .nm{font-size:18px;font-weight:800;margin-bottom:4px}
.dhead .big{font-size:30px;font-weight:800;letter-spacing:-1px;margin:8px 0 2px}
.dgrid{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-top:14px}
.dgrid .c{background:var(--surface2);border-radius:11px;padding:10px 6px;text-align:center}
.dgrid .c .v{font-size:16px;font-weight:800}.dgrid .c .l{font-size:10px;color:var(--mut);margin-top:2px}
/* menu sheet */
.menu-modal{position:fixed;inset:0;background:rgba(0,0,0,.55);z-index:50;display:none;align-items:flex-end}
.menu-modal.open{display:flex}
.menu-panel{background:var(--surface);width:100%;max-width:640px;margin:0 auto;padding:20px 18px calc(20px + env(safe-area-inset-bottom));
border-radius:22px 22px 0 0}
.menu-panel h3{font-size:15px;margin-bottom:14px;font-weight:700}
.menu-opt{padding:14px 15px;border-radius:13px;cursor:pointer;margin-bottom:8px;background:var(--surface2);transition:.15s}
.menu-opt:active{background:var(--line)}
.menu-opt .t{font-weight:700;font-size:14px}.menu-opt .d{font-size:12px;color:var(--mut);margin-top:2px}
.empty{text-align:center;color:var(--faint);padding:48px 20px;font-size:14px}
/* bottom nav */
.bottombar{position:fixed;bottom:0;left:0;right:0;background:rgba(21,28,46,.92);backdrop-filter:blur(12px);
border-top:1px solid var(--line);display:flex;z-index:40;max-width:640px;margin:0 auto;padding-bottom:env(safe-area-inset-bottom)}
.bb{flex:1;text-align:center;padding:11px 0 13px;color:var(--faint);cursor:pointer;font-size:11px;font-weight:600;transition:.15s}
.bb svg{width:22px;height:22px;fill:currentColor;margin-bottom:3px;display:block;margin-inline:auto}.bb.active{color:var(--accent)}
.foot{text-align:center;font-size:11px;color:var(--faint);padding:20px}
.hide{display:none!important}
/* lock */
.lock{position:fixed;inset:0;background:var(--bg);z-index:100;display:flex;
flex-direction:column;align-items:center;justify-content:center;padding:30px}
.lock .logo{font-size:46px;margin-bottom:10px}.lock h2{font-size:22px;font-weight:800;margin-bottom:6px}
.lock h2 b{color:var(--accent)}
.lock .sub{font-size:13px;color:var(--mut);margin-bottom:26px}
.lock input{width:210px;text-align:center;background:var(--surface);border:1px solid var(--line);color:var(--txt);
font-size:24px;letter-spacing:10px;padding:15px;border-radius:14px;outline:none;font-weight:800;transition:.15s}
.lock input:focus{border-color:var(--accent)}
.lock button{margin-top:16px;background:var(--accent);color:#06203f;border:none;
padding:14px 40px;border-radius:14px;font-weight:800;font-size:15px;cursor:pointer}
.lock .err{color:var(--red);font-size:13px;margin-top:12px;min-height:18px}
</style></head><body>
<div class="lock" id="lock"><div class="logo">⚡</div><h2>استراتيجي <b>برو</b></h2>
<div class="sub" id="lkSub">أدخل الرقم السري</div>
<input type="password" id="pin" inputmode="numeric" maxlength="8" autofocus>
<button id="pinBtn" onclick="checkPin()">دخول</button>
<div class="err" id="pinErr"></div></div>
<div class="hd"><h1 id="title">⚡ استراتيجي <b>برو</b></h1>
<div class="r"><div class="iconbtn" id="refreshBtn" title="تحديث">⟳</div><div class="iconbtn" id="langBtn">EN</div><div class="iconbtn" id="menuBtn">⋮</div></div></div>
<div class="wrap" id="app"></div>
<div class="bottombar">
<div class="bb active" data-v="home"><svg viewBox="0 0 24 24"><path d="M12 3l9 8h-3v9h-4v-6H10v6H6v-9H3z"/></svg><span data-i="home"></span></div>
<div class="bb" data-v="picks"><svg viewBox="0 0 24 24"><path d="M12 2l3 7h7l-5.5 4.5L18 21l-6-4-6 4 1.5-7.5L2 9h7z"/></svg><span data-i="picks"></span></div>
<div class="bb" data-v="history"><svg viewBox="0 0 24 24"><path d="M13 3a9 9 0 00-9 9H1l3.9 3.9L5 16l4-4H6a7 7 0 117 7 7 7 0 01-5-2l-1.4 1.4A9 9 0 1013 3zm-1 5v5l4.3 2.5.7-1.2-3.5-2V8z"/></svg><span data-i="history"></span></div>
</div>
<div class="menu-modal" id="menuModal"><div class="menu-panel" id="menuPanel"></div></div>
<script>
const D=__DATA__,FLAT=4;
const I={
 ar:{title:"⚡ استراتيجي برو",home:"الرئيسية",picks:"توقعات اليوم",history:"السجل",
   bestBet:"⭐ أفضل رهان اليوم",monitor:"مراقبة الاستراتيجيات",todayPicks:"توقعات اليوم",
   historyLog:"سجلّ النتائج",profit:"ربح",bankroll:"رأس المال",record:"السجل",days:"أيام",
   trust:"ثقة",roi:"العائد",bets:"رهانات",strategies:"استراتيجيات",winrate:"نسبة الفوز",
   odds:"السعر",noData:"لا توجد بيانات بعد",myPick:"رهاني",score:"النتيجة",won:"فاز",lost:"خسر",
   bet10:"اراهن ١٠",win10:"تكسب",why:"لماذا؟",real:"odds حقيقية",safe:"آمن",balanced:"متوازن",bold:"جريء",
   mProfit:"أعلى ربحاً",mWin:"أعلى نسبة فوز",mTrust:"الأكثر ثقة",mSafe:"الآمنة فقط",
   mProfitD:"رتّب الاستراتيجيات حسب صافي الربح",mWinD:"رتّب حسب نسبة الفوز",mTrustD:"رتّب حسب درجة الثقة",
   mSafeD:"أظهر الاستراتيجيات الآمنة فقط",sortMenu:"ترتيب وفلترة",streakW:"فوز متتالي",streakL:"خسارة متتالية",
   tapHint:"اضغط لرؤية المباريات",netPro:"صافي الربح",allRes:"كل النتائج",cut:"مُقصاة",next24:"24 ساعة القادمة",todayB:"رهان اليوم",
   winner:"رابح",loser:"خاسر",topStrats:"الاستراتيجيات الرابحة",otherStrats:"باقي الاستراتيجيات",matches:"مباراة",
   backtestNoGuarantee:"Backtest تاريخي · لا ضمان للنتائج المستقبلية"},
 en:{title:"⚡ Strategy Pro",home:"Home",picks:"Today",history:"History",
   bestBet:"⭐ Best Bet Today",monitor:"Strategy Monitor",todayPicks:"Today's Picks",
   historyLog:"Results Log",profit:"Profit",bankroll:"Bankroll",record:"Record",days:"days",
   trust:"Trust",roi:"ROI",bets:"bets",strategies:"strategies",winrate:"Win rate",
   odds:"Odds",noData:"No data yet",myPick:"my pick",score:"Score",won:"WON",lost:"LOST",
   bet10:"Bet 10",win10:"win",why:"Why?",real:"real odds",safe:"Safe",balanced:"Balanced",bold:"Bold",
   mProfit:"Top Profit",mWin:"Top Win Rate",mTrust:"Most Trusted",mSafe:"Safe only",
   mProfitD:"Sort strategies by net profit",mWinD:"Sort by win rate",mTrustD:"Sort by trust score",
   mSafeD:"Show only safe strategies",sortMenu:"Sort & Filter",streakW:"win streak",streakL:"loss streak",
   tapHint:"Tap to view matches",netPro:"Net profit",allRes:"All results",cut:"CUT",next24:"Next 24h",todayB:"today",
   winner:"WIN",loser:"LOSS",topStrats:"Winning Strategies",otherStrats:"Other Strategies",matches:"matches",
   backtestNoGuarantee:"Historical backtest · no guarantee of future results"}
};
let lang='ar',view='home',sortKey='profit',filterRisk=null,openName=null,showAll=false;
const $=s=>document.querySelector(s),t=k=>I[lang][k];
function setLang(l){lang=l;document.documentElement.lang=l;document.documentElement.dir=l=='ar'?'rtl':'ltr';
 $('#langBtn').textContent=l=='ar'?'EN':'ع';$('#title').textContent=t('title');
 document.querySelectorAll('[data-i]').forEach(e=>e.textContent=t(e.dataset.i));render()}
function money(n){return'$'+Math.round(Math.abs(n))+(n<0?'-':'')}
function sign(n){return(n>=0?'+':'')+Math.round(n)}
function esc(s){return String(s).replace(/'/g,"\\'")}
function tierBadge(s){if(s.disabled)return`<span class="tier cut">${t('cut')}</span>`;
 if(s.tier==='winner')return`<span class="tier win">✓ ${t('winner')}</span>`;
 if(s.tier==='loser')return`<span class="tier lose">${t('loser')}</span>`;return''}
function sparkline(arr,w,h){if(!arr||arr.length<2)return'';const mn=Math.min(...arr),mx=Math.max(...arr),rg=(mx-mn)||1;
 const pts=arr.map((v,i)=>`${(i/(arr.length-1)*w).toFixed(1)},${(h-(v-mn)/rg*h).toFixed(1)}`).join(' ');
 const up=arr[arr.length-1]>=arr[0];return`<svg class="spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">
 <polyline points="${pts}" fill="none" stroke="${up?'var(--green)':'var(--red)'}" stroke-width="2" stroke-linejoin="round"/></svg>`}
function heroCard(){const b=D.best;if(!b)return'';
 return`<div class="hero"><div class="lbl">⭐ ${t('bestBet')}</div>
 <div class="mtch">${b.home} <span class="vs">—</span> ${b.away} · ${b.sport}</div>
 <div class="row"><div class="pk">${b.pick}</div><div class="od">${b.odds}</div></div>
 ${b.rat_ar?`<div class="why">${lang=='ar'?b.rat_ar:b.rat_en}</div>`:''}</div>`}
function pickCard(p){const isH=p.pick===p.home;
 return`<div class="pick"><div class="teams">
 <span class="${isH?'me':'ot'}">${p.home}</span><span class="vs">vs</span><span class="${!isH?'me':'ot'}">${p.away}</span>
 <span class="od">${p.odds}</span></div>
 <div class="meta"><span class="tag">${p.sport}${p.league?' · '+p.league.slice(0,20):''}</span>
 <span class="pay">${t('bet10')} → <b>${money(p.pay10)}</b></span></div>
 ${p.rat_ar?`<div class="why">${lang=='ar'?p.rat_ar:p.rat_en}</div>`:''}</div>`}
function stratCard(s){const up=s.bankroll>=100;const cls=s.disabled?'lose':(s.tier==='winner'?'win':(s.tier==='loser'?'lose':''));
 const wr=s.bets?Math.round(s.wins/s.bets*100):0;
 return`<div class="scard ${cls}" onclick="showStrat('${esc(s.name)}')">
 <div class="top"><div class="nm">${s.name.split('__')[0]}</div>${tierBadge(s)}</div>
 <div class="mid"><div class="bank ${up?'up':'dn'}">${money(s.bankroll)}</div>
 <div class="rec">${sign(s.profit)} (${s.roi>=0?'+':''}${s.roi}%) · <b>${s.wins}</b>–${s.losses} · ${wr}%</div></div>
 ${s.backtest?`<div class="why">${t('backtestNoGuarantee')}</div>`:''}
 ${sparkline(s.spark,240,30)}
 <div class="foot"><span><b>${s.bets}</b> ${t('bets')} · avg ${s.avg_odds}</span>
 ${s.today_bets?`<span class="today">🎯 ${s.today_bets} ${t('todayB')}</span>`:`<span>${s.days} ${t('days')}</span>`}</div></div>`}
function matchRow(m){const isHome=m.p===m.h;
 return`<div class="mrow"><div class="res ${m.w?'w':'l'}"></div>
 <div class="info"><div class="tm"><span class="${isHome?'me':'ot'}">${m.h}</span> <span style="color:var(--faint)">vs</span> <span class="${!isHome?'me':'ot'}">${m.a}</span></div>
 <div class="sub">${m.d||''} · ${m.s||''}</div></div>
 <div class="sc">${m.hs}–${m.as}</div><div class="od2">${m.o}</div></div>`}
function render(){
 const h=D.headline,a=$('#app');let x='';
 if(view==='home'){
  x+=`<div class="stats"><div class="stat"><div class="v">${h.winrate}%</div><div class="l">${t('winrate')}</div></div>
  <div class="stat"><div class="v ${h.profit>=0?'up':'dn'}" style="color:${h.profit>=0?'var(--green)':'var(--red)'}">${sign(h.profit)}</div><div class="l">${t('netPro')}</div></div>
  <div class="stat"><div class="v">${h.bets}</div><div class="l">${t('bets')}</div></div></div>`;
  x+=heroCard();
  x+=`<div class="sec">${t('todayPicks')}<span class="cnt">${D.picks.length}</span></div>`;
  x+=D.picks.length?D.picks.slice(0,18).map(pickCard).join(''):`<div class="empty">${t('noData')}</div>`;
  // strategy monitor — winners first, losers collapsed
  let strs=[...D.strategies];
  if(sortKey==='profit')strs.sort((a,b)=>b.profit-a.profit);
  else if(sortKey==='win')strs.sort((a,b)=>(b.wins/b.bets)-(a.wins/a.bets));
  else strs.sort((a,b)=>b.profit-a.profit);
  const wins=strs.filter(s=>s.tier==='winner'&&!s.disabled);
  const rest=strs.filter(s=>!(s.tier==='winner'&&!s.disabled));
  x+=`<div class="sec">${t('topStrats')}<span class="cnt">${wins.length}</span></div>`;
  x+=wins.length?wins.map(stratCard).join(''):`<div class="empty">${t('noData')}</div>`;
  if(rest.length){x+=`<div class="sec" onclick="showAll=!showAll;render()" style="cursor:pointer">${t('otherStrats')}<span class="cnt">${rest.length} ${showAll?'▲':'▼'}</span></div>`;
   if(showAll)x+=rest.map(stratCard).join('');}
 }else if(view==='picks'){
  x+=`<div class="sec">${t('todayPicks')}<span class="cnt">${D.today}</span></div>`;
  x+=D.picks.length?D.picks.map(pickCard).join(''):`<div class="empty">${t('noData')}</div>`;
 }else if(view==='history'){
  x+=`<div class="sec">${t('historyLog')}</div>`;
  const all=Object.entries(D.matches).flatMap(([n,ms])=>ms.map(m=>({...m,s:n}))).sort((a,b)=>(b.d||'').localeCompare(a.d||''));
  x+=all.length?all.slice(0,80).map(matchRow).join(''):`<div class="empty">${t('noData')}</div>`;}
 a.innerHTML=x+`<div class="foot">${t('allRes')} · ${D.generated.slice(0,10)}</div>`;
 document.querySelectorAll('.bb').forEach(b=>b.classList.toggle('active',b.dataset.v===view));}
function openMenu(){$('#menuPanel').innerHTML=`<h3>${t('sortMenu')}</h3>
 <div class="menu-opt" onclick="setSort('profit')"><div class="t">💰 ${t('mProfit')}</div></div>
 <div class="menu-opt" onclick="setSort('win')"><div class="t">🎯 ${t('mWin')}</div></div>`;
 $('#menuModal').classList.add('open');}
function setSort(k){sortKey=k;closeMenu();render()}
function closeMenu(){$('#menuModal').classList.remove('open')}
window.showStrat=function(n){openName=n;const s=D.strategies.find(x=>x.name===n);if(!s)return;
 const ms=D.matches[n]||[];const up=s.bankroll>=100;const wr=s.bets?Math.round(s.wins/s.bets*100):0;
 const tp=D.today_by_base[n.split('__')[0]]||[];
 $('#app').innerHTML=`<div class="back" onclick="openName=null;render()">← ${t('topStrats')}</div>
 <div class="dhead"><div class="nm">${s.name.split('__')[0]} ${tierBadge(s)}</div>
 <div class="big ${up?'bank up':'bank dn'}">${money(s.bankroll)}</div>
 <div class="rec">${sign(s.profit)} (${s.roi>=0?'+':''}${s.roi}%) · ${t('netPro')}</div>
 ${sparkline(s.spark,280,36)}
 <div class="dgrid"><div class="c"><div class="v">${wr}%</div><div class="l">${t('winrate')}</div></div>
 <div class="c"><div class="v">${s.wins}–${s.losses}</div><div class="l">${t('record')}</div></div>
 <div class="c"><div class="v">${s.avg_odds}</div><div class="l">${t('odds')}</div></div></div></div>
 ${tp.length?`<div class="sec">${t('todayPicks')}<span class="cnt">${tp.length}</span></div>`+tp.map(p=>{const isH=p.p===p.h;
  return`<div class="pick"><div class="teams"><span class="${isH?'me':'ot'}">${p.h}</span><span class="vs">vs</span><span class="${!isH?'me':'ot'}">${p.a}</span><span class="od">${p.o}</span></div>
  <div class="meta"><span class="tag">${p.s}${p.lg?' · '+p.lg:''}</span><span class="pay">${t('bet10')} → <b>${money(p.pay)}</b></span></div></div>`}).join(''):''}
 <div class="sec">${t('historyLog')}<span class="cnt">${ms.length}</span></div>
 ${ms.length?ms.slice().reverse().map(matchRow).join(''):`<div class="empty">${t('noData')}</div>`}`;
 document.querySelectorAll('.bb').forEach(b=>b.classList.remove('active'));window.scrollTo(0,0);}
document.querySelectorAll('.bb').forEach(b=>b.addEventListener('click',()=>{view=b.dataset.v;openName=null;render()}));
$('#langBtn').addEventListener('click',()=>setLang(lang==='ar'?'en':'ar'));
$('#refreshBtn').addEventListener('click',()=>{const b=document.getElementById('refreshBtn');b.textContent='⏳';
  // أعد تحميل أحدث بيانات من الخادم (تتجدد كل ساعتين تلقائياً)
  location.reload(true);});
$('#menuBtn').addEventListener('click',openMenu);
$('#menuModal').addEventListener('click',e=>{if(e.target.id==='menuModal')closeMenu()});
// لا service worker — التطبيق يُحمّل نسخة طازجة دائماً (أبسط وأضمن، لا مشاكل cache)
// للتثبيت كأيقونة على الهاتف: Chrome ← قائمة ← "تثبيت التطبيق" / "Add to Home screen"
const PIN='08031998';
function checkPin(){const v=document.getElementById('pin').value;
 if(v===PIN){sessionStorage.setItem('sp_auth','1');document.getElementById('lock').classList.add('hide');
  setLang('ar');render();}else{document.getElementById('pinErr').textContent=
   lang=='ar'?'رقم سري خاطئ، حاول مجدداً':'Wrong PIN, try again';
   document.getElementById('pin').value='';}}
document.getElementById('pin').addEventListener('keydown',e=>{if(e.key==='Enter')checkPin()});
document.getElementById('pin').addEventListener('input',()=>document.getElementById('pinErr').textContent='');
if(sessionStorage.getItem('sp_auth')==='1'){document.getElementById('lock').classList.add('hide');setLang('ar');render();}
</script></body></html>"""

MANIFEST={"name":"استراتيجي برو · Strategy Pro","short_name":"Strategy Pro",
"description":"Professional strategy monitoring & picks","lang":"ar","dir":"rtl","start_url":"./index.html",
"display":"standalone","background_color":"#0a0f1c","theme_color":"#0b1220",
"icons":[{"src":"icon.png","sizes":"512x512","type":"image/png","purpose":"any maskable"}]}

SW=r"""const C='sp-v5';
self.addEventListener('install',e=>self.skipWaiting());
self.addEventListener('activate',e=>e.waitUntil(caches.keys().then(ks=>
  Promise.all(ks.filter(k=>k!==C).map(k=>caches.delete(k)))).then(()=>self.clients.claim())));
self.addEventListener('fetch',e=>{
  if(e.request.method!=='GET')return;
  const url=new URL(e.request.url);
  // network-first للصفحة الرئيسية والـ JS/manifest حتى تظهر التحديثات فوراً
  const netFirst=url.pathname.endsWith('/')||url.pathname.endsWith('.html')||
    url.pathname.endsWith('sw.js')||url.pathname.endsWith('manifest.json');
  if(netFirst){
    e.respondWith(fetch(e.request).then(x=>{caches.open(C).then(c=>c.put(e.request,x.clone()));return x;})
      .catch(()=>caches.match(e.request)));
    return;
  }
  // cache-first للأصول الثابتة (أيقونة)
  e.respondWith(caches.open(C).then(c=>c.match(e.request).then(r=>r||fetch(e.request)
    .then(x=>{c.put(e.request,x.clone());return x}).catch(()=>r)))));
});"""


def build_icon(path: Path):
    import struct, zlib
    W = H = 512; px = bytearray()
    for y in range(H):
        px += b"\x00"
        for x in range(W):
            tt = x / W; r = int(10 + 20 * tt); g = int(15 + 200 * tt); b = int(28 + 100 * tt)
            cx, cy = W * .42, H * .5
            if abs(x - cx) < 60 and cy - 170 < y < cy + 80 and (y - cy) > -1.7 * (x - cx):
                r, g, b = 245, 210, 70
            px += bytes([r, g, b])
    def ck(tag, data):
        c = tag + data; return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xffffffff)
    raw = b"".join(bytes([0]) + bytes(px[i*W*3+1:(i+1)*W*3+1]) for i in range(H))
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + ck(b"IHDR", struct.pack(">IIBBBBB", W, H, 8, 2, 0, 0, 0)) +
                     ck(b"IDAT", zlib.compress(raw, 9)) + ck(b"IEND", b""))


def main():
    today = datetime.now(portfolio.BERLIN_TZ).date().isoformat()
    data = gather(today)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "index.html").write_text(HTML.replace("__DATA__", json.dumps(data, ensure_ascii=False)), encoding="utf-8")
    (OUT_DIR / "manifest.json").write_text(json.dumps(MANIFEST, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT_DIR / "sw.js").write_text(SW, encoding="utf-8")
    build_icon(OUT_DIR / "icon.png")
    print(f"✓ Dashboard built → {OUT_DIR}/index.html")
    print(f"  strategies: {data['headline']['strategies']} | bets: {data['headline']['bets']} | "
          f"net: {data['headline']['profit']:+.0f} | best bet: {bool(data['best'])}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
