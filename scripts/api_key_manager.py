#!/usr/bin/env python3
"""نظام إدارة مفاتيح API — دوران تلقائي بين مفاتيح متعددة.

عندما ينفد حد مفتاح، ينتقل تلقائياً للتالي.
يدعم: The Odds API, football-data.org, وأي API بمفتاح.

أضف مفاتيحك:
  python api_key_manager.py --add the_odds_api KEY1
  python api_key_manager.py --add the_odds_api KEY2
  python api_key_manager.py --add football_data TOKEN1

الاستعمال في الكود:
  from api_key_manager import get_key
  key = get_key("the_odds_api")  # يعيد مفتاحاً متاحاً
"""
from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

PROJECT_DIR = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_DIR / "data" / "betting_journal.db"


def init_keys_table():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS api_keys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            service TEXT NOT NULL,
            key_value TEXT NOT NULL,
            email TEXT,
            monthly_limit INTEGER DEFAULT 500,
            used_this_month INTEGER DEFAULT 0,
            last_reset TEXT,
            enabled INTEGER DEFAULT 1,
            created_at TEXT,
            notes TEXT
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS api_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            service TEXT NOT NULL,
            key_id INTEGER NOT NULL,
            endpoint TEXT,
            status_code INTEGER,
            used_at TEXT NOT NULL,
            FOREIGN KEY (key_id) REFERENCES api_keys(id)
        )
    """)
    conn.commit()
    conn.close()


def add_key(service: str, key_value: str, email: str = None,
            monthly_limit: int = 500, notes: str = None):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    exists = c.execute("SELECT id FROM api_keys WHERE key_value=?", (key_value,)).fetchone()
    if exists:
        print(f"  المفتاح موجود بالفعل (id={exists[0]})")
        conn.close()
        return exists[0]
    c.execute("""
        INSERT INTO api_keys (service, key_value, email, monthly_limit,
                             used_this_month, last_reset, enabled, created_at, notes)
        VALUES (?, ?, ?, ?, 0, ?, 1, ?, ?)
    """, (service, key_value, email, monthly_limit,
          datetime.now().strftime("%Y-%m"),
          datetime.now().isoformat(), notes))
    key_id = c.lastrowid
    conn.commit()
    conn.close()
    print(f"  ✓ {service}: مفتاح مضاف (id={key_id})")
    return key_id


def get_key(service: str) -> Optional[str]:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    now = datetime.now()
    current_month = now.strftime("%Y-%m")

    c.execute("UPDATE api_keys SET used_this_month=0, last_reset=? WHERE last_reset!=?",
              (current_month, current_month))

    row = c.execute("""
        SELECT id, key_value, monthly_limit, used_this_month
        FROM api_keys
        WHERE service=? AND enabled=1 AND used_this_month < monthly_limit
        ORDER BY used_this_month ASC
        LIMIT 1
    """, (service,)).fetchone()

    if not row:
        conn.close()
        return None

    key_id, key_value, limit, used = row
    c.execute("UPDATE api_keys SET used_this_month=used_this_month+1 WHERE id=?", (key_id,))
    c.execute("""
        INSERT INTO api_usage (service, key_id, used_at)
        VALUES (?, ?, ?)
    """, (service, key_id, now.isoformat()))
    conn.commit()
    conn.close()
    return key_value


def report_usage():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    rows = c.execute("""
        SELECT service, email, monthly_limit, used_this_month,
               enabled, COUNT(ak.id) as key_count
        FROM api_keys ak
        GROUP BY service
    """).fetchall()

    print("\n📊 استخدام مفاتيح API:")
    print(f"{'الخدمة':<20s} {'المفاتيح':>8s} {'الحد':>6s} {'المستخدم':>9s} {'المتبقي':>8s} {'الحالة':>6s}")
    print("─" * 60)
    for service, email, limit, used, enabled, count in rows:
        remaining = limit - used
        status = "✓ نشط" if enabled else "✗ معطّل"
        print(f"{service:<20s} {count:>8d} {limit:>6d} {used:>9d} {remaining:>8d} {status:>6s}")
    conn.close()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--add", nargs=2, metavar=("SERVICE", "KEY"), help="أضف مفتاح")
    parser.add_argument("--email", type=str, default=None)
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--report", action="store_true", help="تقرير الاستخدام")
    args = parser.parse_args()

    init_keys_table()

    if args.add:
        service, key = args.add
        add_key(service, key, args.email, args.limit)
    
    if args.report or not args.add:
        report_usage()
        print("\n💡 لإضافة مفتاح:")
        print("  python api_key_manager.py --add the_odds_api YOUR_KEY --email your@email.com")
        print("  python api_key_manager.py --add football_data YOUR_TOKEN --limit 10")
