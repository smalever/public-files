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

    # One row per name + atomic slot; names are not unique in this schema.
    defaults = {"morning": "утром", "lunch": "днём во время еды",
                "wednesday_morning": "по средам утром", "evening": "вечером"}
    # Validate the entire source before any SQL mutation, including ignored PRN entries.
    for slot, entries in obs_meds.items():
        if slot not in (*defaults, "as_needed") or not isinstance(entries, list):
            return None, f"Некорректный слот medications.{slot}: ожидается список"
        seen = set()
        for m in entries:
            if not isinstance(m, dict) or not isinstance(m.get("name"), str) or not m["name"].strip():
                return None, f"Некорректный препарат в medications.{slot}"
            key = m["name"].strip().casefold()
            if key in seen:
                return None, f"Повтор препарата в medications.{slot}: {m['name']}"
            seen.add(key)
            for field in ("dosage", "time", "note", "condition", "food"):
                if field in m and not isinstance(m[field], str):
                    return None, f"Некорректное поле {slot}.{field}: ожидается строка"
            if m.get("food", "").strip().casefold() not in ("", "before", "with", "after", "any"):
                return None, f"Некорректное значение food в medications.{slot}"
    items = []
    for slot, default_note in defaults.items():
        for m in obs_meds.get(slot, []):
            items.append(dict(name=m.get("name", "").strip(),
                dosage=m.get("dosage", "").strip(), timing=_timing_json(m),
                slot=slot, note=m.get("note", default_note), food=m.get("food")))
    cols = {r[1] for r in cur.execute("PRAGMA table_info(medications)")}
    if "food" not in cols:
        raise sqlite3.OperationalError('Missing medications.food schema; run health_sync.py migrate explicitly before synchronization.')
    rows = cur.execute("SELECT id,name,when_text,active FROM medications ORDER BY id").fetchall()
    used, assignments = set(), {}
    # First reserve exact matches, then retain a historical/combined ID on one row.
    for exact in (True, False):
        for i, item in enumerate(items):
            if i in assignments:
                continue
            for row in rows:
                if (row[0] not in used and row[1].strip().casefold() == item["name"].casefold()
                        and (not exact or row[2] == item["slot"])):
                    assignments[i] = row[0]
                    used.add(row[0])
                    break
    now_date = datetime.now(MSK).strftime("%Y-%m-%d")
    cur.execute("SAVEPOINT medication_sync")
    try:
        for i, item in enumerate(items):
            food = str(item["food"] or "").strip().casefold() or None
            values = (item["name"], item["dosage"], item["timing"], item["slot"], item["note"], food)
            if i in assignments:
                cur.execute("""UPDATE medications SET name=?,dosage=?,timing=?,when_text=?,note=?,food=?,active=1
                    WHERE id=?""", values + (assignments[i],))
            else:
                new_id = f"med_obs_{time.time_ns()}_{i}"
                cur.execute("""INSERT INTO medications (name,dosage,timing,when_text,note,food,id,frequency,active,added)
                    VALUES (?,?,?,?,?,?,?,'1 раз в день',1,?)""", values + (new_id, now_date))
        for row in rows:
            if row[0] not in used and row[3]:
                cur.execute("UPDATE medications SET active=0 WHERE id=?", (row[0],))
        cur.execute("RELEASE SAVEPOINT medication_sync")
    except Exception:
        cur.execute("ROLLBACK TO SAVEPOINT medication_sync")
        cur.execute("RELEASE SAVEPOINT medication_sync")
        raise
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
    import argparse
    parser = argparse.ArgumentParser(description='Sync existing health database; schema migration is explicit.')
    parser.add_argument('command', nargs='?', choices=['sync', 'migrate'], default='sync')
    args = parser.parse_args()
    conn = sqlite3.connect(Path(DB_PATH).resolve().as_uri() + '?mode=rw', uri=True)
    if args.command == 'migrate':
        try:
            cols = {r[1] for r in conn.execute('PRAGMA table_info(medications)')}
            if not cols:
                raise sqlite3.OperationalError('Missing medications table; initialize the health database separately.')
            if 'food' not in cols:
                with conn:
                    conn.execute('ALTER TABLE medications ADD COLUMN food TEXT')
        finally:
            conn.close()
        raise SystemExit(0)
    meds, warn_m = sync_medications(conn)
    targets, warn_t = sync_targets(conn)
    conn.close()
    if warn_m:
        print(f"Warning (medications): {warn_m}")
    if warn_t:
        print(f"Warning (targets): {warn_t}")
    print("health_sync completed successfully.")
