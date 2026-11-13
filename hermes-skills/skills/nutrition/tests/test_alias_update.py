"""Confirmed alias repairs on isolated synthetic databases only."""
from contextlib import closing
import json
import sqlite3
import unittest
import test_alias_add


class AliasUpdateCLI(unittest.TestCase):
    setUp = test_alias_add.AliasAddCLI.setUp
    cli = test_alias_add.AliasAddCLI.cli
    aliases = test_alias_add.AliasAddCLI.aliases
    def test_preview_and_apply_change_only_one_link(self):
        before = self.db.read_bytes()
        p, r = self.cli('alias-update', '--alias', 'grains', '--id', '2', '--json')
        self.assertEqual(r.returncode, 0)
        self.assertEqual(p['type'], 'preview')
        self.assertEqual(p['before'], {'alias': 'grains', 'product': 'rice'})
        self.assertEqual(p['after'], {'alias': 'grains', 'product': 'milk 2,5%'})
        self.assertEqual(self.db.read_bytes(), before)
        p, r = self.cli('alias-update', '--alias', 'grains', '--id', '2', '--apply', '--confirm', p['confirmation'])
        self.assertEqual(r.returncode, 0, p)
        self.assertEqual((p['saved'], p['verified'], p['backup_removed']), (True, True, True))
        self.assertEqual(self.aliases(), [('grains', 'milk 2,5%')])
        self.assertEqual(self.fixture.counts(), (0, 0))
        self.assertFalse(list(self.fixture.root.glob('alias-update-*.sqlite')))
    def test_rejections_are_readonly_and_stale_confirmation_fails(self):
        p, _ = self.cli('alias-update', '--alias', 'grains', '--id', '2')
        token = p['confirmation']
        before = self.db.read_bytes()
        for args in [('--alias', 'grains', '--id', '2', '--apply'),
                     ('--alias', 'grains', '--id', '2', '--apply', '--confirm', 'wrong'),
                     ('--alias', 'missing', '--id', '2'),
                     ('--alias', 'grains', '--id', '999'),
                     ('--alias', 'grains', '--id', '0'),
                     ('--alias', 'grains', '--id', '1')]:
            p, r = self.cli('alias-update', *args)
            self.assertNotEqual(r.returncode, 0)
            self.assertFalse(p['saved'])
            self.assertEqual(self.db.read_bytes(), before)
        with closing(sqlite3.connect(self.db)) as c:
            c.execute("UPDATE food_aliases SET product='changed' WHERE alias='grains'")
            c.commit()
        p, r = self.cli('alias-update', '--alias', 'grains', '--id', '2', '--apply', '--confirm', token)
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse(p['saved'])

    def test_dangling_repair_normalization_and_ambiguity(self):
        with closing(sqlite3.connect(self.db)) as c:
            c.execute("INSERT INTO food_aliases VALUES ('Свёкла моя', 'missing')")
            c.commit()
        p, _ = self.cli('alias-update', '--alias', ' СВЕКЛА   моя ', '--id', '1')
        self.assertEqual(p['before']['alias'], 'Свёкла моя')
        p, r = self.cli('alias-update', '--alias', 'свекла моя', '--id', '1', '--apply', '--confirm', p['confirmation'])
        self.assertTrue(p['verified'])
        with closing(sqlite3.connect(self.db)) as c:
            c.execute("INSERT INTO food_aliases VALUES ('свекла моя', 'missing')")
            c.commit()
        p, r = self.cli('alias-update', '--alias', 'Свёкла моя', '--id', '2')
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse(p['saved'])

    def test_trigger_cannot_modify_other_data(self):
        with closing(sqlite3.connect(self.db)) as c:
            c.executescript("CREATE TRIGGER evil AFTER UPDATE ON food_aliases BEGIN UPDATE food_db SET kcal=999; END;")
        p, _ = self.cli('alias-update', '--alias', 'grains', '--id', '2')
        before = self.db.read_bytes()
        p, r = self.cli('alias-update', '--alias', 'grains', '--id', '2', '--apply', '--confirm', p['confirmation'])
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse(p['saved'])
        self.assertEqual(self.db.read_bytes(), before)
        self.assertTrue(__import__('pathlib').Path(p['backup_path']).exists())

    def test_postcommit_readback_failure_retains_backup(self):
        p, _ = self.cli('alias-update', '--alias', 'grains', '--id', '2')
        code = self.fixture.script.read_text().replace("saved = True\n        conn.close()\n        conn = None\n        conn = get_conn()", "saved = True\n        conn.close()\n        conn = None\n        raise RuntimeError('synthetic readback failure')\n        conn = get_conn()")
        self.fixture.script.write_text(code)
        p, r = self.cli('alias-update', '--alias', 'grains', '--id', '2', '--apply', '--confirm', p['confirmation'])
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual((p['saved'], p['verified'], p['type']), (True, False, 'verification_failed'))
        self.assertTrue(__import__('pathlib').Path(p['backup_path']).exists())
        self.assertEqual(self.aliases(), [('grains', 'milk 2,5%')])


if __name__ == '__main__':
    unittest.main()
