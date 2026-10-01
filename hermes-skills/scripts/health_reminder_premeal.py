#!/usr/bin/env python3
"""Pre-check брифов «до еды» (12:00 — обед, 18:00 — ужин).

Печатает JSON с непринятыми препаратами food=before для слота и прочими
ежедневными напоминаниями. Если напоминать нечего — {"wakeAgent": false}:
модель не вызывается и сообщение не приходит.
"""
# Run under the scripts venv, not Hermes' interpreter (cron ignores the shebang).
import os as _os, sys as _sys
_VENV = "/opt/data/home/.venv"
if _os.path.realpath(_sys.prefix) != _os.path.realpath(_VENV) and _os.path.exists(_VENV + "/bin/python"):
    for _k in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"):
        _os.environ.pop(_k, None)
    _os.execv(_VENV + "/bin/python", [_VENV + "/bin/python", _os.path.abspath(__file__), *_sys.argv[1:]])

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import medication_reminder_helper as med_helper

MSK = timezone(timedelta(hours=3))
MODES = {"lunch": "lunch_premeal", "evening": "evening_premeal"}


def collect_extra_reminders(slot, now):
    """Прочие ежедневные напоминания для брифа (пока нет; точка расширения)."""
    return []


def build_premeal_result(*, slot, now, medications, extra=None):
    extra = extra or []
    if not medications and not extra:
        return {"wakeAgent": False}
    result = {
        "mode": MODES[slot],
        "date": now.strftime("%Y-%m-%d"),
        "before_meal_meds": medications,
    }
    if extra:
        result["reminders"] = extra
    return result


def collect_premeal_result(slot, now=None):
    now = now or datetime.now(MSK)
    date_str = now.strftime("%Y-%m-%d")
    meds = [
        {"name": m["name"], "dosage": m["dosage"]}
        for m in med_helper.get_pending_meds(slot, date_str, food=("before",))
    ]
    return build_premeal_result(
        slot=slot, now=now, medications=meds, extra=collect_extra_reminders(slot, now)
    )


def main(slot):
    print(json.dumps(collect_premeal_result(slot), ensure_ascii=False))
