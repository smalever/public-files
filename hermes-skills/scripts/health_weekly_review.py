#!/usr/bin/env python3
"""Weekly health review data fetcher. Called by Sunday cron job."""
# Run under the scripts venv, not Hermes' interpreter (cron ignores the shebang).
import os as _os, sys as _sys
_VENV = "/opt/data/home/.venv"
if _os.path.realpath(_sys.prefix) != _os.path.realpath(_VENV) and _os.path.exists(_VENV + "/bin/python"):
    for _k in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"):
        _os.environ.pop(_k, None)
    _os.execv(_VENV + "/bin/python", [_VENV + "/bin/python", _os.path.abspath(__file__), *_sys.argv[1:]])

import json
import re
import os
import sqlite3
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Add scripts directory for health_sync
sys.path.insert(0, str(Path(__file__).resolve().parent))
import health_sync
import plot_health_review
import medication_reminder_helper as med_helper
from health_monitoring import NOTES_DIR, fmt_range, load_monitoring

MSK = timezone(timedelta(hours=3))
HERMES_HOME = Path(os.environ.get("HERMES_HOME", Path(__file__).resolve().parents[1]))
DB = HERMES_HOME / "personal/health/health.db"
NUT_DB = HERMES_HOME / "personal/nutrition/nutrition.db"

now = datetime.now(MSK)
# Interval: last 7 full days (from now-7 to now-1)
start_date = (now - timedelta(days=7)).strftime("%Y-%m-%d")
end_date = (now - timedelta(days=1)).strftime("%Y-%m-%d")

conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
cur = conn.cursor()
cur.execute(f"ATTACH DATABASE '{NUT_DB}' AS nut")

# 1. Sync & targets
warnings = []
_, warn_m = health_sync.sync_medications(conn)
if warn_m:
    warnings.append(warn_m)

obs_targets, warn_t = health_sync.sync_targets(conn)
if warn_t:
    warnings.append(warn_t)

monitoring = load_monitoring()

if obs_targets and obs_targets.get("kcal"):
    targets = obs_targets
else:
    cur.execute("SELECT daily_target_kcal, target_protein, target_fat, target_carbs FROM profile WHERE id=1")
    p_row = cur.fetchone()
    targets = {
        "kcal": p_row["daily_target_kcal"] or 0,
        "protein": p_row["target_protein"] or 0.0,
        "fat": p_row["target_fat"] or 0.0,
        "carbs": p_row["target_carbs"] or 0.0
    } if p_row else {"kcal": 0, "protein": 0.0, "fat": 0.0, "carbs": 0.0}
# Цели КБЖУ не заданы (новый владелец) — сравнение с целью не строим.
targets_set = bool(targets.get("kcal"))

# 2. Weekly Nutrition Aggregates
cur.execute("""
    SELECT 
        date,
        SUM(total_kcal) as day_kcal,
        SUM(total_protein) as day_p,
        SUM(total_fat) as day_f,
        SUM(total_carbs) as day_c
    FROM nut.nutrition
    WHERE date BETWEEN ? AND ?
    GROUP BY date
""", (start_date, end_date))
nut_days = cur.fetchall()

days_logged = len(nut_days)
if days_logged > 0:
    avg_kcal = round(sum(r["day_kcal"] or 0 for r in nut_days) / days_logged)
    avg_protein = round(sum(r["day_p"] or 0 for r in nut_days) / days_logged, 1)
    avg_fat = round(sum(r["day_f"] or 0 for r in nut_days) / days_logged, 1)
    avg_carbs = round(sum(r["day_c"] or 0 for r in nut_days) / days_logged, 1)
else:
    avg_kcal, avg_protein, avg_fat, avg_carbs = 0, 0, 0, 0

nutrition_summary = {
    "period": f"{start_date} ... {end_date}",
    "days_logged": days_logged,
    "avg_daily": {
        "kcal": avg_kcal,
        "protein": avg_protein,
        "fat": avg_fat,
        "carbs": avg_carbs
    },
    "targets": targets if targets_set else None,
    "diff": {
        "kcal": avg_kcal - targets["kcal"],
        "protein": round(avg_protein - targets["protein"], 1),
        "fat": round(avg_fat - targets["fat"], 1),
        "carbs": round(avg_carbs - targets["carbs"], 1)
    } if targets_set else {"kcal": 0, "protein": 0.0, "fat": 0.0, "carbs": 0.0}
}

# Top fat and protein food sources over the week
cur.execute("""
    SELECT 
        i.product,
        ROUND(SUM(i.fat), 1) as total_fat,
        ROUND(SUM(i.protein), 1) as total_protein,
        ROUND(SUM(i.kcal)) as total_kcal
    FROM nut.nutrition_items i
    JOIN nut.nutrition n ON i.nutrition_id = n.id
    WHERE n.date BETWEEN ? AND ?
    GROUP BY i.product
    ORDER BY total_fat DESC
    LIMIT 4
""", (start_date, end_date))
top_fat_items = [dict(r) for r in cur.fetchall()]

cur.execute("""
    SELECT 
        i.product,
        ROUND(SUM(i.protein), 1) as total_protein,
        ROUND(SUM(i.kcal)) as total_kcal
    FROM nut.nutrition_items i
    JOIN nut.nutrition n ON i.nutrition_id = n.id
    WHERE n.date BETWEEN ? AND ?
    GROUP BY i.product
    ORDER BY total_protein DESC
    LIMIT 4
""", (start_date, end_date))
top_protein_items = [dict(r) for r in cur.fetchall()]

# 3. Weekly Health Metrics
# Glucose
cur.execute("SELECT value FROM metrics WHERE type = 'glucose' AND date BETWEEN ? AND ? AND value > 0", (start_date, end_date))
gluc_vals = [r["value"] for r in cur.fetchall()]
glucose_summary = {}
if gluc_vals:
    glucose_summary = {
        "avg": round(sum(gluc_vals) / len(gluc_vals), 1),
        "min": round(min(gluc_vals), 1),
        "max": round(max(gluc_vals), 1),
        "count": len(gluc_vals)
    }
    g_rng = monitoring["glucose_range"]
    if g_rng:
        glucose_summary["target_range"] = g_rng
        glucose_summary["in_target_range"] = sum(1 for v in gluc_vals if g_rng[0] <= v <= g_rng[1])

# Pressure
cur.execute("SELECT systolic, diastolic FROM metrics WHERE type IN ('bp', 'pressure') AND date BETWEEN ? AND ? AND systolic IS NOT NULL", (start_date, end_date))
bp_rows = cur.fetchall()
bp_summary = {}
if bp_rows:
    sys_list = [r["systolic"] for r in bp_rows]
    dia_list = [r["diastolic"] for r in bp_rows]
    bp_summary = {
        "avg_systolic": round(sum(sys_list) / len(sys_list)),
        "avg_diastolic": round(sum(dia_list) / len(dia_list)),
        "count": len(bp_rows)
    }
    sys_rng = monitoring["bp_systolic_range"]
    if sys_rng:
        bp_summary["target_systolic"] = sys_rng
    spike = monitoring["bp_systolic_spike"] or (sys_rng[1] if sys_rng else None)
    if spike:
        bp_summary["spike_threshold"] = spike
        bp_summary["spikes"] = sum(1 for s in sys_list if s > spike)

# Weight
cur.execute("SELECT date, value FROM metrics WHERE type = 'weight' AND date BETWEEN ? AND ? AND value > 0 ORDER BY date ASC, ts ASC", (start_date, end_date))
weight_rows = cur.fetchall()
weight_summary = {}
if weight_rows:
    first_w = weight_rows[0]["value"]
    last_w = weight_rows[-1]["value"]
    weight_summary = {
        "start_of_week": first_w,
        "latest": last_w,
        "delta": round(last_w - first_w, 2),
        "count": len(weight_rows)
    }
    if monitoring["weight_target"]:
        weight_summary["target"] = monitoring["weight_target"]

# 4. Lab Tests Check
cur.execute("SELECT MAX(date) FROM lab_results WHERE date IS NOT NULL")
max_lab_row = cur.fetchone()
last_lab_date_str = max_lab_row[0] if max_lab_row else None
lab_summary = {}
if last_lab_date_str:
    last_lab_date = datetime.strptime(last_lab_date_str, "%Y-%m-%d").date()
    days_since_lab = (now.date() - last_lab_date).days
    months_since_lab = round(days_since_lab / 30.4, 1)
    is_due = days_since_lab >= monitoring["lab_interval_days"]

    lab_summary = {
        "last_date": last_lab_date_str,
        "days_ago": days_since_lab,
        "months_ago": months_since_lab,
        "is_due_for_control": is_due,
        "recommended_panel": monitoring["lab_panel"] if is_due else []
    }

conn.close()

# 5. Weekly Medication Adherence
meds_adherence = {}
try:
    meds_adherence = med_helper.calculate_weekly_adherence(start_date, end_date)
except Exception as e:
    warnings.append(f"Ошибка расчёта регулярности приёма лекарств: {e}")

# Auto-update Obsidian note: Фокус недели.md
try:
    focus_path = NOTES_DIR / "Моё здоровье/Фокус недели.md"
    next_start = now.strftime("%Y-%m-%d")
    next_end = (now + timedelta(days=6)).strftime("%Y-%m-%d")
    # Keep the note's original creation time (vault frontmatter schema, see AGENTS.md).
    focus_created = now.strftime("%Y-%m-%dT%H:%M:%S")
    if focus_path.exists():
        _m = re.search(r"^created:\s*(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})\s*$",
                       focus_path.read_text(encoding="utf-8").split("\n---", 1)[0], re.M)
        if _m:
            focus_created = _m.group(1)
        
    lab_months = round(monitoring["lab_interval_days"] / 30.4)
    lab_block = ""
    if lab_summary.get("is_due_for_control"):
        lab_block = f"- [ ] **Лаборатория (плановый контроль {lab_months} мес.):**\n"
        if monitoring["lab_panel"]:
            lab_block += "  - [ ] Выбрать день натощак и сдать кровь:\n"
            lab_block += "".join(f"    - [ ] {item}\n" for item in monitoring["lab_panel"])
    elif lab_summary:
        lab_block = f"- [x] **Лаборатория:** плановые анализы в графике (контроль раз в {lab_months} мес.).\n"

    meds_status_line = f"- {meds_adherence.get('message', '—')}"

    # Схема приёма — из актуальной «Схемы препаратов», без хардкода названий и времени.
    try:
        meds_focus_block = "\n".join(med_helper.format_week_focus_lines())
    except Exception as e:
        meds_focus_block = "- [ ] Схема приёма: см. [[Схема препаратов]]."
        warnings.append(f"Не удалось собрать схему приёма для Фокуса недели: {type(e).__name__}: {e}")
    has_meds = bool(meds_focus_block.strip()) and bool(meds_adherence.get("total_scheduled"))

    # Приоритет недели и чек-листы — только из заданных владельцем ориентиров.
    protein_txt = fmt_range(monitoring["protein_range"], "г/сут", "–")
    water_txt = fmt_range(monitoring["water_l"], "л", "–")
    priorities = []
    if protein_txt:
        priorities.append(f"удержание белка в коридоре {protein_txt}")
    if water_txt:
        priorities.append(f"питьевой режим ({water_txt})")
    priority_line = (
        f"> 💡 **Главный приоритет:** {' и '.join(priorities)}.\n" if priorities
        else "> 💡 **Главный приоритет:** регулярно записывать питание и замеры.\n"
    )

    habit_lines = []
    if protein_txt:
        habit_lines.append(f"- [ ] Белок в коридоре {protein_txt}.")
    if targets_set and targets.get("fat"):
        habit_lines.append(f"- [ ] Жиры в пределах {targets['fat']:.0f} г/сут.")
    if water_txt:
        habit_lines.append(f"- [ ] Питьевой режим: {water_txt} воды в день.")
    habit_lines.append("- [ ] Записывать приёмы пищи (минимум 4 дня из 7 для надёжной тенденции).")
    habits_block = "- [ ] **Питание и привычки:**\n" + "\n".join(habit_lines) + "\n"

    meds_block = ""
    if has_meds:
        meds_block = (
            f"- [ ] **Препараты и терапия (выполнение: {meds_adherence.get('adherence_pct', 0)}%):**\n"
            f"  {meds_status_line}\n{meds_focus_block}\n"
            "- [ ] Проверить остаток препаратов в аптечке на 2 недели вперёд.\n"
        )

    measure_lines = []
    measure = monitoring["measure"]
    bp_txt = fmt_range(monitoring["bp_systolic_range"], "", "–")
    if "pressure" in measure or "pulse" in measure:
        measure_lines.append("- [ ] Утреннее давление и пульс в покое" + (f" (систолическое {bp_txt})." if bp_txt else "."))
    if "glucose" in measure:
        g_txt = fmt_range(monitoring["glucose_range"], "ммоль/л", "–")
        measure_lines.append("- [ ] Сахар натощак" + (f" (целевой коридор {g_txt})." if g_txt else "."))
    if "weight" in measure:
        w_t = monitoring["weight_target"]
        measure_lines.append("- [ ] Фиксация веса" + (f" (целевой ориентир ~{w_t:g} кг)." if w_t else "."))
    measure_block = ("- [ ] **Замеры и мониторинг:**\n" + "\n".join(measure_lines) + "\n") if measure_lines else ""

    def _target_cell(value):
        return f"**{value}**" if value else "—"

    kcal_txt = fmt_range(monitoring["kcal_range"], "ккал", " – ")
    rows = [
        ("Белки", f"{nutrition_summary['avg_daily']['protein']} г / сут",
         _target_cell(fmt_range(monitoring["protein_range"], "г / сут", " – ")
                      or (f"{targets['protein']:.0f} г / сут" if targets_set else None))),
        ("Калории", f"{nutrition_summary['avg_daily']['kcal']} ккал / сут",
         _target_cell(kcal_txt or (f"{targets['kcal']:.0f} ккал" if targets_set else None))),
        ("Жиры", f"{nutrition_summary['avg_daily']['fat']} г / сут",
         _target_cell(f"до {targets['fat']:.0f} г / сут" if targets_set and targets.get("fat") else None)),
        ("Углеводы", f"{nutrition_summary['avg_daily']['carbs']} г / сут",
         _target_cell(f"до {targets['carbs']:.0f} г / сут" if targets_set and targets.get("carbs") else None)),
        ("Дней с записями", f"{nutrition_summary['days_logged']} из 7", "**минимум 4 для надёжной тенденции**"),
    ]
    if monitoring["glucose_range"] or glucose_summary:
        rows.append(("Глюкоза", f"{glucose_summary.get('avg', '—')} ммоль/л",
                     _target_cell(fmt_range(monitoring["glucose_range"], "ммоль/л", " – "))))
    if has_meds:
        rows.append(("Приём лекарств",
                     f"{meds_adherence.get('total_taken', 0)} из {meds_adherence.get('total_scheduled', 0)} ({meds_adherence.get('adherence_pct', 0)}%)",
                     "**100% без пропусков**"))
    table = "\n".join(f"| **{name}** | {fact} | {goal} |" for name, fact, goal in rows)

    checklist = "\n".join(block for block in (lab_block, habits_block, meds_block, measure_block) if block)

    note_content = f"""---
type: tracker
status: active
created: {focus_created}
updated: {now.strftime('%Y-%m-%dT%H:%M:%S')}
tags: [здоровье, чеклист, неделя, фокус]
period: "{next_start} ... {next_end}"
nutrition_week:
  period: "{nutrition_summary['period']}"
  days_logged: {nutrition_summary['days_logged']}
  reliable: {str(nutrition_summary['days_logged'] >= 4).lower()}
  avg_daily:
    kcal: {nutrition_summary['avg_daily']['kcal']}
    protein: {nutrition_summary['avg_daily']['protein']}
    fat: {nutrition_summary['avg_daily']['fat']}
    carbs: {nutrition_summary['avg_daily']['carbs']}
  diff:
    kcal: {nutrition_summary['diff']['kcal']}
    protein: {nutrition_summary['diff']['protein']}
    fat: {nutrition_summary['diff']['fat']}
    carbs: {nutrition_summary['diff']['carbs']}
---

# 🎯 Фокус недели: Здоровье

> 📅 **Период:** {next_start} — {next_end}  
{priority_line}
---

## 📋 Чек-лист ключевых задач

{checklist}
---

## 📊 Итоги прошлой недели vs Цели

| Параметр | Факт прошлой недели | Цель на новую неделю |
| :--- | :--- | :--- |
{table}

---

## Связанные заметки

- [[Моё здоровье]]
- [[Схема препаратов]]
- [[Расписание и план обследований]]
- [[Цели и мониторинг]]
"""
    with open(focus_path, "w", encoding="utf-8") as f:
        f.write(note_content)
except Exception as e:
    warnings.append(f"Ошибка авто-обновления Фокус недели.md: {e}")

# 5. Protein Cheat Sheet (if weekly average is under target)
protein_tips = []
if targets_set and nutrition_summary["diff"]["protein"] < -10:
    protein_tips = [
        {"item": "Творог 5% (150–200г)", "protein": "25–35г", "fat": "7–10г", "note": "идеально на второй завтрак или полдник"},
        {"item": "Тунец в собственном соку (1 банка)", "protein": "28–30г", "fat": "1г", "note": "чистый белок без жира"},
        {"item": "Грудка куриная / индейка запечённая (150г)", "protein": "35–40г", "fat": "3–4г", "note": "основной приём пищи"},
        {"item": "Омлет: 1 целое яйцо + 3 яичных белка", "protein": "18–20г", "fat": "5–6г", "note": "высокая биодоступность"},
        {"item": "Минтай / треска на пару (200г)", "protein": "32–36г", "fat": "2г", "note": "легкоусвояемый ужин"}
    ]

# 6. Generate 2-month review chart
chart_path = ""
try:
    chart_path = plot_health_review.generate_weekly_chart()
except Exception as e:
    warnings.append(f"Ошибка построения графика: {e}")

result = {
    "mode": "weekly_review",
    "generated_at": now.strftime("%Y-%m-%d %H:%M MSK"),
    "nutrition_summary": nutrition_summary,
    "top_fat_items": top_fat_items,
    "top_protein_items": top_protein_items,
    "protein_tips": protein_tips,
    "glucose_summary": glucose_summary,
    "bp_summary": bp_summary,
    "weight_summary": weight_summary,
    "lab_summary": lab_summary,
    "meds_adherence": meds_adherence,
    "chart_media": chart_path,
    "monitoring": monitoring,
}
if warnings:
    result["sync_warnings"] = warnings

print(json.dumps(result, ensure_ascii=False, indent=2))
