#!/usr/bin/env python3
"""Generate daily performance report for the cloud agent."""
from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR / "scripts"))

from betting_journal import performance_report

def main():
    print(f"# Daily Report — {datetime.now().strftime('%Y-%m-%d')}")
    print()

    report = performance_report(days=30)

    if not report["sources"] and not report["strategies"]:
        print("📊 No resolved predictions yet. Agent is collecting data.")
        print()
        print("The system is running and collecting predictions daily.")
        print("Results will appear here once matches are resolved.")
        return

    print("## Sources Performance")
    if report["sources"]:
        print(f"| Source | Bets | Wins | Accuracy | Profit | ROI |")
        print(f"|--------|------|------|----------|--------|-----|")
        for src, stats in sorted(report["sources"].items(), key=lambda x: -x[1].get("profit", 0)):
            print(f"| {src} | {stats['total']} | {stats['wins']} | {stats['accuracy']:.1f}% | ${stats['profit']:.2f} | {stats['avg_roi']:.1f}% |")

    print()
    print("## Strategies Performance")
    if report["strategies"]:
        print(f"| Strategy | Bets | Wins | Accuracy | Profit |")
        print(f"|----------|------|------|----------|--------|")
        for strat, stats in sorted(report["strategies"].items(), key=lambda x: -x[1].get("profit", 0)):
            print(f"| {strat} | {stats['total']} | {stats['wins']} | {stats['accuracy']:.1f}% | ${stats['profit']:.2f} |")

    print()
    print("## Sports Performance")
    if report["sports"]:
        print(f"| Sport | Bets | Wins | Accuracy | Profit |")
        print(f"|-------|------|------|----------|--------|")
        for sport, stats in sorted(report["sports"].items(), key=lambda x: -x[1].get("profit", 0)):
            print(f"| {sport} | {stats['total']} | {stats['wins']} | {stats['accuracy']:.1f}% | ${stats['profit']:.2f} |")

if __name__ == "__main__":
    main()
