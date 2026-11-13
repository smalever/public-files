"""Universal alias command: synthetic DB only, no personal data or network."""
from contextlib import closing
import json
import sqlite3
import subprocess
import sys
import unittest

import test_meal_fastpath


class AliasAddCLI(unittest.TestCase):
    def setUp(self):
        self.fixture = test_meal_fastpath.MealCLI()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.db = self.fixture.db

    def cli(self, *args):
        r = subprocess.run([sys.executable, str(self.fixture.script), *args],
                           cwd=self.fixture.root, env=self.fixture.env,
                           capture_output=True, text=True, timeout=15)
        self.assertEqual(r.stderr, '', r.stderr)
        return json.loads(r.stdout), r

    def aliases(self):
        with closing(sqlite3.connect(self.db)) as c:
            return c.execute('SELECT alias, product FROM food_aliases ORDER BY alias').fetchall()

    def test_add_by_id_and_exact_name_with_verified_receipt(self):
        for selector in [('--id', '1'), ('--product', 'rice')]:
            alias = 'my rice' if selector[0] == '--id' else 'other rice'
            p, r = self.cli('alias-add', '--alias', alias, *selector, '--json')
            self.assertEqual(r.returncode, 0)
            self.assertEqual((p['status'], p['saved'], p['verified']), ('ok', True, True))
            self.assertEqual((p['alias'], p['product'], p['product_id']), (alias, 'rice', 1))
            self.assertIn((alias, 'rice'), self.aliases())
        self.assertEqual(self.fixture.counts(), (0, 0))
        connections = (self.fixture.root / 'connections.log').read_text().splitlines()
        self.assertTrue(any(x.endswith('?mode=ro') for x in connections))

    def test_normalization_idempotency_and_lookup_in_meal_and_products(self):
        p, _ = self.cli('alias-add', '--alias', '  СВЁКЛА   моя  ', '--id', '1')
        self.assertTrue(p['verified'])
        before = self.aliases()
        p, _ = self.cli('alias-add', '--alias', 'свекла моя', '--product', 'rice')
        self.assertEqual((p['type'], p['saved'], p['verified']), ('already_exists', False, True))
        self.assertEqual(self.aliases(), before)
        p, _ = self.cli('products', '--query', ' СВЕКЛА  моя ', '--json')
        self.assertEqual([r['id'] for r in p['products']], [1])
        p, _ = self.fixture.cli('свекла   МОЯ 27г')
        self.assertTrue(p['verified'])
        self.assertEqual(p['items'][0]['product'], 'rice')

    def test_conflicts_and_invalid_targets_never_change_database(self):
        with closing(sqlite3.connect(self.db)) as c:
            c.executescript("INSERT INTO food_aliases VALUES ('СВЁКЛА   моя','rice');")
        before = self.db.read_bytes()
        cases = [('--alias', 'свекла моя', '--id', '2'),
                 ('--alias', 'RICE', '--id', '2'),
                 ('--alias', 'grains', '--id', '2'),
                 ('--alias', '  ', '--id', '1'),
                 ('--alias', 'test', '--id', '0'),
                 ('--alias', 'test', '--id', '999'),
                 ('--alias', 'test', '--product', 'RICE'),
                 ('--alias', 'test', '--product', 'grains')]
        for args in cases:
            with self.subTest(args=args):
                p, r = self.cli('alias-add', *args, '--json')
                self.assertNotEqual(r.returncode, 0)
                self.assertEqual((p['status'], p['saved'], p['verified']), ('error', False, False))
                self.assertEqual(self.db.read_bytes(), before)
        with closing(sqlite3.connect(self.db)) as c:
            c.execute("UPDATE food_db SET normalized_name='milk alias' WHERE id=2")
            c.commit()
        p, r = self.cli('alias-add', '--alias', 'MILK alias', '--id', '1')
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse(p['saved'])

    def test_missing_database_and_schema_are_errors_not_initialization(self):
        with closing(sqlite3.connect(self.db)) as c:
            c.execute('DROP TABLE food_aliases')
        p, r = self.cli('alias-add', '--alias', 'test', '--id', '1')
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse(p['saved'])
        self.db.unlink()
        p, r = self.cli('alias-add', '--alias', 'test', '--id', '1')
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse(self.db.exists())

    def test_trigger_side_effect_is_denied_and_rolled_back(self):
        with closing(sqlite3.connect(self.db)) as c:
            c.executescript("CREATE TRIGGER evil AFTER INSERT ON food_aliases BEGIN UPDATE food_db SET kcal=999; END;")
        before = self.db.read_bytes()
        p, r = self.cli('alias-add', '--alias', 'test', '--id', '1')
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse(p['saved'])
        self.assertEqual(self.db.read_bytes(), before)

    def test_dangling_alias_is_distinguished_and_readonly_inspectable(self):
        with closing(sqlite3.connect(self.db)) as c:
            c.execute("INSERT INTO food_aliases VALUES ('черный хлеб', 'Хлеб черный (ржаной)')")
            c.commit()
        before = self.db.read_bytes()
        p, r = self.cli('alias-add', '--alias', 'чёрный хлеб', '--id', '1')
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(p['type'], 'dangling_alias')
        self.assertEqual(p['existing'], [{'alias': 'черный хлеб', 'product': 'Хлеб черный (ржаной)'}])
        p, r = self.cli('aliases', '--query', ' ЧЁРНЫЙ  хлеб ', '--json')
        self.assertEqual(r.returncode, 0)
        self.assertEqual(p['count'], 1)
        self.assertEqual(p['aliases'][0], {'alias': 'черный хлеб', 'product': 'Хлеб черный (ржаной)', 'product_ids': [], 'dangling': True})
        self.assertEqual(self.db.read_bytes(), before)

    def test_alias_listing_returns_targets_and_blocks_missing_schema(self):
        p, r = self.cli('aliases', '--query', 'GRAINS', '--json')
        self.assertEqual(p['aliases'][0]['product_ids'], [1])
        self.assertFalse(p['aliases'][0]['dangling'])
        self.fixture.helper.write_text("raise RuntimeError('must not import helper')")
        p, r = self.cli('aliases', '--query', 'unknown', '--json')
        self.assertEqual(p['count'], 0)
        with closing(sqlite3.connect(self.db)) as c:
            c.execute('DROP TABLE food_aliases')
        p, r = self.cli('aliases', '--json')
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(p['status'], 'error')

    def test_readback_failure_reports_committed_state_without_retry(self):
        guard = self.fixture.root / 'sitecustomize.py'
        guard.write_text(guard.read_text().replace("uri = str(args[0])", "uri = str(args[0])\n        if uri.endswith('?mode=ro'): raise RuntimeError('synthetic readback failure')"))
        p, r = self.cli('alias-add', '--alias', 'test', '--id', '1')
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual((p['type'], p['saved'], p['verified']), ('verification_failed', True, False))
        self.assertEqual(self.aliases().count(('test', 'rice')), 1)


if __name__ == '__main__':
    unittest.main()
