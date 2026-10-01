"""Tests for the recipe assistant nutrition context."""

import importlib.util
import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path


SCRIPT_PATH = Path("/opt/data/scripts/recipe_context.py")


def load_module():
    spec = importlib.util.spec_from_file_location("recipe_context", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RecipeContextTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.nutrition_db = Path(self.tmp.name) / "nutrition.db"
        self.missing_weekly_note = Path(self.tmp.name) / "missing-weekly.md"
        conn = sqlite3.connect(self.nutrition_db)
        conn.execute(
            """CREATE TABLE nutrition (
                id INTEGER PRIMARY KEY,
                date TEXT NOT NULL,
                total_kcal REAL,
                total_protein REAL,
                total_fat REAL,
                total_carbs REAL
            )"""
        )
        conn.commit()
        conn.close()
        self.targets = {"kcal": 2300, "protein": 140.0, "fat": 72.0, "carbs": 261.0}

    def tearDown(self):
        self.tmp.cleanup()

    def add_day(self, day, kcal, protein, fat, carbs):
        conn = sqlite3.connect(self.nutrition_db)
        conn.execute(
            "INSERT INTO nutrition(date,total_kcal,total_protein,total_fat,total_carbs) VALUES(?,?,?,?,?)",
            (day, kcal, protein, fat, carbs),
        )
        conn.commit()
        conn.close()

    def test_today_is_primary_and_yesterday_is_not_carried_as_debt(self):
        self.add_day("2026-09-23", 1700, 130, 68, 180)
        self.add_day("2026-09-22", 1200, 55, 30, 120)
        module = load_module()

        result = module.build_context(
            nutrition_db=self.nutrition_db,
            targets=self.targets,
            as_of=date(2026, 9, 23),
            weekly_note=self.missing_weekly_note,
        )

        self.assertEqual(result["priority_order"], ["today", "yesterday", "week"])
        self.assertEqual(result["today"]["remaining"]["protein"], 10.0)
        self.assertEqual(result["today"]["remaining"]["fat"], 4.0)
        self.assertEqual(result["yesterday"]["diff"]["protein"], -85.0)
        self.assertEqual(result["recipe_constraints"]["protein_target_g"], 10.0)
        self.assertLessEqual(result["recipe_constraints"]["fat_max_g"], 4.0)
        self.assertFalse(result["carry_yesterday_deficit"])

    def test_week_is_tertiary_and_reliable_only_with_four_logged_days(self):
        for day in ("2026-09-17", "2026-09-18", "2026-09-19", "2026-09-20"):
            self.add_day(day, 1700, 85, 65, 170)
        module = load_module()

        result = module.build_context(
            nutrition_db=self.nutrition_db,
            targets=self.targets,
            as_of=date(2026, 9, 23),
            weekly_note=self.missing_weekly_note,
        )

        self.assertEqual(result["week"]["days_logged"], 4)
        self.assertTrue(result["week"]["reliable"])
        self.assertEqual(result["week"]["diff"]["protein"], -55.0)
        self.assertIn("protein_under", result["week"]["signals"])

    def test_sparse_week_does_not_drive_recipe(self):
        self.add_day("2026-09-20", 1100, 40, 20, 100)
        module = load_module()

        result = module.build_context(
            nutrition_db=self.nutrition_db,
            targets=self.targets,
            as_of=date(2026, 9, 23),
            weekly_note=self.missing_weekly_note,
        )

        self.assertFalse(result["week"]["reliable"])
        self.assertEqual(result["week"]["signals"], [])
    def test_weekly_obsidian_snapshot_is_preferred_over_db_fallback(self):
        weekly_note = Path(self.tmp.name) / "Фокус недели.md"
        weekly_note.write_text(
            """---
type: note
nutrition_week:
  period: 2026-09-16 ... 2026-09-22
  days_logged: 6
  reliable: true
  avg_daily:
    kcal: 1800
    protein: 90
    fat: 60
    carbs: 170
  diff:
    kcal: -500
    protein: -50
    fat: -12
    carbs: -91
---
# Фокус недели
""",
            encoding="utf-8",
        )
        module = load_module()

        result = module.build_context(
            nutrition_db=self.nutrition_db,
            targets=self.targets,
            as_of=date(2026, 9, 23),
            weekly_note=weekly_note,
        )

        self.assertEqual(result["week"]["source"], "obsidian_weekly_focus")
        self.assertEqual(result["week"]["days_logged"], 6)
        self.assertIn("protein_under", result["week"]["signals"])

    def _empty_targets_env(self):
        note = Path(self.tmp.name) / "Цели и мониторинг.md"
        note.write_text(
            "---\ntargets:\n  daily_target_kcal: 0\n  target_protein: 0\n"
            "  target_fat: 0\n  target_carbs: 0\n---\n",
            encoding="utf-8",
        )
        health_db = Path(self.tmp.name) / "health.db"
        conn = sqlite3.connect(health_db)
        conn.execute(
            "CREATE TABLE profile (id INTEGER PRIMARY KEY, daily_target_kcal INTEGER,"
            " target_protein REAL, target_fat REAL, target_carbs REAL)"
        )
        conn.commit()
        conn.close()
        return note, health_db

    def test_no_targets_gives_unconstrained_context(self):
        note, health_db = self._empty_targets_env()
        self.add_day("2026-09-23", 900, 40, 30, 100)
        module = load_module()

        result = module.build_context(
            nutrition_db=self.nutrition_db,
            as_of=date(2026, 9, 23),
            targets_note=note,
            health_db=health_db,
            weekly_note=self.missing_weekly_note,
        )

        self.assertIsNone(result["targets"])
        self.assertEqual(result["target_source"], "none")
        self.assertEqual(result["recipe_constraints"]["basis"], "no_targets")
        self.assertIsNone(result["recipe_constraints"]["kcal_max"])
        self.assertEqual(result["today"]["actual"]["kcal"], 900.0)
        self.assertIsNone(result["week"])

    def test_zero_profile_row_is_treated_as_no_targets(self):
        note, health_db = self._empty_targets_env()
        conn = sqlite3.connect(health_db)
        conn.execute("INSERT INTO profile VALUES (1, 0, 0, 0, 0)")
        conn.commit()
        conn.close()
        module = load_module()

        targets, source = module.load_targets(note, health_db)

        self.assertIsNone(targets)
        self.assertEqual(source, "none")


class WeeklyNoteContractTests(unittest.TestCase):
    def test_weekly_note_contains_carbs_and_logging_coverage(self):
        source = Path("/opt/data/scripts/health_weekly_review.py").read_text(encoding="utf-8")
        self.assertIn('("Углеводы"', source)
        self.assertIn("Дней с записями", source)
        self.assertIn("nutrition_week:", source)
        self.assertIn("reliable:", source)


if __name__ == "__main__":
    unittest.main()
