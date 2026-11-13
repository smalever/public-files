"""Product edits run only against a guarded synthetic database."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import unittest
import test_meal_fastpath


class ProductUpdateCLI(unittest.TestCase):
    def setUp(self):
        self.fixture = test_meal_fastpath.MealCLI()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root, self.db = self.fixture.root, self.fixture.db
        self.script, self.env = self.fixture.script, self.fixture.env
        self.fixture.helper.write_text("raise RuntimeError('unexpected medication import')\n")

    def run_update(self, *extra, name='rice', id='1', fields=('--kcal', '120')):
        r = subprocess.run([sys.executable, str(self.script), 'product-update',
                            '--id', id, '--name', name, '--json', *fields, *extra],
                           env=self.env, cwd=self.root, capture_output=True, text=True, timeout=15)
        self.assertTrue(r.stdout.strip(), r.stderr)
        return json.loads(r.stdout), r

    def snapshot(self):
        with closing(sqlite3.connect(self.db)) as c:
            return list(c.iterdump())

    def test_preview_is_readonly_and_shows_partial_change(self):
        before = hashlib.sha256(self.db.read_bytes()).digest()
        p, r = self.run_update()
        self.assertEqual(r.returncode, 0)
        self.assertEqual((p['type'], p['saved'], p['verified']), ('preview', False, False))
        self.assertEqual(p['before']['kcal'], 100)
        self.assertEqual(p['after']['kcal'], 120)
        self.assertEqual(p['after']['carbs'], 20)
        self.assertEqual(len(p['confirmation']), 64)
        self.assertEqual(hashlib.sha256(self.db.read_bytes()).digest(), before)
        self.assertFalse(list(self.root.glob('product-update-*.sqlite')))
        self.assertTrue(all(u.endswith('?mode=ro') for u in (self.root/'connections.log').read_text().splitlines()))

    def test_apply_changes_only_target_and_preserves_history(self):
        with closing(sqlite3.connect(self.db)) as c:
            c.execute("INSERT INTO nutrition VALUES(1,'2026-01-01','time','snack',100,2,1,20)")
            c.execute("INSERT INTO nutrition_items VALUES(1,1,'rice',100,100,2,1,20)")
            c.commit()
        original = self.snapshot()
        p, _ = self.run_update()
        result, r = self.run_update('--apply', '--confirm', p['confirmation'])
        self.assertEqual(r.returncode, 0, result)
        self.assertEqual((result['status'], result['saved'], result['verified']), ('ok', True, True))
        self.assertEqual(result['after']['kcal'], 120)
        self.assertEqual(result['after']['carbs'], 20)
        expected = [s.replace("'rice','rice',100.0,", "'rice','rice',120.0,") for s in original]
        self.assertEqual(self.snapshot(), expected)
        self.assertFalse(list(self.root.glob('product-update-*.sqlite')))
        self.assertFalse(Path(result['backup_path']).exists())
        connections = (self.root/'connections.log').read_text().splitlines()
        self.assertTrue(connections[-1].endswith('?mode=ro'))

    def test_rejected_requests_do_not_write(self):
        before = self.snapshot()
        for options in [dict(name='grains'), dict(name='ric'), dict(id='999'), dict(id='0'),
                        dict(fields=()), dict(fields=('--fat','nan')),
                        dict(fields=('--protein','inf')), dict(fields=('--kcal','-1')),
                        dict(fields=('--fat','101')), dict(fields=('--kcal','1001')),
                        dict(fields=('--protein','90','--fat','50'))]:
            with self.subTest(options=options):
                p, r = self.run_update(**options)
                self.assertNotEqual(r.returncode, 0)
                self.assertFalse(p['saved'])
                self.assertEqual(self.snapshot(), before)
        self.assertFalse(list(self.root.glob('product-update-*.sqlite')))

    def test_confirmation_required_and_bound_to_values_and_state(self):
        p, _ = self.run_update()
        for extra, fields in [(('--apply',), ('--kcal','120')),
                              (('--apply','--confirm','bad'), ('--kcal','120')),
                              (('--apply','--confirm',p['confirmation']), ('--kcal','121')),
                              (('--confirm',p['confirmation']), ('--kcal','120'))]:
            before = self.snapshot()
            result, r = self.run_update(*extra, fields=fields)
            self.assertNotEqual(r.returncode, 0)
            self.assertFalse(result['saved'])
            self.assertEqual(self.snapshot(), before)
        with closing(sqlite3.connect(self.db)) as c:
            c.execute('UPDATE food_db SET fat=2 WHERE id=1')
            c.commit()
        result, r = self.run_update('--apply', '--confirm', p['confirmation'])
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse(result['saved'])
        self.assertFalse(list(self.root.glob('product-update-*.sqlite')))

    def test_trigger_side_effect_is_rolled_back_and_backup_retained(self):
        with closing(sqlite3.connect(self.db)) as c:
            c.executescript('CREATE TRIGGER corrupt AFTER UPDATE ON food_db BEGIN UPDATE food_db SET kcal=999 WHERE id=2; END;')
        before = self.snapshot()
        p, _ = self.run_update()
        result, r = self.run_update('--apply', '--confirm', p['confirmation'])
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse(result['saved'])
        self.assertEqual(self.snapshot(), before)
        self.assertTrue(Path(result['backup_path']).exists())

    def test_postcommit_readback_failure_keeps_backup_and_never_claims_success(self):
        p, _ = self.run_update()
        guard = self.root/'sitecustomize.py'
        guard.write_text(guard.read_text().replace('root = os.environ[\'HOME\']',
            "root = os.environ['HOME']\nro_count = 0").replace("uri = str(args[0])",
            "uri = str(args[0])\n        global ro_count\n        if uri.endswith('?mode=ro'):\n            ro_count += 1\n            if ro_count == 3: raise RuntimeError('readback unavailable')"))
        result, r = self.run_update('--apply', '--confirm', p['confirmation'])
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual((result['saved'], result['verified']), (True, False))
        self.assertEqual(result['type'], 'verification_failed')
        self.assertTrue(Path(result['backup_path']).exists())
        with closing(sqlite3.connect(self.db)) as c:
            self.assertEqual(c.execute('SELECT kcal FROM food_db WHERE id=1').fetchone()[0],120)

    def test_wal_database_update_and_backup_failure(self):
        with closing(sqlite3.connect(self.db)) as c:
            c.execute('PRAGMA journal_mode=WAL')
        p, _ = self.run_update(fields=('--fat','0','--carbs','0'))
        result, r = self.run_update('--apply', '--confirm', p['confirmation'], fields=('--fat','0','--carbs','0'))
        self.assertEqual(r.returncode, 0, result)
        self.assertTrue(result['verified'])
        self.assertEqual(result['after']['fat'], 0)
        p, _ = self.run_update()
        original = self.snapshot()
        self.script.write_text(self.script.read_text().replace(
            'source.backup(backup)', "raise OSError('synthetic backup failure')"))
        result, r = self.run_update('--apply', '--confirm', p['confirmation'])
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse(result['saved'])
        self.assertEqual(self.snapshot(), original)
        self.assertTrue(Path(result['backup_path']).exists())

    def test_lock_recheck_rejects_change_after_initial_read(self):
        p, _ = self.run_update()
        self.script.write_text(self.script.read_text().replace(
            "conn = get_conn(write=True)\n        conn.execute('BEGIN IMMEDIATE')",
            "conn = get_conn(write=True)\n        conn.execute('UPDATE food_db SET fat=2 WHERE id=1')\n        conn.commit()\n        conn.execute('BEGIN IMMEDIATE')"))
        result, r = self.run_update('--apply', '--confirm', p['confirmation'])
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse(result['saved'])
        self.assertIsNone(result['backup_path'])
        with closing(sqlite3.connect(self.db)) as c:
            self.assertEqual(c.execute('SELECT kcal FROM food_db WHERE id=1').fetchone()[0], 100)

    def test_missing_database_not_created(self):
        self.db.unlink()
        p, r = self.run_update()
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(p['status'], 'error')
        self.assertEqual((p['saved'], p['verified']), (False, False))
        self.assertFalse(self.db.exists())

    def test_schema_error_has_explicit_write_state(self):
        with closing(sqlite3.connect(self.db)) as c:
            c.execute('DROP TABLE food_db')
        p, r = self.run_update()
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual((p['saved'], p['verified']), (False, False))

    def test_sum_slightly_above_hundred_rejected(self):
        p, r = self.run_update(fields=('--protein','50','--fat','50','--carbs','0.0000005'))
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse(p['saved'])

    def test_rollback_and_close_failures_preserve_original_receipt(self):
        p, _ = self.run_update()
        self.script.write_text(self.script.read_text().replace(
            'conn = get_conn(write=True)\n        conn.execute',
            '''class BrokenCleanup:
            def __init__(self, inner): self.inner = inner
            def __getattr__(self, key): return getattr(self.inner, key)
            def rollback(self): raise OSError('rollback unavailable')
            def close(self):
                self.inner.close()
                raise OSError('close unavailable')
        conn = BrokenCleanup(get_conn(write=True))
        conn.execute''').replace('source.backup(backup)', "raise OSError('original backup failure')"))
        result, r = self.run_update('--apply', '--confirm', p['confirmation'])
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual((result['saved'], result['verified']), (False, False))
        self.assertIn('original backup failure', result['error'])
        self.assertTrue(Path(result['backup_path']).exists())
        self.assertEqual(len(result['cleanup_errors']), 2)


if __name__ == '__main__':
    unittest.main()
