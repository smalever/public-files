#!/usr/bin/env python3
"""Morning health reminder data fetcher. Called by cron job."""
import json, os, sqlite3, sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Add scripts directory to sys.path for health_sync
sys.path.insert(0, str(Path(__file__).resolve().parent))
import health_sync
import medication_reminder_helper as med_helper
from health_monitoring import load_monitoring

MSK = timezone(timedelta(hours=3))
HERMES_HOME = Path(os.environ.get("HERMES_HOME", Path(__file__).resolve().parents[1]))
DB = HERMES_HOME / "personal/health/health.db"

now = datetime.now(MSK)
yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")

conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
cur = conn.cursor()

# Attach nutrition DB (separate database)
NUT_DB = HERMES_HOME / "personal/nutrition/nutrition.db"
cur.execute(f"ATTACH DATABASE '{NUT_DB}' AS nut")

# -- Sync & Load Targets --
# Схему препаратов синхронизирует вечерний pre-check; здесь нужны только цели КБЖУ.
warnings = []
obs_targets, warn_t = health_sync.sync_targets(conn)
if warn_t:
    warnings.append(warn_t)

# -- Latest metrics --
cur.execute("""
    SELECT type, value, unit, systolic, diastolic 
    FROM metrics 
    WHERE date = ?
    ORDER BY ts DESC
""", (now.strftime("%Y-%m-%d"),))
rows = cur.fetchall()
metrics = {}
for r in rows:
    t = r["type"]
    if t in ("pressure", "bp") and r["systolic"]:
        metrics["pressure"] = f"{r['systolic']}/{r['diastolic']} {r['unit'] or 'мм.рт.ст'}"
    elif t == "pulse" and r["value"]:
        metrics["pulse"] = f"{int(r['value'])} {r['unit'] or 'уд/мин'}"
    elif t == "glucose" and r["value"]:
        metrics["glucose"] = f"{r['value']} {r['unit'] or 'ммоль/л'}"
    elif t == "weight" and r["value"]:
        metrics["weight"] = f"{r['value']} {r['unit'] or 'кг'}"

# -- Yesterday's nutrition --
cur.execute("""
    SELECT 
        SUM(total_kcal) as kcal,
        SUM(total_protein) as protein,
        SUM(total_fat) as fat,
        SUM(total_carbs) as carbs
    FROM nut.nutrition
    WHERE date = ?
""", (yesterday,))
row = cur.fetchone()
yesterday_nutrition = {
    "date": yesterday,
    "kcal": int(row["kcal"] or 0),
    "protein": float(row["protein"] or 0),
    "fat": float(row["fat"] or 0),
    "carbs": float(row["carbs"] or 0)
}

# -- Yesterday's detailed food items (grouped by product for the whole day) --
cur.execute("""
    SELECT 
        i.product,
        SUM(i.grams) as grams,
        SUM(i.kcal) as kcal,
        SUM(i.protein) as protein,
        SUM(i.fat) as fat,
        SUM(i.carbs) as carbs
    FROM nut.nutrition_items i
    JOIN nut.nutrition n ON i.nutrition_id = n.id
    WHERE n.date = ?
    GROUP BY i.product
    ORDER BY kcal DESC
""", (yesterday,))
items_rows = cur.fetchall()
yesterday_items = []
for r in items_rows:
    yesterday_items.append({
        "product": r["product"],
        "grams": float(r["grams"] or 0),
        "kcal": int(r["kcal"] or 0),
        "protein": round(float(r["protein"] or 0), 1),
        "fat": round(float(r["fat"] or 0), 1),
        "carbs": round(float(r["carbs"] or 0), 1)
    })

# -- Profile targets --
if obs_targets and obs_targets.get("kcal"):
    targets = obs_targets
else:
    cur.execute("""
        SELECT daily_target_kcal, target_protein, target_fat, target_carbs
        FROM profile WHERE id=1
    """)
    profile = cur.fetchone()
    targets = {}
    if profile:
        targets = {
            "kcal": profile["daily_target_kcal"] or 0,
            "protein": profile["target_protein"] or 0,
            "fat": profile["target_fat"] or 0,
            "carbs": profile["target_carbs"] or 0
        }

# -- Comparison & recommendations --
comparison = {}
recommendations = []
if targets and yesterday_nutrition["kcal"] > 0:
    for key in ["kcal", "protein", "fat", "carbs"]:
        actual = yesterday_nutrition[key]
        target = targets[key]
        if target > 0:
            diff = actual - target
            comparison[key] = {
                "actual": round(actual, 1),
                "target": round(target, 1),
                "diff": round(diff, 1),
                "status": "over" if diff > 0 else "under" if diff < 0 else "ok"
            }
    
    # Simple recommendations
    if comparison.get("kcal", {}).get("status") == "over":
        recommendations.append(f"Вчера было превышение калорий на {abs(comparison['kcal']['diff']):.0f} ккал — сегодня уменьши порции")
    elif comparison.get("kcal", {}).get("status") == "under":
        recommendations.append(f"Вчера не добрал {abs(comparison['kcal']['diff']):.0f} ккал — добавь перекус")
    
    for nutrient in ["protein", "fat", "carbs"]:
        if comparison.get(nutrient, {}).get("status") == "over":
            recommendations.append(f"{nutrient.capitalize()}: перебор {abs(comparison[nutrient]['diff']):.0f}г — сегодня сократи")
        elif comparison.get(nutrient, {}).get("status") == "under":
            recommendations.append(f"{nutrient.capitalize()}: не добрал {abs(comparison[nutrient]['diff']):.0f}г — добавь в рацион")

conn.close()

# -- Препараты «до завтрака» (food=before, ещё не отмечены) --
before_meal_meds = []
try:
    before_meal_meds = [
        {"name": x["name"], "dosage": x["dosage"]}
        for x in med_helper.get_pending_meds("morning", now.strftime("%Y-%m-%d"), food=("before",))
    ]
except Exception as e:
    warnings.append(f"Не удалось получить препараты до еды: {type(e).__name__}: {e}")

result = {
    "mode": "morning",
    "date": now.strftime("%Y-%m-%d"),
    "metrics": metrics,
    "yesterday_nutrition": yesterday_nutrition,
    "yesterday_items": yesterday_items,
    "targets": targets,
    "comparison": comparison,
    "recommendations": recommendations,
    "before_meal_meds": before_meal_meds,
    "measure": load_monitoring()["measure"],
}
if warnings:
    result["sync_warnings"] = warnings
print(json.dumps(result, ensure_ascii=False, indent=2))
