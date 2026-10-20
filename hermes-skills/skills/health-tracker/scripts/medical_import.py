#!/opt/data/home/.venv/bin/python
"""Validated local medical document importer for the container deployment."""
from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import os
import shutil
import sqlite3
import sys
import unicodedata
import tempfile
import stat
from pathlib import Path
from medical_extract import strict_json, validate

SKILL_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = Path("/opt/data/personal/health/health.db")
DOCUMENT_ROOT = Path("/opt/data/personal/health/documents")


def normalized_hash(payload: dict) -> str:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    text = " ".join(unicodedata.normalize("NFC", text).lower().split())
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_payload(path: Path, kind: str) -> dict:
    payload = validate(strict_json(path.read_text(encoding="utf-8")), kind)
    if not isinstance(payload, dict):
        raise ValueError("metadata JSON must be an object")
    required = {"report_date"}
    if kind == "lab":
        required.add("results")
        if not isinstance(payload.get("results"), list) or not payload["results"]:
            raise ValueError("lab payload requires non-empty results list")
        for index, item in enumerate(payload["results"], 1):
            if not isinstance(item, dict) or not item.get("parameter") or not item.get("test_type"):
                raise ValueError(f"lab result #{index} requires parameter and test_type")
    else:
        required.add("conclusion")
    missing = sorted(key for key in required if not payload.get(key))
    if missing:
        raise ValueError("missing fields: " + ", ".join(missing))
    return payload


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def recover_journals(conn, directory):
    """Called under BEGIN IMMEDIATE; reclaim only originals owned by our journal."""
    for journal in directory.glob("*.json"):
        info = journal.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise RuntimeError("unsafe import journal")
        record = strict_json(journal.read_text())
        destination = Path(record["destination"])
        temporary = Path(record["temporary"])
        for path in (destination, temporary):
            if not path.parent.resolve().is_relative_to(DOCUMENT_ROOT.resolve()) or path.is_symlink():
                raise RuntimeError("unsafe journal path")
        committed = conn.execute("SELECT id FROM documents WHERE path=?", (str(destination),)).fetchone()
        if committed and table_exists(conn, "document_imports"):
            digest = conn.execute("SELECT sha256 FROM document_imports WHERE document_id=?", (committed[0],)).fetchone()
            if digest is None or digest[0] != record["sha256"]:
                raise RuntimeError("committed document journal SHA mismatch")
        if committed and not destination.is_file():
            raise RuntimeError("committed journal original missing")
        if destination.exists():
            info = destination.stat()
            if [info.st_dev, info.st_ino] != record["identity"] or sha256_file(destination) != record["sha256"]:
                raise RuntimeError("journal original identity mismatch; manual recovery required")
            if not committed:
                destination.unlink()
        if temporary.exists():
            info = temporary.stat()
            if [info.st_dev, info.st_ino] != record["identity"] or sha256_file(temporary) != record["sha256"]:
                raise RuntimeError("journal temporary identity mismatch")
            temporary.unlink()
        # Also sync on retry when an earlier cleanup unlinked but failed fsync.
        for parent in {destination.parent, temporary.parent}:
            sync_directory(parent)
        journal.unlink()
        sync_directory(directory)


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Import a user-confirmed medical original and structured draft"
    )
    parser.add_argument("kind", choices=("lab", "doctor"))
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--metadata-json", required=True, type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--confirm", action="store_true",
        help="Required after the user explicitly confirms the shown draft",
    )
    args = parser.parse_args()

    if args.source.is_symlink():
        parser.error("source symlinks are not accepted")
    source = args.source.resolve()
    metadata_path = args.metadata_json.resolve()
    if not source.is_file():
        parser.error(f"source does not exist: {source}")
    if source.is_relative_to(DOCUMENT_ROOT.resolve()):
        parser.error("archived originals cannot be used as new import sources")
    if not metadata_path.is_file():
        parser.error(f"metadata JSON does not exist: {metadata_path}")

    try:
        payload = load_payload(metadata_path, args.kind)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False))
        return 2

    original_hash = sha256_file(source)
    ocr_hash = normalized_hash(payload)
    report_date = payload["report_date"]
    destination = DOCUMENT_ROOT / report_date / source.name
    preview = {
        "status": "dry_run" if args.dry_run else "ready",
        "kind": args.kind,
        "source_name": source.name,
        "report_date": report_date,
        "destination": str(destination),
        "sha256": original_hash,
        "record_count": len(payload.get("results", [])) if args.kind == "lab" else 1,
    }

    if args.dry_run:
        print(json.dumps(preview, ensure_ascii=False, indent=2))
        return 0
    if not args.confirm:
        print(json.dumps({
            "message": "Show the structured draft to the user, obtain explicit confirmation, then rerun with --confirm.",
            **preview,
            "status": "confirmation_required",
        }, ensure_ascii=False, indent=2))
        return 3

    if not DB_PATH.is_file():
        print(json.dumps({"status": "error", "message": "database must already exist"}))
        return 2
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    temporary = None
    published = False
    journal = None
    committed = False
    try:
        conn.execute("BEGIN IMMEDIATE")
        journal_dir = DOCUMENT_ROOT / ".import-journal"
        DOCUMENT_ROOT.mkdir(parents=True, exist_ok=True)
        sync_directory(DOCUMENT_ROOT.parent)
        if journal_dir.is_symlink():
            raise RuntimeError("unsafe journal directory")
        journal_dir.mkdir(mode=0o700, exist_ok=True)
        sync_directory(DOCUMENT_ROOT)
        info = journal_dir.stat()
        if info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise RuntimeError("journal directory must be owner-only")
        recover_journals(conn, journal_dir)
        if table_exists(conn, "document_imports"):
            duplicate = conn.execute(
                "SELECT document_id FROM document_imports WHERE sha256=?", (original_hash,)
            ).fetchone()
            if duplicate:
                print(json.dumps({"status": "duplicate", "document_id": duplicate[0]}, ensure_ascii=False))
                return 0

        # Preserve legacy rows without backfilling or migrating dedup metadata.
        for row in conn.execute("SELECT id,path FROM documents"):
            existing = Path(row["path"])
            if existing.is_file() and sha256_file(existing) == original_hash:
                print(json.dumps({"status": "duplicate", "document_id": row["id"]}))
                return 0

        if not destination.parent.resolve().is_relative_to(DOCUMENT_ROOT.resolve()):
            raise RuntimeError("destination escapes document root")
        destination.parent.mkdir(parents=True, exist_ok=True)
        sync_directory(DOCUMENT_ROOT)
        if destination.exists() or destination.is_symlink():
            raise RuntimeError("destination already exists; original will not be overwritten")
        fd, name = tempfile.mkstemp(prefix=".medical-import-", dir=destination.parent)
        os.close(fd)
        temporary = Path(name)
        shutil.copy2(source, temporary)
        if sha256_file(temporary) != original_hash:
            temporary.unlink(missing_ok=True)
            raise RuntimeError("copied original failed SHA-256 verification")
        os.chmod(temporary, 0o600)
        with temporary.open("rb") as stream:
            os.fsync(stream.fileno())
        sync_directory(temporary.parent)
        info = temporary.stat()
        fd, name = tempfile.mkstemp(prefix="import-", suffix=".pending", dir=journal_dir)
        pending_journal = Path(name)
        with os.fdopen(fd, "w") as stream:
            json.dump({"destination": str(destination), "temporary": str(temporary), "sha256": original_hash, "identity": [info.st_dev, info.st_ino]}, stream)
            stream.flush()
            os.fsync(stream.fileno())
        journal = pending_journal.with_suffix(".json")
        pending_journal.rename(journal)
        sync_directory(journal_dir)

        cur = conn.execute(
            "INSERT INTO documents(path, lab_name, report_date) VALUES(?,?,?)",
            (str(destination), payload.get("lab_name"), report_date),
        )
        document_id = cur.lastrowid
        if args.kind == "lab":
            has_lab_name = any(column[1] == "lab_name" for column in conn.execute("PRAGMA table_info(lab_results)"))
            for item in payload["results"]:
                conn.execute(
                    "INSERT INTO lab_results(document_id,date,test_type,parameter,value,unit,ref_min,ref_max,is_critical) "
                    "VALUES(?,?,?,?,?,?,?,?,?)",
                    (
                        document_id, report_date, item["test_type"], item["parameter"],
                        item.get("value"), item.get("unit"), item.get("ref_min"),
                        item.get("ref_max"), int(bool(item.get("is_critical", False))),
                    ),
                )
                if has_lab_name:
                    conn.execute("UPDATE lab_results SET lab_name=? WHERE rowid=last_insert_rowid()", (payload.get("lab_name"),))
        else:
            conn.execute(
                "INSERT INTO doctor_notes(date,doctor_name,specialization,conclusion,document_id) VALUES(?,?,?,?,?)",
                (
                    report_date, payload.get("doctor_name"), payload.get("specialization"),
                    payload["conclusion"], document_id,
                ),
            )
        if table_exists(conn, "document_imports"):
            mime_type = mimetypes.guess_type(source.name)[0] or "application/octet-stream"
            conn.execute(
                "INSERT INTO document_imports(document_id,sha256,size_bytes,mime_type,original_name,normalized_ocr_hash,source,source_message_id,imported_at) "
                "VALUES(?,?,?,?,?,?,?,?,datetime('now'))",
                (
                    document_id, original_hash, source.stat().st_size, mime_type, source.name,
                    ocr_hash, "container-local", payload.get("source_message_id"),
                ),
            )
        row = conn.execute(
            "SELECT id,path,report_date FROM documents WHERE id=?", (document_id,)
        ).fetchone()
        if row is None:
            raise RuntimeError("read-back failed")
        # Exclusive publication does not overwrite a concurrent or legacy original.
        os.link(temporary, destination)
        published = True
        sync_directory(destination.parent)
        conn.commit()
        committed = True
        temporary.unlink()
        sync_directory(temporary.parent)
        journal.unlink()
        sync_directory(journal_dir)
        print(json.dumps({
            "status": "created", "document_id": document_id,
            "path": row["path"], "report_date": row["report_date"],
            "record_count": preview["record_count"], "sha256": original_hash,
        }, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        conn.rollback()
        try:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            if published and not committed:
                destination.unlink(missing_ok=True)
            if temporary is not None:
                sync_directory(temporary.parent)
            if published and not committed and (temporary is None or destination.parent != temporary.parent):
                sync_directory(destination.parent)
            # Never discard recovery evidence before compensation is durable.
            if journal is not None:
                journal.unlink(missing_ok=True)
                sync_directory(journal.parent)
        except OSError:
            exc = RuntimeError("cleanup durability failed; retained journal requires recovery")
        print(json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False))
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
