"""Tests for skills_update.py with a local catalog (no network)."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "skills_update.py"


class SkillsUpdateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.root = base / "root"
        self.src = base / "src"
        cat = self.src / "hermes-skills"
        (cat / "skills/demo").mkdir(parents=True)
        (cat / "scripts").mkdir(parents=True)
        (cat / "skills/demo/SKILL.md").write_text("v1\n", encoding="utf-8")
        (cat / "scripts/demo_helper.py").write_text("X = 1\n", encoding="utf-8")
        self.catalog = cat / "catalog.yaml"
        self._write_catalog("1.0.0", "2026-10-01")
        self.root.mkdir()
        self.env = {**os.environ, "BOT_HELP_ROOT": str(self.root), "BOT_HELP_SOURCE_DIR": str(self.src)}

    def tearDown(self):
        self.tmp.cleanup()

    def _write_catalog(self, version, updated):
        self.catalog.write_text(
            "skills:\n"
            "  - name: demo\n"
            "    title: Демо\n"
            "    summary: Тестовый навык\n"
            f"    version: {version}\n"
            "    created: 2026-10-01\n"
            f"    updated: {updated}\n"
            "    path: skills/demo\n"
            "    target: skills-custom/notes/demo\n"
            "    files:\n"
            "      - {from: scripts/demo_helper.py, to: scripts/demo_helper.py}\n",
            encoding="utf-8",
        )

    def run_cmd(self, *args):
        proc = subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True,
                              text=True, env=self.env, timeout=60)
        return proc.returncode, json.loads(proc.stdout)

    def test_check_lists_new_skill_and_mark_clears_it(self):
        code, data = self.run_cmd("check")
        self.assertEqual(code, 0)
        self.assertEqual([s["name"] for s in data["new_since_last_check"]], ["demo"])
        self.assertEqual([s["name"] for s in data["not_installed"]], ["demo"])
        self.run_cmd("check", "--mark")
        _, data = self.run_cmd("check")
        self.assertEqual(data["new_since_last_check"], [])

    def test_install_requires_yes(self):
        code, data = self.run_cmd("install", "demo")
        self.assertEqual(code, 0)
        self.assertTrue(data["dry_run"])
        self.assertFalse((self.root / "skills-custom/notes/demo/SKILL.md").exists())
        code, data = self.run_cmd("install", "demo", "--yes")
        self.assertTrue(data["installed"])
        self.assertEqual((self.root / "skills-custom/notes/demo/SKILL.md").read_text(), "v1\n")
        self.assertEqual((self.root / "scripts/demo_helper.py").read_text(), "X = 1\n")

    def test_update_detected_and_owner_changes_block_install(self):
        self.run_cmd("install", "demo", "--yes")
        skill = self.root / "skills-custom/notes/demo/SKILL.md"
        skill.write_text("v1 + правка владельца\n", encoding="utf-8")
        (self.src / "hermes-skills/skills/demo/SKILL.md").write_text("v2\n", encoding="utf-8")
        self._write_catalog("1.1.0", "2026-10-05")

        _, data = self.run_cmd("check")
        self.assertEqual(data["updates"][0]["installed_version"], "1.0.0")
        self.assertEqual(data["modified_by_owner"][0]["name"], "demo")

        code, data = self.run_cmd("install", "demo", "--yes")
        self.assertEqual(code, 3)
        self.assertIn("blocked", data)
        self.assertEqual(skill.read_text(), "v1 + правка владельца\n")

        _, data = self.run_cmd("diff", "demo")
        self.assertIn("+v2", data["diff"])

    def test_merge_via_staging(self):
        self.run_cmd("install", "demo", "--yes")
        skill = self.root / "skills-custom/notes/demo/SKILL.md"
        skill.write_text("v1 + правка владельца\n", encoding="utf-8")
        (self.src / "hermes-skills/skills/demo/SKILL.md").write_text("v2\n", encoding="utf-8")
        self._write_catalog("1.1.0", "2026-10-05")

        _, data = self.run_cmd("fetch", "demo")
        staged = Path(data["staging"]) / "skills-custom/notes/demo/SKILL.md"
        staged.write_text("v2 + правка владельца\n", encoding="utf-8")
        code, data = self.run_cmd("install", "demo", "--from-staging", "--force", "--yes")
        self.assertEqual(code, 0)
        self.assertTrue(data["backup"])
        self.assertEqual(skill.read_text(), "v2 + правка владельца\n")
        _, data = self.run_cmd("status")
        self.assertEqual(data["installed"]["demo"]["version"], "1.1.0")
        self.assertFalse(data["installed"]["demo"]["modified_by_owner"])

    def test_adopt_records_existing_files(self):
        (self.root / "skills-custom/notes/demo").mkdir(parents=True)
        (self.root / "scripts").mkdir()
        (self.root / "skills-custom/notes/demo/SKILL.md").write_text("v1\n", encoding="utf-8")
        (self.root / "scripts/demo_helper.py").write_text("X = 1\n", encoding="utf-8")
        _, data = self.run_cmd("adopt")
        self.assertEqual(data["adopted"], ["demo"])
        _, data = self.run_cmd("check")
        self.assertEqual(data["modified_by_owner"], [])
        self.assertEqual(data["not_installed"], [])


if __name__ == "__main__":
    unittest.main()
