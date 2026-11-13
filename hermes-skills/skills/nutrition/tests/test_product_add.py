"""Product additions use guarded synthetic databases, never personal data."""
from contextlib import closing
import json
import sqlite3
import subprocess
import sys
import unittest
import test_meal_fastpath


class ProductAddCLI(unittest.TestCase):
    def setUp(self):
        self.fixture = test_meal_fastpath.MealCLI()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.db = self.fixture.db
        self.fixture.helper.write_text("raise RuntimeError('unexpected medication import')\n")

    def cli(self, name='Бисквит творожный', values=('310.2', '3.2', '13', '43')):
        args = ['product-add', '--name', name, '--json']
        for field, value in zip(('kcal', 'protein', 'fat', 'carbs'), values):
            args.extend(['--' + field, value])
        r = subprocess.run([sys.executable, str(self.fixture.script), *args],
                           cwd=self.fixture.root, env=self.fixture.env,
                           capture_output=True, text=True, timeout=15)
        self.assertTrue(r.stdout.strip(), r.stderr)
        self.assertEqual(r.stderr, '')
        return json.loads(r.stdout), r

    def snapshot(self):
        with closing(sqlite3.connect(self.db)) as c:
            return list(c.iterdump())

    def test_add_verified_product_without_changing_other_records(self):
        before = self.snapshot()
        p, r = self.cli()
        self.assertEqual(r.returncode, 0, p)
        self.assertEqual((p['type'], p['saved'], p['verified']), ('created', True, True))
        self.assertEqual(p['per_grams'], 100)
        expected = dict(id=3, product='Бисквит творожный', normalized_name='бисквит творожный',
                        kcal=310.2, protein=3.2, fat=13.0, carbs=43.0)
        self.assertEqual(p['product'], expected)
        after = self.snapshot()
        additions = [line for line in after if line not in before]
        self.assertEqual(len(additions), 1)
        self.assertTrue(additions[0].startswith('INSERT INTO "food_db"'))
        self.assertTrue(all(line in after for line in before))
        self.assertEqual(self.fixture.counts(), (0, 0))
        self.assertFalse(list(self.fixture.root.glob('*.sqlite')))
        self.assertTrue((self.fixture.root/'connections.log').read_text().splitlines()[-1].endswith('?mode=ro'))

    def test_normalized_duplicate_is_idempotent_and_different_values_conflict(self):
        p, _ = self.cli('  СВЁКЛА   моя  ')
        self.assertTrue(p['verified'])
        before = self.snapshot()
        p, r = self.cli('свекла моя')
        self.assertEqual(r.returncode, 0)
        self.assertEqual((p['type'], p['saved'], p['verified']), ('already_exists', False, True))
        p, r = self.cli('свекла моя', ('311', '3.2', '13', '43'))
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(p['type'], 'product_conflict')
        self.assertEqual(self.snapshot(), before)

    def test_invalid_values_and_names_never_write(self):
        before = self.snapshot()
        for values in [('nan','3','1','2'), ('inf','3','1','2'), ('-1','3','1','2'),
                       ('1001','3','1','2'), ('300','101','0','0'),
                       ('300','50','50','0.0000005'), ('300','3','-1','2')]:
            with self.subTest(values=values):
                p, r = self.cli(values=values)
                self.assertNotEqual(r.returncode, 0)
                self.assertFalse(p['saved'])
                self.assertEqual(self.snapshot(), before)
        for name in ('   ', 'bad\nname', 'bad\x00name'):
            if '\x00' in name:
                continue  # OS argv itself rejects NUL before the CLI runs.
            p, r = self.cli(name)
            self.assertNotEqual(r.returncode, 0)
            self.assertFalse(p['saved'])
            self.assertEqual(self.snapshot(), before)

    def test_alias_and_normalized_name_collisions_are_rejected(self):
        with closing(sqlite3.connect(self.db)) as c:
            c.executescript("INSERT INTO food_aliases VALUES ('missing alias','missing product');")
        before = self.snapshot()
        for name in ('grains', 'missing alias', 'missing product', 'RICE'):
            p, r = self.cli(name)
            self.assertNotEqual(r.returncode, 0)
            self.assertFalse(p['saved'])
            self.assertEqual(self.snapshot(), before)

    def test_trigger_side_effects_are_denied_and_rolled_back(self):
        for action in ("UPDATE food_db SET kcal=999 WHERE id=1;",
                       "INSERT INTO food_aliases VALUES ('evil', NEW.product);",
                       "INSERT INTO food_db(product) VALUES ('evil');",
                       "SELECT RAISE(IGNORE);"):
            with self.subTest(action=action):
                with closing(sqlite3.connect(self.db)) as c:
                    c.executescript('DROP TRIGGER IF EXISTS evil; CREATE TRIGGER evil BEFORE INSERT ON food_db BEGIN ' + action + ' END;')
                before = self.snapshot()
                p, r = self.cli()
                self.assertNotEqual(r.returncode, 0)
                self.assertFalse(p['saved'])
                self.assertEqual(self.snapshot(), before)

    def test_readback_failure_reports_saved_without_retry(self):
        guard = self.fixture.root / 'sitecustomize.py'
        guard.write_text(guard.read_text().replace("uri = str(args[0])", "uri = str(args[0])\n        if uri.endswith('?mode=ro'): raise RuntimeError('synthetic readback failure')"))
        p, r = self.cli()
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual((p['type'], p['saved'], p['verified']), ('verification_failed', True, False))
        with closing(sqlite3.connect(self.db)) as c:
            self.assertEqual(c.execute('SELECT count(*) FROM food_db').fetchone()[0], 3)

    def test_missing_database_or_schema_is_not_initialized(self):
        with closing(sqlite3.connect(self.db)) as c:
            c.execute('DROP TABLE food_aliases')
        p, r = self.cli()
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse(p['saved'])
        self.db.unlink()
        p, r = self.cli()
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse(p['saved'])
        self.assertFalse(self.db.exists())

    def test_wal_and_autoincrement_schema(self):
        with closing(sqlite3.connect(self.db)) as c:
            c.executescript('ALTER TABLE food_db RENAME TO old_food; CREATE TABLE food_db(id INTEGER PRIMARY KEY AUTOINCREMENT, product TEXT, normalized_name TEXT, kcal REAL, protein REAL, fat REAL, carbs REAL); INSERT INTO food_db SELECT * FROM old_food; DROP TABLE old_food;')
            c.execute('PRAGMA journal_mode=WAL')
        p, r = self.cli()
        self.assertEqual(r.returncode, 0, p)
        self.assertTrue(p['verified'])
        with closing(sqlite3.connect(self.db)) as c:
            self.assertEqual(c.execute("SELECT seq FROM sqlite_sequence WHERE name='food_db'").fetchone()[0], 3)

    def test_added_product_is_available_to_meal_lookup(self):
        self.assertTrue(self.cli()[0]['verified'])
        self.fixture.helper.write_text('def determine_reminder(*args): return None\n')
        p, r = self.fixture.cli('бисквит творожный 100г')
        self.assertEqual(r.returncode, 0)
        self.assertTrue(p['verified'])
        self.assertEqual(p['items'][0]['product'], 'Бисквит творожный')
        self.assertEqual(p['items'][0]['protein'], 3.2)


if __name__ == '__main__':
    unittest.main()
