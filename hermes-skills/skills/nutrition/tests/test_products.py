"""Read-only products CLI tests, isolated from personal data and network."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import unittest

import test_meal_fastpath


class ProductsCLI(unittest.TestCase):
    def setUp(self):
        self.fixture = test_meal_fastpath.MealCLI()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root
        self.db = self.fixture.db
        self.script = self.fixture.script
        self.env = self.fixture.env
        # Any accidental helper import fails, even without a function call.
        self.fixture.helper.write_text("raise RuntimeError('products imported medication helper')\n")
        with closing(sqlite3.connect(self.db)) as c:
            c.executescript("""
INSERT INTO food_db VALUES
(3,'Котлеты куриные жареные','котлеты куриные',210,18,12,8),
(4,'Котлеты куриные паровые','котлеты куриные паровые',150,20,5,6),
(5,'Свёкла отварная','свёкла',49,1.8,0.1,10.8),
(6,'Котлеты рыбные','котлеты рыбные',180,15,9,10);
INSERT INTO food_aliases VALUES
('биточки из птицы','Котлеты куриные жареные'),
('куриные котлеты','Котлеты куриные жареные');
""")

    def run_cli(self, query, machine=True):
        before = hashlib.sha256(self.db.read_bytes()).digest()
        r = subprocess.run([sys.executable, str(self.script), 'products', '--query', query]
                           + (['--json'] if machine else []), cwd=self.root, env=self.env,
                           capture_output=True, text=True, timeout=15)
        self.assertEqual(hashlib.sha256(self.db.read_bytes()).digest(), before)
        self.assertEqual(r.stderr, '')
        return r

    def test_all_words_search_returns_full_deduplicated_matches(self):
        r = self.run_cli('КУРИНЫЕ котлеты')
        self.assertEqual(r.returncode, 0, r.stderr)
        p = json.loads(r.stdout)
        self.assertEqual((p['status'], p['count'], p['per_grams']), ('ok', 2, 100))
        self.assertEqual([x['id'] for x in p['products']], [3, 4])
        self.assertEqual(p['products'][0]['product'], 'Котлеты куриные жареные')
        self.assertEqual([p['products'][0][k] for k in ('kcal', 'protein', 'fat', 'carbs')],
                         [210, 18, 12, 8])
        connections = (self.root / 'connections.log').read_text().splitlines()
        self.assertTrue(connections)
        self.assertTrue(all(x.endswith('?mode=ro') for x in connections))

    def test_alias_and_cyrillic_yo(self):
        for query, ids in [('БИТОЧКИ птицы', [3]), ('свекла', [5])]:
            with self.subTest(query=query):
                r = self.run_cli(query)
                self.assertEqual(r.returncode, 0)
                self.assertEqual([x['id'] for x in json.loads(r.stdout)['products']], ids)

    def test_no_match_and_sql_input_are_empty_not_errors(self):
        for query in ('несуществующий продукт', "'; DROP TABLE food_db; --", 'котлеты банан'):
            with self.subTest(query=query):
                r = self.run_cli(query)
                self.assertEqual(r.returncode, 0)
                p = json.loads(r.stdout)
                self.assertEqual((p['status'], p['count'], p['products']), ('ok', 0, []))

    def test_blank_and_missing_query_rejected_without_connection(self):
        r = self.run_cli('   ')
        self.assertEqual(r.returncode, 2)
        self.assertEqual(json.loads(r.stdout)['type'], 'empty_query')
        r = subprocess.run([sys.executable, str(self.script), 'products', '--json'],
                           env=self.env, cwd=self.root, capture_output=True, text=True, timeout=15)
        self.assertEqual(r.returncode, 2)
        self.assertFalse((self.root / 'connections.log').exists())

    def test_text_output(self):
        r = self.run_cli('свекла', machine=False)
        self.assertEqual(r.returncode, 0)
        self.assertIn('Свёкла отварная', r.stdout)
        self.assertIn('100 г', r.stdout)

    def test_missing_database_is_not_created(self):
        self.db.unlink()
        r = subprocess.run([sys.executable, str(self.script), 'products', '--query', 'котлеты', '--json'],
                           env=self.env, cwd=self.root, capture_output=True, text=True, timeout=15)
        self.assertEqual(r.returncode, 1)
        self.assertEqual(json.loads(r.stdout)['status'], 'error')
        self.assertFalse(self.db.exists())

    def test_schema_error_is_not_empty_success(self):
        with closing(sqlite3.connect(self.db)) as c:
            c.execute('DROP TABLE food_aliases')
        r = self.run_cli('котлеты')
        self.assertEqual(r.returncode, 1)
        self.assertEqual(json.loads(r.stdout)['status'], 'error')

    def test_query_only_enabled_and_real_write_attempt_blocked(self):
        # Instrument only the isolated copy; check both independent SQLite protections.
        code = self.script.read_text().replace(
            "conn.execute('PRAGMA query_only=ON')",
            """conn.execute('PRAGMA query_only=ON')
        assert conn.execute('PRAGMA query_only').fetchone()[0] == 1
        try:
            conn.execute("INSERT INTO food_aliases VALUES ('bad', 'rice')")
        except sqlite3.OperationalError as exc:
            assert 'readonly' in str(exc).lower(), str(exc)
        else:
            raise AssertionError('Write unexpectedly succeeded')
        conn.execute('PRAGMA query_only=OFF')
        try:
            conn.execute("INSERT INTO food_aliases VALUES ('bad', 'rice')")
        except sqlite3.OperationalError as exc:
            assert 'readonly' in str(exc).lower(), str(exc)
        else:
            raise AssertionError('mode=ro did not prevent write')
        conn.execute('PRAGMA query_only=ON')""")
        self.script.write_text(code)
        r = self.run_cli('котлеты')
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual(json.loads(r.stdout)['count'], 3)


if __name__ == '__main__':
    unittest.main()
