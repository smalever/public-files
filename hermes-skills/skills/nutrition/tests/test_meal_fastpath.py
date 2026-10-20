"""CLI integration tests: copied source, synthetic SQLite, no personal helpers/data."""
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[1] / 'scripts' / 'nutrition.py'
SCRATCH = Path(os.environ.get('TMPDIR', '/tmp'))


class MealCLI(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='nutrition-fastpath-', dir=SCRATCH)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db = self.root / 'synthetic.db'
        code = SOURCE.read_text().replace('/opt/data/personal/nutrition/nutrition.db', str(self.db))
        code = code.replace('/opt/data/scripts', str(self.root / 'helpers'))
        self.script = self.root / 'nutrition.py'
        self.script.write_text(code)
        (self.root / 'helpers').mkdir()
        self.helper = self.root / 'helpers' / 'medication_reminder_helper.py'
        self.helper.write_text('def determine_reminder(*args): return ("lunch", [])\ndef format_reminder(slot, meds, meal_info):\n    return "REMINDER:" + __import__("json").dumps(meal_info)\n')
        (self.root / 'sitecustomize.py').write_text('''import sys, os
root = os.environ['HOME']
def guard(event, args):
    if event.startswith('socket.'):
        raise RuntimeError('network blocked')
    if event == 'open' and isinstance(args[0], (str, bytes)):
        path = os.fsdecode(args[0])
        if path.startswith(('/opt/data/personal', '/opt/data/scripts', '/notes')):
            raise RuntimeError('production access blocked: ' + path)
    if event == 'sqlite3.connect':
        uri = str(args[0])
        if not uri.startswith('file://' + root + '/'):
            raise RuntimeError('non-synthetic DB blocked')
        with open(root + '/connections.log', 'a') as f: f.write(uri + '\\n')
sys.addaudithook(guard)
''')
        self.env = dict(os.environ, HOME=str(self.root), TMPDIR=str(self.root),
                        XDG_CACHE_HOME=str(self.root / 'cache'), PYTHONDONTWRITEBYTECODE='1',
                        PYTHONPATH=str(self.root), PYTHONPYCACHEPREFIX=str(self.root / 'pycache'))
        with closing(sqlite3.connect(self.db)) as c:
            c.executescript('''CREATE TABLE food_db(id INTEGER PRIMARY KEY, product TEXT, normalized_name TEXT, kcal REAL, protein REAL, fat REAL, carbs REAL);
CREATE TABLE food_aliases(alias TEXT, product TEXT);
CREATE TABLE nutrition(id INTEGER PRIMARY KEY, date TEXT, ts TEXT, meal TEXT, total_kcal REAL, total_protein REAL, total_fat REAL, total_carbs REAL);
CREATE TABLE nutrition_items(id INTEGER PRIMARY KEY, nutrition_id INTEGER, product TEXT, grams REAL, kcal REAL, protein REAL, fat REAL, carbs REAL);
INSERT INTO food_db VALUES(1,'rice','rice',100,2,1,20),(2,'milk 2,5%','milk 2,5%',50,3,2.5,4);
INSERT INTO food_aliases VALUES('grains','rice');''')

    def cli(self, items='rice 100г', meal='snack', extra=(), machine=True):
        cmd = [sys.executable, str(self.script), 'meal', '--meal', meal, '--items', items]
        if machine:
            cmd.append('--json')
        result = subprocess.run(cmd + list(extra), cwd=self.root, env=self.env,
                                capture_output=True, text=True, timeout=15)
        if machine:
            self.assertTrue(result.stdout.strip(), result.stderr)
            try:
                payload = json.loads(result.stdout)
            except ValueError:
                self.fail(f'Not one JSON object: {result.stdout!r}; {result.stderr}')
            return payload, result
        return result

    def counts(self):
        with closing(sqlite3.connect(self.db)) as c:
            return tuple(c.execute('SELECT count(*) FROM ' + table).fetchone()[0]
                         for table in ('nutrition', 'nutrition_items'))

    def test_decimal_comma_name_and_weight(self):
        p, _ = self.cli('milk 2,5% 12,5г, grains 25г')
        self.assertTrue(p['verified'])
        self.assertEqual([i['grams'] for i in p['items']], [12.5, 25])
        self.assertEqual(p['items'][0]['product'], 'milk 2,5%')

    def test_readback_rejects_parent_and_item_corruption(self):
        triggers = [
            "UPDATE nutrition SET date='wrong' WHERE id=NEW.id;",
            "UPDATE nutrition SET meal='dinner' WHERE id=NEW.id;",
            "UPDATE nutrition SET total_kcal=999 WHERE id=NEW.id;",
        ]
        for action in triggers:
            with self.subTest(action=action):
                with closing(sqlite3.connect(self.db)) as c:
                    c.executescript('DROP TRIGGER IF EXISTS corrupt; CREATE TRIGGER corrupt AFTER INSERT ON nutrition BEGIN ' + action + ' END;')
                p, r = self.cli()
                self.assertEqual((p['status'], p['saved'], p['verified']), ('error', True, False))
                self.assertEqual(p['type'], 'verification_failed')
                self.assertNotIn('записан', r.stdout)
        with closing(sqlite3.connect(self.db)) as c:
            c.executescript('DROP TRIGGER corrupt; CREATE TRIGGER corrupt AFTER INSERT ON nutrition_items BEGIN UPDATE nutrition_items SET grams=999 WHERE id=NEW.id; END;')
        p, _ = self.cli()
        self.assertEqual((p['status'], p['saved'], p['verified']), ('error', True, False))
        self.assertEqual(self.counts(), (4, 4))  # no retry/extra insertion

    def test_readonly_connection_failure_reports_saved(self):
        guard = self.root / 'sitecustomize.py'
        guard.write_text(guard.read_text().replace("uri = str(args[0])", "uri = str(args[0])\n        if uri.endswith('?mode=ro'): raise RuntimeError('synthetic readback failure')"))
        p, _ = self.cli()
        self.assertEqual((p['status'], p['saved'], p['verified']), ('error', True, False))
        self.assertIn('synthetic readback failure', p['error'])
        self.assertEqual(self.counts(), (1, 1))

    def test_repeated_snacks_keep_sixty_minute_reminder_totals(self):
        self.assertTrue(self.cli()[0]['verified'])
        p, _ = self.cli('rice 50г')
        reminder = json.loads(p['medication_reminder'].removeprefix('REMINDER:'))
        self.assertEqual(reminder['kcal'], 150)
        self.assertEqual(reminder['meals_in_window'], 2)
        self.assertEqual(reminder['window_minutes'], 60)
        self.assertEqual(self.counts(), (2, 2))

    def test_helper_import_and_runtime_failures_preserve_verified_save(self):
        for body, warning in [("raise ImportError('synthetic helper failure')", 'Не удалось загрузить'),
                              ("def determine_reminder(*args): raise RuntimeError('synthetic helper failure')", 'Не удалось проверить')]:
            self.helper.write_text(body)
            p, r = self.cli()
            self.assertTrue(p['saved'])
            self.assertTrue(p['verified'])
            self.assertIsNone(p['medication_reminder'])
            self.assertIn(warning, r.stderr)
        self.assertEqual(self.counts(), (2, 2))

    def test_similar_and_fuzzy_never_save(self):
        for text in ('rixe 100г', 'milk 100г'):
            p, _ = self.cli(text)
            self.assertEqual(p['type'], 'fuzzy_match')
            self.assertFalse(p['saved'])
        self.assertEqual(self.counts(), (0, 0))

    def test_legacy_text_output(self):
        r = self.cli(machine=False)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Прием пищи 'snack' записан", r.stdout)
        self.assertIn('REMINDER:', r.stdout)

    def test_main_meals_require_explicit_additional(self):
        for meal in ('breakfast', 'lunch', 'dinner'):
            with self.subTest(meal=meal):
                self.assertEqual(self.cli(meal=meal)[0]['status'], 'ok')
                before = self.counts()
                p, _ = self.cli(meal=meal)
                self.assertEqual(p.get('type'), 'additional_meal_confirmation')
                self.assertFalse(p['saved'])
                self.assertEqual(self.counts(), before)
                p, _ = self.cli(meal=meal, extra=('--allow-additional',))
                self.assertTrue(p['verified'])

    def test_entire_input_must_be_valid(self):
        invalid = ['rice 100г garbage', 'rice 100г,', 'rice 100г, milk 2,5% nope',
                   'rice 0г', 'rice -2г', 'rice nanг', 'rice infг',
                   'rice ' + '9' * 400 + 'г', ';rice 10г', '', 'rice 100г milk 20г',
                   'rice 100г,, milk 2,5% 20г']
        for text in invalid:
            with self.subTest(text=text):
                p, _ = self.cli(text)
                self.assertEqual(p.get('type'), 'bad_items_format')
                self.assertIs(p.get('saved'), False)
                self.assertEqual(self.counts(), (0, 0))

    def test_multiple_unresolved_collected_without_partial_save(self):
        with closing(sqlite3.connect(self.db)) as c:
            c.execute("INSERT INTO food_db VALUES(3,'rice variant','ambiguous',80,1,1,10)")
            c.execute("INSERT INTO food_db VALUES(4,'rice other','ambiguous',90,1,1,10)")
            c.commit()
        p, _ = self.cli('rice 100г, ambiguous 10г, rixe 20г, xyzxyzxyz 30г')
        self.assertFalse(p['saved'])
        self.assertEqual([x['product'] for x in p['problems']], ['ambiguous', 'rixe', 'xyzxyzxyz'])
        self.assertEqual([x['type'] for x in p['problems']], ['multiple_matches', 'fuzzy_match', 'product_not_found'])
        self.assertEqual(self.counts(), (0, 0))

    def test_exact_alias_verified_receipt(self):
        p, r = self.cli('rice 100г, grains 50г')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((p['status'], p['saved'], p['verified']), ('ok', True, True))
        self.assertEqual(p['totals'], dict(kcal=150, protein=3, fat=1.5, carbs=30))
        self.assertEqual([i['product'] for i in p['items']], ['rice', 'rice'])
        for key in ('id', 'date', 'ts', 'meal', 'medication_reminder'):
            self.assertIn(key, p)
        self.assertEqual(self.counts(), (1, 2))
        connections = (self.root / 'connections.log').read_text().splitlines()
        self.assertTrue(connections[0].endswith('?mode=rw'))
        self.assertTrue(any(u.endswith('?mode=ro') for u in connections[1:]))


if __name__ == '__main__':
    unittest.main()
