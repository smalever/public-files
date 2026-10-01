#!/usr/bin/env python3
"""Build read-only nutrition context for recipe selection."""

import argparse
import json
import os
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import yaml


MSK = timezone(timedelta(hours=3))
HERMES_HOME = Path(os.environ.get("HERMES_HOME", "/opt/data"))
DEFAULT_NUTRITION_DB = HERMES_HOME / "personal/nutrition/nutrition.db"
DEFAULT_HEALTH_DB = HERMES_HOME / "personal/health/health.db"
DEFAULT_TARGETS_NOTE = Path("/notes/Моё здоровье/Цели и мониторинг.md")
DEFAULT_WEEKLY_NOTE = Path("/notes/Моё здоровье/Фокус недели.md")
NUTRIENTS = ("kcal", "protein", "fat", "carbs")


def _round(value):
    return round(float(value or 0), 1)


def _has_targets(targets):
    """Цели заданы, если указана суточная норма калорий."""
    return bool(targets) and bool(targets.get("kcal"))


def load_targets(targets_note=DEFAULT_TARGETS_NOTE, health_db=DEFAULT_HEALTH_DB):
    """Load targets from Obsidian, falling back to the health profile.

    Returns (targets, source). Если цели КБЖУ не заданы ни в заметке, ни в профиле
    (новый владелец, нули), возвращает (None, "none"): рецепт подбирается без рамок.
    """
    try:
        content = Path(targets_note).read_text(encoding="utf-8")
        if content.startswith("---"):
            frontmatter = yaml.safe_load(content.split("---", 2)[1]) or {}
            raw = frontmatter.get("targets") or {}
            targets = {
                "kcal": int(raw.get("daily_target_kcal") or 0),
                "protein": _round(raw.get("target_protein")),
                "fat": _round(raw.get("target_fat")),
                "carbs": _round(raw.get("target_carbs")),
            }
            if all(targets.values()):
                return targets, "obsidian"
    except (OSError, ValueError, AttributeError, yaml.YAMLError):
        pass

    row = None
    try:
        conn = sqlite3.connect(f"file:{health_db}?mode=ro", uri=True)
        try:
            row = conn.execute(
                "SELECT daily_target_kcal, target_protein, target_fat, target_carbs "
                "FROM profile WHERE id=1"
            ).fetchone()
        finally:
            conn.close()
    except sqlite3.Error:
        row = None
    if not row or not row[0]:
        return None, "none"
    return {
        "kcal": int(row[0] or 0),
        "protein": _round(row[1]),
        "fat": _round(row[2]),
        "carbs": _round(row[3]),
    }, "sqlite_fallback"


def _day_totals(conn, day):
    row = conn.execute(
        """SELECT COUNT(*) AS entries,
                  COALESCE(SUM(total_kcal), 0),
                  COALESCE(SUM(total_protein), 0),
                  COALESCE(SUM(total_fat), 0),
                  COALESCE(SUM(total_carbs), 0)
           FROM nutrition WHERE date=?""",
        (day.isoformat(),),
    ).fetchone()
    return {
        "date": day.isoformat(),
        "logged": row[0] > 0,
        "entries": int(row[0]),
        "actual": {
            "kcal": _round(row[1]),
            "protein": _round(row[2]),
            "fat": _round(row[3]),
            "carbs": _round(row[4]),
        },
    }


def _add_comparison(summary, targets, today=False):
    diff = {}
    for nutrient in NUTRIENTS:
        diff[nutrient] = _round(summary["actual"][nutrient] - targets[nutrient])
    summary["diff"] = diff
    if today:
        summary["remaining"] = {
            nutrient: _round(max(targets[nutrient] - summary["actual"][nutrient], 0))
            for nutrient in NUTRIENTS
        }
    return summary


def _signals_from_diff(diff, targets, reliable):
    signals = []
    if reliable:
        for nutrient in NUTRIENTS:
            target = targets[nutrient]
            if not target:
                continue
            ratio = diff[nutrient] / target
            if ratio <= -0.10:
                signals.append(f"{nutrient}_under")
            elif ratio >= 0.10:
                signals.append(f"{nutrient}_over")
    return signals


def _weekly_from_note(weekly_note, targets):
    try:
        content = Path(weekly_note).read_text(encoding="utf-8")
        if not content.startswith("---"):
            return None
        frontmatter = yaml.safe_load(content.split("---", 2)[1]) or {}
        raw = frontmatter.get("nutrition_week")
        if not isinstance(raw, dict):
            return None
        avg_raw = raw.get("avg_daily") or {}
        diff_raw = raw.get("diff") or {}
        averages = {nutrient: _round(avg_raw.get(nutrient)) for nutrient in NUTRIENTS}
        diff = {
            nutrient: _round(diff_raw.get(nutrient, averages[nutrient] - targets[nutrient]))
            for nutrient in NUTRIENTS
        }
        days_logged = int(raw.get("days_logged") or 0)
        reliable = bool(raw.get("reliable")) and days_logged >= 4
        return {
            "source": "obsidian_weekly_focus",
            "period": str(raw.get("period") or ""),
            "days_logged": days_logged,
            "coverage_days": 7,
            "reliable": reliable,
            "avg_daily": averages,
            "diff": diff,
            "signals": _signals_from_diff(diff, targets, reliable),
        }
    except (OSError, ValueError, TypeError, yaml.YAMLError):
        return None


def _weekly_summary(conn, as_of, targets):
    start = as_of - timedelta(days=7)
    end = as_of - timedelta(days=1)
    rows = conn.execute(
        """SELECT date,
                  SUM(total_kcal), SUM(total_protein),
                  SUM(total_fat), SUM(total_carbs)
           FROM nutrition
           WHERE date BETWEEN ? AND ?
           GROUP BY date""",
        (start.isoformat(), end.isoformat()),
    ).fetchall()
    days_logged = len(rows)
    if days_logged:
        averages = {
            "kcal": _round(sum(r[1] or 0 for r in rows) / days_logged),
            "protein": _round(sum(r[2] or 0 for r in rows) / days_logged),
            "fat": _round(sum(r[3] or 0 for r in rows) / days_logged),
            "carbs": _round(sum(r[4] or 0 for r in rows) / days_logged),
        }
    else:
        averages = {nutrient: 0.0 for nutrient in NUTRIENTS}
    diff = {nutrient: _round(averages[nutrient] - targets[nutrient]) for nutrient in NUTRIENTS}
    reliable = days_logged >= 4
    signals = _signals_from_diff(diff, targets, reliable)
    return {
        "source": "nutrition_db_fallback",
        "period": f"{start.isoformat()} ... {end.isoformat()}",
        "days_logged": days_logged,
        "coverage_days": 7,
        "reliable": reliable,
        "avg_daily": averages,
        "diff": diff,
        "signals": signals,
    }


def _recipe_constraints(today):
    remaining = today["remaining"]
    return {
        "kcal_max": _round(min(remaining["kcal"], 750)),
        "protein_target_g": _round(min(remaining["protein"], 60)),
        "fat_max_g": _round(min(remaining["fat"], 25)),
        "carbs_max_g": _round(min(remaining["carbs"], 90)),
        "basis": "today_remaining_only",
    }


def _no_target_constraints():
    return {
        "kcal_max": None,
        "protein_target_g": None,
        "fat_max_g": None,
        "carbs_max_g": None,
        "basis": "no_targets",
    }


def build_context(nutrition_db=DEFAULT_NUTRITION_DB, targets=None, as_of=None,
                  targets_note=DEFAULT_TARGETS_NOTE, health_db=DEFAULT_HEALTH_DB,
                  weekly_note=DEFAULT_WEEKLY_NOTE):
    """Return ordered recipe signals without carrying past deficits forward."""
    as_of = as_of or datetime.now(MSK).date()
    target_source = "provided"
    if targets is None:
        targets, target_source = load_targets(targets_note, health_db)

    if not _has_targets(targets):
        try:
            conn = sqlite3.connect(f"file:{nutrition_db}?mode=ro", uri=True)
            try:
                today = _day_totals(conn, as_of)
            finally:
                conn.close()
        except sqlite3.Error:
            today = None
        return {
            "as_of": as_of.isoformat(),
            "targets": None,
            "target_source": "none",
            "priority_order": ["preferences"],
            "today": today,
            "yesterday": None,
            "week": None,
            "recipe_constraints": _no_target_constraints(),
            "carry_yesterday_deficit": False,
            "rules": [
                "Цели КБЖУ не заданы: подбирай любое блюдо по продуктам, вкусу и пожеланиям.",
                "Не придумывай дефицит или перебор; сегодняшний факт — только справочно.",
                "Можно один раз предложить задать цели в заметке «Цели и мониторинг».",
            ],
        }

    conn = sqlite3.connect(nutrition_db)
    try:
        today = _add_comparison(_day_totals(conn, as_of), targets, today=True)
        yesterday = _add_comparison(
            _day_totals(conn, as_of - timedelta(days=1)), targets
        )
        week = _weekly_from_note(weekly_note, targets) or _weekly_summary(conn, as_of, targets)
    finally:
        conn.close()

    return {
        "as_of": as_of.isoformat(),
        "targets": targets,
        "target_source": target_source,
        "priority_order": ["today", "yesterday", "week"],
        "today": today,
        "yesterday": yesterday,
        "week": week,
        "recipe_constraints": _recipe_constraints(today),
        "carry_yesterday_deficit": False,
        "rules": [
            "Сегодняшний остаток КБЖУ задаёт ограничения блюда.",
            "Вчерашние отклонения только слабо корректируют выбор и не переносятся как долг.",
            "Недельные сигналы учитываются третьими и только при 4+ заполненных днях.",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description="Read-only recipe nutrition context")
    parser.add_argument("--date", help="Дата расчёта YYYY-MM-DD; по умолчанию сегодня MSK")
    parser.add_argument("--nutrition-db", type=Path, default=DEFAULT_NUTRITION_DB)
    parser.add_argument("--health-db", type=Path, default=DEFAULT_HEALTH_DB)
    parser.add_argument("--targets-note", type=Path, default=DEFAULT_TARGETS_NOTE)
    parser.add_argument("--weekly-note", type=Path, default=DEFAULT_WEEKLY_NOTE)
    args = parser.parse_args()
    as_of = date.fromisoformat(args.date) if args.date else datetime.now(MSK).date()
    result = build_context(
        nutrition_db=args.nutrition_db,
        as_of=as_of,
        targets_note=args.targets_note,
        health_db=args.health_db,
        weekly_note=args.weekly_note,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
