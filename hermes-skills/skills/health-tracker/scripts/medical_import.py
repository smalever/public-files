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
from pathlib import Path

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
    payload = json.loads(path.read_text(encoding="utf-8"))
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

    source = args.source.resolve()
    metadata_path = args.metadata_json.resolve()
    if not source.is_file():
        parser.error(f"source does not exist: {source}")
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
            "status": "confirmation_required",
            "message": "Show the structured draft to the user, obtain explicit confirmation, then rerun with --confirm.",
            **preview,
        }, ensure_ascii=False, indent=2))
        return 3

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        if table_exists(conn, "document_imports"):
            duplicate = conn.execute(
                "SELECT document_id FROM document_imports WHERE sha256=?", (original_hash,)
            ).fetchone()
            if duplicate:
                print(json.dumps({"status": "duplicate", "document_id": duplicate[0]}, ensure_ascii=False))
                return 0

        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists() and sha256_file(destination) != original_hash:
            raise RuntimeError(f"destination exists with different content: {destination}")
        temporary = destination.with_name(destination.name + ".tmp-import")
        shutil.copy2(source, temporary)
        if sha256_file(temporary) != original_hash:
            temporary.unlink(missing_ok=True)
            raise RuntimeError("copied original failed SHA-256 verification")

        conn.execute("BEGIN IMMEDIATE")
        cur = conn.execute(
            "INSERT INTO documents(path, lab_name, report_date) VALUES(?,?,?)",
            (str(destination), payload.get("lab_name"), report_date),
        )
        document_id = cur.lastrowid
        if args.kind == "lab":
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
        temporary.replace(destination)
        conn.commit()
        print(json.dumps({
            "status": "created", "document_id": document_id,
            "path": row["path"], "report_date": row["report_date"],
            "record_count": preview["record_count"], "sha256": original_hash,
        }, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        conn.rollback()
        temporary = destination.with_name(destination.name + ".tmp-import")
        temporary.unlink(missing_ok=True)
        print(json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False))
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
