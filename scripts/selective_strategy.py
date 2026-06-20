#!/usr/bin/env python3
"""Evaluate a selective (non-draw, high-confidence) strategy on finished matches."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    import pandas as pd  # type: ignore
except Exception:
    print("Missing dependency: pandas. Install with: pip install pandas", file=sys.stderr)
    raise


def implied_probs(h: float, d: float, a: float):
    p_h = 1.0 / h
    p_d = 1.0 / d
    p_a = 1.0 / a
    s = p_h + p_d + p_a
    return p_h / s, p_d / s, p_a / s


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True, help="Path to football-data CSV")
    ap.add_argument("--out", default="reports/selective_strategy.md")
    ap.add_argument("--min-acc", type=float, default=0.90)
    ap.add_argument("--thresholds", default="0.45,0.50,0.55,0.60,0.65,0.70,0.75")
    args = ap.parse_args()

    path = Path(args.csv)
    if not path.exists():
        print(f"missing: {path}", file=sys.stderr)
        return 1

    df = pd.read_csv(path, encoding="utf-8-sig")
    # use Avg odds if available, else B365
    if all(c in df.columns for c in ["AvgH", "AvgD", "AvgA"]):
        odds_cols = ["AvgH", "AvgD", "AvgA"]
    else:
        odds_cols = ["B365H", "B365D", "B365A"]

    df = df.dropna(subset=["FTHG", "FTAG"] + odds_cols).copy()
    if df.empty:
        print("No finished matches with odds.", file=sys.stderr)
        return 1

    actual = df.apply(lambda r: "H" if r.FTHG > r.FTAG else ("A" if r.FTHG < r.FTAG else "D"), axis=1)

    p_h, p_d, p_a = implied_probs(df[odds_cols[0]], df[odds_cols[1]], df[odds_cols[2]])
    probs = pd.DataFrame({"H": p_h, "D": p_d, "A": p_a})
    best = probs.idxmax(axis=1)
    conf = probs.max(axis=1)

    thresholds = [float(x) for x in args.thresholds.split(",") if x]

    rows = []
    best90 = None
    for t in thresholds:
        idx = (best != "D") & (conf >= t)
        if idx.sum() == 0:
            rows.append((t, 0.0, None))
            continue
        acc = (best[idx].values == actual[idx].values).mean()
        cov = idx.mean()
        rows.append((t, cov, acc))
        if best90 is None and acc >= args.min_acc:
            best90 = (t, cov, acc, int(idx.sum()))

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    report = []
    report.append("# تقييم استراتيجية انتقائية (استبعاد التعادل + عتبة ثقة)")
    report.append(f"- المصدر: {path.name}")
    report.append(f"- عدد المباريات المكتملة: {len(df)}")
    report.append(f"- مقياس الدقة هنا هو Top-1 Accuracy فقط (فوز/تعادل/خسارة).")
    report.append("")
    report.append("## نتائج حسب العتبة")
    report.append("| العتبة | التغطية | الدقة |")
    report.append("|---:|---:|---:|")
    for t, cov, acc in rows:
        if acc is None:
            report.append(f"| {t:.2f} | 0% | n/a |")
        else:
            report.append(f"| {t:.2f} | {cov*100:.1f}% | {acc*100:.1f}% |")

    report.append("")
    if best90:
        t, cov, acc, n = best90
        report.append(
            f"أفضل عتبة تحقق ≥ {args.min_acc*100:.0f}%: **{t:.2f}** (التغطية {cov*100:.1f}%, عدد المباريات {n}, الدقة {acc*100:.1f}%)."
        )
    else:
        report.append(f"لا توجد عتبة وصلت إلى ≥ {args.min_acc*100:.0f}%.")

    out_path.write_text("\n".join(report), encoding="utf-8")
    print(f"saved: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
