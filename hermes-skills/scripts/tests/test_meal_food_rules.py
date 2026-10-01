import importlib.util
import sqlite3
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

HELPER_PATH = Path("/opt/data/scripts/medication_reminder_helper.py")
NUTRITION_PATH = Path("/opt/data/skills-custom/health/nutrition/scripts/nutrition.py")
MSK = timezone(timedelta(hours=3))


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


EVENING = [
    {"name": "Лозартан", "dosage": "5 мг", "note": ""},
    {"name": "Метформин (Глюкофаж)", "dosage": "1000 мг", "note": "во время ужина"},
    {"name": "Магния цитрат (+ B6)", "dosage": "3 капсулы", "note": ""},
]


class FoodRulesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.h = load(HELPER_PATH, "mrh_food_test")

    def test_baked_apples_ok_for_metformin_no_scare(self):
        info = {"kcal": 319, "protein": 1.8, "fat": 1.8, "carbs": 72.0}
        text = self.h.format_reminder("evening", EVENING, meal_info=info)
        self.assertNotIn("⚠️", text)
        self.assertNotIn("не пей", text)
        self.assertIn("✅ Еда подходит под все препараты", text)

    def test_coffee_soft_hints_only_for_present_meds(self):
        morning = [
            {"name": "Витамин D3 + K2 (Бренд)", "dosage": "2 капсулы", "note": "", "food": "with"},
            {"name": "Омега-3 (Бренд)", "dosage": "1 капсула", "note": "", "food": "with"},
        ]
        info = {"kcal": 57, "protein": 3.3, "fat": 2.7, "carbs": 5.1}
        text = self.h.format_reminder("morning", morning, meal_info=info)
        self.assertIn("💡 Омега-3: усвоится лучше", text)
        self.assertNotIn("Витамин D3 + K2:", text)
        self.assertNotIn("Магни", text)
        self.assertNotIn("⚠️", text)

    def test_normal_dinner_passes(self):
        info = {"kcal": 550, "protein": 35, "fat": 20, "carbs": 50}
        text = self.h.format_reminder("evening", EVENING, meal_info=info)
        self.assertIn("✅ Еда подходит под все препараты", text)

    def test_meal_rule(self):
        self.assertTrue(self.h.check_meal_requirement("meal", {"kcal": 150}))
        self.assertFalse(self.h.check_meal_requirement("meal", {"kcal": 200}))

    def test_fat_rule(self):
        self.assertTrue(self.h.check_meal_requirement("fat", {"fat": 9.9}))
        self.assertFalse(self.h.check_meal_requirement("fat", {"fat": 10}))

    def test_food_rule(self):
        self.assertTrue(self.h.check_meal_requirement("food", {"kcal": 60}))
        self.assertFalse(self.h.check_meal_requirement("food", {"kcal": 100}))


class WindowTotalsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.n = load(NUTRITION_PATH, "nutrition_window_test")

    def _conn(self, rows):
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE nutrition (id INTEGER PRIMARY KEY, date TEXT, ts TEXT, meal TEXT, "
                     "total_kcal INTEGER, total_protein REAL, total_fat REAL, total_carbs REAL)")
        conn.executemany("INSERT INTO nutrition (date, ts, meal, total_kcal, total_protein, total_fat, total_carbs) "
                         "VALUES (?,?,?,?,?,?,?)", rows)
        return conn

    def test_sums_only_last_60_minutes(self):
        conn = self._conn([
            ("2026-09-27", "27.09.2026 18:00:00", "snack", 500, 30, 20, 40),   # 90 мин назад — не входит
            ("2026-09-27", "27.09.2026 19:00:00", "dinner", 400, 30, 15, 20),  # 30 мин назад
            ("2026-09-27", "27.09.2026 19:30:00", "snack", 319, 1.8, 1.8, 72), # сейчас
        ])
        now = datetime(2026, 9, 27, 19, 30, 0, tzinfo=MSK)
        t = self.n.window_meal_totals(conn.cursor(), now, 60)
        self.assertEqual(t["meals_in_window"], 2)
        self.assertEqual(t["kcal"], 719)
        self.assertAlmostEqual(t["protein"], 31.8)

    def test_window_crosses_midnight(self):
        conn = self._conn([
            ("2026-09-27", "27.09.2026 23:40:00", "dinner", 400, 30, 15, 20),
            ("2026-09-28", "28.09.2026 00:10:00", "snack", 100, 2, 1, 20),
        ])
        now = datetime(2026, 9, 28, 0, 10, 0, tzinfo=MSK)
        t = self.n.window_meal_totals(conn.cursor(), now, 60)
        self.assertEqual(t["meals_in_window"], 2)


if __name__ == "__main__":
    unittest.main()
