"""Regression tests for the morning brief payload contract.

Medication reminders are owned by meal logging (`nutrition.py meal`) and the
evening pre-check. The morning brief stays measurements + nutrition only, and
must not re-add the `sync_medications()` call the evening script already owns.
"""

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "health_reminder_morning.py"
# Patch every data source before executing the unchanged production script.
# HERMES_HOME alone does not redirect the shared modules' absolute defaults.
BOOTSTRAP = """
import os, runpy, sys
from pathlib import Path
script, root, notes = map(Path, sys.argv[1:])
os.environ['HERMES_HOME'] = str(root)
os.environ['BOT_NOTES_DIR'] = str(notes)
sys.path.insert(0, str(script.parent))
import health_sync
import medication_reminder_helper
import health_monitoring
health_sync.DB_PATH = root / 'personal/health/health.db'
health_sync.MEDS_NOTE = notes / 'Моё здоровье/Схема препаратов.md'
health_sync.TARGETS_NOTE = notes / 'Моё здоровье/Цели и мониторинг.md'
medication_reminder_helper.HEALTH_DB_PATH = health_sync.DB_PATH
medication_reminder_helper.MEDS_NOTE_PATH = health_sync.MEDS_NOTE
health_monitoring.TARGETS_NOTE = health_sync.TARGETS_NOTE
sys.argv = [str(script)]
runpy.run_path(str(script), run_name='__main__')
"""
EXPECTED_KEYS = {
    "mode",
    "date",
    "metrics",
    "yesterday_nutrition",
    "yesterday_items",
    "targets",
    "comparison",
    "recommendations",
    "before_meal_meds",
    "measure",
}


class MorningBriefPayloadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        root = Path(cls.tmp.name)
        notes = root / "notes"
        health_dir = root / "personal/health"
        nutrition_dir = root / "personal/nutrition"
        health_dir.mkdir(parents=True)
        nutrition_dir.mkdir(parents=True)
        (notes / "Моё здоровье").mkdir(parents=True)
        (notes / "Моё здоровье/Цели и мониторинг.md").write_text(
            "---\ntargets:\n  daily_target_kcal: 2000\n  target_protein: 100\n"
            "  target_fat: 60\n  target_carbs: 250\nmonitoring:\n  measure: [weight]\n---\n",
            encoding="utf-8",
        )
        (notes / "Моё здоровье/Схема препаратов.md").write_text(
            "---\nmedications:\n  morning: []\n  lunch: []\n  evening: []\n---\n",
            encoding="utf-8",
        )
        with sqlite3.connect(health_dir / "health.db") as conn:
            conn.executescript("""
                CREATE TABLE metrics (date TEXT, ts TEXT, type TEXT, value REAL,
                                      unit TEXT, systolic REAL, diastolic REAL);
                CREATE TABLE profile (id INTEGER PRIMARY KEY, daily_target_kcal INTEGER,
                                      target_protein REAL, target_fat REAL, target_carbs REAL);
                INSERT INTO profile VALUES (1, 0, 0, 0, 0);
            """)
        with sqlite3.connect(nutrition_dir / "nutrition.db") as conn:
            conn.executescript("""
                CREATE TABLE nutrition (id INTEGER PRIMARY KEY, date TEXT,
                    total_kcal REAL, total_protein REAL, total_fat REAL, total_carbs REAL);
                CREATE TABLE nutrition_items (nutrition_id INTEGER, product TEXT,
                    grams REAL, kcal REAL, protein REAL, fat REAL, carbs REAL);
            """)
        cls.proc = subprocess.run(
            [sys.executable, "-I", "-B", "-c", BOOTSTRAP,
             str(SCRIPT_PATH), str(root), str(notes)],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=root,
            env={"PATH": os.defpath, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        cls.payload = json.loads(cls.proc.stdout) if cls.proc.returncode == 0 else None

    def test_script_runs_clean(self):
        self.assertEqual(self.proc.returncode, 0, msg=self.proc.stderr)
        self.assertEqual(self.proc.stderr.strip(), "")

    def test_payload_keys_are_exactly_the_contract(self):
        self.assertIsNotNone(self.payload)
        # sync_warnings is optional and appears only on a note parse failure.
        self.assertEqual(set(self.payload) - {"sync_warnings"}, EXPECTED_KEYS)
        self.assertEqual(self.payload["mode"], "morning")

    def test_morning_script_does_not_duplicate_medication_work(self):
        source = SCRIPT_PATH.read_text(encoding="utf-8")
        self.assertNotIn("sync_medications", source)
        self.assertNotIn("get_active_meds_for_slot", source)


if __name__ == "__main__":
    unittest.main()
