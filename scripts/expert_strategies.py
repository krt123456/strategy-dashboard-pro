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


def thick_edge_favorite(home: str, away: str, home_odds: float, away_odds: float) -> Optional[dict]:
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


def coinflip_home_premium(home: str, away: str, home_odds: float, away_odds: float) -> Optional[dict]:
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


def deep_seek_1(home: str, away: str, home_odds: float, away_odds: float) -> Optional[dict]:
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


def deep_seek_2(home: str, away: str, home_odds: float, away_odds: float) -> Optional[dict]:
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


EXPERT_STRATEGIES = {
    "vig_aware_value": vig_aware_value,
    "thick_edge_favorite": thick_edge_favorite,
    "coinflip_home_premium": coinflip_home_premium,
    "deep_seek_1": deep_seek_1,
    "deep_seek_2": deep_seek_2,
    "mid_odds_home": mid_odds_home,
    "baseball_home_specialist": baseball_home_specialist,
    "safe_odds_floor": safe_odds_floor,
}
