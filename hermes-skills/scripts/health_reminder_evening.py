#!/usr/bin/env python3
"""🌙 20:00 «Итоги дня»: какие препараты за сегодня ещё не отмечены.

Владелец sync_medications(). Если всё отмечено — {"wakeAgent": false}.
"""

import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Shared health modules live next to this script.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import health_sync
import medication_reminder_helper as med_helper

MSK = timezone(timedelta(hours=3))
DB = Path("/opt/data/personal/health/health.db")


def build_evening_result(*, now, pending, warnings=None):
    """pending: {slot: [{name, dosage, food}]} — только непринятые."""
    if not any(pending.values()):
        return {"wakeAgent": False}
    result = {
        "mode": "day_summary",
        "date": now.strftime("%Y-%m-%d"),
        "pending": {s: v for s, v in pending.items() if v},
    }
    if warnings:
        result["sync_warnings"] = warnings
    return result


def collect_evening_result(now=None):
    """Synchronize the medication scheme and collect today's unmarked medications."""
    now = now or datetime.now(MSK)
    date_str = now.strftime("%Y-%m-%d")
    warnings = []

    with sqlite3.connect(DB) as conn:
        _, warning = health_sync.sync_medications(conn)
        if warning:
            warnings.append(warning)

    return build_evening_result(
        now=now, pending=med_helper.get_day_pending(date_str), warnings=warnings
    )


def serialize_result(result):
    """Serialize cron control/data JSON as one complete stdout line."""
    return json.dumps(result, ensure_ascii=False)


def main():
    print(serialize_result(collect_evening_result()))


if __name__ == "__main__":
    main()
