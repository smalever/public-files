"""Tests for owner-specific monitoring settings loaded from the targets note."""

import importlib.util
import tempfile
import unittest
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "health_monitoring.py"


def load_module():
    spec = importlib.util.spec_from_file_location("health_monitoring", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class HealthMonitoringTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.note = Path(self.tmp.name) / "Цели и мониторинг.md"
        self.m = load_module()

    def tearDown(self):
        self.tmp.cleanup()

    def test_missing_note_gives_empty_settings(self):
        mon = self.m.load_monitoring(self.note)
        self.assertIsNone(mon["glucose_range"])
        self.assertIsNone(mon["weight_target"])
        self.assertEqual(mon["measure"], [])
        self.assertEqual(mon["lab_panel"], [])
        self.assertEqual(mon["lab_interval_days"], 90.0)

    def test_note_without_monitoring_block(self):
        self.note.write_text("---\ntargets:\n  daily_target_kcal: 0\n---\n", encoding="utf-8")
        mon = self.m.load_monitoring(self.note)
        self.assertIsNone(mon["protein_range"])
        self.assertEqual(mon["measure"], [])

    def test_full_block_is_parsed(self):
        self.note.write_text(
            "---\nmonitoring:\n"
            "  protein_range: [100, 120]\n"
            "  glucose_range: [4.5, 6.0]\n"
            "  weight_target: 80\n"
            "  measure: [pressure, weight]\n"
            "  lab_panel: [Глюкоза]\n"
            "  lab_interval_days: 180\n"
            "---\n",
            encoding="utf-8",
        )
        mon = self.m.load_monitoring(self.note)
        self.assertEqual(mon["protein_range"], [100.0, 120.0])
        self.assertEqual(mon["glucose_range"], [4.5, 6.0])
        self.assertEqual(mon["weight_target"], 80.0)
        self.assertEqual(mon["measure"], ["pressure", "weight"])
        self.assertEqual(mon["lab_panel"], ["Глюкоза"])
        self.assertEqual(mon["lab_interval_days"], 180.0)

    def test_broken_values_are_ignored(self):
        self.note.write_text(
            "---\nmonitoring:\n  glucose_range: [7, 5]\n  weight_target: abc\n  measure: weight\n---\n",
            encoding="utf-8",
        )
        mon = self.m.load_monitoring(self.note)
        self.assertIsNone(mon["glucose_range"])
        self.assertIsNone(mon["weight_target"])
        self.assertEqual(mon["measure"], [])

    def test_fmt_range(self):
        self.assertEqual(self.m.fmt_range([120.0, 140.0], "г", "–"), "120–140 г")
        self.assertIsNone(self.m.fmt_range(None, "г"))


if __name__ == "__main__":
    unittest.main()
