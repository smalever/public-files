import contextlib
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import medical_extract as extract
import medical_import as importer


class MedicalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / "source.pdf"
        self.source.write_bytes(b"%PDF-1.7\nsynthetic")
        (self.root / "config.yaml").write_text('providers:\n  newapi:\n    api: http://127.0.0.1:3000/v1\n    key_env: NEWAPI_API_KEY\n')
        env = patch.dict(os.environ, {"HERMES_HOME": str(self.root), "NEWAPI_API_KEY": "synthetic"})
        env.start()
        self.addCleanup(env.stop)
        self.payload = {"report_date": "2026-10-05", "results": [{"parameter": "synthetic", "test_type": "blood", "value": 1.2}]}

    def test_json_and_validation(self):
        self.assertEqual(extract.strict_json('```json\n{"a":1}\n```'), {"a": 1})
        for text in ('{"a":1,"a":2}', '{"a":NaN}', '{} trailing'):
            with self.assertRaises(ValueError):
                extract.strict_json(text)
        for date in ("../escape", "2026-02-30", None):
            with self.assertRaises(ValueError):
                extract.validate({**self.payload, "report_date": date}, "lab")
        for row in ({"value": True}, {"value": float("inf")}, {"is_critical": "false"}, {"ref_min": 2, "ref_max": 1}):
            with self.assertRaises(ValueError):
                extract.validate({**self.payload, "results": [{**self.payload["results"][0], **row}]}, "lab")
        extract.validate({"report_date": "2026-10-05", "conclusion": "synthetic"}, "doctor")

    def test_endpoint_and_containment(self):
        self.assertIn(extract.MODEL, extract.endpoint("https://configured.example/v1"))
        for base in ("https://user:secret@example.com", "https://example.com/arbitrary", "http://remote.example", "https://example.com?x=1"):
            with self.assertRaises(ValueError):
                extract.endpoint(base)
        link = self.root / "escape.pdf"
        link.symlink_to("/dev/null")
        with self.assertRaises(ValueError):
            extract.contained(link, self.root)

    def test_mock_native_api(self):
        envelope = {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": json.dumps(self.payload)}]}}]}
        response = io.BytesIO(json.dumps(envelope).encode())
        with patch("urllib.request.OpenerDirector.open", return_value=response) as api:
            self.assertEqual(extract.extract(self.source, self.root, "lab"), self.payload)
            request = api.call_args.args[0]
            self.assertEqual(api.call_args.kwargs["timeout"], 120)
            self.assertTrue(request.full_url.startswith("http://127.0.0.1:3000/v1beta/"))
            self.assertIn("inlineData", json.loads(request.data)["contents"][0]["parts"][1])
        self.source.write_bytes(b"not PDF")
        with self.assertRaises(ValueError):
            extract.extract(self.source, self.root, "lab")

    def run_import(self, *flags):
        metadata = self.root / "draft.json"
        metadata.write_text(json.dumps(self.payload))
        output = io.StringIO()
        with patch.object(importer, "DB_PATH", self.root / "health.db"), patch.object(importer, "DOCUMENT_ROOT", self.root / "docs"), patch("sys.argv", ["import", "lab", "--source", str(self.source), "--metadata-json", str(metadata), *flags]), contextlib.redirect_stdout(output):
            status = importer.main()
        return status, json.loads(output.getvalue())

    def schema(self, failing=False):
        with sqlite3.connect(self.root / "health.db") as db:
            db.executescript('CREATE TABLE documents(id INTEGER PRIMARY KEY,path TEXT,lab_name TEXT,report_date TEXT); CREATE TABLE lab_results(document_id INTEGER REFERENCES documents(id),date TEXT,test_type TEXT,parameter TEXT,value REAL,unit TEXT,ref_min REAL,ref_max REAL,is_critical INTEGER);')
            if failing:
                db.executescript("CREATE TRIGGER fail BEFORE INSERT ON lab_results BEGIN SELECT RAISE(ABORT,'synthetic failure'); END;")

    def test_confirmation_create_dedup(self):
        self.assertEqual(self.run_import()[1]["status"], "confirmation_required")
        self.assertFalse((self.root / "health.db").exists())
        self.schema()
        self.assertEqual(self.run_import("--confirm")[1]["status"], "created")
        self.assertEqual(self.run_import("--confirm")[1]["status"], "duplicate")

    def test_rollback(self):
        self.schema(failing=True)
        self.assertEqual(self.run_import("--confirm")[0], 1)
        with sqlite3.connect(self.root / "health.db") as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM documents").fetchone()[0], 0)
        self.assertFalse(list((self.root / "docs").rglob("*.pdf")))
        self.assertFalse(list((self.root / "docs").rglob(".medical-import-*")))

    def test_commit_failure_compensation(self):
        self.schema()
        real_connect = sqlite3.connect
        class FailingCommit(sqlite3.Connection):
            def commit(self):
                raise sqlite3.OperationalError("synthetic commit failure")
        with patch.object(importer.sqlite3, "connect", side_effect=lambda path: real_connect(path, factory=FailingCommit)):
            self.assertEqual(self.run_import("--confirm")[0], 1)
        with real_connect(self.root / "health.db") as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM documents").fetchone()[0], 0)
        self.assertFalse(list((self.root / "docs").rglob("*.pdf")))

    def test_missing_date_refusal_and_size(self):
        payload = {**self.payload, "report_date": None, "document_title": "Synthetic report"}
        for candidate, succeeds in (({"finishReason": "STOP", "content": {"parts": [{"text": json.dumps(payload)}]}}, True), ({"finishReason": "SAFETY"}, False), ({"finishReason": "STOP", "content": {"parts": []}}, False)):
            response = io.BytesIO(json.dumps({"candidates": [candidate]}).encode())
            with patch("urllib.request.OpenerDirector.open", return_value=response):
                if succeeds:
                    self.assertIsNone(extract.extract(self.source, self.root, "lab")["report_date"])
                else:
                    with self.assertRaises(ValueError):
                        extract.extract(self.source, self.root, "lab")
        with patch.object(extract, "MAX_PDF", 4):
            with self.assertRaises(ValueError):
                extract.extract(self.source, self.root, "lab")

    def durability_events(self):
        events = []
        real_unlink = Path.unlink
        def unlink(path, *args, **kwargs):
            events.append(("unlink", path))
            return real_unlink(path, *args, **kwargs)
        def sync(path):
            events.append(("sync", path))
        return events, patch.object(Path, "unlink", unlink), patch.object(importer, "sync_directory", side_effect=sync)

    def assert_cleanup_order(self, events):
        journals = [i for i, (action, path) in enumerate(events) if action == "unlink" and path.suffix == ".json"]
        self.assertTrue(journals)
        for index in journals:
            journal = events[index][1]
            removals = [(i, path) for i, (action, path) in enumerate(events[:index]) if action == "unlink" and path.parent.name == "2026-10-05"]
            self.assertTrue(removals)
            for removed_at, path in removals:
                self.assertIn(("sync", path.parent), events[removed_at + 1:index])
            self.assertEqual(events[index + 1], ("sync", journal.parent))

    def test_success_and_rollback_durability_order(self):
        self.schema()
        events, unlink, sync = self.durability_events()
        with unlink, sync:
            self.assertEqual(self.run_import("--confirm")[0], 0)
        docs = self.root / "docs"
        self.assertEqual(events[:3], [("sync", self.root), ("sync", docs), ("sync", docs)])
        self.assert_cleanup_order(events)
        # Use a second source to exercise rollback without duplicate short-circuit.
        self.source = self.root / "second.pdf"
        self.source.write_bytes(b"%PDF-1.7\nsecond synthetic")
        with sqlite3.connect(self.root / "health.db") as db:
            db.executescript("CREATE TRIGGER fail BEFORE INSERT ON lab_results BEGIN SELECT RAISE(ABORT,'synthetic failure'); END;")
        events, unlink, sync = self.durability_events()
        with unlink, sync:
            self.assertEqual(self.run_import("--confirm")[0], 1)
        self.assert_cleanup_order(events)

    def test_recovery_durability_both_commit_states(self):
        self.schema()
        class Crash(BaseException):
            pass
        real_link = os.link
        def crash_link(source, destination):
            real_link(source, destination)
            raise Crash()
        with patch.object(importer.os, "link", side_effect=crash_link):
            with self.assertRaises(Crash):
                self.run_import("--confirm")
        events, unlink, sync = self.durability_events()
        with unlink, sync:
            self.assertEqual(self.run_import("--confirm")[0], 0)
        self.assert_cleanup_order(events)
        # Crash after DB commit, before temporary cleanup: recovery must keep original.
        self.source = self.root / "committed.pdf"
        self.source.write_bytes(b"%PDF-1.7\ncommitted synthetic")
        real_unlink = Path.unlink
        def crash_unlink(path, *args, **kwargs):
            if path.name.startswith(".medical-import-"):
                raise Crash()
            return real_unlink(path, *args, **kwargs)
        with patch.object(Path, "unlink", crash_unlink):
            with self.assertRaises(Crash):
                self.run_import("--confirm")
        events, unlink, sync = self.durability_events()
        with unlink, sync:
            self.assertEqual(self.run_import("--confirm")[1]["status"], "duplicate")
        self.assert_cleanup_order(events)
        self.assertTrue((self.root / "docs" / "2026-10-05" / "committed.pdf").exists())

    def test_failed_compensation_retains_journal(self):
        self.schema(failing=True)
        def sync(path):
            if path.name == "2026-10-05" and list((self.root / "docs" / ".import-journal").glob("*.json")):
                raise OSError("synthetic fsync failure")
        with patch.object(importer, "sync_directory", side_effect=sync):
            self.assertEqual(self.run_import("--confirm")[0], 1)
        self.assertTrue(list((self.root / "docs" / ".import-journal").glob("*.json")))
        events, unlink, sync = self.durability_events()
        with unlink, sync:
            self.run_import("--confirm")
        journal_index = next(i for i, (action, path) in enumerate(events) if action == "unlink" and path.suffix == ".json")
        self.assertIn(("sync", self.root / "docs" / "2026-10-05"), events[:journal_index])

    def test_config_fail_closed(self):
        for text in ('providers: {}', 'providers:\n  newapi:\n    api: http://192.0.2.1:3000/v1\n', 'providers:\n  newapi:\n    api: https://example.com/v1\n    key_env: OTHER\n'):
            (self.root / "config.yaml").write_text(text)
            with self.assertRaises((KeyError, ValueError)):
                extract.configured_endpoint()

    def test_crash_recovery_and_lab_name(self):
        self.schema()
        with sqlite3.connect(self.root / "health.db") as db:
            db.execute("ALTER TABLE lab_results ADD COLUMN lab_name TEXT")
        self.payload["lab_name"] = "Synthetic Lab"
        real_link = os.link
        class Crash(BaseException):
            pass
        def crash_link(source, destination):
            real_link(source, destination)
            raise Crash()
        with patch.object(importer.os, "link", side_effect=crash_link):
            with self.assertRaises(Crash):
                self.run_import("--confirm")
        self.assertTrue(list((self.root / "docs").rglob("source.pdf")))
        self.assertEqual(self.run_import("--confirm")[1]["status"], "created")
        with sqlite3.connect(self.root / "health.db") as db:
            self.assertEqual(db.execute("SELECT lab_name FROM lab_results").fetchone()[0], "Synthetic Lab")
        self.assertFalse(list((self.root / "docs" / ".import-journal").glob("*.json")))


if __name__ == "__main__":
    unittest.main()
