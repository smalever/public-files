import importlib.util
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCRIPTS = Path("/opt/data/scripts")
MSK = timezone(timedelta(hours=3))


def load(name):
    spec = importlib.util.spec_from_file_location(name + "_t", SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class DaySummaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load("health_reminder_evening")
        cls.now = datetime(2026, 9, 22, 20, 0, tzinfo=MSK)

    def test_all_taken_suppresses_agent(self):
        self.assertEqual(self.module.build_evening_result(now=self.now, pending={}), {"wakeAgent": False})
        self.assertEqual(self.module.build_evening_result(now=self.now, pending={"evening": []}), {"wakeAgent": False})

    def test_pending_returns_payload(self):
        pending = {"morning": [{"name": "Цинк", "dosage": "1", "food": "after"}], "evening": []}
        result = self.module.build_evening_result(now=self.now, pending=pending)
        self.assertEqual(result["mode"], "day_summary")
        self.assertEqual(result["date"], "2026-09-22")
        self.assertEqual(list(result["pending"]), ["morning"])

    def test_wake_agent_gate_is_serialized_on_one_complete_line(self):
        output = self.module.serialize_result({"wakeAgent": False})
        self.assertEqual(output, '{"wakeAgent": false}')


class PremealTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load("health_reminder_premeal")
        cls.now = datetime(2026, 9, 22, 12, 0, tzinfo=MSK)

    def test_nothing_to_remind_is_silent(self):
        r = self.module.build_premeal_result(slot="lunch", now=self.now, medications=[], extra=[])
        self.assertEqual(r, {"wakeAgent": False})

    def test_before_meal_meds_wake_agent(self):
        r = self.module.build_premeal_result(slot="evening", now=self.now,
                                             medications=[{"name": "X", "dosage": "1"}])
        self.assertEqual(r["mode"], "evening_premeal")
        self.assertEqual(r["before_meal_meds"], [{"name": "X", "dosage": "1"}])

    def test_other_reminders_wake_agent(self):
        r = self.module.build_premeal_result(slot="lunch", now=self.now, medications=[], extra=["y"])
        self.assertEqual(r["mode"], "lunch_premeal")
        self.assertEqual(r["reminders"], ["y"])


if __name__ == "__main__":
    unittest.main()
