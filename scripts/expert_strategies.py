#!/usr/bin/env python3
"""Expert strategy suite — born from root-cause analysis, not parameter sweeps.

These strategies implement the insights documented in reports/strategy_intelligence.md:

  1. vig_aware_value  — only bet when the model edge exceeds the actual bookmaker
     vig (overround - 1). This kills the favorite-longshot trap where thin
     fair-odds edges become live losers after the vig.
  2. thick_edge_favorite — restrict to the extreme-favorite zone where the edge
     is thickest and most robust to the vig.
  3. coinflip_home_premium — the durable home-advantage mispricing edge in the
     genuinely-uncertain band, with the vig explicitly subtracted.

Each strategy is vig-aware: it removes the overround from the two bookmaker
odds to get fair probabilities, then demands the model edge clear the vig.
This is genuine value betting rather than the EV>0 trap that lost money live.
"""
from __future__ import annotations
from typing import Optional


def _fair_probs(home_odds: float, away_odds: float):
    """Remove the bookmaker overround to get vig-free probabilities."""
    ih = 1.0 / home_odds if home_odds > 1 else 0.0
    ia = 1.0 / away_odds if away_odds > 1 else 0.0
    over = ih + ia
    if over <= 0:
        return 0.0, 0.0, 0.0
    return ih / over, ia / over, over - 1.0  # fair_home, fair_away, vig


def vig_aware_value(home: str, away: str, home_odds: float, away_odds: float,
                    model_home: float, margin: float = 0.03) -> Optional[dict]:
    """Bet the side whose MODEL probability beats the FAIR market probability by
    more than the vig + a safety margin. Edge must survive the bookmaker cut.

    model_home = our model's home win probability (e.g. ELO or LightGBM).
    """
    fh, fa, vig = _fair_probs(home_odds, away_odds)
    if vig <= 0:
        return None
    model_away = 1.0 - model_home
    edge_h = model_home - fh
    edge_a = model_away - fa
    if edge_h > vig / 2 + margin:
        side, prob, edge = "home", model_home, edge_h
    elif edge_a > vig / 2 + margin:
        side, prob, edge = "away", model_away, edge_a
    else:
        return None
    odds = home_odds if side == "home" else away_odds
    return {
        "pick": home if side == "home" else away,
        "model_prob": round(prob, 4),
        "odds_at_prediction": round(odds, 2),
        "strategy": "vig_aware_value",
        "source": "expert_vig",
        "confidence": "A" if edge > vig + 0.05 else "B",
        "notes": f"vig-aware edge {edge:+.1%} over vig {vig:.1%}",
    }


def thick_edge_favorite(home, away, home_odds, away_odds, sport="") -> Optional[dict]:
    """Restrict to extreme favorites: bet only when a side's FAIR probability is
    >= 0.80 AND its bookmaker odds are < 1.30. This is the thickest, most
    vig-resistant edge zone (backtest +3.9% even at fair odds)."""
    fh, fa, vig = _fair_probs(home_odds, away_odds)
    if fh >= 0.80 and home_odds < 1.30:
        return {
            "pick": home, "model_prob": round(fh, 4),
            "odds_at_prediction": round(home_odds, 2),
            "strategy": "thick_edge_favorite", "source": "expert_vig",
            "confidence": "A", "notes": f"thick edge fav fair {fh:.0%} vig {vig:.1%}",
        }
    if fa >= 0.80 and away_odds < 1.30:
        return {
            "pick": away, "model_prob": round(fa, 4),
            "odds_at_prediction": round(away_odds, 2),
            "strategy": "thick_edge_favorite", "source": "expert_vig",
            "confidence": "A", "notes": f"thick edge fav fair {fa:.0%} vig {vig:.1%}",
        }
    return None


def coinflip_home_premium(home, away, home_odds, away_odds, sport="") -> Optional[dict]:
    """The durable home-advantage mispricing edge: when the market sees a genuine
    coin-flip (fair home prob 0.46–0.58), back the home side. Confined to odds
    where the vig-adjusted payout is still positive."""
    fh, fa, vig = _fair_probs(home_odds, away_odds)
    if 0.46 <= fh <= 0.58 and vig < 0.10:  # skip badly overround markets
        return {
            "pick": home, "model_prob": round(fh + 0.04, 4),  # home premium
            "odds_at_prediction": round(home_odds, 2),
            "strategy": "coinflip_home_premium", "source": "expert_vig",
            "confidence": "B", "notes": f"coinflip home premium fair {fh:.0%}",
        }
    return None


def deep_seek_1(home, away, home_odds, away_odds, sport="") -> Optional[dict]:
    """المنطقة الذهبية — Deep Seek 1: home teams in the proven sweet spot.

    Root-cause evidence (1312 graded results):
    - HOME + real odds + prob 0.50-0.60: +$54 on 218 bets (68.3% win)
    - The home-underpricing bias peaks when the market is slightly uncertain
    - Vig kills bets below 1.50; odds above 2.40 have 28% win rate

    Formula: fair_home 0.50-0.62, odds 1.50-2.40, edge > vig/2 + 0.02.
    Fallback: if vig is negligible (synthetic odds), use prob-only filter with wider band.
    """
    fh, fa, vig = _fair_probs(home_odds, away_odds)
    if vig < 0.005:
        # Synthetic/fair odds: skip vig math, use probability band only
        if not (0.50 <= fh <= 0.62):
            return None
        if not (1.50 <= home_odds <= 2.40):
            return None
        return {
            "pick": home, "model_prob": round(fh + 0.06, 4),
            "odds_at_prediction": round(home_odds, 2),
            "strategy": "deep_seek_1", "source": "expert_vig",
            "confidence": "B",
            "notes": f"golden zone home fh={fh:.0%} (fair odds)",
        }
    if vig >= 0.12:
        return None
    if not (0.50 <= fh <= 0.62):
        return None
    if not (1.50 <= home_odds <= 2.40):
        return None
    edge = fh + 0.06 - (1.0 / home_odds)
    if edge < vig / 2 + 0.02:
        return None
    return {
        "pick": home, "model_prob": round(fh + 0.06, 4),
        "odds_at_prediction": round(home_odds, 2),
        "strategy": "deep_seek_1", "source": "expert_vig",
        "confidence": "A" if edge > vig + 0.02 else "B",
        "notes": f"golden zone home fh={fh:.0%} edge={edge:+.1%} vig={vig:.1%}",
    }


def deep_seek_2(home, away, home_odds, away_odds, sport="") -> Optional[dict]:
    """صياد القيمة — Deep Seek 2: value hunting on either side.

    Root-cause evidence:
    - AWAY bets with real odds win 69% but are scarcer
    - The key is catching the underrated side regardless of home/away
    - Relaxed from edge>vig+3% to edge>vig/2+2% for more picks

    Formula: either side, fair_prob 0.45-0.65, odds 1.55-2.70, edge > vig/2 + 0.02.
    Fallback: if vig is negligible, use prob + odds band only.
    """
    fh, fa, vig = _fair_probs(home_odds, away_odds)
    if vig < 0.005:
        # Fair odds: bet either side in the probability sweet spot
        home_ok = 0.50 <= fh <= 0.65 and 1.55 <= home_odds <= 2.70
        away_ok = 0.45 <= fa <= 0.55 and 1.55 <= away_odds <= 2.70
        if home_ok:
            return {
                "pick": home, "model_prob": round(fh + 0.05, 4),
                "odds_at_prediction": round(home_odds, 2),
                "strategy": "deep_seek_2", "source": "expert_vig",
                "confidence": "C",
                "notes": f"value home fh={fh:.0%} (fair odds)",
            }
        if away_ok:
            return {
                "pick": away, "model_prob": round(fa, 4),
                "odds_at_prediction": round(away_odds, 2),
                "strategy": "deep_seek_2", "source": "expert_vig",
                "confidence": "C",
                "notes": f"value away fa={fa:.0%} (fair odds)",
            }
        return None
    if vig >= 0.10:
        return None
    home_edge = fh + 0.05 - (1.0 / home_odds) if home_odds > 1 else -1
    away_edge = fa - (1.0 / away_odds) if away_odds > 1 else -1
    min_edge = vig / 2 + 0.02
    if home_edge >= min_edge and 0.48 <= fh <= 0.65 and 1.55 <= home_odds <= 2.70:
        return {
            "pick": home, "model_prob": round(fh + 0.05, 4),
            "odds_at_prediction": round(home_odds, 2),
            "strategy": "deep_seek_2", "source": "expert_vig",
            "confidence": "A" if home_edge > vig else "B",
            "notes": f"value home fh={fh:.0%} h_edge={home_edge:+.1%} vig={vig:.1%}",
        }
    if away_edge >= min_edge and 0.45 <= fa <= 0.60 and 1.55 <= away_odds <= 2.70:
        return {
            "pick": away, "model_prob": round(fa, 4),
            "odds_at_prediction": round(away_odds, 2),
            "strategy": "deep_seek_2", "source": "expert_vig",
            "confidence": "A" if away_edge > vig else "B",
            "notes": f"value away fa={fa:.0%} a_edge={away_edge:+.1%} vig={vig:.1%}",
        }
    return None


def mid_odds_home(home: str, away: str, home_odds: float, away_odds: float) -> Optional[dict]:
    """Deep Seek 3 — المستفيد من الدروس: المنطقة الذهبية + تجنّب منطقة الموت.

    التحليل العميق: <1.5 = منطقة موت (473 رهان، 64% فوز، −$112). 1.5-1.8 مع 68% فوز = ربح.
    هذه الاستراتيجية تراهن على المضيف في النطاقين المربحين:
      A) 1.5-1.8 مع إشارة سوق قوية (fh≥55%) — المنطقة الذهبية
      B) 1.8-2.5 مع أفضلية المضيف (fh≥45%) — ميزة الأرض المتوسطة
    وتتجنب تماماً <1.5 (منطقة الموت) وتتجنب <45% fh (ضعيف جداً).
    """
    fh, fa, vig = _fair_probs(home_odds, away_odds)
    if vig > 0.10:
        return None
    if home_odds < 1.50:  # منطقة الموت — 473 رهان خسرت $112
        return None
    if fh < 0.45:  # أضعف من أن يراهن عليه
        return None
    if home_odds <= 1.80:
        if fh < 0.55:  # المنطقة الذهبية تحتاج إشارة سوق قوية
            return None
        conf = "A"
    elif home_odds <= 2.50:
        conf = "B"
    else:
        return None
    return {
        "pick": home, "model_prob": round(fh, 4),
        "odds_at_prediction": round(home_odds, 2),
        "strategy": "deep_seek_3", "source": "expert_vig",
        "confidence": conf,
        "notes": f"deep_seek_3 home fh={fh:.0%} zone={'golden' if home_odds<=1.8 else 'mid'}",
    }


def baseball_home_specialist(home: str, away: str, home_odds: float, away_odds: float) -> Optional[dict]:
    """Deep Seek 4 — متخصص baseball: أفضل رياضة (+$33.90، 68% فوز).

    baseball أثبت أنه الرياضة الأكثر ربحية. يستفيد من ميزة الأرض + السوق.
    يراهن على المضيف فقط في نطاق odds 1.4-2.2 (يتجنب منطقة الموت <1.4).
    """
    fh, fa, vig = _fair_probs(home_odds, away_odds)
    if vig > 0.10 or not (1.40 <= home_odds <= 2.20):
        return None
    if fh < 0.45:
        return None
    return {
        "pick": home, "model_prob": round(fh, 4),
        "odds_at_prediction": round(home_odds, 2),
        "strategy": "deep_seek_4", "source": "expert_vig",
        "confidence": "A" if fh >= 0.55 else "B",
        "notes": f"deep_seek_4 baseball home fh={fh:.0%} (#1 sport +$33.90)",
    }


def safe_odds_floor(home: str, away: str, home_odds: float, away_odds: float) -> Optional[dict]:
    """Deep Seek 5 — طبقة أمان: يرفض منطقة الموت <1.50.

    التحليل العميق: أي odds <1.50 تخسر رياضياً (319+473 رهان، −$172).
    هذه طبقة أمان لكل الاستراتيجيات — لا تراهن أبداً تحت 1.50.
    الفارق عن deep_seek_3: يقبل أي fh≥48% طالما odds في النطاق المربح.
    """
    fh, fa, vig = _fair_probs(home_odds, away_odds)
    if vig > 0.12:
        return None
    if home_odds < 1.50:  # منطقة الموت المؤكدة
        return None
    if not (1.50 <= home_odds <= 2.50):
        return None
    if fh < 0.48:
        return None
    return {
        "pick": home, "model_prob": round(fh, 4),
        "odds_at_prediction": round(home_odds, 2),
        "strategy": "deep_seek_5", "source": "expert_vig",
        "confidence": "A" if fh >= 0.55 else "B",
        "notes": f"deep_seek_5 safe floor odds>1.5 fh={fh:.0%}",
    }


def deep_seek_6_tt_away(home: str, away: str, home_odds: float, away_odds: float, sport: str = "") -> Optional[dict]:
    """Deep Seek 6 — table tennis AWAY specialist (86% away win rate, +$3.74).

    TT data: HOME loses 41%, AWAY wins 86%! The better player is listed second.
    Activates only on tabletennis, bets the AWAY side in the profitable odds zone.
    Strictly avoids home bets (they lose in TT).
    """
    if sport not in ("tabletennis", "table_tennis", ""):
        return None
    fh, fa, vig = _fair_probs(home_odds, away_odds)
    if vig > 0.10 or away_odds < 1.20:
        return None  # avoid extreme TT away favorites
    if not (1.20 <= away_odds <= 2.50):
        return None
    if fa < 0.40:  # away side must have at least 40% fair chance
        return None
    conf = "A" if fa >= 0.55 else "B"
    return {
        "pick": away, "model_prob": round(fa, 4),
        "odds_at_prediction": round(away_odds, 2),
        "strategy": "deep_seek_6", "source": "expert_vig",
        "confidence": conf,
        "notes": f"deep_seek_6 TT away fa={fa:.0%} (TT away 86% win)",
    }


def deep_seek_7_baseball_compound(home: str, away: str, home_odds: float, away_odds: float, sport: str = "") -> Optional[dict]:
    """Deep Seek 7 — baseball multi-factor HOME specialist.

    Baseball: best sport (+$36.83, 69% home win). Uses multiple factors:
    - Sport must be baseball
    - HOME side only (69% win on home)
    - Odds 1.3-2.2 zone (avoids death zone <1.3, avoids weak favorites >2.2)
    - Strict home probability requirement (fh >= 0.42)
    - Confidence: A if strong favorite (fh>=0.55), B otherwise
    """
    if sport not in ("baseball", ""):
        return None
    fh, fa, vig = _fair_probs(home_odds, away_odds)
    if vig > 0.10:
        return None
    if not (1.30 <= home_odds <= 2.20):
        return None
    if fh < 0.42:  # baseball home teams win more, lower threshold acceptable
        return None
    conf = "A" if fh >= 0.55 else "B"
    return {
        "pick": home, "model_prob": round(fh, 4),
        "odds_at_prediction": round(home_odds, 2),
        "strategy": "deep_seek_7", "source": "expert_vig",
        "confidence": conf,
        "notes": f"deep_seek_7 baseball home fh={fh:.0%} (69% win rate)",
    }


def deep_seek_8_tennis_hybrid(home: str, away: str, home_odds: float, away_odds: float, sport: str = "") -> Optional[dict]:
    """Deep Seek 8 — tennis adaptive hybrid (side-aware).

    Tennis: AWAY wins 72% (+$1.35), HOME wins 61% (+$0.43). Both are decent
    but away has the edge. This strategy picks the BETTER side based on odds:
    - If away is the stronger player (away_odds < home_odds), bet away
    - If home is the stronger player AND odds are in golden zone, bet home
    - Avoids extreme favorites (<1.25) and the vig trap
    """
    if sport not in ("tennis", ""):
        return None
    fh, fa, vig = _fair_probs(home_odds, away_odds)
    if vig > 0.10:
        return None
    if away_odds < home_odds and 1.25 <= away_odds <= 2.50:
        # away is stronger — bet away (tennis away bias)
        if fa < 0.40: return None
        return {
            "pick": away, "model_prob": round(fa, 4),
            "odds_at_prediction": round(away_odds, 2),
            "strategy": "deep_seek_8", "source": "expert_vig",
            "confidence": "A" if fa >= 0.55 else "B",
            "notes": f"deep_seek_8 tennis away fav fa={fa:.0%}",
        }
    if home_odds < away_odds and 1.35 <= home_odds <= 2.50:
        # home is stronger — bet home (tennis home works too, just less profit)
        if fh < 0.45: return None
        return {
            "pick": home, "model_prob": round(fh, 4),
            "odds_at_prediction": round(home_odds, 2),
            "strategy": "deep_seek_8", "source": "expert_vig",
            "confidence": "B",
            "notes": f"deep_seek_8 tennis home fav fh={fh:.0%}",
        }
    return None


def deep_seek_9_football_away(home: str, away: str, home_odds: float, away_odds: float, sport: str = "") -> Optional[dict]:
    """Deep Seek 9 — football AWAY premium (80% away win rate, +$3.70).

    Football data: AWAY wins 80% with +$3.70 profit! The strategies are picking
    strong away favorites correctly. HOME only 58%, loses $-11.67. This strategy
    bets the AWAY side exclusively in football, with strict odds control.
    """
    if sport not in ("football", "soccer", ""):
        return None
    fh, fa, vig = _fair_probs(home_odds, away_odds)
    if vig > 0.12:  # football margins can be wider, allow up to 12%
        return None
    if not (1.30 <= away_odds <= 2.50):
        return None
    if fa < 0.42:  # away side must have a real chance
        return None
    return {
        "pick": away, "model_prob": round(fa, 4),
        "odds_at_prediction": round(away_odds, 2),
        "strategy": "deep_seek_9", "source": "expert_vig",
        "confidence": "A" if fa >= 0.55 else "B",
        "notes": f"deep_seek_9 football away fa={fa:.0%} (80% away win)",
    }


def deep_seek_10_hybrid_auto(home: str, away: str, home_odds: float, away_odds: float, sport: str = "") -> Optional[dict]:
    """Deep Seek 10 — auto-detecting multi-sport hybrid.

    Chooses the best strategy automatically based on sport:
    - tabletennis → deep_seek_6 logic (bet away)
    - baseball → deep_seek_7 logic (bet home)
    - tennis → deep_seek_8 logic (side-aware)
    - football → deep_seek_9 logic (bet away)
    - other sports → best-guess (prefer higher fair prob side, odds > 1.5)
    The most complex strategy — routes to per-sport specialists automatically.
    """
    # route to per-sport specialists
    if sport in ("tabletennis", "table_tennis"):
        r = deep_seek_6_tt_away(home, away, home_odds, away_odds, sport)
    elif sport == "baseball":
        r = deep_seek_7_baseball_compound(home, away, home_odds, away_odds, sport)
    elif sport == "tennis":
        r = deep_seek_8_tennis_hybrid(home, away, home_odds, away_odds, sport)
    elif sport in ("football", "soccer"):
        r = deep_seek_9_football_away(home, away, home_odds, away_odds, sport)
    else:
        r = None
    if r:
        r["strategy"] = "deep_seek_10"
        r["notes"] = r["notes"].replace("deep_seek_", "deep_seek_10 via ")
        return r
    # fallback for sports without a specialist: bet the side with higher fair prob AT odds >1.5
    fh, fa, vig = _fair_probs(home_odds, away_odds)
    if vig > 0.10: return None
    if fh >= fa and fh >= 0.50 and home_odds >= 1.50 and home_odds <= 2.50:
        return {"pick": home, "model_prob": round(fh, 4), "odds_at_prediction": round(home_odds, 2),
                "strategy": "deep_seek_10", "source": "expert_vig", "confidence": "B",
                "notes": f"deep_seek_10 generic home {sport} fh={fh:.0%}"}
    if fa > fh and fa >= 0.45 and away_odds >= 1.50 and away_odds <= 2.50:
        return {"pick": away, "model_prob": round(fa, 4), "odds_at_prediction": round(away_odds, 2),
                "strategy": "deep_seek_10", "source": "expert_vig", "confidence": "B",
                "notes": f"deep_seek_10 generic away {sport} fa={fa:.0%}"}
    return None


def deep_seek_11_multifilter(home: str, away: str, home_odds: float, away_odds: float, sport: str = "") -> Optional[dict]:
    """Deep Seek 11 — الفلتر النهائي (5 فلاتر متتالية).

    ╔═══════════════════════════════════════════════════════════════╗
    ║  الفلتر 1: الرياضة  →  مسموح فقط: TT, baseball, tennis, football
    ║  الفلتر 2: الطرف    →  TT=away, baseball=home, tennis=أقوى, football=away
    ║  الفلتر 3: odds     →  نطاق دقيق لكل رياضة (يتجنب الموت <1.5)
    ║  الفلتر 4: vig      →  ≤10% فقط (يرفض الأسواق المُضخّمة)
    ║  الفلتر 5: الحافة   →  edge > vig/2 + 3% (يجب أن تتجاوز العمولة)
    ╚═══════════════════════════════════════════════════════════════╝

    لو فشل أي فلتر → لا رهان. هذا يُنتج عدداً قليلاً من التنبؤات لكن بدقة عالية جداً.
    """
    fh, fa, vig = _fair_probs(home_odds, away_odds)

    # ── الفلتر 4: vig معقول ──
    if vig > 0.10 or vig <= 0:
        return None

    # ── الفلتر 1+2+3: لكل رياضة قواعدها ──
    if sport in ("tabletennis", "table_tennis"):
        if away_odds < 1.25 or away_odds > 2.50 or fa < 0.42:
            return None
        side, prob, odds, edge = "away", fa, away_odds, fa - (1.0 / away_odds if away_odds > 1 else 0)
    elif sport == "baseball":
        if home_odds < 1.35 or home_odds > 2.20 or fh < 0.45:
            return None
        side, prob, odds, edge = "home", fh, home_odds, fh - (1.0 / home_odds if home_odds > 1 else 0)
    elif sport == "tennis":
        if away_odds < home_odds and 1.25 <= away_odds <= 2.50 and fa >= 0.42:
            side, prob, odds = "away", fa, away_odds
        elif home_odds < away_odds and 1.35 <= home_odds <= 2.50 and fh >= 0.45:
            side, prob, odds = "home", fh, home_odds
        else:
            return None
        edge = prob - (1.0 / odds if odds > 1 else 0)
    elif sport in ("football", "soccer"):
        if away_odds < 1.35 or away_odds > 2.50 or fa < 0.44:
            return None
        side, prob, odds, edge = "away", fa, away_odds, fa - (1.0 / away_odds if away_odds > 1 else 0)
    else:
        return None  # الفلتر 1: رياضة غير مدعومة

    # ── الفلتر 5: الحافة يجب أن تتجاوز الـ vig ──
    if edge is None or edge < vig / 2 + 0.03:
        return None

    confidence = "A" if edge > vig + 0.03 else ("B" if edge > vig / 2 + 0.05 else "C")
    pick_name = home if side == "home" else away
    return {
        "pick": pick_name, "model_prob": round(prob, 4),
        "odds_at_prediction": round(odds, 2),
        "strategy": "deep_seek_11", "source": "expert_vig",
        "confidence": confidence,
        "notes": f"deep_seek_11 5-filter {sport} {side} edge={edge:+.1%} vig={vig:.1%}",
    }


def nova_fade_favorite(home: str, away: str, home_odds: float, away_odds: float, sport: str = "") -> Optional[dict]:
    """Nova 1 — مكافحة المفضّل الثقيل (contrarian, مبنية على الدليل).

    دليل 14 يوماً: المفضّلون الثقال (odds 1.0-1.4، 2046 رهان) فازوا 70.9% فقط بينما
    التعادل يطلب 71-100% → خسارة −324. إذن سعر المفضّل مُبالغ فيه والكلب مُرخّص.
    تراهن على الكلب (الطرف الآخر) عندما يكون المفضّل odds≤1.45 والكلب سائل [2.5,5.5].
    """
    fav_odds = min(home_odds, away_odds)
    if fav_odds > 1.45:
        return None
    if home_odds <= away_odds:
        dog, dog_odds = away, away_odds
    else:
        dog, dog_odds = home, home_odds
    if not (2.50 <= dog_odds <= 5.50):
        return None
    prob = 1.0 / dog_odds
    return {
        "pick": dog, "model_prob": round(prob, 4),
        "odds_at_prediction": round(dog_odds, 2),
        "strategy": "nova_fade_favorite", "source": "expert_vig",
        "confidence": "B",
        "notes": f"nova fade fav {fav_odds:.2f} dog@{dog_odds:.2f} prob{prob:.0%}",
    }


def nova_sweet_spot(home: str, away: str, home_odds: float, away_odds: float, sport: str = "") -> Optional[dict]:
    """Nova 2 — الشريحة الذهبية فقط (تجنّب المخاطرة).

    دليل 14 يوماً: شريحة odds 2.0-2.5 هي الوحيدة غير الخاسرة (50.4% فوز، ~تعادل/موجب)،
    بينما كل ما دونها يخسر. لا تراهن إلا داخل [2.00,2.50] على أي طرف، فتتجنّب منطقة
    الموت (<1.5) وفخّ المفضّلات والفخّ الأوسط (1.5-2.0).
    """
    LO, HI = 2.00, 2.50
    cands = []
    if LO <= home_odds <= HI:
        cands.append((home, home_odds))
    if LO <= away_odds <= HI:
        cands.append((away, away_odds))
    if not cands:
        return None
    pick, odds = min(cands, key=lambda c: c[1])  # الأعلى احتمالاً ضمن الشريحة
    prob = 1.0 / odds
    return {
        "pick": pick, "model_prob": round(prob, 4),
        "odds_at_prediction": round(odds, 2),
        "strategy": "nova_sweet_spot", "source": "expert_vig",
        "confidence": "B",
        "notes": f"nova sweet_spot 2.0-2.5 @{odds:.2f} prob{prob:.0%}",
    }


def nova_underdog(home: str, away: str, home_odds: float, away_odds: float, sport: str = "") -> Optional[dict]:
    """Nova 3 — صياد الكلاب المتوسط (longshot value).

    دليل 14 يوماً: شريحة 2.5+ كانت موجبة (+3، عينة صغيرة). تراهن على الكلب في
    [2.50,4.00] فقط عندما يكون المفضّل معتدلاً [1.30,1.70] (ليس منطقة موت ولا متطرّفاً).
    """
    if home_odds < away_odds:
        fav_o, dog_o, dog = home_odds, away_odds, away
    else:
        fav_o, dog_o, dog = away_odds, home_odds, home
    if not (2.50 <= dog_o <= 4.00):
        return None
    if not (1.30 <= fav_o <= 1.70):
        return None
    prob = 1.0 / dog_o
    return {
        "pick": dog, "model_prob": round(prob, 4),
        "odds_at_prediction": round(dog_o, 2),
        "strategy": "nova_underdog", "source": "expert_vig",
        "confidence": "C",
        "notes": f"nova underdog dog@{dog_o:.2f} fav{fav_o:.2f} prob{prob:.0%}",
    }


def nova_pickem(home: str, away: str, home_odds: float, away_odds: float, sport: str = "") -> Optional[dict]:
    """Nova 4 — مركز الشريحة (pick'em، الأقل تأثّراً بالـvig).

    حول even-money [1.90,2.15] يكون أثر الـvig في أصغره. تراهن على الطرف الأعلى
    احتمالاً ضمن هذا النطاق الضيّق — أعلى دقة وأقل حجم.
    """
    LO, HI = 1.90, 2.15
    cands = []
    if LO <= home_odds <= HI:
        cands.append((home, home_odds))
    if LO <= away_odds <= HI:
        cands.append((away, away_odds))
    if not cands:
        return None
    pick, odds = min(cands, key=lambda c: c[1])
    prob = 1.0 / odds
    return {
        "pick": pick, "model_prob": round(prob, 4),
        "odds_at_prediction": round(odds, 2),
        "strategy": "nova_pickem", "source": "expert_vig",
        "confidence": "B",
        "notes": f"nova pickem 1.9-2.15 @{odds:.2f} prob{prob:.0%}",
    }


def nova_volley_home(home: str, away: str, home_odds: float, away_odds: float, sport: str = "") -> Optional[dict]:
    """Nova 5 — كرة الطائرة HOME (الحافة الأقوى المُصدّقة).

    دليل فريد (dedup-aware): volleyball HOME 14/14 = 100% عند odds~1.45.
    الطائرة رياضة قليلة المفاجآت (best-of-5 sets)، والمضيف المُسعّر مفضّلاً يفوز
    باستمرار. تحذير: العيّنة صغيرة (14 مباراة فريدة) — تُراقب عن كثب.
    يراهن على HOME في الطائرة فقط ضمن [1.25, 2.30]."""
    if sport != "volleyball":
        return None
    if not (1.25 <= home_odds <= 2.30):
        return None
    fh = 1.0 / home_odds
    return {
        "pick": home, "model_prob": round(fh, 4),
        "odds_at_prediction": round(home_odds, 2),
        "strategy": "nova_volley_home", "source": "expert_vig",
        "confidence": "A",
        "notes": f"nova volley HOME @{home_odds:.2f} (14/14 live unique)",
    }


def nova_baseball_away(home: str, away: str, home_odds: float, away_odds: float, sport: str = "") -> Optional[dict]:
    """Nova 6 — البيسبول AWAY (الحافة الحقيقية الأكثر مصداقية).

    دليل فريد (dedup-aware): baseball AWAY 20/28 = 71% عند odds~1.73، التعادل 58%
    → حافة +13% (الأقوى إحصائياً في النظام). البيسبول: المضيف يفوز 48% فقط بينما
    الضيف المُفضّل يفوز 71% — ميزة الضيف مُسكّرة في السوق.
    يراهن على AWAY في البيسبول فقط ضمن [1.40, 2.40] مع away_prob ≥ 0.38."""
    if sport != "baseball":
        return None
    if not (1.40 <= away_odds <= 2.40):
        return None
    fa = 1.0 / away_odds
    if fa < 0.38:
        return None
    return {
        "pick": away, "model_prob": round(fa, 4),
        "odds_at_prediction": round(away_odds, 2),
        "strategy": "nova_baseball_away", "source": "expert_vig",
        "confidence": "B",
        "notes": f"nova baseball AWAY @{away_odds:.2f} (71% live unique)",
    }


def nova_fade_fav_v2(home: str, away: str, home_odds: float, away_odds: float, sport: str = "") -> Optional[dict]:
    """Nova fade favorite v2 — مُحسّنة بالدليل الحيّ (2026-06-26).

    الأصل nova_fade_favorite حقّق +30% ROI على 132 مباراة فريدة، لكن التحليل كشف:
      - الحافة في tennis (+37.9) وtabletennis (+6.7) فقط؛ volleyball خسر.
      - شريحة odds 4.5+ خسرت (longshots حقيقية، 18% فوز).
    v2 تقصُر على tennis/tabletennis + سقف odds 4.5 (تستبعد الذيل الخاسر) + المفضّل ≤1.50.
    """
    if sport not in ("tennis", "tabletennis", "table_tennis"):
        return None
    fav_odds = min(home_odds, away_odds)
    if fav_odds > 1.50:
        return None
    if home_odds <= away_odds:
        dog, dog_odds = away, away_odds
    else:
        dog, dog_odds = home, home_odds
    if not (2.50 <= dog_odds <= 4.50):
        return None
    prob = 1.0 / dog_odds
    return {
        "pick": dog, "model_prob": round(prob, 4),
        "odds_at_prediction": round(dog_odds, 2),
        "strategy": "nova_fade_fav_v2", "source": "expert_vig",
        "confidence": "B",
        "notes": f"nova fade v2 {sport} fav{fav_odds:.2f} dog@{dog_odds:.2f}",
    }


def nova_baseball_away_v2(home: str, away: str, home_odds: float, away_odds: float, sport: str = "") -> Optional[dict]:
    """Nova baseball away v2 — مُحسّنة بالدليل الحيّ (2026-06-26).

    الأصل nova_baseball_away حقّق +31% ROI (67% فوز على 39 مباراة) — أقوى حافة مؤكَّدة.
    v2 توسّع النطاق قليلاً (away_odds حتى 2.60) وتشدّد الحدّ الأدنى للاحتمال (≥0.40)
    لالتقاط مزيد من الضيوف الأقوياء المُسكّرين مع الحفاظ على الجودة.
    """
    if sport != "baseball":
        return None
    if not (1.45 <= away_odds <= 2.60):
        return None
    fa = 1.0 / away_odds
    if fa < 0.40:
        return None
    return {
        "pick": away, "model_prob": round(fa, 4),
        "odds_at_prediction": round(away_odds, 2),
        "strategy": "nova_baseball_away_v2", "source": "expert_vig",
        "confidence": "B",
        "notes": f"nova baseball away v2 @{away_odds:.2f} (proven +31% edge)",
    }




# ============================================================================
#  v1 strategies (2026-06-27) — evidence-based improvements of the 8 profitable
#  strategies + 1 validated inverse. Append-only: originals stay live for monitoring.
#  Each v1 is built from deep odds-bucket x sport anatomy of its parent (unique-match
#  dedup-aware), NOT guesses. Naming: <parent>_v1.
# ============================================================================

def nova_fade_favorite_v1(home, away, home_odds, away_odds, sport="") -> Optional[dict]:
    """fade_favorite v1 — parent +25% ROI (163 bets). Anatomy: edge in tabletennis
    (+39.5) & tennis (+29.7) ONLY; volleyball/football/cricket/handball all negative;
    odds 5.0+ nearly dead (3/14, +1.7). v1 = restrict to tt/tennis + cap odds at 5.0."""
    if sport not in ("tennis", "tabletennis", "table_tennis"):
        return None
    fav_odds = min(home_odds, away_odds)
    if fav_odds > 1.45:
        return None
    if home_odds <= away_odds:
        dog, dog_odds = away, away_odds
    else:
        dog, dog_odds = home, home_odds
    if not (2.50 <= dog_odds <= 5.00):
        return None
    prob = 1.0 / dog_odds
    return {"pick": dog, "model_prob": round(prob, 4), "odds_at_prediction": round(dog_odds, 2),
            "strategy": "nova_fade_favorite_v1", "source": "expert_vig", "confidence": "B",
            "notes": f"fade_fav v1 {sport} dog@{dog_odds:.2f}"}


def nova_fade_fav_v2_v1(home, away, home_odds, away_odds, sport="") -> Optional[dict]:
    """fade_fav_v2 v1 — parent +33% ROI (28 bets), strongest ROI. Anatomy: odds
    2.5-3.5 is the core (+17.9 on 39); 3.5-5.0 thinner. v1 = tighten to the 2.5-3.6
    sweet spot where conviction is highest (sacrifices volume for ROI)."""
    if sport not in ("tennis", "tabletennis", "table_tennis"):
        return None
    fav_odds = min(home_odds, away_odds)
    if fav_odds > 1.50:
        return None
    if home_odds <= away_odds:
        dog, dog_odds = away, away_odds
    else:
        dog, dog_odds = home, home_odds
    if not (2.50 <= dog_odds <= 3.60):
        return None
    prob = 1.0 / dog_odds
    return {"pick": dog, "model_prob": round(prob, 4), "odds_at_prediction": round(dog_odds, 2),
            "strategy": "nova_fade_fav_v2_v1", "source": "expert_vig", "confidence": "A",
            "notes": f"fade_fav_v2 v1 {sport} core@{dog_odds:.2f}"}


def nova_baseball_away_v1(home, away, home_odds, away_odds, sport="") -> Optional[dict]:
    """baseball_away v1 — parent +26% ROI (42 bets). Anatomy: 2.0-2.5 bucket (+8.9
    on 17) outperforms 1.5-2.0 (+2.7 on 34) per-bet. v1 = shift the floor up to 1.70
    and require fa>=0.40, concentrating on the stronger-priced away dogs."""
    if sport != "baseball":
        return None
    if not (1.70 <= away_odds <= 2.50):
        return None
    fa = 1.0 / away_odds
    if fa < 0.40:
        return None
    return {"pick": away, "model_prob": round(fa, 4), "odds_at_prediction": round(away_odds, 2),
            "strategy": "nova_baseball_away_v1", "source": "expert_vig", "confidence": "B",
            "notes": f"baseball_away v1 @{away_odds:.2f} (2.0-2.5 sweet)"}


def deep_seek_9_v1(home, away, home_odds, away_odds, sport="") -> Optional[dict]:
    """deep_seek_9 (football away) v1 — parent +10% ROI (79 bets). Anatomy: 2.0-2.5
    bucket strongly positive (+9.6 on 24), but 1.5-2.0 LOSES (-3.5 on 38). v1 =
    exclude the losing 1.5-2.0 band; bet football away at 1.30-1.50 OR 2.00-2.50."""
    if sport not in ("football", "soccer", ""):
        return None
    fh, fa, vig = _fair_probs(home_odds, away_odds)
    if vig > 0.12:
        return None
    if not ((1.30 <= away_odds <= 1.50) or (2.00 <= away_odds <= 2.50)):
        return None
    if fa < 0.42:
        return None
    return {"pick": away, "model_prob": round(fa, 4), "odds_at_prediction": round(away_odds, 2),
            "strategy": "deep_seek_9_v1", "source": "expert_vig",
            "confidence": "A" if fa >= 0.55 else "B",
            "notes": f"ds9 v1 football away fa={fa:.0%} (skips dead 1.5-2.0)"}


def nova_underdog_v1(home, away, home_odds, away_odds, sport="") -> Optional[dict]:
    """underdog v1 — parent +8% ROI (110 bets). Anatomy: tabletennis (+16.8 on 48)
    carries it; tennis is a net drag (-1.1 on 65). v1 = exclude tennis, keep the
    moderate-favorite/mid-dog structure where the tt edge lives."""
    if sport == "tennis":
        return None
    if home_odds < away_odds:
        fav_o, dog_o, dog = home_odds, away_odds, away
    else:
        fav_o, dog_o, dog = away_odds, home_odds, home
    if not (2.50 <= dog_o <= 4.00):
        return None
    if not (1.30 <= fav_o <= 1.70):
        return None
    prob = 1.0 / dog_o
    return {"pick": dog, "model_prob": round(prob, 4), "odds_at_prediction": round(dog_o, 2),
            "strategy": "nova_underdog_v1", "source": "expert_vig", "confidence": "C",
            "notes": f"underdog v1 {sport} dog@{dog_o:.2f} (no tennis)"}


def nova_sweet_spot_v1(home, away, home_odds, away_odds, sport="") -> Optional[dict]:
    """sweet_spot v1 — parent +6% ROI (190 bets). Anatomy: the 2.0-2.5 band is the
    whole edge (+19.6); per-sport tt/football/tennis/baseball all positive, only
    handball negative (1 bet). v1 = same 2.0-2.5 band but skip the weak sports and
    tighten to 2.00-2.40 where win-rate sits just above breakeven."""
    if sport in ("handball", "futsal", "hockey"):
        return None
    LO, HI = 2.00, 2.40
    cands = []
    if LO <= home_odds <= HI:
        cands.append((home, home_odds))
    if LO <= away_odds <= HI:
        cands.append((away, away_odds))
    if not cands:
        return None
    pick, odds = min(cands, key=lambda c: c[1])
    prob = 1.0 / odds
    return {"pick": pick, "model_prob": round(prob, 4), "odds_at_prediction": round(odds, 2),
            "strategy": "nova_sweet_spot_v1", "source": "expert_vig", "confidence": "B",
            "notes": f"sweet_spot v1 {sport} @{odds:.2f}"}


def deep_seek_3_v1(home, away, home_odds, away_odds, sport="") -> Optional[dict]:
    """deep_seek_3 (mid-odds home) v1 — parent +2% ROI (51 bets, marginal). Anatomy:
    1.5-2.0 band ~flat (-0.6 on 40); 2.0-2.5 positive (+1.9 on 11); tabletennis drags
    (-3.5). v1 = home bets 1.80-2.50 only (skip the flat low band), exclude tt."""
    if sport in ("tabletennis", "table_tennis"):
        return None
    fh, fa, vig = _fair_probs(home_odds, away_odds)
    if vig > 0.10:
        return None
    if not (1.80 <= home_odds <= 2.50):
        return None
    if fh < 0.45:
        return None
    return {"pick": home, "model_prob": round(fh, 4), "odds_at_prediction": round(home_odds, 2),
            "strategy": "deep_seek_3_v1", "source": "expert_vig",
            "confidence": "B", "notes": f"ds3 v1 home fh={fh:.0%} (1.8-2.5, no tt)"}


def deep_seek_5_v1(home, away, home_odds, away_odds, sport="") -> Optional[dict]:
    """deep_seek_5 (safe floor) v1 — parent ~0% ROI (51 bets, breakeven). Anatomy:
    1.5-2.0 net negative (-2.5 on 47); 2.0-2.5 positive (+2.7 on 4); tennis/tt drag.
    v1 = raise the floor to 1.85 and exclude tennis/tt, keeping darts/cricket/football/
    baseball where it was net-positive."""
    if sport in ("tennis", "tabletennis", "table_tennis"):
        return None
    fh, fa, vig = _fair_probs(home_odds, away_odds)
    if vig > 0.12:
        return None
    if not (1.85 <= home_odds <= 2.50):
        return None
    if fh < 0.42:
        return None
    return {"pick": home, "model_prob": round(fh, 4), "odds_at_prediction": round(home_odds, 2),
            "strategy": "deep_seek_5_v1", "source": "expert_vig",
            "confidence": "B", "notes": f"ds5 v1 home fh={fh:.0%} (1.85+, no tennis/tt)"}


def deep_seek_6_inverse_v1(home, away, home_odds, away_odds, sport="") -> Optional[dict]:
    """deep_seek_6 INVERSE v1 — the validated inversion. Parent deep_seek_6 bets TT
    AWAY and loses (-15% ROI). Real-odds test of the OPPOSITE (bet HOME at home_odds)
    = +14% ROI, 52% win on 193 matches. The directional bias was simply backwards:
    in these tt feeds the listed-first (home) player is underrated. v1 bets TT HOME
    in the same profitable odds zone the parent used for away."""
    if sport not in ("tabletennis", "table_tennis"):
        return None
    fh, fa, vig = _fair_probs(home_odds, away_odds)
    if vig > 0.10 or home_odds < 1.20:
        return None
    if not (1.20 <= home_odds <= 2.50):
        return None
    if fh < 0.40:
        return None
    return {"pick": home, "model_prob": round(fh, 4), "odds_at_prediction": round(home_odds, 2),
            "strategy": "deep_seek_6_inverse_v1", "source": "expert_vig",
            "confidence": "B", "notes": f"ds6 INVERSE v1 TT home fh={fh:.0%} (+14% live)"}




# ============================================================================
#  TENNIS STRATEGY SUITE (2026-06-27) — built from deep analysis of 272 real
#  1xBet tennis matches (unique, with opening+closing odds + movement + tier).
#  Core finding: tennis heavy favorites are systematically OVERPRICED; dogs are
#  underpriced. Movement, favorite-strength, tier, and side all sharpen the edge.
#  These use the cache's rich params (home_odds/away_odds/home_move/away_move).
#  Append-only; all compete in the tournament. Naming: tennis_*.
# ============================================================================

def _t_dog(home, away, home_odds, away_odds):
    """Return (dog_name, dog_odds, dog_move_side) — the underdog and which move."""
    if home_odds > away_odds:
        return home, home_odds, "home"
    return away, away_odds, "away"


def tennis_fade_fav(home, away, home_odds, away_odds, sport="", **kw) -> Optional[dict]:
    """tennis_fade_fav — the proven core: fade favorite<=1.40, back dog 2.5-6.0.
    +50% ROI on 98 matches. The single strongest broad tennis edge."""
    if sport != "tennis":
        return None
    if min(home_odds, away_odds) > 1.40:
        return None
    dog, do, _ = _t_dog(home, away, home_odds, away_odds)
    if not (2.50 <= do <= 6.00):
        return None
    return {"pick": dog, "model_prob": round(1.0/do, 4), "odds_at_prediction": round(do, 2),
            "strategy": "tennis_fade_fav", "source": "expert_vig", "confidence": "B",
            "notes": f"tennis fade fav, dog@{do:.2f}"}


def tennis_fade_fav_stable(home, away, home_odds, away_odds, sport="",
                           home_move=None, away_move=None, **kw) -> Optional[dict]:
    """tennis_fade_fav_stable — the STRONGEST combo: fade fav<=1.40, dog 3.0-7.0,
    AND the dog's odds are STABLE (|move|<=3%). +86% ROI on 67 matches. Drifting
    dogs (market moving against them) lose; stable dogs are pure mispricing."""
    if sport != "tennis":
        return None
    if min(home_odds, away_odds) > 1.40:
        return None
    dog, do, side = _t_dog(home, away, home_odds, away_odds)
    if not (3.00 <= do <= 7.00):
        return None
    mv = (home_move if side == "home" else away_move)
    if mv is not None and abs(mv) > 0.03:
        return None  # skip dogs whose price is moving (info we don't want to fade)
    return {"pick": dog, "model_prob": round(1.0/do, 4), "odds_at_prediction": round(do, 2),
            "strategy": "tennis_fade_fav_stable", "source": "expert_vig", "confidence": "A",
            "notes": f"tennis fade fav + stable, dog@{do:.2f}"}


def tennis_extreme_fav_fade(home, away, home_odds, away_odds, sport="", **kw) -> Optional[dict]:
    """tennis_extreme_fav_fade — when the favorite is EXTREME (<=1.25), the dog
    (3.0-8.0) is most overlooked. +49% ROI on 53 matches; the upset premium is
    largest exactly where the crowd is most certain."""
    if sport != "tennis":
        return None
    if min(home_odds, away_odds) > 1.25:
        return None
    dog, do, _ = _t_dog(home, away, home_odds, away_odds)
    if not (3.00 <= do <= 8.00):
        return None
    return {"pick": dog, "model_prob": round(1.0/do, 4), "odds_at_prediction": round(do, 2),
            "strategy": "tennis_extreme_fav_fade", "source": "expert_vig", "confidence": "B",
            "notes": f"tennis extreme-fav fade, dog@{do:.2f}"}


def tennis_big_dog(home, away, home_odds, away_odds, sport="", **kw) -> Optional[dict]:
    """tennis_big_dog — pure longshot value: back any dog priced 4.0-7.0. +61% ROI
    on 40 matches. Big tennis dogs win ~32% but pay >4x; the market overrounds
    favorites so the longshot side carries positive EV."""
    if sport != "tennis":
        return None
    cands = []
    if 4.0 <= home_odds <= 7.0 and home_odds > away_odds:
        cands.append((home, home_odds))
    if 4.0 <= away_odds <= 7.0 and away_odds > home_odds:
        cands.append((away, away_odds))
    if not cands:
        return None
    pick, do = cands[0]
    return {"pick": pick, "model_prob": round(1.0/do, 4), "odds_at_prediction": round(do, 2),
            "strategy": "tennis_big_dog", "source": "expert_vig", "confidence": "C",
            "notes": f"tennis big dog @{do:.2f}"}


def tennis_mid_dog(home, away, home_odds, away_odds, sport="", **kw) -> Optional[dict]:
    """tennis_mid_dog — the reliable middle: back the dog priced 2.7-4.0. +35% ROI
    on 77 matches. Sweet spot between vig-eaten short prices and pure longshots."""
    if sport != "tennis":
        return None
    cands = []
    if 2.7 <= home_odds <= 4.0 and home_odds > away_odds:
        cands.append((home, home_odds))
    if 2.7 <= away_odds <= 4.0 and away_odds > home_odds:
        cands.append((away, away_odds))
    if not cands:
        return None
    pick, do = cands[0]
    return {"pick": pick, "model_prob": round(1.0/do, 4), "odds_at_prediction": round(do, 2),
            "strategy": "tennis_mid_dog", "source": "expert_vig", "confidence": "B",
            "notes": f"tennis mid dog @{do:.2f}"}


def tennis_home_dog(home, away, home_odds, away_odds, sport="", **kw) -> Optional[dict]:
    """tennis_home_dog — listed-first underdog edge: when HOME (first-listed) is the
    dog at 2.5-6.0 and favorite<=1.45. +60% ROI on 37 matches. The first-listed
    player is slightly underrated by these feeds."""
    if sport != "tennis":
        return None
    if home_odds <= away_odds:
        return None  # home must be the dog
    if min(home_odds, away_odds) > 1.45:
        return None
    if not (2.50 <= home_odds <= 6.00):
        return None
    return {"pick": home, "model_prob": round(1.0/home_odds, 4), "odds_at_prediction": round(home_odds, 2),
            "strategy": "tennis_home_dog", "source": "expert_vig", "confidence": "B",
            "notes": f"tennis home(first) dog @{home_odds:.2f}"}


def tennis_itf_dog(home, away, home_odds, away_odds, sport="", league="", **kw) -> Optional[dict]:
    """tennis_itf_dog — ITF/Challenger inefficiency: lower tours are less efficient,
    so the fade-favorite edge is cleanest there. +42% ROI on 65 ITF matches.
    Restricts the fade-dog to ITF/Challenger/lower events only."""
    if sport != "tennis":
        return None
    l = (league or "").lower()
    if not ("itf" in l or "challenger" in l):
        return None
    if min(home_odds, away_odds) > 1.45:
        return None
    dog, do, _ = _t_dog(home, away, home_odds, away_odds)
    if not (2.50 <= do <= 6.50):
        return None
    return {"pick": dog, "model_prob": round(1.0/do, 4), "odds_at_prediction": round(do, 2),
            "strategy": "tennis_itf_dog", "source": "expert_vig", "confidence": "B",
            "notes": f"tennis ITF/CH fade dog @{do:.2f}"}




# ============================================================================
#  BASKETBALL STRATEGY SUITE (2026-06-27) — built from a 12,942-match betexplorer
#  backtest with real market probabilities. CORE FINDING: basketball is the
#  OPPOSITE of tennis — it is an efficient market that slightly UNDERPRICES
#  favorites and home teams. Fading favorites loses badly (-31%); backing them
#  (esp. the home side in tight games) wins small but consistently on huge volume.
#  All edges below are statistically robust (1000s of bets). Naming: bball_*.
# ============================================================================

def bball_coinflip_home(home, away, home_odds, away_odds, sport="", **kw) -> Optional[dict]:
    """bball_coinflip_home — the STRONGEST: back HOME when the market sees a near
    coin-flip slightly favoring home (home_prob 0.50-0.58). +8.5% ROI on 1428
    backtest games. The genuine home-court edge is underpriced in tight games."""
    if sport != "basketball":
        return None
    ph = 1.0 / home_odds if home_odds > 1 else 0
    if not (0.50 <= ph <= 0.58):
        return None
    return {"pick": home, "model_prob": round(ph, 4), "odds_at_prediction": round(home_odds, 2),
            "strategy": "bball_coinflip_home", "source": "expert_vig", "confidence": "B",
            "notes": f"bball home coinflip ph={ph:.0%} (+8.5% backtest)"}


def bball_coinflip_home_wide(home, away, home_odds, away_odds, sport="", **kw) -> Optional[dict]:
    """bball_coinflip_home_wide — wider home-edge band 0.46-0.60 for more volume.
    +6% ROI region on ~2500 games. Same home-underpricing edge, looser filter."""
    if sport != "basketball":
        return None
    ph = 1.0 / home_odds if home_odds > 1 else 0
    if not (0.46 <= ph <= 0.60):
        return None
    return {"pick": home, "model_prob": round(ph, 4), "odds_at_prediction": round(home_odds, 2),
            "strategy": "bball_coinflip_home_wide", "source": "expert_vig", "confidence": "B",
            "notes": f"bball home wide ph={ph:.0%}"}


def bball_home_edge(home, away, home_odds, away_odds, sport="", **kw) -> Optional[dict]:
    """bball_home_edge — broad home advantage: back HOME across 0.45-0.88 prob.
    +3.5% ROI on 8155 games — the largest-volume reliable basketball edge. Home
    teams win more than the market prices across nearly the whole range."""
    if sport != "basketball":
        return None
    ph = 1.0 / home_odds if home_odds > 1 else 0
    if not (0.45 <= ph <= 0.88):
        return None
    return {"pick": home, "model_prob": round(ph, 4), "odds_at_prediction": round(home_odds, 2),
            "strategy": "bball_home_edge", "source": "expert_vig", "confidence": "B",
            "notes": f"bball home edge ph={ph:.0%}"}


def bball_market_strong(home, away, home_odds, away_odds, sport="", league="", **kw) -> Optional[dict]:
    """bball_market_strong — back the strong favorite (fair prob 0.72-0.88), either
    side. +3.0% on 3889 games; +4.6% in MEN's leagues specifically (women's are
    more efficient). Strong favorites are slightly underpriced after the vig."""
    if sport != "basketball":
        return None
    ph = 1.0 / home_odds if home_odds > 1 else 0
    pa = 1.0 / away_odds if away_odds > 1 else 0
    top = max(ph, pa)
    if not (0.72 <= top <= 0.88):
        return None
    if ph >= pa:
        pick, odds, prob = home, home_odds, ph
    else:
        pick, odds, prob = away, away_odds, pa
    return {"pick": pick, "model_prob": round(prob, 4), "odds_at_prediction": round(odds, 2),
            "strategy": "bball_market_strong", "source": "expert_vig", "confidence": "A",
            "notes": f"bball strong fav {prob:.0%}"}


def bball_market_strong_men(home, away, home_odds, away_odds, sport="", league="", **kw) -> Optional[dict]:
    """bball_market_strong_men — same as market_strong but MEN's leagues only,
    where the edge is sharper (+4.6% vs +3.0%). Women's basketball pricing is
    tighter, so this restricts to the more exploitable men's markets."""
    if sport != "basketball":
        return None
    if "women" in (league or "").lower():
        return None
    ph = 1.0 / home_odds if home_odds > 1 else 0
    pa = 1.0 / away_odds if away_odds > 1 else 0
    top = max(ph, pa)
    if not (0.72 <= top <= 0.88):
        return None
    if ph >= pa:
        pick, odds, prob = home, home_odds, ph
    else:
        pick, odds, prob = away, away_odds, pa
    return {"pick": pick, "model_prob": round(prob, 4), "odds_at_prediction": round(odds, 2),
            "strategy": "bball_market_strong_men", "source": "expert_vig", "confidence": "A",
            "notes": f"bball strong fav MEN {prob:.0%}"}


def bball_extreme_fav(home, away, home_odds, away_odds, sport="", **kw) -> Optional[dict]:
    """bball_extreme_fav — near-locks: fair prob 0.88-0.96, either side. 96% win,
    +4.9% ROI on 1518 games. The safest basketball edge — extreme favorites are
    very slightly underpriced and almost always deliver."""
    if sport != "basketball":
        return None
    ph = 1.0 / home_odds if home_odds > 1 else 0
    pa = 1.0 / away_odds if away_odds > 1 else 0
    top = max(ph, pa)
    if not (0.88 <= top <= 0.96):
        return None
    if ph >= pa:
        pick, odds, prob = home, home_odds, ph
    else:
        pick, odds, prob = away, away_odds, pa
    return {"pick": pick, "model_prob": round(prob, 4), "odds_at_prediction": round(odds, 2),
            "strategy": "bball_extreme_fav", "source": "expert_vig", "confidence": "A",
            "notes": f"bball extreme fav {prob:.0%} (96% win)"}


EXPERT_STRATEGIES = {
    "vig_aware_value": vig_aware_value,
    "thick_edge_favorite": thick_edge_favorite,
    "coinflip_home_premium": coinflip_home_premium,
    "deep_seek_1": deep_seek_1,
    "deep_seek_2": deep_seek_2,
    "deep_seek_3": mid_odds_home,
    "deep_seek_4": baseball_home_specialist,
    "deep_seek_5": safe_odds_floor,
    "deep_seek_6": deep_seek_6_tt_away,
    "deep_seek_7": deep_seek_7_baseball_compound,
    "deep_seek_8": deep_seek_8_tennis_hybrid,
    "deep_seek_9": deep_seek_9_football_away,
    "deep_seek_10": deep_seek_10_hybrid_auto,
    "deep_seek_11": deep_seek_11_multifilter,
    "nova_fade_favorite": nova_fade_favorite,
    "nova_sweet_spot": nova_sweet_spot,
    "nova_underdog": nova_underdog,
    "nova_pickem": nova_pickem,
    "nova_volley_home": nova_volley_home,
    "nova_baseball_away": nova_baseball_away,
    "nova_fade_fav_v2": nova_fade_fav_v2,
    "nova_baseball_away_v2": nova_baseball_away_v2,
    "nova_fade_favorite_v1": nova_fade_favorite_v1,
    "nova_fade_fav_v2_v1": nova_fade_fav_v2_v1,
    "nova_baseball_away_v1": nova_baseball_away_v1,
    "deep_seek_9_v1": deep_seek_9_v1,
    "nova_underdog_v1": nova_underdog_v1,
    "nova_sweet_spot_v1": nova_sweet_spot_v1,
    "deep_seek_3_v1": deep_seek_3_v1,
    "deep_seek_5_v1": deep_seek_5_v1,
    "deep_seek_6_inverse_v1": deep_seek_6_inverse_v1,
    "tennis_fade_fav": tennis_fade_fav,
    "tennis_fade_fav_stable": tennis_fade_fav_stable,
    "tennis_extreme_fav_fade": tennis_extreme_fav_fade,
    "tennis_big_dog": tennis_big_dog,
    "tennis_mid_dog": tennis_mid_dog,
    "tennis_home_dog": tennis_home_dog,
    "tennis_itf_dog": tennis_itf_dog,
    "bball_coinflip_home": bball_coinflip_home,
    "bball_coinflip_home_wide": bball_coinflip_home_wide,
    "bball_home_edge": bball_home_edge,
    "bball_market_strong": bball_market_strong,
    "bball_market_strong_men": bball_market_strong_men,
    "bball_extreme_fav": bball_extreme_fav,
}
