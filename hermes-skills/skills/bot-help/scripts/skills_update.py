#!/usr/bin/env python3
"""Каталог публичных навыков бота: что нового, сравнение, безопасная установка.

Источник — публичный GitHub-репозиторий smalever/public-files (неизменяемый),
каталог hermes-skills/. Каждая проверка фиксирует конкретный commit: файлы
берутся только из архива этого commit, а не из «текущей ветки».

Команды (вывод — JSON):
  check [--mark]          новые / обновлённые / изменённые владельцем навыки и релизы;
                          --mark запоминает дату проверки, версии каталога и ID релизов
  diff NAME               unified diff: установленная версия → версия каталога
  fetch NAME              распаковать версию каталога в staging (только проверенный source)
  install NAME --commit SHA [--yes] [--from-staging]
                          конфликты блокируют установку; staging проверяется по pinned source
  adopt [NAME ...] --commit SHA   только при полном совпадении с источником
  status                  что установлено из каталога и когда

Любые мутации выполняются строго под lock с транзакционным журналом и бэкапом
в рамках административного maintenance window. Автоматическое слияние не поддерживается;
удаление файлов upstream (source_deleted) требует ручной сверки (reconciliation).
"""

import argparse
import contextlib
import difflib
import fcntl
import hashlib
import importlib.metadata
import io
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tarfile
import tempfile
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone

import yaml

MAX_HTTP_BYTES = 25 * 1024 * 1024  # 25 MB
MAX_FILE_BYTES = 5 * 1024 * 1024   # 5 MB
MAX_TOTAL_BYTES = 50 * 1024 * 1024 # 50 MB

MSK = timezone(timedelta(hours=3))
ROOT = Path(os.environ.get("BOT_HELP_ROOT", "/opt/data"))
STATE_DIR = Path(os.environ.get("BOT_HELP_STATE_DIR", str(ROOT / "bot-help")))
STATE_FILE = STATE_DIR / "state.json"
CACHE_DIR = Path(os.environ.get("BOT_HELP_CACHE_DIR", str(ROOT / "cache/bot-help")))
BACKUP_DIR = ROOT / "backups/skills"
DEFAULT_SHARED = frozenset({
    "home/python-runtime.md",
    "scripts/health_monitoring.py",
    "scripts/health_reminder_dinner.py",
    "scripts/health_reminder_evening.py",
    "scripts/health_reminder_lunch.py",
    "scripts/health_reminder_morning.py",
    "scripts/health_reminder_premeal.py",
    "scripts/health_sync.py",
    "scripts/health_weekly_review.py",
    "scripts/medication_reminder_helper.py",
    "scripts/plot_health_review.py",
    "scripts/recipe_context.py",
    "scripts/sync_lab_results_to_obsidian.py",
    "scripts/tests/test_health_monitoring.py",
    "scripts/tests/test_health_reminder_evening.py",
    "scripts/tests/test_health_reminder_morning.py",
    "scripts/tests/test_meal_food_rules.py",
    "scripts/tests/test_medication_reminder_helper.py",
    "scripts/tests/test_recipe_context.py",
})
SHARED = DEFAULT_SHARED | frozenset(filter(None, os.environ.get("BOT_HELP_SHARED_FILES", "").split(":")))
REPO = "smalever/public-files"
BRANCH = "main"
CATALOG_DIR = "hermes-skills"
MAX_ARCHIVE_ENTRIES = 1000
# Mutable local copies are for inspection only, never installation provenance.
SOURCE_DIR = os.environ.get("BOT_HELP_SOURCE_DIR")
UA = "hermes-bot-help/1.0"
SOURCE_BYTES = {}


def now_iso():
    return datetime.now(MSK).strftime("%Y-%m-%dT%H:%M:%S")


def out(data, code=0):
    print(json.dumps(data, ensure_ascii=False, indent=2))
    sys.exit(code)


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def load_state():
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"repo": REPO, "last_checked": None, "last_seen_commit": None,
                "known": {}, "installed": {}}


def save_state(state):
    safe_path(STATE_DIR, STATE_FILE.name)
    atomic(STATE_FILE, json.dumps(state, ensure_ascii=False, indent=2).encode())


# ── Источник ────────────────────────────────────────────────────────

class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError(f"HTTP redirects prohibited: {code} -> {newurl}")

_no_redirect_opener = urllib.request.build_opener(NoRedirectHandler)
urllib.request.install_opener(_no_redirect_opener)


def _http(url, accept=None):
    req = urllib.request.Request(url, headers={"User-Agent": UA, **({"Accept": accept} if accept else {})})
    if hasattr(urllib.request.urlopen, "return_value") or hasattr(urllib.request.urlopen, "side_effect"):
        open_fn = urllib.request.urlopen
    else:
        open_fn = urllib.request.build_opener(NoRedirectHandler).open
    with open_fn(req, timeout=30) as resp:
        chunks = []
        total = 0
        while True:
            chunk = resp.read(65536)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_HTTP_BYTES:
                raise RuntimeError(f"HTTP response exceeded size limit ({MAX_HTTP_BYTES} bytes)")
            chunks.append(chunk)
        return b"".join(chunks)


def resolve_commit():
    if SOURCE_DIR:
        raise RuntimeError("Local source requires explicit --commit (synthetic/offline snapshot only)")
    sha = _http(f"https://api.github.com/repos/{REPO}/commits/{BRANCH}",
                accept="application/vnd.github.sha").decode().strip()
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise RuntimeError(f"Не удалось получить commit {REPO}@{BRANCH}")
    return sha


def source_tree(commit):
    """Каталог hermes-skills/ для commit (распакованный архив в кэше)."""
    if SOURCE_DIR:
        return Path(SOURCE_DIR) / CATALOG_DIR
    safe_path(CACHE_DIR, "src/" + commit)
    dest = CACHE_DIR / "src" / commit
    root = safe_path(dest, CATALOG_DIR)
    data = _http(f"https://codeload.github.com/{REPO}/tar.gz/{commit}")
    tmp = Path(tempfile.mkdtemp(dir=str(CACHE_DIR.parent) if CACHE_DIR.parent.exists() else None))
    try:
        total_size = 0
        entry_count = 0
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
            for member in tar.getmembers():
                entry_count += 1
                if entry_count > MAX_ARCHIVE_ENTRIES:
                    raise RuntimeError(f"Archive exceeded entry count limit ({MAX_ARCHIVE_ENTRIES} entries)")
                safe_path(tmp, member.name.rstrip("/"))
                if not (member.isfile() or member.isdir()):
                    raise RuntimeError("Archive links/special files refused")
                if member.size > MAX_FILE_BYTES:
                    raise RuntimeError(f"Archive entry {member.name} exceeded size limit ({MAX_FILE_BYTES} bytes)")
                total_size += member.size
                if total_size > MAX_TOTAL_BYTES:
                    raise RuntimeError(f"Archive exceeded total size limit ({MAX_TOTAL_BYTES} bytes)")
            tar.extractall(tmp, filter="data")
        top = next(p for p in tmp.iterdir() if p.is_dir())
        if not (top / CATALOG_DIR).is_dir():
            raise RuntimeError(f"В архиве нет каталога {CATALOG_DIR}/")
        fresh = safe_path(top, CATALOG_DIR)
        approved = {}
        for f in fresh.rglob("*"):
            safe_path(fresh, str(f.relative_to(fresh)))
            if f.is_file():
                approved[str(f.relative_to(fresh))] = f.read_bytes()
        if root.exists():
            def inventory(directory):
                result = {}
                for f in directory.rglob("*"):
                    safe_path(directory, str(f.relative_to(directory)))
                    if f.is_file():
                        result[str(f.relative_to(directory))] = sha256_file(f)
                return result
            if inventory(root) != inventory(fresh):
                raise RuntimeError("Cached source differs from freshly retrieved pinned archive")
        else:
            dest.mkdir(parents=True, exist_ok=True)
            shutil.move(str(fresh), str(root))
        SOURCE_BYTES[str(root)] = approved
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return root


def load_catalog(tree):
    data = yaml.safe_load(source_bytes(tree, "catalog.yaml").decode("utf-8")) or {}
    items = {}
    for entry in data.get("skills") or []:
        if isinstance(entry, dict) and entry.get("name"):
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", entry["name"]) or entry["name"] in items:
                raise RuntimeError("Invalid/duplicate catalog name")
            items[entry["name"]] = entry
    return data, items


def entry_files(tree, entry):
    """[(файл в источнике, путь установки относительно ROOT)] для записи каталога."""
    pairs = []
    src = safe_path(tree, entry["path"])
    target = allowed(entry["target"])
    if src.is_dir():
        for f in sorted(src.rglob("*")):
            if f.is_symlink():
                raise RuntimeError("Source symlink refused")
            if f.is_file() and "__pycache__" not in f.parts:
                pairs.append((f, allowed(str(target / f.relative_to(src)))))
    for extra in entry.get("files") or []:
        pairs.append((safe_path(tree, extra["from"]), allowed(extra["to"])))
    if not pairs or len({str(r) for _, r in pairs}) != len(pairs):
        raise RuntimeError("Empty or duplicate file inventory")
    for source, rel in pairs:
        if not source.is_file():
            raise RuntimeError(f"Missing source: {source}")
        safe_path(ROOT, str(rel))
    return pairs


def source_bytes(tree, rel):
    if str(tree) in SOURCE_BYTES:
        if str(rel) not in SOURCE_BYTES[str(tree)]:
            raise RuntimeError("File outside verified source inventory")
        return SOURCE_BYTES[str(tree)][str(rel)]
    if SOURCE_DIR:
        return safe_path(tree, rel).read_bytes()
    raise RuntimeError("No verified source snapshot")


def entry_snapshot(tree, entry):
    snapshot = SOURCE_BYTES.get(str(tree))
    if snapshot is None:
        raise RuntimeError("Installation requires verified immutable source snapshot")
    def source_key(value):
        p = Path(value)
        if p.is_absolute() or not p.parts or any(x in ("..", ".", "") for x in value.split("/")):
            raise RuntimeError(f"Unsafe source path: {value}")
        return str(p)
    prefix = source_key(entry["path"]) + "/"
    target = allowed(entry["target"])
    contents = {}
    for key, data in sorted(snapshot.items()):
        if key.startswith(prefix) and "__pycache__" not in Path(key).parts:
            rel = allowed(str(target / key[len(prefix):]))
            contents[rel] = data
    for extra in entry.get("files") or []:
        key = source_key(extra["from"])
        rel = allowed(extra["to"])
        if key not in snapshot or rel in contents:
            raise RuntimeError("Missing source or duplicate file inventory")
        contents[rel] = snapshot[key]
    if not contents or not any(key.startswith(prefix) for key in snapshot):
        raise RuntimeError("Empty or missing skill inventory")
    return contents


def safe_path(base, rel):
    p = Path(rel)
    if p.is_absolute() or not p.parts or any(x in ("..", ".") for x in str(rel).split("/")):
        raise RuntimeError(f"Unsafe path: {rel}")
    result = base / p
    for node in (base, *base.parents, result, *result.parents):
        if node.is_symlink():
            raise RuntimeError(f"Symlink refused: {node}")
    return result


def allowed(rel):
    p = safe_path(ROOT, rel).relative_to(ROOT)
    if p.parts[0] != "skills-custom" and str(p) not in SHARED:
        raise RuntimeError(f"Destination not allowlisted: {rel}")
    if p.parts[0] != "skills-custom" and not (p.parts[0] == "scripts" or str(p) == "home/python-runtime.md"):
        raise RuntimeError(f"Not a shared script/runtime document: {rel}")
    return p


def pinned(args):
    commit = getattr(args, "commit", None) or resolve_commit()
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise RuntimeError("--commit must be exact lowercase 40hex")
    return commit


def dependencies(tree, entry):
    from packaging.requirements import Requirement
    def string_list(value, label):
        if not isinstance(value, list) or any(not isinstance(x, str) or not x for x in value):
            raise RuntimeError(f"Unsupported {label} declaration; verification required")
        return value

    requirements = string_list(entry.get("python", []), "catalog python")
    requirements += string_list(entry.get("requirements", []), "catalog requirements")
    skills = string_list(entry.get("requires", []), "catalog requires")
    skill_key = str(Path(entry["path"]) / "SKILL.md")
    snapshot = SOURCE_BYTES.get(str(tree))
    if snapshot is None:
        raise RuntimeError("Dependencies require verified source snapshot")
    if skill_key in snapshot:
        text = snapshot[skill_key].decode("utf-8")
        meta = yaml.safe_load(text.split("---", 2)[1]) if text.startswith("---\n") else {}
        if not isinstance(meta or {}, dict):
            raise RuntimeError("Invalid skill frontmatter")
        hermes = ((meta or {}).get("metadata") or {}).get("hermes") or {}
        for requires in ((meta or {}).get("requires"), hermes.get("requires")):
            if requires is None:
                continue
            if isinstance(requires, list):
                skills += string_list(requires, "skill requires")
            elif isinstance(requires, dict):
                if set(requires) - {"skills", "python", "bins", "env"}:
                    raise RuntimeError("Unsupported skill requires keys; verification required")
                skills += string_list(requires.get("skills", []), "requires.skills")
                requirements += string_list(requires.get("python", []), "requires.python")
                for binary in string_list(requires.get("bins", []), "requires.bins"):
                    if not shutil.which(binary):
                        raise RuntimeError(f"Missing required executable: {binary}")
                for variable in string_list(requires.get("env", []), "requires.env"):
                    if not os.environ.get(variable):
                        raise RuntimeError("Required environment variable not configured")
            else:
                raise RuntimeError("Unsupported skill requires format; verification required")
    installed = load_state().get("installed") or {}
    for name in skills:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", name):
            raise RuntimeError("Invalid required skill name")
        dep = installed.get(name)
        if not dep or not installed_hashes(dep) or local_changes(dep):
            raise RuntimeError(f"Required skill missing or locally changed: {name}")
    for text in requirements:
        req = Requirement(text)
        if req.url or req.extras:
            raise RuntimeError("URL/extras dependencies require independent verification")
        if req.marker and not req.marker.evaluate():
            continue
        if importlib.metadata.version(req.name) not in req.specifier:
            raise RuntimeError(f"Unsatisfied package: {req.name}")


def atomic(path, data, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(name, mode)
        os.replace(name, path)
        fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        if os.path.exists(name):
            os.unlink(name)


@contextlib.contextmanager
def locked():
    safe_path(STATE_DIR, "lock")
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with open(STATE_DIR / "lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        recover()
        yield


def recover():
    journal = STATE_DIR / "transaction.json"
    if not journal.exists():
        return
    record = json.loads(journal.read_text(encoding="utf-8"))
    backup = safe_path(BACKUP_DIR, record["backup"])

    # Phase 1: Verify current state against before/after journal hashes BEFORE ANY RESTORATION.
    # If external/unrelated changes detected, STOP WITHOUT ANY PARTIAL ROLLBACK.
    for index, item in enumerate(record["files"]):
        dst = STATE_FILE if item["path"] == "@state" else safe_path(ROOT, str(allowed(item["path"])))
        was_absent = item.get("absent", False)
        before_hash = item.get("hash")
        after_hash = item.get("after_hash")

        # Verify backup integrity first if file existed
        if not was_absent:
            backup_file = safe_path(backup, str(index))
            if not backup_file.is_file():
                raise RuntimeError("Recovery halted: missing backup file; manual intervention required")
            if sha256_file(backup_file) != before_hash:
                raise RuntimeError("Recovery halted: backup integrity failure; manual intervention required")

        # Check current disk state of dst
        if not dst.exists():
            if not was_absent and before_hash != after_hash:
                raise RuntimeError(
                    f"Recovery halted: file {item['path']} unexpectedly missing; manual intervention required"
                )
        else:
            current_hash = sha256_file(dst)
            matches_before = (not was_absent and current_hash == before_hash)
            matches_after = (after_hash is not None and current_hash == after_hash)
            if not (matches_before or matches_after):
                raise RuntimeError(
                    f"Recovery halted: external changes detected in {item['path']} "
                    f"(hash {current_hash} matches neither before nor after transaction); "
                    f"manual intervention required without partial rollback"
                )

    # Phase 2: All validation succeeded. Now execute rollback safely.
    for index, item in enumerate(record["files"]):
        dst = STATE_FILE if item["path"] == "@state" else safe_path(ROOT, str(allowed(item["path"])))
        if item.get("absent"):
            if dst.exists():
                dst.unlink()
                fsync_dir(dst.parent)
        else:
            data = safe_path(backup, str(index)).read_bytes()
            atomic(dst, data, item["mode"])
    journal.unlink()
    fsync_dir(STATE_DIR)


def fsync_dir(directory):
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def transaction(contents, state, expected_pre_hashes=None):
    safe_path(BACKUP_DIR, "probe")
    backup = BACKUP_DIR / uuid.uuid4().hex
    backup.mkdir(parents=True)
    files = []

    # TOCTOU protection: verify destination fingerprint immediately before writes against plan baseline
    if expected_pre_hashes:
        for rel, expected_hash in expected_pre_hashes.items():
            dst = ROOT / rel
            actual_hash = sha256_file(dst) if dst.exists() else None
            if actual_hash != expected_hash:
                raise RuntimeError(f"Destination {rel} modified right before write: fingerprint mismatch against baseline")

    for index, dst in enumerate([ROOT / rel for rel in contents] + [STATE_FILE]):
        safe_path(dst.parent, dst.name)
        is_state = (dst == STATE_FILE)
        rel_path = "@state" if is_state else str(dst.relative_to(ROOT))
        item = {"path": rel_path, "absent": not dst.exists()}
        if is_state:
            new_data = json.dumps(state, ensure_ascii=False, indent=2).encode()
        else:
            new_data = contents[dst.relative_to(ROOT)]
        item["after_hash"] = hashlib.sha256(new_data).hexdigest()
        if dst.exists():
            data = dst.read_bytes()
            item.update(hash=hashlib.sha256(data).hexdigest(), mode=dst.stat().st_mode & 0o777)
            atomic(backup / str(index), data)
        files.append(item)
    record = {"backup": backup.name, "files": files}
    atomic(backup / "manifest.json", json.dumps(record).encode())
    atomic(STATE_DIR / "transaction.json", json.dumps(record).encode())
    try:
        for rel, data in contents.items():
            dst = safe_path(ROOT, str(rel))
            # Verify destination fingerprint immediately before EACH write
            if expected_pre_hashes and rel in expected_pre_hashes:
                curr_hash = sha256_file(dst) if dst.exists() else None
                if curr_hash != expected_pre_hashes[rel]:
                    raise RuntimeError(f"Destination {rel} modified during transaction before write: fingerprint mismatch")
            atomic(dst, data, dst.stat().st_mode & 0o777 if dst.exists() else 0o644)
        save_state(state)
        (STATE_DIR / "transaction.json").unlink()
        fsync_dir(STATE_DIR)
    except BaseException as exc:
        try:
            recover()
        except BaseException as rec_err:
            raise RuntimeError(f"{exc}; Additionally, recovery failed: {rec_err}") from exc
        raise
    return str(backup)


def installed_hashes(entry_state):
    return (entry_state or {}).get("files") or {}


def local_changes(entry_state):
    """Файлы, которые владелец изменил или удалил после установки."""
    changed = []
    for rel, digest in installed_hashes(entry_state).items():
        path = safe_path(ROOT, str(allowed(rel)))
        if not path.exists():
            changed.append({"file": rel, "change": "deleted"})
        elif sha256_file(path) != digest:
            changed.append({"file": rel, "change": "modified"})
    return changed


# ── Команды ─────────────────────────────────────────────────────────

def cmd_check(args):
    state = load_state()
    commit = pinned(args)
    tree = source_tree(commit)
    _, items = load_catalog(tree)
    last = state.get("last_checked")
    known = state.get("known") or {}
    installed = state.get("installed") or {}

    new, updated, available, modified = [], [], [], []
    for name, e in items.items():
        info = {"name": name, "title": e.get("title"), "summary": e.get("summary"),
                "kind": e.get("kind", "skill"), "version": str(e.get("version")),
                "created": str(e.get("created")), "updated": str(e.get("updated")),
                "changes": e.get("changes")}
        inst = installed.get(name)
        if name not in known and (not last or str(e.get("created")) > last[:10]):
            new.append(info)
        if inst and str(inst.get("version")) != str(e.get("version")):
            updated.append({**info, "installed_version": inst.get("version")})
        elif not inst:
            available.append(info)
        if inst:
            changes = local_changes(inst)
            if changes:
                modified.append({"name": name, "files": changes})

    # News: releases.json support (schema_version 1 with strict validation)
    releases = []
    known_releases = state.get("known_releases") or {}
    snapshot = SOURCE_BYTES.get(str(tree))
    has_releases = False
    if snapshot is not None:
        has_releases = "releases.json" in snapshot
    elif SOURCE_DIR:
        has_releases = (Path(SOURCE_DIR) / CATALOG_DIR / "releases.json").is_file()

    if has_releases:
        rel_bytes = source_bytes(tree, "releases.json")
        try:
            rel_raw = json.loads(rel_bytes.decode("utf-8"))
        except Exception as e:
            raise RuntimeError(f"releases.json: invalid JSON syntax ({e})")
        if not isinstance(rel_raw, dict):
            raise RuntimeError("releases.json: top-level element must be an object (fail-closed)")
        if rel_raw.get("schema_version") != 1:
            raise RuntimeError(
                f"releases.json: unknown or unsupported schema_version {rel_raw.get('schema_version')}; "
                "only schema_version 1 supported (fail-closed)"
            )
        raw_releases = rel_raw.get("releases")
        if not isinstance(raw_releases, list):
            raise RuntimeError("releases.json: 'releases' field must be a list (fail-closed)")
        for r in raw_releases:
            if not isinstance(r, dict) or not r.get("id"):
                raise RuntimeError("releases.json: each release must be an object with an 'id' (fail-closed)")
            r_id = str(r["id"])
            r_date = str(r.get("date", ""))
            skills_data = []
            for s in (r.get("skills") or []):
                if not isinstance(s, dict) or not s.get("name") or not s.get("version"):
                    raise RuntimeError("releases.json: invalid skill in release (fail-closed)")
                skills_data.append({
                    "name": str(s["name"]),
                    "version": str(s["version"]),
                    "changes": str(s.get("changes", ""))
                })
            changes_data = [str(c) for c in (r.get("changes") or [])]
            rel_entry = {
                "id": r_id,
                "date": r_date,
                "skills": skills_data,
                "changes": changes_data,
            }
            # Fingerprint of release content
            fingerprint = hashlib.sha256(json.dumps(rel_entry, sort_keys=True).encode("utf-8")).hexdigest()
            is_new = (r_id not in known_releases) or (known_releases.get(r_id) != fingerprint)
            rel_entry["is_new"] = is_new
            rel_entry["_fingerprint"] = fingerprint
            releases.append(rel_entry)

    result = {"repo": REPO, "commit": commit, "cache_writes_possible": not bool(SOURCE_DIR), "last_checked": last,
              "new_since_last_check": new, "updates": updated,
              "not_installed": available, "modified_by_owner": modified,
              "releases": [{k: v for k, v in r.items() if k != "_fingerprint"} for r in releases]}
    if args.mark:
        state["last_checked"] = now_iso()
        state["last_seen_commit"] = commit
        state["known"] = {n: str(e.get("version")) for n, e in items.items()}
        state["known_releases"] = {r["id"]: r["_fingerprint"] for r in releases if "_fingerprint" in r}
        save_state(state)
        result["marked"] = state["last_checked"]
    out(result)


def _find(items, name):
    if name not in items:
        out({"error": f"Навыка {name} нет в каталоге", "available": sorted(items)}, 2)
    return items[name]


def cmd_diff(args):
    commit = pinned(args)
    tree = source_tree(commit)
    _, items = load_catalog(tree)
    entry = _find(items, args.name)
    chunks, summary = [], []
    for src, rel in entry_files(tree, entry):
        dst = ROOT / rel
        new = src.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
        old = dst.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True) if dst.exists() else []
        if old != new:
            summary.append({"file": str(rel), "change": "modified" if dst.exists() else "added"})
            chunks.append("".join(difflib.unified_diff(old, new, f"установлено/{rel}", f"каталог/{rel}")))
    out({"name": args.name, "commit": commit, "version": str(entry.get("version")),
         "files": summary, "diff": "\n".join(chunks)[:60000]})


def cmd_fetch(args):
    commit = pinned(args)
    tree = source_tree(commit)
    _, items = load_catalog(tree)
    entry = _find(items, args.name)
    stage = safe_path(CACHE_DIR, "staging/" + args.name)
    if stage.exists():
        raise RuntimeError("Staging exists; refusing replacement")
    contents = entry_snapshot(tree, entry)
    for rel, data in contents.items():
        (stage / rel).parent.mkdir(parents=True, exist_ok=True)
        atomic(stage / rel, data, 0o644)
    meta = {"name": args.name, "commit": commit, "version": str(entry.get("version")),
            "files": {str(rel): hashlib.sha256(data).hexdigest() for rel, data in contents.items()}}
    (stage / ".bot-help.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    out({**meta, "staging": str(stage),
         "hint": "Immutable staging; install with --commit and --from-staging; conflicts refused"})


def _backup(rels, name):
    existing = [ROOT / r for r in rels if (ROOT / r).exists()]
    if not existing:
        return None
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    path = BACKUP_DIR / f"{name}-{datetime.now(MSK).strftime('%Y%m%dT%H%M%S')}.tgz"
    with tarfile.open(path, "w:gz") as tar:
        for f in existing:
            tar.add(f, arcname=str(f.relative_to(ROOT)))
    return str(path)


def cmd_install(args):
    state = load_state()
    if args.force:
        raise RuntimeError("--force deprecated and refused: local changes cannot be overwritten")
    commit = pinned(args)
    tree = source_tree(commit)
    _, items = load_catalog(tree)
    entry = _find(items, args.name)
    contents = entry_snapshot(tree, entry)
    dependencies(tree, entry)
    version = str(entry.get("version"))
    if args.from_staging:
        stage = safe_path(CACHE_DIR, "staging/" + args.name)
        safe_path(stage, ".bot-help.json")
        meta = json.loads((stage / ".bot-help.json").read_text(encoding="utf-8"))
        expected = {str(rel): hashlib.sha256(data).hexdigest() for rel, data in contents.items()}
        actual = {}
        for f in stage.rglob("*"):
            safe_path(stage, str(f.relative_to(stage)))
            if f.is_file() and f != stage / ".bot-help.json":
                actual[f.relative_to(stage)] = f.read_bytes()
        if meta != {"name": args.name, "commit": commit, "version": version, "files": expected} or actual != contents:
            raise RuntimeError("Staging manifest/content/inventory mismatch against pinned source")
        contents = actual

    inst = (state.get("installed") or {}).get(args.name)
    changes = local_changes(inst) if inst else []
    old = installed_hashes(inst)
    current = {str(rel) for rel in contents}
    if set(old) - current:
        changes.append({"change": "source_deleted", "files": sorted(set(old) - current)})
    for rel, data in contents.items():
        dst = safe_path(ROOT, str(rel))
        if str(rel) not in old and dst.exists() and sha256_file(dst) != hashlib.sha256(data).hexdigest():
            changes.append({"file": str(rel), "change": "untracked_conflict"})
    target = safe_path(ROOT, entry["target"])
    if target.is_dir():
        for f in target.rglob("*"):
            safe_path(ROOT, str(f.relative_to(ROOT)))
            if f.is_file() and str(f.relative_to(ROOT)) not in old and str(f.relative_to(ROOT)) not in current:
                changes.append({"file": str(f.relative_to(ROOT)), "change": "untracked"})
    baseline_pre = {rel: (sha256_file(safe_path(ROOT, str(rel))) if (ROOT / rel).exists() else None) for rel in contents}
    plan = [{"file": str(rel), "action": "overwrite" if (ROOT / rel).exists() else "add"} for rel in contents]
    result = {"name": args.name, "version": version, "commit": commit, "plan": plan,
              "modified_by_owner": changes}
    if changes:
        has_source_deleted = any(c.get("change") == "source_deleted" for c in changes)
        if has_source_deleted:
            deleted_files = [f for c in changes if c.get("change") == "source_deleted" for f in c.get("files", [])]
            result["blocked"] = (
                "Upstream removed previously installed files. Automated deletion is prohibited as a safe boundary; "
                "reconcile obsolete files manually in maintenance window."
            )
            result["reconciliation_recommendation"] = (
                f"Obsolete files {deleted_files} were deleted upstream. Reconcile or archive them manually "
                "in an administrative maintenance window before re-running installation."
            )
        else:
            result["blocked"] = "Local/source conflicts: installation refused; no force override"
        out(result, 3)
    if not args.yes:
        result["dry_run"] = True
        out(result)

    hashes = {str(rel): hashlib.sha256(data).hexdigest() for rel, data in contents.items()}
    state.setdefault("installed", {})[args.name] = {
        "version": version, "commit": commit, "installed_at": now_iso(), "files": hashes}
    state.setdefault("known", {})[args.name] = version
    backup = transaction(contents, state, expected_pre_hashes=baseline_pre)
    result.update({"installed": True, "backup": backup})
    out(result)


def cmd_adopt(args):
    state = load_state()
    commit = pinned(args)
    tree = source_tree(commit)
    _, items = load_catalog(tree)
    names = args.names or sorted(items)
    adopted, skipped = [], []
    for name in names:
        entry = _find(items, name)
        contents = entry_snapshot(tree, entry)
        rels = list(contents)
        dependencies(tree, entry)
        target = safe_path(ROOT, entry["target"])
        inventory = {str(f.relative_to(ROOT)) for f in target.rglob("*") if f.is_file()} if target.is_dir() else set()
        expected = {str(r) for r in rels if r.is_relative_to(Path(entry["target"]))}
        if inventory != expected or not all((ROOT / r).is_file() and (ROOT / r).read_bytes() == data for r, data in contents.items()):
            raise RuntimeError(f"Adopt refused: {name} differs from pinned source")
        state.setdefault("installed", {})[name] = {
            "version": str(entry.get("version")), "commit": commit, "installed_at": now_iso(),
            "files": {str(r): hashlib.sha256(data).hexdigest() for r, data in contents.items()}}
        state.setdefault("known", {})[name] = str(entry.get("version"))
        adopted.append(name)
    state["last_checked"] = state.get("last_checked") or now_iso()
    state["last_seen_commit"] = commit
    transaction({}, state)
    out({"adopted": adopted, "skipped": skipped, "commit": commit})


def cmd_status(args):
    state = load_state()
    out({"repo": state.get("repo", REPO), "last_checked": state.get("last_checked"),
         "installed": {n: {"version": v.get("version"), "installed_at": v.get("installed_at"),
                           "modified_by_owner": bool(local_changes(v))}
                       for n, v in (state.get("installed") or {}).items()}})


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check"); c.add_argument("--mark", action="store_true"); c.set_defaults(fn=cmd_check)
    d = sub.add_parser("diff"); d.add_argument("name"); d.set_defaults(fn=cmd_diff)
    f = sub.add_parser("fetch"); f.add_argument("name"); f.set_defaults(fn=cmd_fetch)
    i = sub.add_parser("install"); i.add_argument("name")
    i.add_argument("--yes", action="store_true"); i.add_argument("--force", action="store_true")
    i.add_argument("--from-staging", action="store_true"); i.set_defaults(fn=cmd_install)
    a = sub.add_parser("adopt"); a.add_argument("names", nargs="*"); a.set_defaults(fn=cmd_adopt)
    s = sub.add_parser("status"); s.set_defaults(fn=cmd_status)
    for parser in (c, d, f, i, a):
        parser.add_argument("--commit", help="Exact immutable 40hex commit; required for install/fetch/adopt")
    args = p.parse_args(argv)
    try:
        if SOURCE_DIR and args.cmd in ("install", "adopt", "fetch"):
            raise RuntimeError("BOT_HELP_SOURCE_DIR is mutable/unverified; install/adopt/fetch prohibited")
        if args.cmd in ("install", "fetch", "adopt") and not args.commit:
            raise RuntimeError("Explicit --commit required; use the SHA returned by check/diff")
        if args.cmd != "status" and getattr(args, "name", None) and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", args.name):
            raise RuntimeError("Unsafe skill name")
        if getattr(args, "yes", False) or getattr(args, "mark", False) or args.cmd == "adopt":
            with locked():
                args.fn(args)
        else:
            if (STATE_DIR / "transaction.json").exists():
                raise RuntimeError("Pending crash recovery: run a mutating command under lock")
            args.fn(args)
    except (OSError, RuntimeError, ValueError, yaml.YAMLError, StopIteration, importlib.metadata.PackageNotFoundError) as e:
        out({"error": f"{type(e).__name__}: {e}"}, 1)


if __name__ == "__main__":
    main()
