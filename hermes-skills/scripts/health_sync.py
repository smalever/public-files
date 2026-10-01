#!/usr/bin/env python3
"""Sync module between Obsidian vault notes and health SQLite database."""

import hashlib
import json
import logging
import re
import sqlite3
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
import yaml

MSK = timezone(timedelta(hours=3))
HERMES_HOME = Path("/opt/data")
DB_PATH = HERMES_HOME / "personal/health/health.db"
VAULT_PATH = Path("/notes")

MEDS_NOTE = VAULT_PATH / "Моё здоровье/Схема препаратов.md"
TARGETS_NOTE = VAULT_PATH / "Моё здоровье/Цели и мониторинг.md"

logger = logging.getLogger("health_sync")

def _parse_frontmatter(file_path: Path):
    """Safely extracts and parses YAML frontmatter from a markdown file."""
    if not file_path.exists():
        return None, f"Файл не найден: {file_path}"
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
        if not content.startswith("---"):
            return None, "В файле отсутствует YAML frontmatter (нет открывающего '---')"
        parts = content.split("---", 2)
        if len(parts) < 3:
            return None, "YAML frontmatter не закрыт разделителем '---'"
        data = yaml.safe_load(parts[1])
        if not isinstance(data, dict):
            return None, "Frontmatter не является словарём YAML"
        return data, None
    except yaml.YAMLError as e:
        return None, f"Синтаксическая ошибка в YAML: {e}"
    except Exception as e:
        return None, f"Ошибка чтения файла: {e}"

def _timing_json(med: dict) -> str:
    """JSON-массив времён приёма: только из поля `time` схемы, без выдуманных значений."""
    t = str(med.get("time") or "").strip()
    return json.dumps([t] if t else [])


def sync_medications(conn: sqlite3.Connection):
    """
    Reads medications from Obsidian note and syncs them into SQLite medications table.
    Returns: (meds_dict, warning_message)
    """
    cur = conn.cursor()
    frontmatter, error = _parse_frontmatter(MEDS_NOTE)
    
    if error or not frontmatter or "medications" not in frontmatter:
        # Fallback to DB
        warn = error or "В заметке нет секции 'medications'"
        return None, warn

    obs_meds = frontmatter["medications"]
    if not isinstance(obs_meds, dict):
        return None, "Секция 'medications' имеет неверный формат"

    # Build flat list of all active medications from Obsidian
    current_items = []
    
    # 1. morning
    for m in obs_meds.get("morning", []):
        current_items.append({
            "name": m.get("name", "").strip(),
            "dosage": m.get("dosage", "").strip(),
            "timing": _timing_json(m),
            "when_text": "morning",
            "note": m.get("note", "утром"),
            "food": m.get("food"),
        })

    # 2. lunch
    for m in obs_meds.get("lunch", []):
        current_items.append({
            "name": m.get("name", "").strip(),
            "dosage": m.get("dosage", "").strip(),
            "timing": _timing_json(m),
            "when_text": "lunch",
            "note": m.get("note", "днём во время еды"),
            "food": m.get("food"),
        })

    # 3. wednesday_morning
    for m in obs_meds.get("wednesday_morning", []):
        current_items.append({
            "name": m.get("name", "").strip(),
            "dosage": m.get("dosage", "").strip(),
            "timing": _timing_json(m),
            "when_text": "wednesday_morning",
            "note": m.get("note", "по средам утром"),
            "food": m.get("food"),
        })

    # 4. evening
    for m in obs_meds.get("evening", []):
        current_items.append({
            "name": m.get("name", "").strip(),
            "dosage": m.get("dosage", "").strip(),
            "timing": _timing_json(m),
            "when_text": "evening",
            "note": m.get("note", "вечером"),
            "food": m.get("food"),
        })

    # Handle duplicates across morning & evening (e.g. одна омега-3 утром и вечером → morning_evening)
    merged_items = {}
    for item in current_items:
        name = item["name"]
        if not name:
            continue
        key = name.lower()
        if key in merged_items:
            existing = merged_items[key]
            # If present both morning and evening -> morning_evening
            times = set(json.loads(existing["timing"]) + json.loads(item["timing"]))
            existing["timing"] = json.dumps(sorted(list(times)))
            if ("morning" in existing["when_text"] and "evening" in item["when_text"]) or \
               ("evening" in existing["when_text"] and "morning" in item["when_text"]):
                existing["when_text"] = "morning_evening"
                existing["note"] = "утром и вечером"
        else:
            merged_items[key] = item

    # Признак приёма относительно еды (before/with/after/any).
    cols = {r[1] for r in cur.execute("PRAGMA table_info(medications)")}
    if "food" not in cols:
        cur.execute("ALTER TABLE medications ADD COLUMN food TEXT")
    for item in merged_items.values():
        f = str(item.get("food") or "").strip().casefold()
        item["food"] = f if f in ("before", "with", "after", "any") else None

    # Sync to SQLite
    active_names = set(merged_items.keys())
    now_date = datetime.now(MSK).strftime("%Y-%m-%d")

    # Read existing medications from DB
    cur.execute("SELECT id, name, dosage, timing, when_text, note, active FROM medications")
    db_rows = cur.fetchall()
    db_map = {r[1].strip().lower(): r for r in db_rows}

    # Update or insert active medications
    for idx, (key, item) in enumerate(merged_items.items()):
        if key in db_map:
            db_row = db_map[key]
            db_id = db_row[0]
            cur.execute("""
                UPDATE medications 
                SET dosage = ?, timing = ?, when_text = ?, note = ?, food = ?, active = 1
                WHERE id = ?
            """, (item["dosage"], item["timing"], item["when_text"], item["note"], item["food"], db_id))
        else:
            new_id = f"med_obs_{int(time.time()*1000)}_{idx}"
            cur.execute("""
                INSERT INTO medications (id, name, dosage, frequency, timing, when_text, note, food, active, added)
                VALUES (?, ?, ?, '1 раз в день', ?, ?, ?, ?, 1, ?)
            """, (new_id, item["name"], item["dosage"], item["timing"], item["when_text"], item["note"], item["food"], now_date))

    # Deactivate medications removed from Obsidian
    for key, db_row in db_map.items():
        if key not in active_names and db_row[6] == 1: # if was active
            cur.execute("UPDATE medications SET active = 0 WHERE id = ?", (db_row[0],))

    conn.commit()
    return obs_meds, None

def sync_targets(conn: sqlite3.Connection):
    """
    Reads profile nutrition targets from Obsidian note and syncs to SQLite profile table.
    Returns: (targets_dict, warning_message)
    """
    cur = conn.cursor()
    frontmatter, error = _parse_frontmatter(TARGETS_NOTE)

    if error or not frontmatter or "targets" not in frontmatter:
        return None, error or "В заметке нет секции 'targets'"

    raw_targets = frontmatter["targets"]
    if not isinstance(raw_targets, dict):
        return None, "Секция 'targets' имеет неверный формат"

    targets = {
        "kcal": int(raw_targets.get("daily_target_kcal") or 0),
        "protein": float(raw_targets.get("target_protein") or 0),
        "fat": float(raw_targets.get("target_fat") or 0),
        "carbs": float(raw_targets.get("target_carbs") or 0)
    }

    # Update profile in SQLite
    cur.execute("""
        UPDATE profile 
        SET daily_target_kcal = ?, target_protein = ?, target_fat = ?, target_carbs = ?
        WHERE id = 1
    """, (targets["kcal"], targets["protein"], targets["fat"], targets["carbs"]))
    conn.commit()

    return targets, None

if __name__ == "__main__":
    conn = sqlite3.connect(DB_PATH)
    meds, warn_m = sync_medications(conn)
    targets, warn_t = sync_targets(conn)
    conn.close()
    if warn_m:
        print(f"Warning (medications): {warn_m}")
    if warn_t:
        print(f"Warning (targets): {warn_t}")
    print("health_sync completed successfully.")
