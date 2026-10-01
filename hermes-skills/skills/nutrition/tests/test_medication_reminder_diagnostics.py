import importlib.util
import io
import sqlite3
import tempfile
import unittest
from contextlib import closing, redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


NUTRITION_PATH = Path("/opt/data/skills-custom/health/nutrition/scripts/nutrition.py")
MSK = timezone(timedelta(hours=3))


def load_nutrition():
    spec = importlib.util.spec_from_file_location("nutrition_test", NUTRITION_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BrokenReminderHelper:
    @staticmethod
    def determine_reminder(*args, **kwargs):
        raise RuntimeError("test reminder failure")


class NutritionReminderDiagnosticsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_nutrition()

    def test_reminder_failure_is_reported_to_stderr_without_raising(self):
        stderr = io.StringIO()
        now = datetime(2026, 9, 22, 20, 0, tzinfo=MSK)

        with redirect_stderr(stderr):
            result = self.module.render_medication_reminder(
                med_helper=BrokenReminderHelper,
                date_str="2026-09-22",
                current_msk_dt=now,
                total_meals_today=1,
                meal_info={"meal": "dinner", "kcal": 500, "fat": 20, "protein": 30, "carbs": 40},
            )

        self.assertIsNone(result)
        warning = stderr.getvalue()
        self.assertIn("Не удалось проверить напоминание о лекарствах", warning)
        self.assertIn("RuntimeError", warning)
        self.assertIn("test reminder failure", warning)

    def test_helper_import_failure_is_reported_without_raising(self):
        stderr = io.StringIO()

        def broken_loader():
            raise ImportError("test helper import failure")

        with redirect_stderr(stderr):
            helper = self.module.load_medication_reminder_helper(loader=broken_loader)

        self.assertIsNone(helper)
        warning = stderr.getvalue()
        self.assertIn("Не удалось загрузить модуль напоминаний о лекарствах", warning)
        self.assertIn("ImportError", warning)
        self.assertIn("test helper import failure", warning)

    def test_add_meal_stays_saved_when_reminder_helper_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "nutrition.db"
            with closing(sqlite3.connect(db_path)) as conn:
                conn.executescript(
                    """
                    CREATE TABLE food_db (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        product TEXT UNIQUE NOT NULL,
                        kcal INTEGER, protein REAL, fat REAL, carbs REAL,
                        normalized_name TEXT, added_at TEXT DEFAULT CURRENT_TIMESTAMP
                    );
                    CREATE TABLE food_aliases (
                        alias TEXT PRIMARY KEY, product TEXT NOT NULL
                    );
                    CREATE TABLE nutrition (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        date TEXT NOT NULL, ts TEXT NOT NULL, meal TEXT NOT NULL,
                        total_kcal INTEGER, total_protein REAL,
                        total_fat REAL, total_carbs REAL
                    );
                    CREATE TABLE nutrition_items (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        nutrition_id INTEGER NOT NULL, product TEXT NOT NULL,
                        grams REAL NOT NULL, kcal INTEGER,
                        protein REAL, fat REAL, carbs REAL
                    );
                    INSERT INTO food_db
                        (product, kcal, protein, fat, carbs, normalized_name)
                    VALUES ('Тестовая еда', 100, 10, 5, 7, 'тестовая еда');
                    """
                )

            args = SimpleNamespace(
                date="2026-09-22", meal="dinner", items="Тестовая еда 100г"
            )
            stdout = io.StringIO()
            stderr = io.StringIO()
            fixed_now = datetime(2026, 9, 22, 20, 0, tzinfo=MSK)

            with patch.object(self.module, "DB_PATH", str(db_path)), patch.object(
                self.module, "load_medication_reminder_helper", return_value=BrokenReminderHelper
            ), patch.object(self.module, "datetime") as datetime_mock, redirect_stdout(
                stdout
            ), redirect_stderr(stderr):
                datetime_mock.now.return_value = fixed_now
                self.module.add_meal(args)

            with closing(sqlite3.connect(db_path)) as conn:
                meal_count = conn.execute("SELECT COUNT(*) FROM nutrition").fetchone()[0]
                item_count = conn.execute("SELECT COUNT(*) FROM nutrition_items").fetchone()[0]

            self.assertEqual(meal_count, 1)
            self.assertEqual(item_count, 1)
            self.assertIn("Прием пищи 'dinner' записан", stdout.getvalue())
            self.assertIn("Не удалось проверить напоминание о лекарствах", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
