import importlib.util
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path


HELPER_PATH = Path("/opt/data/scripts/medication_reminder_helper.py")
if not HELPER_PATH.exists():
    HELPER_PATH = Path(__file__).resolve().parents[1] / "medication_reminder_helper.py"


def load_helper():
    spec = importlib.util.spec_from_file_location("medication_reminder_helper_test", HELPER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class MedicationReminderHelperTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.db_path = root / "health.db"
        self.note_path = root / "Схема препаратов.md"
        self.note_path.write_text(
            """---
medications:
  evening:
    - name: "Тестовый препарат"
      dosage: "1 таблетка"
      note: "во время еды"
---
""",
            encoding="utf-8",
        )
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.executescript(
                """
                CREATE TABLE medications (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    dosage TEXT,
                    frequency TEXT,
                    timing TEXT,
                    when_text TEXT,
                    note TEXT,
                    active BOOLEAN DEFAULT 1,
                    added DATE
                );
                CREATE TABLE med_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date TEXT NOT NULL,
                    ts TEXT NOT NULL,
                    medication TEXT NOT NULL,
                    dosage TEXT,
                    method TEXT,
                    med_id TEXT
                );
                INSERT INTO medications (id, name, dosage, when_text, active)
                VALUES ('med-test', 'Тестовый препарат', '1 таблетка', 'evening', 1);
                """
            )

        self.helper = load_helper()
        self.helper.HEALTH_DB_PATH = self.db_path
        self.helper.MEDS_NOTE_PATH = self.note_path
        with closing(sqlite3.connect(self.db_path)) as conn:
            self.helper.init_db(conn)

    def tearDown(self):
        self.tmp.cleanup()

    def test_repeated_slot_confirmation_does_not_duplicate_med_log(self):
        first = self.helper.mark_slot_taken("evening", "2026-09-22", "first")
        second = self.helper.mark_slot_taken("evening", "2026-09-22", "repeat")

        with closing(sqlite3.connect(self.db_path)) as conn:
            slot_count = conn.execute(
                "SELECT COUNT(*) FROM medication_slot_log WHERE date=? AND slot=?",
                ("2026-09-22", "evening"),
            ).fetchone()[0]
            med_count = conn.execute(
                "SELECT COUNT(*) FROM med_log WHERE date=? AND method=?",
                ("2026-09-22", "evening"),
            ).fetchone()[0]
            confirmed_text = conn.execute(
                "SELECT confirmed_text FROM medication_slot_log WHERE date=? AND slot=?",
                ("2026-09-22", "evening"),
            ).fetchone()[0]

        self.assertTrue(first)
        self.assertFalse(second)
        self.assertEqual(slot_count, 1)
        self.assertEqual(med_count, 1)
        self.assertEqual(confirmed_text, "first")

    def test_confirmation_payload_reports_already_closed_slot(self):
        payload = self.helper.build_slot_confirmation_payload(
            "evening", "2026-09-22", created=False
        )
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["type"], "meds_already_taken")
        self.assertEqual(payload["slot"], "evening")
        self.assertIn("уже были отмечены", payload["message"])

    def test_explicit_today_uses_real_msk_timestamp(self):
        now = datetime(2026, 9, 22, 20, 37, 45, tzinfo=timezone(timedelta(hours=3)))
        self.helper.mark_slot_taken("evening", "2026-09-22", "now", now=now)

        with closing(sqlite3.connect(self.db_path)) as conn:
            ts = conn.execute(
                "SELECT ts FROM medication_slot_log WHERE date=? AND slot=?",
                ("2026-09-22", "evening"),
            ).fetchone()[0]

        self.assertEqual(ts, "22.09.2026 20:37:45")

    def test_cyrillic_name_matching_preserves_real_medication_id(self):
        self.note_path.write_text(
            """---
medications:
  evening:
    - name: "тестовый препарат"
      dosage: "1 таблетка"
---
""",
            encoding="utf-8",
        )
        self.helper.mark_slot_taken("evening", "2026-09-22", "casefold")

        with closing(sqlite3.connect(self.db_path)) as conn:
            med_id = conn.execute(
                "SELECT med_id FROM med_log WHERE date=? AND method=?",
                ("2026-09-22", "evening"),
            ).fetchone()[0]

        self.assertEqual(med_id, "med-test")

    def test_auto_slot_in_morning_returns_morning_even_when_already_taken(self):
        now = datetime(2026, 9, 22, 10, 0, tzinfo=timezone(timedelta(hours=3)))
        slot = self.helper.determine_confirmation_slot(
            now,
            morning_taken=True,
            lunch_taken=False,
            active_lunch=[],
        )
        self.assertEqual(slot, "morning")


class PerDrugIntakeTests(unittest.TestCase):
    NOTE = """---
medications:
  morning:
    - name: "Ко-Пренесса"
      dosage: "1 таб"
      food: "before"
    - name: "Витамин D3 + K2 (Бренд)"
      dosage: "2 капс"
      food: "with"
    - name: "Цинк (цинка пиколинат)"
      dosage: "1 капс"
      note: "после завтрака"
  evening:
    - name: "Метформин (Глюкофаж)"
      dosage: "1000 мг"
      food: "with"
    - name: "Магния цитрат (+ B6)"
      dosage: "3 капс"
      food: "with"
---
"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.db_path = root / "health.db"
        note = root / "Схема препаратов.md"
        note.write_text(self.NOTE, encoding="utf-8")
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.executescript("""
                CREATE TABLE medications (id TEXT PRIMARY KEY, name TEXT NOT NULL, dosage TEXT,
                    frequency TEXT, timing TEXT, when_text TEXT, note TEXT, food TEXT, active BOOLEAN DEFAULT 1, added DATE);
                CREATE TABLE med_log (id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT NOT NULL, ts TEXT NOT NULL,
                    medication TEXT NOT NULL, dosage TEXT, method TEXT, med_id TEXT);
            """)
        self.h = load_helper()
        self.h.HEALTH_DB_PATH = self.db_path
        self.h.MEDS_NOTE_PATH = note
        with closing(sqlite3.connect(self.db_path)) as conn:
            self.h.init_db(conn)
        self.d = "2026-09-22"
        self.msk = timezone(timedelta(hours=3))

    def tearDown(self):
        self.tmp.cleanup()

    def test_food_field_and_note_fallback(self):
        meds = {m["name"]: m["food"] for m in self.h.get_active_meds_for_slot("morning", self.d)}
        self.assertEqual(meds["Ко-Пренесса"], "before")
        self.assertEqual(meds["Цинк (цинка пиколинат)"], "after")  # из note

    def test_take_only_named(self):
        now = datetime(2026, 9, 22, 7, 30, tzinfo=self.msk)
        r = self.h.take_meds(self.d, names=["ко-пренессу"], now=now)
        self.assertEqual(r["marked"], ["Ко-Пренесса"])
        self.assertFalse(self.h.is_slot_taken("morning", self.d))
        pending = [m["name"] for m in self.h.get_pending_meds("morning", self.d)]
        self.assertNotIn("Ко-Пренесса", pending)
        self.assertEqual(len(pending), 2)

    def test_take_all_closes_slot_and_repeat_is_idempotent(self):
        now = datetime(2026, 9, 22, 9, 0, tzinfo=self.msk)
        r1 = self.h.take_meds(self.d, now=now)
        r2 = self.h.take_meds(self.d, now=now)
        self.assertEqual(len(r1["marked"]), 3)
        self.assertEqual(r2["type"], "meds_already_taken")
        self.assertTrue(self.h.is_slot_taken("morning", self.d))
        with closing(sqlite3.connect(self.db_path)) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM med_log").fetchone()[0], 3)

    def test_named_in_evening_finds_morning_leftover(self):
        now = datetime(2026, 9, 22, 20, 0, tzinfo=self.msk)
        r = self.h.take_meds(self.d, names=["цинк", "магний"], now=now)
        self.assertEqual(sorted(r["slots"]), ["evening", "morning"])
        self.assertIn("Цинк (цинка пиколинат)", r["marked"])
        self.assertIn("Магния цитрат (+ B6)", r["marked"])

    def test_unknown_name_reported(self):
        now = datetime(2026, 9, 22, 9, 0, tzinfo=self.msk)
        r = self.h.take_meds(self.d, names=["аспирин"], now=now)
        self.assertEqual(r["type"], "meds_not_found")
        self.assertEqual(r["unknown"], ["аспирин"])

    def test_meal_reminder_skips_before_meds_and_mentions_them(self):
        now = datetime(2026, 9, 22, 8, 30, tzinfo=self.msk)
        slot, meds = self.h.determine_reminder(self.d, now, 1)
        text = self.h.format_reminder(slot, meds, {"kcal": 400, "protein": 20, "fat": 15, "carbs": 30})
        self.assertNotIn("- Ко-Пренесса", text)
        self.assertIn("До еды, ещё не отмечено: Ко-Пренесса", text)
        self.h.take_meds(self.d, names=["Ко-Пренесса"], now=now)
        slot, meds = self.h.determine_reminder(self.d, now, 1)
        text = self.h.format_reminder(slot, meds, {"kcal": 400, "protein": 20, "fat": 15, "carbs": 30})
        self.assertNotIn("Ко-Пренесса", text)

    def test_no_meal_reminder_when_only_before_left(self):
        now = datetime(2026, 9, 22, 8, 30, tzinfo=self.msk)
        self.h.take_meds(self.d, names=["витамин d3", "цинк"], now=now)
        self.assertIsNone(self.h.determine_reminder(self.d, now, 1))

    def _with_lunch_note(self):
        note = self.h.MEDS_NOTE_PATH
        note.write_text(
            self.NOTE.replace(
                "  evening:",
                '  lunch:\n    - name: "Дневной тест"\n      dosage: "1 таб"\n      food: "with"\n  evening:',
                1,
            ),
            encoding="utf-8",
        )

    def _names(self, hour, meals=1):
        now = datetime(2026, 9, 22, hour, 30, tzinfo=self.msk)
        res = self.h.determine_reminder(self.d, now, meals)
        if res is None:
            return None, []
        slot, meds = res
        return slot, [m["name"] for m in meds]

    def test_reminder_windows_by_time_of_day(self):
        self._with_lunch_note()
        slot, names = self._names(10)
        self.assertEqual(slot, "morning")
        self.assertNotIn("Дневной тест", names)
        slot, names = self._names(13)  # 12-16: утро + день вместе
        self.assertEqual(slot, "morning_lunch")
        self.assertIn("Цинк (цинка пиколинат)", names)
        self.assertIn("Дневной тест", names)
        slot, names = self._names(17)  # 16-18: утро уже не напоминаем
        self.assertEqual(slot, "lunch")
        self.assertEqual([n for n in names if n != "Ко-Пренесса"], ["Дневной тест"])
        slot, names = self._names(19)  # после 18: дневные и вечерние вместе, утренние уже нет
        self.assertEqual(slot, "lunch_evening")
        self.assertIn("Метформин (Глюкофаж)", names)
        self.assertIn("Дневной тест", names)
        self.assertNotIn("Цинк (цинка пиколинат)", names)

    def test_reminder_does_not_depend_on_meals_count(self):
        # Регресс: после 12:00 при 2+ приёмах за день утренние терялись.
        for meals in (1, 2, 5):
            slot, names = self._names(13, meals)
            self.assertIn("Витамин D3 + K2 (Бренд)", names, meals)

    def test_boundaries_16_and_18(self):
        self._with_lunch_note()
        self.assertEqual(self._names(15)[0], "morning_lunch")
        self.assertEqual(self._names(16)[0], "lunch")
        self.assertEqual(self._names(17)[0], "lunch")
        self.assertEqual(self._names(18)[0], "lunch_evening")
        self.assertEqual(self._names(23)[0], "lunch_evening")

    def test_after_18_without_lunch_meds_is_plain_evening(self):
        # Если дневных препаратов нет или все приняты — заголовок просто «вечерние».
        self.assertEqual(self._names(19)[0], "evening")
        self._with_lunch_note()
        self.h.take_meds(self.d, names=["Дневной тест"], now=self._at(19))
        self.assertEqual(self._names(19)[0], "evening")

    def test_taken_morning_med_not_reminded_again(self):
        now = datetime(2026, 9, 22, 13, 0, tzinfo=self.msk)
        self.h.take_meds(self.d, names=["витамин d3"], now=now)
        _, names = self._names(13)
        self.assertNotIn("Витамин D3 + K2 (Бренд)", names)
        self.assertIn("Цинк (цинка пиколинат)", names)

    def _at(self, hour):
        return datetime(2026, 9, 22, hour, 30, tzinfo=self.msk)

    def _taken(self):
        with closing(sqlite3.connect(self.db_path)) as conn:
            return sorted(r[0] for r in conn.execute(
                "SELECT medication FROM med_intake_log WHERE date = ?", (self.d,)))

    def test_take_all_follows_reminder_windows(self):
        self._with_lunch_note()
        r = self.h.take_meds(self.d, now=self._at(13))  # 12-16: утро + день
        self.assertEqual(sorted(r["slots"]), ["lunch", "morning"])
        self.assertIn("Дневной тест", r["marked"])
        self.assertEqual(len(r["marked"]), 4)
        self.assertEqual(self._taken().count("Метформин (Глюкофаж)"), 0)

    def test_take_all_after_16_skips_missed_morning(self):
        self._with_lunch_note()
        r = self.h.take_meds(self.d, now=self._at(17))  # 16-18: только день
        self.assertEqual(r["marked"], ["Дневной тест"])
        self.assertEqual(self._taken(), ["Дневной тест"])

    def test_take_all_after_18_lunch_and_evening_together(self):
        self._with_lunch_note()
        r = self.h.take_meds(self.d, now=self._at(19))
        self.assertEqual(sorted(r["slots"]), ["evening", "lunch"])
        self.assertEqual(
            sorted(r["marked"]),
            ["Дневной тест", "Магния цитрат (+ B6)", "Метформин (Глюкофаж)"],
        )
        self.assertNotIn("Витамин D3 + K2 (Бренд)", self._taken())

    def test_take_all_after_18_only_evening_when_no_lunch_meds(self):
        r = self.h.take_meds(self.d, now=self._at(19))
        self.assertEqual(r["slots"], ["evening"])
        self.assertEqual(sorted(r["marked"]),
                         ["Магния цитрат (+ B6)", "Метформин (Глюкофаж)"])
        self.assertNotIn("Витамин D3 + K2 (Бренд)", self._taken())

    def test_late_reminder_is_not_lost_after_18_for_missed_lunch(self):
        self._with_lunch_note()
        text = self.h.format_reminder(*self.h.determine_reminder(self.d, self._at(21), 3))
        self.assertIn("дневные и вечерние", text)
        self.assertIn("Дневной тест", text)
        self.assertIn("Метформин (Глюкофаж)", text)

    def test_take_all_empty_window_is_not_an_error(self):
        r = self.h.take_meds(self.d, now=self._at(17))  # слот lunch пуст
        self.assertEqual(r["marked"], [])
        self.assertEqual(r["type"], "meds_already_taken")
        self.assertIn("по названию", r["message"])
        self.assertEqual(self._taken(), [])

    def test_named_med_is_recorded_at_any_hour(self):
        for hour in (9, 13, 17, 19, 23):
            with self.subTest(hour=hour):
                self.tearDown()
                self.setUp()  # чистая БД на каждый час
                r = self.h.take_meds(self.d, names=["Ко-Пренесса"], now=self._at(hour))
                self.assertEqual(r["marked"], ["Ко-Пренесса"])
                self.assertEqual(self._taken(), ["Ко-Пренесса"])
                self.assertEqual(r["slots"], ["morning"])
                self.tearDown()

    def test_named_med_late_does_not_touch_other_meds(self):
        r = self.h.take_meds(self.d, names=["пренесса"], now=self._at(21))
        self.assertEqual(self._taken(), ["Ко-Пренесса"])
        self.assertEqual(r["type"], "meds_taken")

    def test_named_med_repeat_is_idempotent(self):
        self.h.take_meds(self.d, names=["Ко-Пренесса"], now=self._at(20))
        r = self.h.take_meds(self.d, names=["Ко-Пренесса"], now=self._at(20))
        self.assertEqual(r["type"], "meds_already_taken")
        self.assertEqual(self._taken(), ["Ко-Пренесса"])

    def test_catalog_lists_all_meds_with_schedule(self):
        cat = {e["name"]: e for e in self.h.get_medication_catalog()}
        self.assertIn("Ко-Пренесса", cat)
        self.assertEqual(cat["Ко-Пренесса"]["schedule"][0]["slot"], "morning")
        self.assertEqual(cat["Ко-Пренесса"]["schedule"][0]["food"], "before")
        self.assertEqual(cat["Цинк (цинка пиколинат)"]["schedule"][0]["food"], "after")  # из note
        self.assertIn("- Ко-Пренесса: утро (1 таб, до еды)", self.h.format_catalog_text())

    def test_catalog_merges_one_med_from_several_slots_and_as_needed(self):
        note = self.NOTE.replace(
            '  evening:\n',
            '  evening:\n    - name: "Цинк (цинка пиколинат)"\n      dosage: "1 капс"\n'
            '      time: "20:00"\n      food: "with"\n',
            1,
        )
        # as_needed — в конец frontmatter, перед закрывающим ---
        head, _, tail = note.rpartition("---")
        note = head + ('  as_needed:\n    - name: "Ибупрофен"\n      dosage: "25 мг"\n'
                       '      condition: "головная боль"\n---') + tail
        self.h.MEDS_NOTE_PATH.write_text(note, encoding="utf-8")
        cat = {e["name"]: e for e in self.h.get_medication_catalog()}
        self.assertEqual([s["slot"] for s in cat["Цинк (цинка пиколинат)"]["schedule"]],
                         ["morning", "evening"])
        self.assertEqual(cat["Цинк (цинка пиколинат)"]["schedule"][1]["time"], "20:00")
        cap = cat["Ибупрофен"]["schedule"][0]
        self.assertEqual((cap["slot"], cap["food"], cap["condition"]), ("as_needed", None, "головная боль"))
        text = self.h.format_catalog_text()
        self.assertIn("вечер 20:00", text)
        self.assertIn("по потребности (25 мг, головная боль)", text)

    def test_resolve_typos_and_autocorrect(self):
        for q in ("ко пренесса", "копренесса", "ко-пренеса", "по пренессу", "пренессу"):
            with self.subTest(q=q):
                top = self.h.resolve_medication_name(q)[0]
                self.assertEqual(top["name"], "Ко-Пренесса")
                self.assertTrue(top["confident"])
        self.assertEqual(self.h.resolve_medication_name("глюкафаж")[0]["name"], "Метформин (Глюкофаж)")
        self.assertEqual(self.h.resolve_medication_name("пиколинат")[0]["name"], "Цинк (цинка пиколинат)")

    def test_resolve_unknown_is_not_confident(self):
        for c in self.h.resolve_medication_name("аспирин"):
            self.assertFalse(c["confident"])
        self.assertEqual(self.h.resolve_medication_name("я"), [])

    def test_resolve_does_not_write_anything(self):
        self.h.get_health_conn().close()  # создать таблицы, чтобы было что проверять
        self.h.resolve_medication_name("копренесса")
        self.h.get_medication_catalog()
        self.assertEqual(self._taken(), [])

    def test_take_misspelled_name_gives_suggestion_and_records_nothing(self):
        # take_meds сам по нечёткому совпадению НЕ пишет: подсказка уходит боту,
        # бот переспрашивает или повторяет вызов с точным названием.
        r = self.h.take_meds(self.d, names=["копренеса"], now=self._at(10))
        self.assertEqual(r["type"], "meds_not_found")
        self.assertEqual(r["marked"], [])
        sug = r["suggestions"]["копренеса"][0]
        self.assertEqual(sug["name"], "Ко-Пренесса")
        self.assertTrue(sug["confident"])
        self.assertEqual(self._taken(), [])
        # повтор с исправленным названием — записывается
        r2 = self.h.take_meds(self.d, names=[sug["name"]], now=self._at(10))
        self.assertEqual(r2["marked"], ["Ко-Пренесса"])
        self.assertEqual(self._taken(), ["Ко-Пренесса"])

    def test_take_exact_name_has_no_suggestions_key(self):
        r = self.h.take_meds(self.d, names=["Ко-Пренесса"], now=self._at(10))
        self.assertNotIn("suggestions", r)

    def test_focus_lines_follow_scheme(self):
        note = self.NOTE.replace(
            "  evening:\n",
            '  wednesday_morning:\n    - name: "Витамин B12"\n      dosage: "1 ампула"\n'
            '      food: "before"\n      note: "внутримышечно, 1 раз в неделю"\n  evening:\n', 1,
        ).replace('dosage: "1000 мг"\n', 'dosage: "1000 мг"\n      time: "20:00"\n', 1
        ).replace('dosage: "3 капсулы"\n', 'dosage: "3 капсулы"\n      time: "20:00"\n', 1)
        self.h.MEDS_NOTE_PATH.write_text(note, encoding="utf-8")
        lines = self.h.format_week_focus_lines()
        self.assertEqual(lines[0], "- [ ] **Среда (утром, до еды):** Витамин B12 — 1 ампула (внутримышечно, 1 раз в неделю).")
        self.assertEqual(
            lines[1],
            "- [ ] Ежедневно, утром: до еды — Ко-Пренесса; во время еды — Витамин D3 + K2; после еды — Цинк.",
        )
        self.assertEqual(
            lines[2],
            "- [ ] Ежедневно, вечером (20:00): во время еды — Метформин, Магния цитрат.",
        )
        self.assertEqual(len(lines), 3)  # пустой lunch не выводится, as_needed нет
        self.assertNotIn("08:00", "\n".join(lines))

    def test_sync_timing_comes_only_from_scheme(self):
        import json as _json
        hs_path = Path("/opt/data/scripts/health_sync.py")
        if not hs_path.exists():
            hs_path = Path(__file__).resolve().parents[1] / "health_sync.py"
        spec = importlib.util.spec_from_file_location("health_sync_test", str(hs_path))
        hs = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(hs)
        note = self.NOTE.replace('dosage: "1000 мг"\n', 'dosage: "1000 мг"\n      time: "20:00"\n', 1)
        self.h.MEDS_NOTE_PATH.write_text(note, encoding="utf-8")
        hs.MEDS_NOTE = self.h.MEDS_NOTE_PATH
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            _, warn = hs.sync_medications(conn)
            self.assertIsNone(warn)
            timing = {r["name"]: _json.loads(r["timing"]) for r in conn.execute("SELECT name, timing FROM medications")}
        self.assertEqual(timing["Ко-Пренесса"], [])          # времени в схеме нет — не выдумываем 08:00
        self.assertEqual(timing["Метформин (Глюкофаж)"], ["20:00"])
        for times in timing.values():
            self.assertNotIn("08:00", times)

    def test_legacy_slot_log_counts_as_all_taken(self):
        self.h.get_health_conn().close()
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute("INSERT INTO medication_slot_log (date, slot, ts) VALUES (?, 'evening', 'x')", (self.d,))
            conn.commit()
        self.assertTrue(self.h.is_slot_taken("evening", self.d))

    def test_weekly_adherence_counts_per_drug(self):
        now = datetime(2026, 9, 22, 9, 0, tzinfo=self.msk)
        self.h.take_meds(self.d, now=now)
        self.h.take_meds(self.d, names=["глюкофаж"], now=now.replace(hour=20))
        a = self.h.calculate_weekly_adherence(self.d, self.d)
        self.assertEqual(a["total_scheduled"], 5)
        self.assertEqual(a["total_taken"], 4)
        self.assertEqual(a["missed_list"][0]["medications"], ["Магния цитрат (+ B6)"])


if __name__ == "__main__":
    unittest.main()
