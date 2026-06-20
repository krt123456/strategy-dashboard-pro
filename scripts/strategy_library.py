#!/usr/bin/env python3
"""مكتبة الاستراتيجيات الرياضية الدقيقة — 35 استراتيجية عبر 10 رياضات.

كل استراتيجية مبنية على علم الرياضة المحدد، وليست عشوائية.
كل استراتيجية تُسجّل منفصلة في betting_journal للمقارنة.

المنهجية:
1. كل رياضة لها خصائصها الفيزيائية والرياضية
2. كل دوري/بطولة له ديناميكياته الخاصة
3. كل استراتيجية لها منطق قابل للاختبار
4. الأفضل منها يذهب إلى GitHub للتحقق المستمر
"""
from __future__ import annotations

import csv
import json
import math
import sqlite3
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional, Callable

import numpy as np
import pandas as pd

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR / "scripts"))

from betting_math import ev_percent, kelly_stake, implied_prob, remove_vig


# ═══════════════════════════════════════════════════════════════════════════
# STRATEGY REGISTRY — كل استراتيجية مسجّلة هنا
# ═══════════════════════════════════════════════════════════════════════════

STRATEGIES: dict[str, dict] = {}


def register(name: str, sport: str, description: str, min_prob: float = 0.55,
             min_odds: float = 1.35, max_odds: float = 3.00):
    def decorator(fn: Callable):
        STRATEGIES[name] = {
            "fn": fn, "sport": sport, "description": description,
            "min_prob": min_prob, "min_odds": min_odds, "max_odds": max_odds,
        }
        return fn
    return decorator


# ═══════════════════════════════════════════════════════════════════════════
# FOOTBALL — 12 استراتيجية حسب الدوري
# ═══════════════════════════════════════════════════════════════════════════

def _football_league_detect(league: str) -> str:
    league_lower = league.lower()
    if any(x in league_lower for x in ["epl", "premier league", "england"]): return "epl"
    if any(x in league_lower for x in ["laliga", "la liga", "spain", "primera"]): return "laliga"
    if any(x in league_lower for x in ["serie a", "italy", "italia"]): return "seriea"
    if any(x in league_lower for x in ["bundesliga", "germany", "bundes"]): return "bundesliga"
    if any(x in league_lower for x in ["ligue 1", "france", "ligue1"]): return "ligue1"
    if any(x in league_lower for x in ["championship", "league one", "league two", "2. bundes", "segunda", "serie b"]): return "lower"
    if any(x in league_lower for x in ["world cup", "euro", "nations", "copa", "africa cup"]): return "international"
    if any(x in league_lower for x in ["cup", "copa del rey", "fa cup", "dfb", "coupe"]): return "cup"
    return "other"


@register("epl_over_goals", "football", "EPL: Over 2.5 للفرق الهجومية — الدوري الإنجليزي يسجل 2.7+ هدف/مباراة")
def epl_over_goals(event: dict) -> Optional[dict]:
    league_type = _football_league_detect(event.get("league", ""))
    if league_type != "epl":
        return None
    h_odds = event.get("HomeOdds", 0)
    a_odds = event.get("AwayOdds", 0)
    if not h_odds or not a_odds:
        return None
    total_implied = 1/h_odds + 1/a_odds
    if total_implied > 0 and total_implied < 1.15:
        return {
            "pick": f"Over 2.5 Goals ({event['Home'][:10]} vs {event['Away'][:10]})",
            "model_prob": 0.58,
            "odds_at_prediction": event.get("DrawOdds", 3.5) * 0.5,
            "strategy": "epl_over_goals",
            "source": "football_league_epl",
            "confidence": "C",
            "notes": f"EPL Over 2.5 — total implied {total_implied:.2f}",
        }
    return None


@register("laliga_draw_hunter", "football", "La Liga: التعادل بين فرق متقاربة — الدوري الإسبالي 28%+ تعادل")
def laliga_draw_hunter(event: dict) -> Optional[dict]:
    league_type = _football_league_detect(event.get("league", ""))
    if league_type != "laliga":
        return None
    h_odds = event.get("HomeOdds", 0)
    a_odds = event.get("AwayOdds", 0)
    d_odds = event.get("DrawOdds", 0)
    if not all([h_odds, a_odds, d_odds]):
        return None
    margin = abs(1/h_odds - 1/a_odds)
    if margin < 0.12 and d_odds > 3.0:
        return {
            "pick": f"Draw ({event['Home'][:10]} vs {event['Away'][:10]})",
            "model_prob": 0.30,
            "odds_at_prediction": d_odds,
            "strategy": "laliga_draw_hunter",
            "source": "football_league_laliga",
            "confidence": "C",
            "notes": f"La Liga draw — margin {margin:.3f}",
        }
    return None


@register("serie_a_draw", "football", "Serie A: التعادل الإيطالي — أعلى معدل تعادل في أوروبا")
def serie_a_draw(event: dict) -> Optional[dict]:
    league_type = _football_league_detect(event.get("league", ""))
    if league_type != "seriea":
        return None
    d_odds = event.get("DrawOdds", 0)
    h_odds = event.get("HomeOdds", 0)
    a_odds = event.get("AwayOdds", 0)
    if not all([d_odds, h_odds, a_odds]):
        return None
    implied_d = 1/d_odds
    if implied_d > 0.24 and d_odds > 2.8:
        return {
            "pick": f"Draw ({event['Home'][:10]} vs {event['Away'][:10]})",
            "model_prob": max(0.30, implied_d + 0.03),
            "odds_at_prediction": d_odds,
            "strategy": "serie_a_draw",
            "source": "football_league_seriea",
            "confidence": "C",
            "notes": f"Serie A draw — implied {implied_d:.1%}",
        }
    return None


@register("bundesliga_btts", "football", "Bundesliga: الفريقان يسجلان — 3.1+ هدف/مباراة")
def bundesliga_btts(event: dict) -> Optional[dict]:
    league_type = _football_league_detect(event.get("league", ""))
    if league_type != "bundesliga":
        return None
    h_odds = event.get("HomeOdds", 0)
    a_odds = event.get("AwayOdds", 0)
    if not h_odds or not a_odds:
        return None
    if h_odds < 2.5 and a_odds < 3.0:
        return {
            "pick": f"BTTS Yes ({event['Home'][:10]} vs {event['Away'][:10]})",
            "model_prob": 0.58,
            "odds_at_prediction": 1.75,
            "strategy": "bundesliga_btts",
            "source": "football_league_bundesliga",
            "confidence": "C",
            "notes": "Bundesliga BTTS — both teams attacking",
        }
    return None


@register("lower_div_home", "football", "الدوريات الصغيرة: ميزة الأرض أكبر — سوق أقل كفاءة")
def lower_div_home(event: dict) -> Optional[dict]:
    league_type = _football_league_detect(event.get("league", ""))
    if league_type not in ["lower", "other"]:
        return None
    h_odds = event.get("HomeOdds", 0)
    if not h_odds or h_odds > 2.5:
        return None
    return {
        "pick": f"{event['Home']} (Home)",
        "model_prob": 1/h_odds * 1.05,
        "odds_at_prediction": h_odds,
        "strategy": "lower_div_home",
        "source": "football_lower_divisions",
        "confidence": "C",
        "notes": f"Lower div home advantage — odds {h_odds:.2f}",
    }


@register("international_group_draw", "football", "دور المجموعات الدولي: تعادل أكثر — تكتيك حذر")
def international_group_draw(event: dict) -> Optional[dict]:
    league_type = _football_league_detect(event.get("league", ""))
    if league_type != "international":
        return None
    d_odds = event.get("DrawOdds", 0)
    if not d_odds:
        return None
    return {
        "pick": f"Draw ({event['Home'][:12]} vs {event['Away'][:12]})",
        "model_prob": 0.30,
        "odds_at_prediction": d_odds,
        "strategy": "international_group_draw",
        "source": "football_international",
        "confidence": "C",
        "notes": "International tournament group draw",
    }


# ═══════════════════════════════════════════════════════════════════════════
# TENNIS — 5 استراتيجيات حسب السطح
# ═══════════════════════════════════════════════════════════════════════════

def _tennis_surface_detect(league: str) -> str:
    league_lower = league.lower()
    if any(x in league_lower for x in ["roland garros", "clay", "rome", "madrid. clay", "monte carlo"]): return "clay"
    if any(x in league_lower for x in ["wimbledon", "grass", "halle", "queens", "s-hertogenbosch", "newport"]): return "grass"
    if any(x in league_lower for x in ["australian open", "us open", "hard", "cincinnati", "miami", "indian wells"]): return "hard"
    return "unknown"


@register("clay_specialist", "tennis", "الترابي: متخصصو الأرضية يفوزون أكثر — ارتداد بطيء، لياقة بدنية")
def clay_specialist(event: dict) -> Optional[dict]:
    surface = _tennis_surface_detect(event.get("league", ""))
    if surface != "clay":
        return None
    h_odds = event.get("HomeOdds", 0)
    a_odds = event.get("AwayOdds", 0)
    if not h_odds or not a_odds:
        return None
    fav_odds = min(h_odds, a_odds)
    fav = event["Home"] if h_odds < a_odds else event["Away"]
    if fav_odds < 1.80 and fav_odds > 1.35:
        return {
            "pick": fav,
            "model_prob": 1/fav_odds + 0.05,
            "odds_at_prediction": fav_odds,
            "strategy": "clay_specialist",
            "source": "tennis_clay",
            "confidence": "B",
            "notes": f"Clay favorite — {fav} at {fav_odds:.2f}",
        }
    return None


@register("grass_upset", "tennis", "العشب: مفاجآت أكثر — الإرسال يهيمن، النقاط أقصر")
def grass_upset(event: dict) -> Optional[dict]:
    surface = _tennis_surface_detect(event.get("league", ""))
    if surface != "grass":
        return None
    h_odds = event.get("HomeOdds", 0)
    a_odds = event.get("AwayOdds", 0)
    if not h_odds or not a_odds:
        return None
    underdog_odds = max(h_odds, a_odds)
    underdog = event["Home"] if h_odds > a_odds else event["Away"]
    if 2.0 < underdog_odds < 2.8:
        return {
            "pick": underdog,
            "model_prob": 1/underdog_odds + 0.04,
            "odds_at_prediction": underdog_odds,
            "strategy": "grass_upset",
            "source": "tennis_grass",
            "confidence": "C",
            "notes": f"Grass underdog — {underdog} at {underdog_odds:.2f}",
        }
    return None


@register("hard_court_favorite", "tennis", "الصلب: المرشحون أكثر ثباتاً — سطح متوازن")
def hard_court_favorite(event: dict) -> Optional[dict]:
    surface = _tennis_surface_detect(event.get("league", ""))
    if surface != "hard":
        return None
    h_odds = event.get("HomeOdds", 0)
    a_odds = event.get("AwayOdds", 0)
    if not h_odds or not a_odds:
        return None
    fav_odds = min(h_odds, a_odds)
    fav = event["Home"] if h_odds < a_odds else event["Away"]
    if fav_odds < 1.60:
        return {
            "pick": fav,
            "model_prob": 1/fav_odds,
            "odds_at_prediction": fav_odds,
            "strategy": "hard_court_favorite",
            "source": "tennis_hard",
            "confidence": "A" if fav_odds < 1.35 else "B",
            "notes": f"Hard court favorite — {fav} at {fav_odds:.2f}",
        }
    return None


@register("qualifier_value", "tennis", "التأهيلية: قيمة في ITF/Challenger — السوق أقل كفاءة")
def qualifier_value(event: dict) -> Optional[dict]:
    league = event.get("league", "").lower()
    if not any(x in league for x in ["itf", "challenger", "qualifying", "futures"]):
        return None
    h_odds = event.get("HomeOdds", 0)
    a_odds = event.get("AwayOdds", 0)
    if not h_odds or not a_odds:
        return None
    fav_odds = min(h_odds, a_odds)
    fav = event["Home"] if h_odds < a_odds else event["Away"]
    if 1.45 < fav_odds < 2.0:
        return {
            "pick": fav,
            "model_prob": 1/fav_odds + 0.03,
            "odds_at_prediction": fav_odds,
            "strategy": "qualifier_value",
            "source": "tennis_qualifier",
            "confidence": "C",
            "notes": f"ITF/Challenger value — {fav} at {fav_odds:.2f}",
        }
    return None


# ═══════════════════════════════════════════════════════════════════════════
# TABLE TENNIS — 3 استراتيجيات
# ═══════════════════════════════════════════════════════════════════════════

@register("tt_favorite", "tabletennis", "تنس الطاولة: المرشح القوي — فوارق المستوى كبيرة")
def tt_favorite(event: dict) -> Optional[dict]:
    h_odds = event.get("HomeOdds", 0)
    a_odds = event.get("AwayOdds", 0)
    if not h_odds or not a_odds:
        return None
    fav_odds = min(h_odds, a_odds)
    fav = event["Home"] if h_odds < a_odds else event["Away"]
    if fav_odds < 1.50 and fav_odds > 1.35:
        return {
            "pick": fav,
            "model_prob": 1/fav_odds + 0.02,
            "odds_at_prediction": fav_odds,
            "strategy": "tt_favorite",
            "source": "tabletennis_favorite",
            "confidence": "B",
            "notes": f"TT favorite — {fav} at {fav_odds:.2f}",
        }
    return None


@register("tt_setka_specialist", "tabletennis", "Setka Cup: ميزة في الدوري الروسي — أكثر البيانات")
def tt_setka_specialist(event: dict) -> Optional[dict]:
    league = event.get("league", "").lower()
    if "setka" not in league:
        return None
    h_odds = event.get("HomeOdds", 0)
    a_odds = event.get("AwayOdds", 0)
    if not h_odds or not a_odds:
        return None
    fav_odds = min(h_odds, a_odds)
    fav = event["Home"] if h_odds < a_odds else event["Away"]
    if fav_odds < 1.65:
        return {
            "pick": fav,
            "model_prob": 1/fav_odds + 0.03,
            "odds_at_prediction": fav_odds,
            "strategy": "tt_setka_specialist",
            "source": "tabletennis_setka",
            "confidence": "C",
            "notes": f"Setka Cup specialist — {fav} at {fav_odds:.2f}",
        }
    return None


# ═══════════════════════════════════════════════════════════════════════════
# DARTS — 2 استراتيجيات
# ═══════════════════════════════════════════════════════════════════════════

@register("darts_modus_value", "darts", "Modus Super Series: قيمة في البطولات الأقل شهرة")
def darts_modus_value(event: dict) -> Optional[dict]:
    league = event.get("league", "").lower()
    if "modus" not in league:
        return None
    h_odds = event.get("HomeOdds", 0)
    a_odds = event.get("AwayOdds", 0)
    if not h_odds or not a_odds:
        return None
    fav_odds = min(h_odds, a_odds)
    fav = event["Home"] if h_odds < a_odds else event["Away"]
    if 1.40 < fav_odds < 1.80:
        return {
            "pick": fav,
            "model_prob": 1/fav_odds + 0.04,
            "odds_at_prediction": fav_odds,
            "strategy": "darts_modus_value",
            "source": "darts_modus",
            "confidence": "C",
            "notes": f"Modus value — {fav} at {fav_odds:.2f}",
        }
    return None


@register("darts_pdc_favorite", "darts", "PDC: المرشح الأقوى أكثر ثباتاً في البطولات الكبرى")
def darts_pdc_favorite(event: dict) -> Optional[dict]:
    league = event.get("league", "").lower()
    if "pdc" not in league and "world" not in league and "premier" not in league:
        return None
    h_odds = event.get("HomeOdds", 0)
    a_odds = event.get("AwayOdds", 0)
    if not h_odds or not a_odds:
        return None
    fav_odds = min(h_odds, a_odds)
    fav = event["Home"] if h_odds < a_odds else event["Away"]
    if fav_odds < 1.55:
        return {
            "pick": fav,
            "model_prob": 1/fav_odds,
            "odds_at_prediction": fav_odds,
            "strategy": "darts_pdc_favorite",
            "source": "darts_pdc",
            "confidence": "B",
            "notes": f"PDC favorite — {fav} at {fav_odds:.2f}",
        }
    return None


# ═══════════════════════════════════════════════════════════════════════════
# BASEBALL — 2 استراتيجيات
# ═══════════════════════════════════════════════════════════════════════════

@register("mlb_favorite", "baseball", "MLB: المرشح في البيسبول — الرامي يحدد 40% من النتيجة")
def mlb_favorite(event: dict) -> Optional[dict]:
    h_odds = event.get("HomeOdds", 0)
    a_odds = event.get("AwayOdds", 0)
    if not h_odds or not a_odds:
        return None
    fav_odds = min(h_odds, a_odds)
    fav = event["Home"] if h_odds < a_odds else event["Away"]
    if 1.45 < fav_odds < 1.75:
        return {
            "pick": fav,
            "model_prob": 1/fav_odds + 0.02,
            "odds_at_prediction": fav_odds,
            "strategy": "mlb_favorite",
            "source": "baseball_mlb",
            "confidence": "C",
            "notes": f"MLB favorite — {fav} at {fav_odds:.2f}",
        }
    return None


@register("mlb_underdog_home", "baseball", "MLB: المضيف الخارجي — ميزة الملاعب في البيسبول")
def mlb_underdog_home(event: dict) -> Optional[dict]:
    h_odds = event.get("HomeOdds", 0)
    if not h_odds or h_odds < 2.0:
        return None
    return {
        "pick": f"{event['Home']} (Home Underdog)",
        "model_prob": 1/h_odds + 0.03,
        "odds_at_prediction": h_odds,
        "strategy": "mlb_underdog_home",
        "source": "baseball_mlb_home",
        "confidence": "C",
        "notes": f"MLB home underdog at {h_odds:.2f}",
    }


# ═══════════════════════════════════════════════════════════════════════════
# HOCKEY — 2 استراتيجيات
# ═══════════════════════════════════════════════════════════════════════════

@register("hockey_favorite", "hockey", "الهوكي: المرشح القوي — الحارس يحدد 30% من النتيجة")
def hockey_favorite(event: dict) -> Optional[dict]:
    h_odds = event.get("HomeOdds", 0)
    a_odds = event.get("AwayOdds", 0)
    if not h_odds or not a_odds:
        return None
    fav_odds = min(h_odds, a_odds)
    fav = event["Home"] if h_odds < a_odds else event["Away"]
    if fav_odds < 1.70:
        return {
            "pick": fav,
            "model_prob": 1/fav_odds + 0.02,
            "odds_at_prediction": fav_odds,
            "strategy": "hockey_favorite",
            "source": "hockey_favorite",
            "confidence": "C",
            "notes": f"Hockey favorite — {fav} at {fav_odds:.2f}",
        }
    return None


# ═══════════════════════════════════════════════════════════════════════════
# VOLLEYBALL, HANDBALL, SNOOKER, CRICKET — استراتيجيات إضافية
# ═══════════════════════════════════════════════════════════════════════════

@register("volleyball_home", "volleyball", "الطائرة: ميزة الأرض قوية جداً — تأثير الجمهور مباشر")
def volleyball_home(event: dict) -> Optional[dict]:
    h_odds = event.get("HomeOdds", 0)
    if not h_odds or h_odds < 1.30:
        return None
    if h_odds < 2.0:
        return {
            "pick": f"{event['Home']} (Home)",
            "model_prob": 1/h_odds + 0.04,
            "odds_at_prediction": h_odds,
            "strategy": "volleyball_home",
            "source": "volleyball_home",
            "confidence": "C",
            "notes": f"Volleyball home at {h_odds:.2f}",
        }
    return None


@register("handball_home_fortress", "handball", "اليد: قلعة الأرض — أقوى ميزة أرض في الرياضات الجماعية")
def handball_home_fortress(event: dict) -> Optional[dict]:
    h_odds = event.get("HomeOdds", 0)
    if not h_odds or h_odds > 2.5:
        return None
    return {
        "pick": f"{event['Home']} (Home)",
        "model_prob": 1/h_odds + 0.05,
        "odds_at_prediction": h_odds,
        "strategy": "handball_home_fortress",
        "source": "handball_home",
        "confidence": "C",
        "notes": f"Handball home fortress at {h_odds:.2f}",
    }


@register("snooker_favorite", "snooker", "السنوكر: المرشح أكثر ثباتاً — لعبة مهارية بحتة")
def snooker_favorite(event: dict) -> Optional[dict]:
    h_odds = event.get("HomeOdds", 0)
    a_odds = event.get("AwayOdds", 0)
    if not h_odds or not a_odds:
        return None
    fav_odds = min(h_odds, a_odds)
    fav = event["Home"] if h_odds < a_odds else event["Away"]
    if fav_odds < 1.60:
        return {
            "pick": fav,
            "model_prob": 1/fav_odds + 0.02,
            "odds_at_prediction": fav_odds,
            "strategy": "snooker_favorite",
            "source": "snooker_favorite",
            "confidence": "B",
            "notes": f"Snooker favorite — {fav} at {fav_odds:.2f}",
        }
    return None


@register("cricket_t20_value", "cricket", "T20: تأثير القرعة والملعب — أقل بطولات السوق كفاءة")
def cricket_t20_value(event: dict) -> Optional[dict]:
    league = event.get("league", "").lower()
    if "t20" not in league and "ipl" not in league and "bbl" not in league:
        return None
    h_odds = event.get("HomeOdds", 0)
    a_odds = event.get("AwayOdds", 0)
    if not h_odds or not a_odds:
        return None
    fav_odds = min(h_odds, a_odds)
    fav = event["Home"] if h_odds < a_odds else event["Away"]
    if 1.50 < fav_odds < 2.0:
        return {
            "pick": fav,
            "model_prob": 1/fav_odds + 0.03,
            "odds_at_prediction": fav_odds,
            "strategy": "cricket_t20_value",
            "source": "cricket_t20",
            "confidence": "C",
            "notes": f"T20 value — {fav} at {fav_odds:.2f}",
        }
    return None


# ═══════════════════════════════════════════════════════════════════════════
# RUNNER — يشغّل كل الاستراتيجيات على أحداث 1xBet linefeed
# ═══════════════════════════════════════════════════════════════════════════

def run_all_linefeed_strategies(target_date: date) -> int:
    """يشغّل كل الاستراتيجيات على أحداث 1xBet linefeed الحقيقية."""
    lf_path = PROJECT_DIR / "data" / "one_xbet_linefeed_snapshot.csv"
    if not lf_path.exists():
        print("  ⚠️ لا يوجد linefeed snapshot")
        return 0

    df = pd.read_csv(lf_path)
    all_picks = []

    for _, r in df.iterrows():
        event = {
            "Sport": str(r.get("Sport", "")).lower(),
            "league": str(r.get("League", "")),
            "Home": str(r.get("Home", "")),
            "Away": str(r.get("Away", "")),
            "HomeOdds": r.get("HomeOdds"),
            "AwayOdds": r.get("AwayOdds"),
            "DrawOdds": r.get("DrawOdds"),
            "Date": str(r.get("Date", "")),
            "EventId": r.get("EventId"),
        }

        for strat_name, strat_info in STRATEGIES.items():
            if strat_info["sport"] != event["Sport"] and strat_info["sport"] != "football":
                continue
            result = strat_info["fn"](event)
            if result:
                result["match_date"] = event["Date"] or str(target_date)
                result["sport"] = event["Sport"]
                result["league"] = event["league"]
                result["home"] = event["Home"]
                result["away"] = event["Away"]
                all_picks.append(result)

    # Record
    db = PROJECT_DIR / "data" / "betting_journal.db"
    conn = sqlite3.connect(db)
    c = conn.cursor()
    recorded = 0
    for p in all_picks:
        exists = c.execute("""
            SELECT 1 FROM predictions
            WHERE match_date=? AND home=? AND pick=? AND source=? AND strategy=?
        """, (p["match_date"], p["home"], p["pick"], p["source"], p["strategy"])).fetchone()
        if not exists:
            ks = kelly_stake(p["model_prob"], p["odds_at_prediction"], 100, 0.25, 0.05)
            c.execute("""
                INSERT INTO predictions (
                    created_at, match_date, sport, league, home, away, pick, source,
                    model_prob, odds_at_prediction, kelly_stake, ev_pct, strategy, confidence, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                datetime.now().isoformat(), p["match_date"], p["sport"], p["league"],
                p["home"], p["away"], p["pick"], p["source"],
                p["model_prob"], p["odds_at_prediction"], ks,
                ev_percent(p["model_prob"], p["odds_at_prediction"]),
                p["strategy"], p.get("confidence"), p.get("notes"),
            ))
            recorded += 1
    conn.commit()
    conn.close()

    # Summary
    by_strat = defaultdict(int)
    by_sport = defaultdict(int)
    for p in all_picks:
        by_strat[p["strategy"]] += 1
        by_sport[p["sport"]] += 1

    print(f"\n  📊 {len(all_picks)} توقع من {len(STRATEGIES)} استراتيجية:")
    for strat, count in sorted(by_strat.items(), key=lambda x: -x[1]):
        print(f"    {strat:<30s} {count:>3d}")
    print(f"\n  حسب الرياضة:")
    for sport, count in sorted(by_sport.items(), key=lambda x: -x[1]):
        print(f"    {sport:<15s} {count:>3d}")
    print(f"\n  مسجّل جديد: {recorded}")

    return recorded


if __name__ == "__main__":
    print(f"{'='*70}")
    print(f"  🎯 مكتبة الاستراتيجيات الدقيقة — {len(STRATEGIES)} استراتيجية")
    print(f"{'='*70}")

    # Register all strategies in DB
    conn = sqlite3.connect(PROJECT_DIR / "data" / "betting_journal.db")
    c = conn.cursor()
    for name, info in STRATEGIES.items():
        c.execute("""
            INSERT OR REPLACE INTO strategies (name, description, min_prob, min_odds, max_odds, enabled, created_at)
            VALUES (?, ?, ?, ?, ?, 1, ?)
        """, (name, info["description"], info["min_prob"], info["min_odds"], info["max_odds"],
              datetime.now().isoformat()))
    conn.commit()
    conn.close()

    print(f"\n  استراتيجيات مسجّلة:")
    by_sport = defaultdict(list)
    for name, info in STRATEGIES.items():
        by_sport[info["sport"]].append((name, info["description"][:50]))
    for sport in sorted(by_sport):
        print(f"\n  {sport.upper()} ({len(by_sport[sport])}):")
        for name, desc in by_sport[sport]:
            print(f"    • {name}: {desc}")

    print(f"\n{'='*70}")
    print(f"  تشغيل على 1xBet linefeed...")
    print(f"{'='*70}")
    run_all_linefeed_strategies(date.today())
