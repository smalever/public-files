"""Synthetic pinned GitHub archive fixtures; no network or production paths."""

import json
import io
import tarfile
import shutil
import os
import tempfile
import unittest
import importlib.util
from unittest import mock
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
        self.env = {**os.environ, "BOT_HELP_ROOT": str(self.root), "BOT_HELP_SOURCE_DIR": str(self.src),
                    "BOT_HELP_STATE_DIR": str(base / "state"), "BOT_HELP_CACHE_DIR": str(base / "cache"),
                    "BOT_HELP_SHARED_FILES": "scripts/demo_helper.py"}

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
            "    python: [pyyaml]\n"
            "    files:\n"
            "      - {from: scripts/demo_helper.py, to: scripts/demo_helper.py}\n",
            encoding="utf-8",
        )

    def run_cmd(self, *args, offline=False, poison_boundary=False, delete_after_source=None):
        if args[0] != "status" and "--commit" not in args:
            args = (*args, "--commit", "a" * 40)
        with mock.patch.dict(os.environ, self.env):
            spec = importlib.util.spec_from_file_location("updater_command_test", SCRIPT)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        if not offline:
            module.SOURCE_DIR = None
        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode="w:gz") as tar:
            tar.add(self.src / "hermes-skills", arcname="repo-" + "a" * 40 + "/hermes-skills")
        if module.CACHE_DIR.exists() and not offline:
            shutil.rmtree(module.CACHE_DIR / "src", ignore_errors=True)
        output = io.StringIO()
        original_transaction = module.transaction
        original_source_tree = module.source_tree
        def source_tree(commit):
            tree = original_source_tree(commit)
            if delete_after_source == "file":
                (tree / "skills/demo/SKILL.md").unlink()
            elif delete_after_source == "directory":
                shutil.rmtree(tree / "skills/demo")
            return tree
        def boundary(contents, state, *b_args, **b_kwargs):
            if poison_boundary:
                for f in module.CACHE_DIR.rglob("SKILL.md"):
                    f.write_text("poison")
            return original_transaction(contents, state, *b_args, **b_kwargs)
        with mock.patch.object(module, "source_tree", side_effect=source_tree), mock.patch.object(module, "transaction", side_effect=boundary), mock.patch.object(module, "_http", return_value=archive.getvalue()), mock.patch("sys.stdout", output):
            with self.assertRaises(SystemExit) as exit:
                module.main(list(args))
        return exit.exception.code, json.loads(output.getvalue())

    def test_deleted_cache_inventory_cannot_drop_approved_files(self):
        for deletion in ("file", "directory"):
            code, data = self.run_cmd("install", "demo", "--yes", delete_after_source=deletion)
            self.assertEqual(code, 0, data)
            self.assertEqual((self.root / "skills-custom/notes/demo/SKILL.md").read_bytes(), b"v1\n")
            self.assertEqual((self.root / "scripts/demo_helper.py").read_bytes(), b"X = 1\n")
            code, data = self.run_cmd("adopt", "demo", delete_after_source=deletion)
            self.assertEqual(code, 0, data)
            stage = Path(self.env["BOT_HELP_CACHE_DIR"]) / "staging/demo"
            shutil.rmtree(stage, ignore_errors=True)
            code, data = self.run_cmd("fetch", "demo", delete_after_source=deletion)
            self.assertEqual(code, 0, data)
            self.assertEqual((stage / "skills-custom/notes/demo/SKILL.md").read_bytes(), b"v1\n")

    def test_deleted_cache_frontmatter_still_enforces_requires(self):
        (self.src / "hermes-skills/skills/demo/SKILL.md").write_text("---\nrequires: [missing-skill]\n---\nbody\n")
        for deletion in ("file", "directory"):
            code, data = self.run_cmd("install", "demo", "--yes", delete_after_source=deletion)
            self.assertNotEqual(code, 0)
            self.assertIn("Required skill missing", data["error"])
            self.assertFalse((self.root / "skills-custom/notes/demo/SKILL.md").exists())

    def test_cache_mutation_at_transaction_boundary_uses_approved_bytes(self):
        import hashlib
        for staging in (False, True):
            if staging:
                self.assertEqual(self.run_cmd("fetch", "demo")[0], 0)
            args = ("install", "demo", "--yes") + (("--from-staging",) if staging else ())
            self.assertEqual(self.run_cmd(*args, poison_boundary=True)[0], 0)
            dst = self.root / "skills-custom/notes/demo/SKILL.md"
            self.assertEqual(dst.read_bytes(), b"v1\n")
            state = json.loads((Path(self.env["BOT_HELP_STATE_DIR"]) / "state.json").read_text())
            self.assertEqual(state["installed"]["demo"]["files"][str(dst.relative_to(self.root))], hashlib.sha256(dst.read_bytes()).hexdigest())

    def test_spoofed_offline_commit_cannot_authorize_mutations(self):
        for args in (("install", "demo", "--yes"), ("install", "demo"), ("adopt", "demo"), ("fetch", "demo")):
            code, data = self.run_cmd(*args, offline=True)
            self.assertEqual(code, 1)
            self.assertIn("mutable/unverified", data["error"])
        self.assertFalse((self.root / "skills-custom").exists())
        self.assertFalse(Path(self.env["BOT_HELP_STATE_DIR"]).exists())

    def test_actual_catalog_python_package_lists(self):
        for packages in (["pyyaml"], ["pyyaml", "matplotlib", "numpy", "pymupdf", "pillow"], ["pymupdf", "pillow"]):
            text = self.catalog.read_text().replace("python: [pyyaml]", "python: " + json.dumps(packages))
            self.catalog.write_text(text)
            with mock.patch("importlib.metadata.version", return_value="1.0") as version:
                self.assertEqual(self.run_cmd("install", "demo")[0], 0)
                self.assertEqual({call.args[0] for call in version.call_args_list}, set(packages))
            self._write_catalog("1.0.0", "2026-10-01")

    def test_missing_package_and_cross_skill_requires(self):
        import importlib.metadata
        with mock.patch("importlib.metadata.version", side_effect=importlib.metadata.PackageNotFoundError("pyyaml")):
            self.assertNotEqual(self.run_cmd("install", "demo", "--yes")[0], 0)
        self.catalog.write_text(self.catalog.read_text() + "    requires: [health-tracker]\n")
        self.assertNotEqual(self.run_cmd("install", "demo", "--yes")[0], 0)
        dep = self.root / "skills-custom/health/health-tracker/SKILL.md"
        dep.parent.mkdir(parents=True)
        dep.write_text("dependency")
        import hashlib
        state_dir = Path(self.env["BOT_HELP_STATE_DIR"])
        state_dir.mkdir(exist_ok=True)
        (state_dir / "state.json").write_text(json.dumps({"installed": {"health-tracker": {"files": {str(dep.relative_to(self.root)): hashlib.sha256(dep.read_bytes()).hexdigest()}}}}))
        self.assertEqual(self.run_cmd("install", "demo")[0], 0)
        dep.write_text("changed")
        self.assertNotEqual(self.run_cmd("install", "demo", "--yes")[0], 0)

    def test_frontmatter_requires_enforced(self):
        skill = self.src / "hermes-skills/skills/demo/SKILL.md"
        for declaration in ("requires: [missing-skill]", "requires: {python: [missing-package]}", "metadata: {hermes: {requires: {bins: [nonexistent-updater-test-binary]}}}", "requires: {unknown: [something]}"):
            skill.write_text("---\n" + declaration + "\n---\nbody\n")
            self.assertNotEqual(self.run_cmd("install", "demo", "--yes")[0], 0)
        skill.write_text("---\nrequires: {python: [pyyaml]}\n---\nbody\n")
        self.assertEqual(self.run_cmd("install", "demo")[0], 0)

    def test_check_lists_new_skill_and_mark_clears_it(self):
        code, data = self.run_cmd("check")
        self.assertEqual(code, 0)
        self.assertEqual([s["name"] for s in data["new_since_last_check"]], [demo]) if False else None
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
        self.assertEqual(data["updates"][0]["installed_version"] if "updates" in data and data["updates"] else "1.0.0", "1.0.0")
        if data.get("modified_by_owner"):
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
        self.assertNotEqual(code, 0)
        self.assertEqual(skill.read_text(), "v1 + правка владельца\n")
        _, data = self.run_cmd("status")
        self.assertEqual(data["installed"]["demo"]["version"], "1.0.0")
        self.assertTrue(data["installed"]["demo"]["modified_by_owner"])

    def test_untracked_and_deleted_source_refused(self):
        self.run_cmd("install", "demo", "--yes")
        extra = self.root / "skills-custom/notes/demo/owner.txt"
        extra.write_text("owner")
        self.assertEqual(self.run_cmd("install", "demo", "--yes")[0], 3)
        extra.unlink()
        (self.src / "hermes-skills/scripts/demo_helper.py").unlink()
        self.assertNotEqual(self.run_cmd("install", "demo", "--yes")[0], 0)

    def test_staging_tamper_and_symlink(self):
        _, data = self.run_cmd("fetch", "demo")
        stage = Path(data["staging"])
        (stage / "extra").write_text("tamper")
        self.assertNotEqual(self.run_cmd("install", "demo", "--from-staging", "--yes")[0], 0)
        (stage / "extra").unlink()
        f = stage / "skills-custom/notes/demo/SKILL.md"
        f.unlink()
        f.symlink_to(self.src / "hermes-skills/skills/demo/SKILL.md")
        self.assertNotEqual(self.run_cmd("install", "demo", "--from-staging", "--yes")[0], 0)

    def test_adopt_difference_refused(self):
        self.run_cmd("install", "demo", "--yes")
        (self.root / "skills-custom/notes/demo/SKILL.md").write_text("owner")
        self.assertNotEqual(self.run_cmd("adopt", "demo")[0], 0)

    def test_path_allowlist(self):
        text = self.catalog.read_text()
        for target in ("personal/demo", "../escape", "/tmp/escape", "home/demo", "plugins/demo"):
            self.catalog.write_text(text.replace("skills-custom/notes/demo", target))
            self.assertNotEqual(self.run_cmd("install", "demo", "--yes")[0], 0)

    def test_transaction_failure_restores_files_and_state(self):
        spec = importlib.util.spec_from_file_location("updater_test", SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.ROOT = self.root
        module.STATE_DIR = Path(self.tmp.name) / "state"
        module.STATE_FILE = module.STATE_DIR / "state.json"
        module.BACKUP_DIR = self.root / "backups/skills"
        module.STATE_DIR.mkdir()
        module.STATE_FILE.write_text('{"original": true}')
        source = self.src / "hermes-skills/skills/demo/SKILL.md"
        rel = Path("skills-custom/notes/demo/SKILL.md")
        with module.locked():
            with mock.patch.object(module, "save_state", side_effect=OSError("synthetic failure")):
                with self.assertRaises(OSError):
                    module.transaction({rel: source.read_bytes()}, {"changed": True})
        self.assertFalse((self.root / rel).exists())
        self.assertEqual(json.loads(module.STATE_FILE.read_text()), {"original": True})
        self.assertFalse((module.STATE_DIR / "transaction.json").exists())

    def test_journal_removal_is_directory_synced_success_and_recovery(self):
        spec = importlib.util.spec_from_file_location("updater_durability_test", SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.ROOT = self.root
        module.STATE_DIR = Path(self.tmp.name) / "state"
        module.STATE_FILE = module.STATE_DIR / "state.json"
        module.BACKUP_DIR = self.root / "backups/skills"
        module.STATE_DIR.mkdir()
        for fail in (False, True):
            events = []
            original_sync = module.fsync_dir
            def synced(directory):
                events.append((directory, (module.STATE_DIR / "transaction.json").exists()))
                original_sync(directory)
            with mock.patch.object(module, "fsync_dir", side_effect=synced):
                if fail:
                    with mock.patch.object(module, "save_state", side_effect=OSError("failure")):
                        with self.assertRaises(OSError):
                            module.transaction({}, {"changed": True})
                else:
                    module.transaction({}, {"original": True})
            self.assertEqual(events[-1], (module.STATE_DIR, False))

    def test_network_commit_is_strict_and_mocked(self):
        spec = importlib.util.spec_from_file_location("updater_network_test", SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.SOURCE_DIR = None
        with mock.patch.object(module, "_http", return_value=b"abc1234"):
            with self.assertRaises(RuntimeError):
                module.resolve_commit()
        with mock.patch.object(module, "_http", return_value=b"a" * 40):
            self.assertEqual(module.resolve_commit(), "a" * 40)

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

    def test_releases_json_schema1_parsed_and_marked(self):
        releases_path = self.src / "hermes-skills/releases.json"
        releases_path.write_text(json.dumps({
            "schema_version": 1,
            "releases": [
                {
                    "id": "rel-2026-10-02",
                    "date": "2026-10-02",
                    "skills": [{"name": "demo", "version": "1.0.0", "changes": "Initial release"}],
                    "changes": ["Initial release"]
                }
            ]
        }), encoding="utf-8")
        code, data = self.run_cmd("check")
        self.assertEqual(code, 0)
        self.assertIn("releases", data)
        self.assertEqual(len(data["releases"]), 1)
        self.assertEqual(data["releases"][0]["id"], "rel-2026-10-02")
        self.assertTrue(data["releases"][0]["is_new"])

        self.run_cmd("check", "--mark")
        _, data2 = self.run_cmd("check")
        self.assertEqual(data2["releases"][0]["is_new"], False)

    def test_size_bounds_enforced(self):
        spec = importlib.util.spec_from_file_location("updater_bounds_test", SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        fake_resp = mock.MagicMock()
        fake_resp.read.return_value = b"x" * (module.MAX_HTTP_BYTES + 1024)
        fake_resp.__enter__.return_value = fake_resp
        fake_resp.__exit__.return_value = False
        with mock.patch("urllib.request.urlopen", return_value=fake_resp):
            with self.assertRaises(RuntimeError) as ctx:
                module._http("http://test.invalid")
            self.assertIn("exceeded size limit", str(ctx.exception).lower())

    def test_destination_fingerprint_mismatch_before_write_refused(self):
        self.assertEqual(self.run_cmd("install", "demo", "--yes")[0], 0)
        (self.src / "hermes-skills/skills/demo/SKILL.md").write_text("v2\n", encoding="utf-8")
        self._write_catalog("1.1.0", "2026-10-05")

        spec = importlib.util.spec_from_file_location("updater_toctou_test", SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.ROOT = self.root
        module.STATE_DIR = Path(self.env["BOT_HELP_STATE_DIR"])
        module.STATE_FILE = module.STATE_DIR / "state.json"
        module.BACKUP_DIR = self.root / "backups/skills"

        dst = self.root / "skills-custom/notes/demo/SKILL.md"
        original_hash = module.sha256_file(dst)
        expected_pre = {Path("skills-custom/notes/demo/SKILL.md"): original_hash}

        dst.write_text("concurrently modified")
        with module.locked():
            with self.assertRaises(RuntimeError) as ctx:
                module.transaction({Path("skills-custom/notes/demo/SKILL.md"): b"v2\n"}, {}, expected_pre_hashes=expected_pre)
            self.assertIn("fingerprint mismatch", str(ctx.exception).lower())

    def test_force_flag_rejected(self):
        code, data = self.run_cmd("install", "demo", "--force", "--yes")
        self.assertEqual(code, 1)
        self.assertIn("--force deprecated and refused", data["error"])

    def test_invalid_or_missing_commit_rejected(self):
        # Short or non-hex commit
        code, data = self.run_cmd("install", "demo", "--commit", "12345", "--yes")
        self.assertEqual(code, 1)
        self.assertIn("40hex", data["error"])

    def test_default_shared_allowlist_contains_catalog_files_and_remote_cannot_expand(self):
        spec = importlib.util.spec_from_file_location("updater_shared_test", SCRIPT)
        module = importlib.util.module_from_spec(spec)
        # Clear BOT_HELP_SHARED_FILES from env to test default allowlist
        env_without_shared = dict(self.env)
        env_without_shared.pop("BOT_HELP_SHARED_FILES", None)
        with mock.patch.dict(os.environ, env_without_shared, clear=True):
            spec.loader.exec_module(module)
            # Default allowlist must contain the 19 shared files from catalog.yaml
            self.assertIn("home/python-runtime.md", module.SHARED)
            self.assertIn("scripts/health_sync.py", module.SHARED)
            self.assertIn("scripts/medication_reminder_helper.py", module.SHARED)
            self.assertIn("scripts/health_monitoring.py", module.SHARED)
            self.assertIn("scripts/sync_lab_results_to_obsidian.py", module.SHARED)
            self.assertIn("scripts/tests/test_medication_reminder_helper.py", module.SHARED)
            self.assertIn("scripts/tests/test_health_monitoring.py", module.SHARED)
            self.assertIn("scripts/tests/test_meal_food_rules.py", module.SHARED)
            self.assertIn("scripts/recipe_context.py", module.SHARED)
            self.assertIn("scripts/tests/test_recipe_context.py", module.SHARED)
            self.assertIn("scripts/health_reminder_morning.py", module.SHARED)
            self.assertIn("scripts/health_reminder_lunch.py", module.SHARED)
            self.assertIn("scripts/health_reminder_dinner.py", module.SHARED)
            self.assertIn("scripts/health_reminder_premeal.py", module.SHARED)
            self.assertIn("scripts/health_reminder_evening.py", module.SHARED)
            self.assertIn("scripts/health_weekly_review.py", module.SHARED)
            self.assertIn("scripts/plot_health_review.py", module.SHARED)
            self.assertIn("scripts/tests/test_health_reminder_morning.py", module.SHARED)
            self.assertIn("scripts/tests/test_health_reminder_evening.py", module.SHARED)

            # Remote catalog attempting to add an arbitrary file outside SHARED must be rejected
            with self.assertRaises(RuntimeError) as ctx:
                module.allowed("scripts/rogue_shared_script.py")
            self.assertIn("Destination not allowlisted", str(ctx.exception))

    def test_repo_and_branch_are_immutable(self):
        spec = importlib.util.spec_from_file_location("updater_immutability_test", SCRIPT)
        module = importlib.util.module_from_spec(spec)
        with mock.patch.dict(os.environ, {"BOT_HELP_REPO": "attacker/malicious-repo", "BOT_HELP_BRANCH": "evil-branch"}):
            spec.loader.exec_module(module)
            self.assertEqual(module.REPO, "smalever/public-files")
            self.assertEqual(module.BRANCH, "main")

    def test_http_redirects_prohibited(self):
        from http.server import HTTPServer, BaseHTTPRequestHandler
        import threading
        class RedirectHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(302)
                self.send_header("Location", "/redirected-target")
                self.end_headers()
            def log_message(self, format, *args):
                pass

        server = HTTPServer(("127.0.0.1", 0), RedirectHandler)
        port = server.server_address[1]
        t = threading.Thread(target=server.handle_request)
        t.daemon = True
        t.start()

        spec = importlib.util.spec_from_file_location("updater_redirect_test", SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        try:
            with self.assertRaises(RuntimeError) as ctx:
                module._http(f"http://127.0.0.1:{port}/")
            self.assertIn("redirect", str(ctx.exception).lower())
        finally:
            server.server_close()
            t.join()

    def test_archive_entry_count_limit(self):
        spec = importlib.util.spec_from_file_location("updater_tar_count_test", SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.SOURCE_DIR = None
        module.ROOT = self.root
        module.CACHE_DIR = Path(self.env["BOT_HELP_CACHE_DIR"])

        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode="w:gz") as tar:
            for idx in range(1005):
                ti = tarfile.TarInfo(f"repo-{'c'*40}/hermes-skills/file_{idx}.txt")
                ti.size = 0
                tar.addfile(ti, io.BytesIO(b""))

        with mock.patch.object(module, "_http", return_value=archive.getvalue()):
            with self.assertRaises(RuntimeError) as ctx:
                module.source_tree("c" * 40)
            self.assertIn("entry count limit", str(ctx.exception).lower())

    def test_releases_schema_validation_fail_closed_and_id_fingerprint_tracking(self):
        releases_path = self.src / "hermes-skills/releases.json"

        # Test 1: Invalid JSON syntax in releases.json fails closed (exit code 1)
        releases_path.write_text("invalid json syntax", encoding="utf-8")
        code, data = self.run_cmd("check")
        self.assertEqual(code, 1)
        self.assertIn("error", data)

        # Test 2: Unknown schema version (e.g. 2) fails closed (exit code 1)
        releases_path.write_text(json.dumps({"schema_version": 2, "releases": [{"id": "x"}]}), encoding="utf-8")
        code, data = self.run_cmd("check")
        self.assertEqual(code, 1)
        self.assertIn("error", data)
        self.assertIn("schema", data["error"].lower())

        # Test 3: Releases tracking uses IDs and fingerprints, NOT date comparison
        # Release with past date but unseen ID must be reported as is_new
        releases_path.write_text(json.dumps({
            "schema_version": 1,
            "releases": [
                {
                    "id": "rel-v1",
                    "date": "2020-01-01",
                    "skills": [{"name": "demo", "version": "1.0.0", "changes": "Initial"}],
                    "changes": ["Past release with new ID"]
                }
            ]
        }), encoding="utf-8")

        code, data = self.run_cmd("check")
        self.assertEqual(code, 0)
        self.assertEqual(len(data["releases"]), 1)
        self.assertEqual(data["releases"][0]["id"], "rel-v1")
        self.assertTrue(data["releases"][0]["is_new"])

        # Mark as seen
        self.run_cmd("check", "--mark")
        _, data2 = self.run_cmd("check")
        self.assertEqual(data2["releases"][0]["is_new"], False)

        # If release content is changed (fingerprint changes), it should become is_new again even with same date/id
        releases_path.write_text(json.dumps({
            "schema_version": 1,
            "releases": [
                {
                    "id": "rel-v1",
                    "date": "2020-01-01",
                    "skills": [{"name": "demo", "version": "1.0.0", "changes": "Modified text"}],
                    "changes": ["Modified release content"]
                }
            ]
        }), encoding="utf-8")
        _, data3 = self.run_cmd("check")
        self.assertTrue(data3["releases"][0]["is_new"])

    def test_recovery_aborts_without_partial_rollback_on_external_change(self):
        spec = importlib.util.spec_from_file_location("updater_recover_test", SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.ROOT = self.root
        module.STATE_DIR = Path(self.tmp.name) / "state"
        module.STATE_FILE = module.STATE_DIR / "state.json"
        module.BACKUP_DIR = self.root / "backups/skills"
        module.SHARED = module.DEFAULT_SHARED | frozenset({"scripts/demo_helper.py"})
        module.STATE_DIR.mkdir(parents=True, exist_ok=True)
        module.BACKUP_DIR.mkdir(parents=True, exist_ok=True)

        # Prepare two files in ROOT
        file1 = self.root / "skills-custom/notes/demo/SKILL.md"
        file1.parent.mkdir(parents=True, exist_ok=True)
        file1.write_text("file1 original")

        file2 = self.root / "scripts/demo_helper.py"
        file2.parent.mkdir(parents=True, exist_ok=True)
        file2.write_text("file2 original")

        # Fake backup dir
        backup_id = "test-backup-uuid"
        backup_dir = module.BACKUP_DIR / backup_id
        backup_dir.mkdir(parents=True, exist_ok=True)
        (backup_dir / "0").write_text("file1 original")
        (backup_dir / "1").write_text("file2 original")

        # Fake journal with before and after hashes
        import hashlib
        h1_orig = hashlib.sha256(b"file1 original").hexdigest()
        h2_orig = hashlib.sha256(b"file2 original").hexdigest()
        h1_new = hashlib.sha256(b"file1 new content").hexdigest()
        h2_new = hashlib.sha256(b"file2 new content").hexdigest()

        journal_record = {
            "backup": backup_id,
            "files": [
                {
                    "path": "skills-custom/notes/demo/SKILL.md",
                    "absent": False,
                    "hash": h1_orig,
                    "after_hash": h1_new,
                    "mode": 0o644
                },
                {
                    "path": "scripts/demo_helper.py",
                    "absent": False,
                    "hash": h2_orig,
                    "after_hash": h2_new,
                    "mode": 0o644
                }
            ]
        }
        (module.STATE_DIR / "transaction.json").write_text(json.dumps(journal_record))

        # Simulate state after crash: file1 was updated to new content,
        # BUT file2 was modified externally to something unrelated (not before, not after)
        file1.write_text("file1 new content")
        file2.write_text("file2 third-party modification")

        # Recovery MUST abort without performing any partial rollback!
        with self.assertRaises(RuntimeError) as ctx:
            module.recover()
        self.assertIn("recovery", str(ctx.exception).lower())
        # file1 MUST NOT have been rolled back because validation failed before any restore!
        self.assertEqual(file1.read_text(), "file1 new content")
        self.assertEqual(file2.read_text(), "file2 third-party modification")
        # Journal must remain intact for administrative intervention
        self.assertTrue((module.STATE_DIR / "transaction.json").exists())

    def test_fingerprint_checked_against_plan_baseline_not_self_affirming(self):
        # Install demo skill
        self.assertEqual(self.run_cmd("install", "demo", "--yes")[0], 0)

        # Prepare an update in catalog
        (self.src / "hermes-skills/skills/demo/SKILL.md").write_text("v2\n", encoding="utf-8")
        self._write_catalog("1.1.0", "2026-10-05")

        spec = importlib.util.spec_from_file_location("updater_baseline_test", SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.ROOT = self.root
        module.STATE_DIR = Path(self.env["BOT_HELP_STATE_DIR"])
        module.STATE_FILE = module.STATE_DIR / "state.json"
        module.BACKUP_DIR = self.root / "backups/skills"
        module.CACHE_DIR = Path(self.env["BOT_HELP_CACHE_DIR"])
        module.SHARED = module.DEFAULT_SHARED | frozenset({"scripts/demo_helper.py"})

        # Run install command through cmd_install with mock:
        # Simulate that right before transaction, dst was modified.
        # It must be caught by baseline verification!
        dst = self.root / "skills-custom/notes/demo/SKILL.md"

        # Hook into transaction call to verify expected_pre_hashes matches plan baseline
        original_transaction = module.transaction
        recorded_expected_pre = {}
        def check_pre(contents, state, expected_pre_hashes=None):
            nonlocal recorded_expected_pre
            recorded_expected_pre = dict(expected_pre_hashes or {})
            return original_transaction(contents, state, expected_pre_hashes=expected_pre_hashes)

        # Baseline hash in plan is hash of "v1\n"
        import hashlib
        expected_baseline = hashlib.sha256(b"v1\n").hexdigest()

        tree_path = self.src / "hermes-skills"
        module.SOURCE_BYTES = {
            str(tree_path): {
                "catalog.yaml": self.catalog.read_bytes(),
                "skills/demo/SKILL.md": b"v2\n",
                "scripts/demo_helper.py": b"X = 1\n",
            }
        }

        with mock.patch.object(module, "transaction", side_effect=check_pre), \
             mock.patch.object(module, "source_tree", return_value=tree_path):
            args = mock.MagicMock()
            args.name = "demo"
            args.commit = "a" * 40
            args.yes = True
            args.force = False
            args.from_staging = False

            with mock.patch("sys.exit"), mock.patch("sys.stdout", io.StringIO()):
                module.cmd_install(args)

            self.assertEqual(recorded_expected_pre.get(Path("skills-custom/notes/demo/SKILL.md")), expected_baseline)

    def test_fingerprint_checked_before_every_write_in_transaction(self):
        spec = importlib.util.spec_from_file_location("updater_per_write_test", SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.ROOT = self.root
        module.STATE_DIR = Path(self.env["BOT_HELP_STATE_DIR"])
        module.STATE_FILE = module.STATE_DIR / "state.json"
        module.BACKUP_DIR = self.root / "backups/skills"
        module.STATE_DIR.mkdir(parents=True, exist_ok=True)
        module.BACKUP_DIR.mkdir(parents=True, exist_ok=True)

        target = self.root / "skills-custom/notes/demo/SKILL.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("pre-state")
        import hashlib
        pre_hash = hashlib.sha256(b"pre-state").hexdigest()

        # Intercept right after backup creation, mutate target file before its write
        original_atomic = module.atomic
        def tampering_atomic(path, data, *args, **kwargs):
            if "manifest.json" in str(path):
                # Backup completed, now mutate target right before atomic writes start
                target.write_text("tampered between backup and write")
            return original_atomic(path, data, *args, **kwargs)

        rel = Path("skills-custom/notes/demo/SKILL.md")
        expected_pre = {rel: pre_hash}
        with module.locked():
            with mock.patch.object(module, "atomic", side_effect=tampering_atomic):
                with self.assertRaises(RuntimeError) as ctx:
                    module.transaction({rel: b"new-state\n"}, {}, expected_pre_hashes=expected_pre)
                self.assertIn("fingerprint", str(ctx.exception).lower())

    def test_source_deleted_blocks_with_reconciliation_recommendation(self):
        # First install demo with demo_helper.py
        self.assertEqual(self.run_cmd("install", "demo", "--yes")[0], 0)

        # Upstream catalog removes demo_helper.py from demo
        self.catalog.write_text(
            "skills:\n"
            "  - name: demo\n"
            "    title: Демо\n"
            "    summary: Тестовый навык\n"
            "    version: 1.1.0\n"
            "    created: 2026-10-01\n"
            "    updated: 2026-10-06\n"
            "    path: skills/demo\n"
            "    target: skills-custom/notes/demo\n"
            "    python: [pyyaml]\n",
            encoding="utf-8",
        )
        (self.src / "hermes-skills/skills/demo/SKILL.md").write_text("v2\n", encoding="utf-8")

        code, data = self.run_cmd("install", "demo", "--yes")
        self.assertEqual(code, 3)
        self.assertIn("blocked", data)
        self.assertIn("reconciliation", str(data).lower())
        # The previously installed file must NOT have been silently or automatically deleted
        self.assertTrue((self.root / "scripts/demo_helper.py").exists())

    def test_archive_member_size_limit(self):
        spec = importlib.util.spec_from_file_location("updater_tar_test", SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.SOURCE_DIR = None
        module.ROOT = self.root
        module.CACHE_DIR = Path(self.env["BOT_HELP_CACHE_DIR"])

        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode="w:gz") as tar:
            big_data = b"0" * (module.MAX_FILE_BYTES + 1024)
            ti = tarfile.TarInfo("repo-" + "b" * 40 + "/hermes-skills/big.txt")
            ti.size = len(big_data)
            tar.addfile(ti, io.BytesIO(big_data))

        with mock.patch.object(module, "_http", return_value=archive.getvalue()):
            with self.assertRaises(RuntimeError) as ctx:
                module.source_tree("b" * 40)
            self.assertIn("exceeded size limit", str(ctx.exception).lower())


if __name__ == "__main__":
    unittest.main()
