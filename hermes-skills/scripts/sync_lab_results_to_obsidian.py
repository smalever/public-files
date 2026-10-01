#!/usr/bin/env python3
"""Sync new lab results from health.db into Obsidian 'Динамика анализов.md' and 'Фокус недели.md'."""

import argparse
import re
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERMES_HOME = Path("/opt/data")
DB_PATH = HERMES_HOME / "personal/health/health.db"
VAULT_PATH = Path("/notes")
LAB_NOTE_PATH = VAULT_PATH / "Моё здоровье/Динамика анализов.md"
FOCUS_NOTE_PATH = VAULT_PATH / "Моё здоровье/Фокус недели.md"


def _touch_updated(text):
    """Set frontmatter `updated` to now (vault schema: YYYY-MM-DDTHH:mm:ss, Moscow time)."""
    if not text.startswith("---\n"):
        return text
    end = text.find("\n---", 4)
    if end == -1:
        return text
    now = datetime.now(timezone(timedelta(hours=3))).strftime("%Y-%m-%dT%H:%M:%S")
    fm = text[:end]
    if re.search(r"^updated:.*$", fm, re.M):
        fm = re.sub(r"^updated:.*$", f"updated: {now}", fm, count=1, flags=re.M)
    else:
        fm = fm + f"\nupdated: {now}"
    return fm + text[end:]

PARAM_ALIASES = {
    "hba1c": ["гликированный", "гликозилированный", "hba1c"],
    "glucose": ["глюкоза"],
    "chol_total": ["холестерин общий", "холестерин"],
    "ldl": ["лпнп", "холестерин лпнп"],
    "hdl": ["лпвп", "холестерин лпвп"],
    "triglycerides": ["триглицериды"],
    "atherogenic": ["коэффициент атерогенности", "индекс атерогенности"],
    "alt": ["алт", "аланинаминотрансфераза"],
    "ast": ["аст", "аспартатаминотрансфераза"],
    "bilirubin": ["билирубин общий"],
    "creatinine": ["креатинин"],
    "uric_acid": ["мочевая кислота"],
    "cortisol": ["кортизол"],
    "amylase": ["амилаза", "панкреатическая амилаза"],
    "lipase": ["липаза"],
    "potassium": ["калий"],
    "sodium": ["натрий"]
}

def match_param_key(name: str) -> str:
    name_low = name.lower()
    for key, aliases in PARAM_ALIASES.items():
        for a in aliases:
            if a in name_low:
                return key
    return name_low

def sync_labs_to_obsidian(target_date: str = None):
    if not DB_PATH.exists() or not LAB_NOTE_PATH.exists():
        # Таблица динамики анализов — необязательная заметка владельца; без неё данные остаются в health.db.
        print(f"Skip: DB={DB_PATH.exists()}, note={LAB_NOTE_PATH.exists()} (заметка «Динамика анализов» не ведётся)")
        return False

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    if not target_date:
        cur.execute("SELECT MAX(date) FROM lab_results WHERE date IS NOT NULL")
        row = cur.fetchone()
        if not row or not row[0]:
            print("No lab results found in DB")
            conn.close()
            return False
        target_date = row[0]

    cur.execute("""
        SELECT parameter, value, unit, ref_min, ref_max, is_critical
        FROM lab_results
        WHERE date = ?
    """, (target_date,))
    results = cur.fetchall()
    conn.close()

    if not results:
        print(f"No results for date {target_date}")
        return False

    # Map parameters
    mapped = {}
    for r in results:
        param, val, unit, ref_min, ref_max, is_crit = r
        key = match_param_key(param)
        unit_str = f" {unit}" if unit else ""
        
        # Check if outside range
        warn = ""
        if is_crit or (ref_max is not None and val > ref_max) or (ref_min is not None and val < ref_min):
            warn = " ⚠️"
            
        formatted_val = f"**{val}{unit_str}**{warn}" if warn else f"{val}{unit_str}"
        mapped[key] = {
            "raw_val": val,
            "display": formatted_val,
            "warn": bool(warn)
        }

    # Format date label (e.g. 24.09.2026)
    try:
        dt = datetime.strptime(target_date, "%Y-%m-%d")
        col_date_label = dt.strftime("%d.%m.%Y")
    except Exception:
        col_date_label = target_date

    # Read and update 'Динамика анализов.md'
    with open(LAB_NOTE_PATH, "r", encoding="utf-8") as f:
        content = f.read()

    lines = content.splitlines()
    new_lines = []
    in_table = False
    table_header_idx = -1

    for line in lines:
        if line.strip().startswith("| **HbA1c"):
            in_table = True
        
        # Check table header
        if "| Показатель | Референс |" in line:
            # Replace placeholder column with actual date or add column
            if "Осень 2026 (план)" in line:
                line = line.replace("Осень 2026 (план)", col_date_label)
            elif col_date_label not in line:
                # Insert before last column
                parts = line.split("|")
                # parts: ['', ' Показатель ', ' Референс ', ' 04–06.05.2026 ', ... '']
                if len(parts) >= 5:
                    parts.insert(-2, f" {col_date_label} ")
                    line = "|".join(parts)
        
        # Match table rows
        if in_table and line.strip().startswith("|") and not line.strip().startswith("| :"):
            # Update row values
            for key, data in mapped.items():
                alias_matched = False
                for a in PARAM_ALIASES.get(key, [key]):
                    if f"**{a}" in line.lower() or f" {a}" in line.lower():
                        alias_matched = True
                        break
                
                if alias_matched:
                    parts = [p.strip() for p in line.split("|")[1:-1]]
                    if len(parts) >= 4:
                        # parts[0]: Parameter, parts[1]: Ref, parts[2]: May 2026, parts[3]: Autumn, parts[4]: Status
                        # If placeholder exists:
                        if "_ждём сдачи_" in parts[3]:
                            parts[3] = data["display"]
                        else:
                            parts[3] = data["display"]
                        line = f"| {' | '.join(parts)} |"
                    break
        
        if line.strip().startswith("---") and in_table:
            in_table = False

        new_lines.append(line)

    updated_note = _touch_updated("\n".join(new_lines))
    with open(LAB_NOTE_PATH, "w", encoding="utf-8") as f:
        f.write(updated_note)

    print(f"Updated {LAB_NOTE_PATH} with lab data for {col_date_label}")

    # Отметить плановый контроль в 'Фокус недели.md', если там стоит открытый пункт лаборатории
    if FOCUS_NOTE_PATH.exists():
        try:
            with open(FOCUS_NOTE_PATH, "r", encoding="utf-8") as f:
                f_content = f.read()
            if "- [ ] **Лаборатория" in f_content:
                # Mark as completed
                f_content = re.sub(
                    r"- \[ \] \*\*Лаборатория.*?(?=\n- \[ \] \*\*Питание|\n- \[ \] \*\*Препараты)",
                    f"- [x] **Лаборатория:** плановые анализы сданы ({col_date_label}), показатели внесены в [[Динамика анализов]].\n\n",
                    f_content,
                    flags=re.DOTALL
                )
                with open(FOCUS_NOTE_PATH, "w", encoding="utf-8") as f:
                    f.write(_touch_updated(f_content))
                print(f"Updated {FOCUS_NOTE_PATH} - marked lab test as completed [x].")
        except Exception as e:
            print(f"Error updating focus note: {e}")

    return True

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", type=str, default=None, help="Lab date YYYY-MM-DD")
    args = parser.parse_args()
    sync_labs_to_obsidian(args.date)
