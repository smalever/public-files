"""Regression tests for the morning brief payload contract.

Medication reminders are owned by meal logging (`nutrition.py meal`) and the
evening pre-check. The morning brief stays measurements + nutrition only, and
must not re-add the `sync_medications()` call the evening script already owns.
"""

import json
import subprocess
import unittest
from pathlib import Path


SCRIPT_PATH = Path("/opt/data/scripts/health_reminder_morning.py")
PYTHON = "/opt/data/home/.venv/bin/python"
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
        cls.proc = subprocess.run(
            [PYTHON, str(SCRIPT_PATH)],
            capture_output=True,
            text=True,
            timeout=120,
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
