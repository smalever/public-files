import importlib.util
import sqlite3
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

HELPER_PATH = Path("/opt/data/scripts/medication_reminder_helper.py")
if not HELPER_PATH.exists():
    HELPER_PATH = Path(__file__).resolve().parents[1] / "medication_reminder_helper.py"
NUTRITION_PATH = Path("/opt/data/skills-custom/health/nutrition/scripts/nutrition.py")
if not NUTRITION_PATH.exists():
    NUTRITION_PATH = Path(__file__).resolve().parents[2] / "skills/nutrition/scripts/nutrition.py"
MSK = timezone(timedelta(hours=3))


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


BACKUP_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "skills/health-tracker/scripts/backup.py"

NEUTRAL_MEDS_EVENING = [
    {"name": "Препарат А", "dosage": "1 таб", "food": "with", "note": ""},
    {"name": "Препарат Б", "dosage": "2 капс", "food": "after", "note": ""},
]

NEUTRAL_MEDS_MORNING = [
    {"name": "Препарат до еды", "dosage": "1 таб", "food": "before", "note": ""},
    {"name": "Препарат с едой", "dosage": "1 таб", "food": "with", "note": ""},
]


class FoodRulesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.h = load(HELPER_PATH, "mrh_food_test")

    def test_food_rules_disabled_by_default(self):
        """Heuristics must be disabled by default; FOOD_RULES must be empty."""
        self.assertEqual(self.h.FOOD_RULES, {})

    def test_reminder_returns_food_facts_from_schema_without_threshold_heuristics(self):
        """Format reminder returns schema facts and meal totals without arbitrary thresholds."""
        info = {"kcal": 50, "protein": 2.0, "fat": 1.0, "carbs": 8.0}
        text = self.h.format_reminder("evening", NEUTRAL_MEDS_EVENING, meal_info=info)
        self.assertIn("Препарат А (1 таб) [🍽 во время еды]", text)
        self.assertIn("Препарат Б (2 капс) [🍽 после еды]", text)
        self.assertIn("🍽 Съедено в этом приёме: 50 ккал — Б 2.0 / Ж 1.0 / У 8.0 г", text)
        self.assertNotIn("💡", text)
        self.assertNotIn("лучше от", text)
        self.assertNotIn("✅ Еда подходит", text)

    def test_reminder_with_before_meds_shows_schema_facts(self):
        text = self.h.format_reminder("morning", NEUTRAL_MEDS_MORNING)
        self.assertIn("⏰ До еды, ещё не отмечено: Препарат до еды", text)
        self.assertIn("- Препарат с едой (1 таб) [🍽 во время еды]", text)

    def test_no_subcutaneous_heuristic_on_wednesday_note(self):
        """Notes mentioning 'по средам' must not assume subcutaneous injection."""
        meds = [{"name": "Тестовая добавка", "dosage": "1 капс", "note": "по средам", "food": "any"}]
        text = self.h.format_reminder("morning", meds)
        self.assertNotIn("подкожно", text)


class UnsafeBackupDisabledTests(unittest.TestCase):
    def test_backup_fails_closed_without_copying_db(self):
        """backup.py must fail-closed with admin guidance and never copy live SQLite db."""
        import subprocess
        result = subprocess.run(
            ["/home/sma/.venv/bin/python", str(BACKUP_SCRIPT_PATH)],
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        output = (result.stdout + result.stderr).lower()
        self.assertIn("disabled", output)
        self.assertNotIn("backup created", output)


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
