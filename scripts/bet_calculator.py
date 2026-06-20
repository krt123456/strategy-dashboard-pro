#!/usr/bin/env python3
"""حاسبة الرهان المباشرة — النموذج + سعر 1xBet الحقيقي.

العملية:
1. النموذج يقول: الفريق X فوزه 76%
2. أنت تتحقق من سعر 1xBet الحقيقي
3. النظام يحسب فوراً: EV, Kelly, GO/NO-GO

Usage:
  python bet_calculator.py                    # عرض كل التوقعات
  python bet_calculator.py --pick 1 --odds 1.66  # احسب برهان 1 بالسعر الحقيقي
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR / "scripts"))

from betting_math import ev_percent, kelly_stake, implied_prob


def load_picks(target_date: str = None):
    """Load the latest reasonable picks."""
    reports = PROJECT_DIR / "reports"
    if target_date:
        path = reports / f"reasonable_picks_{target_date}.csv"
        if path.exists():
            import pandas as pd
            return pd.read_csv(path).to_dict("records")
    # Find latest
    files = sorted(reports.glob("reasonable_picks_*.csv"), reverse=True)
    if files:
        import pandas as pd
        return pd.read_csv(files[0]).to_dict("records")
    return []


def show_picks(picks):
    print(f"\n{'='*70}")
    print(f"  🎯 توقعات النموذج (الاحتمالات مختبرة)")
    print(f"{'='*70}")
    print(f"\n  {'#':<3s} {'المباراة':<35s} {'الاختيار':<15s} {'الاحتمال':>8s}")
    print(f"  {'─'*67}")
    for i, p in enumerate(picks):
        match = f"{p.get('home','')[:15]} vs {p.get('away','')[:15]}"
        print(f"  {i+1:<3d} {match:<35s} {p.get('pick','')[:13]:<15s} {p.get('model_prob',0):>7.0%}")
    print(f"\n  استعمل: python bet_calculator.py --pick N --odds X.XX")


def calculate(pick: dict, real_odds: float, bankroll: float = 100.0):
    model_prob = float(pick.get("model_prob", 0))
    home = pick.get("home", "?")
    away = pick.get("away", "?")
    selection = pick.get("pick", "?")
    be_odds = float(pick.get("market_odds", 0))

    ev = ev_percent(model_prob, real_odds)
    implied = implied_prob(real_odds)
    ks = kelly_stake(model_prob, real_odds, bankroll, 0.25, 0.05)
    profit = (real_odds - 1) * bankroll

    print(f"\n{'='*70}")
    print(f"  💰 حاسبة الرهان")
    print(f"{'='*70}")
    print(f"\n  🆚 {home} vs {away}")
    print(f"  ✅ الاختيار: {selection}")
    print(f"\n  📊 احتمال النموذج:     {model_prob:.1%}")
    print(f"  💰 سعر 1xBet الحقيقي:  {real_odds:.2f}")
    print(f"  📈 الاحتمال الضمني:    {implied:.1%}")
    print(f"  📐 الفارق (edge):      {model_prob - implied:+.1%}")

    print(f"\n  {'─'*50}")
    print(f"  💵 لو راهنت ${bankroll:.0f}:")
    print(f"     الربح = ${profit:.0f}")
    print(f"     الخسارة = ${bankroll:.0f}")

    print(f"\n  📊 القيمة المتوقعة (EV):")
    ev_dollar = ev / 100 * bankroll
    print(f"     EV = {ev:+.1f}% = ${ev_dollar:+.0f} لكل ${bankroll:.0f}")

    print(f"\n  🎯 حجم الرهان (Kelly):")
    if ks > 0:
        print(f"     Kelly = ${ks:.2f} ({ks/bankroll*100:.1f}%)")
        print(f"     Quarter-Kelly (آمن) = ${ks:.2f}")
    else:
        print(f"     ❌ Kelly = $0 — لا تراهن")

    print(f"\n  {'─'*50}")
    if ev > 5:
        print(f"  ✅✅ GO — رهان ممتاز! EV عالية")
    elif ev > 0:
        print(f"  ✅ GO — رهان مربح")
    elif ev > -5:
        print(f"  ⚠️ NO-GO — EV سلبية قليلاً. انتظر سعراً أفضل")
    else:
        print(f"  ❌ NO-GO — لا تراهن. السعر سيء")

    if be_odds > 0:
        print(f"\n  ℹ️ مقارنة: betexplorer كان يقول {be_odds:.2f}")
        print(f"     الفارق عن 1xBet: {(be_odds-real_odds)/real_odds*100:+.1f}%")
    print(f"{'='*70}")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="حاسبة الرهان بالسعر الحقيقي")
    parser.add_argument("--pick", type=int, default=None, help="رقم المباراة من القائمة")
    parser.add_argument("--odds", type=float, default=None, help="السعر الحقيقي من 1xBet")
    parser.add_argument("--date", type=str, default=None)
    parser.add_argument("--bankroll", type=float, default=100.0)
    args = parser.parse_args()

    picks = load_picks(args.date)
    if not picks:
        print("لا توجد توقعات. شغّل unified_predictor.py أولاً.")
        return

    if args.pick is None:
        show_picks(picks)
        print(f"\n  مثال: python bet_calculator.py --pick 1 --odds 1.66")
        return

    idx = args.pick - 1
    if idx < 0 or idx >= len(picks):
        print(f"رقم غير صحيح. اختر من 1 إلى {len(picks)}")
        return

    if args.odds is None:
        pick = picks[idx]
        print(f"\n  المباراة: {pick.get('home','')} vs {pick.get('away','')}")
        print(f"  الاختيار: {pick.get('pick','')}")
        print(f"  احتمال النموذج: {float(pick.get('model_prob',0)):.0%}")
        print(f"\n  أدخل سعر 1xBet: python bet_calculator.py --pick {args.pick} --odds X.XX")
        return

    calculate(picks[idx], args.odds, args.bankroll)


if __name__ == "__main__":
    main()
