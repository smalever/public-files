#!/usr/bin/env python3
"""Каталог публичных навыков бота: что нового, сравнение, установка.

Источник — публичный GitHub-репозиторий (по умолчанию smalever/public-files,
каталог hermes-skills/). Каждая проверка фиксирует конкретный commit: файлы
берутся только из архива этого commit, а не из «текущей ветки».

Команды (вывод — JSON):
  check [--mark]          новые / обновлённые / изменённые владельцем навыки;
                          --mark запоминает проверку (дата + версии каталога)
  diff NAME               unified diff: установленная версия → версия каталога
  fetch NAME              распаковать версию каталога в staging для ручного слияния
  install NAME [--yes] [--force] [--from-staging]
                          без --yes — только план; --force нужен, если владелец
                          менял навык; --from-staging ставит слитую копию
  adopt [NAME ...]        принять текущие файлы как установленные (развёртывание)
  status                  что установлено из каталога и когда

Ничего не пишет без --yes/--mark/adopt. Перед установкой делает бэкап.
"""

import argparse
import difflib
import hashlib
import io
import json
import os
import shutil
import sys
import tarfile
import tempfile
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

MSK = timezone(timedelta(hours=3))
ROOT = Path(os.environ.get("BOT_HELP_ROOT", "/opt/data"))
STATE_DIR = Path(os.environ.get("BOT_HELP_STATE_DIR", str(ROOT / "bot-help")))
STATE_FILE = STATE_DIR / "state.json"
CACHE_DIR = Path(os.environ.get("BOT_HELP_CACHE_DIR", str(ROOT / "cache/bot-help")))
BACKUP_DIR = ROOT / "backups/skills"
REPO = os.environ.get("BOT_HELP_REPO", "smalever/public-files")
BRANCH = os.environ.get("BOT_HELP_BRANCH", "main")
CATALOG_DIR = os.environ.get("BOT_HELP_CATALOG_DIR", "hermes-skills")
# Для тестов и развёртывания из локальной копии репозитория (без сети).
SOURCE_DIR = os.environ.get("BOT_HELP_SOURCE_DIR")
UA = "hermes-bot-help/1.0"


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
    except (OSError, ValueError):
        return {"repo": REPO, "last_checked": None, "last_seen_commit": None,
                "known": {}, "installed": {}}


def save_state(state):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, STATE_FILE)


# ── Источник ────────────────────────────────────────────────────────

def _http(url, accept=None):
    req = urllib.request.Request(url, headers={"User-Agent": UA, **({"Accept": accept} if accept else {})})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


def resolve_commit():
    if SOURCE_DIR:
        return "local"
    sha = _http(f"https://api.github.com/repos/{REPO}/commits/{BRANCH}",
                accept="application/vnd.github.sha").decode().strip()
    if len(sha) < 7:
        raise RuntimeError(f"Не удалось получить commit {REPO}@{BRANCH}")
    return sha


def source_tree(commit):
    """Каталог hermes-skills/ для commit (распакованный архив в кэше)."""
    if SOURCE_DIR:
        return Path(SOURCE_DIR) / CATALOG_DIR
    dest = CACHE_DIR / "src" / commit
    root = dest / CATALOG_DIR
    if root.is_dir():
        return root
    data = _http(f"https://codeload.github.com/{REPO}/tar.gz/{commit}")
    tmp = Path(tempfile.mkdtemp(dir=str(CACHE_DIR.parent) if CACHE_DIR.parent.exists() else None))
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
            tar.extractall(tmp, filter="data")
        top = next(p for p in tmp.iterdir() if p.is_dir())
        if not (top / CATALOG_DIR).is_dir():
            raise RuntimeError(f"В архиве нет каталога {CATALOG_DIR}/")
        dest.mkdir(parents=True, exist_ok=True)
        shutil.move(str(top / CATALOG_DIR), str(root))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return root


def load_catalog(tree):
    data = yaml.safe_load((tree / "catalog.yaml").read_text(encoding="utf-8")) or {}
    items = {}
    for entry in data.get("skills") or []:
        if isinstance(entry, dict) and entry.get("name"):
            items[entry["name"]] = entry
    return data, items


def entry_files(tree, entry):
    """[(файл в источнике, путь установки относительно ROOT)] для записи каталога."""
    pairs = []
    src = tree / entry["path"]
    target = Path(entry["target"])
    if src.is_dir():
        for f in sorted(src.rglob("*")):
            if f.is_file() and "__pycache__" not in f.parts:
                pairs.append((f, target / f.relative_to(src)))
    for extra in entry.get("files") or []:
        pairs.append((tree / extra["from"], Path(extra["to"])))
    return pairs


def installed_hashes(entry_state):
    return (entry_state or {}).get("files") or {}


def local_changes(entry_state):
    """Файлы, которые владелец изменил или удалил после установки."""
    changed = []
    for rel, digest in installed_hashes(entry_state).items():
        path = ROOT / rel
        if not path.exists():
            changed.append({"file": rel, "change": "deleted"})
        elif sha256_file(path) != digest:
            changed.append({"file": rel, "change": "modified"})
    return changed


# ── Команды ─────────────────────────────────────────────────────────

def cmd_check(args):
    state = load_state()
    commit = resolve_commit()
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

    result = {"repo": REPO, "commit": commit, "last_checked": last,
              "new_since_last_check": new, "updates": updated,
              "not_installed": available, "modified_by_owner": modified}
    if args.mark:
        state["last_checked"] = now_iso()
        state["last_seen_commit"] = commit
        state["known"] = {n: str(e.get("version")) for n, e in items.items()}
        save_state(state)
        result["marked"] = state["last_checked"]
    out(result)


def _find(items, name):
    if name not in items:
        out({"error": f"Навыка {name} нет в каталоге", "available": sorted(items)}, 2)
    return items[name]


def cmd_diff(args):
    commit = resolve_commit()
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
    commit = resolve_commit()
    tree = source_tree(commit)
    _, items = load_catalog(tree)
    entry = _find(items, args.name)
    stage = CACHE_DIR / "staging" / args.name
    shutil.rmtree(stage, ignore_errors=True)
    for src, rel in entry_files(tree, entry):
        (stage / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, stage / rel)
    meta = {"name": args.name, "commit": commit, "version": str(entry.get("version"))}
    (stage / ".bot-help.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    out({**meta, "staging": str(stage),
         "hint": "Слей правки владельца в staging, затем install NAME --from-staging --force --yes"})


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
    if args.from_staging:
        stage = CACHE_DIR / "staging" / args.name
        meta = json.loads((stage / ".bot-help.json").read_text(encoding="utf-8"))
        pairs = [(f, f.relative_to(stage)) for f in sorted(stage.rglob("*"))
                 if f.is_file() and f.name != ".bot-help.json"]
        commit, version = meta["commit"], meta["version"]
    else:
        commit = resolve_commit()
        tree = source_tree(commit)
        _, items = load_catalog(tree)
        entry = _find(items, args.name)
        pairs = entry_files(tree, entry)
        version = str(entry.get("version"))

    inst = (state.get("installed") or {}).get(args.name)
    changes = local_changes(inst) if inst else []
    plan = [{"file": str(rel), "action": "overwrite" if (ROOT / rel).exists() else "add"} for _, rel in pairs]
    result = {"name": args.name, "version": version, "commit": commit, "plan": plan,
              "modified_by_owner": changes}
    if changes and not args.force:
        result["blocked"] = ("Владелец менял этот навык: покажи diff, предложи слить правки "
                             "(fetch → правка staging → install --from-staging --force --yes) или заменить (--force).")
        out(result, 3)
    if not args.yes:
        result["dry_run"] = True
        out(result)

    backup = _backup([str(rel) for _, rel in pairs], args.name)
    hashes = {}
    for src, rel in pairs:
        dst = ROOT / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        hashes[str(rel)] = sha256_file(dst)
    state.setdefault("installed", {})[args.name] = {
        "version": version, "commit": commit, "installed_at": now_iso(), "files": hashes}
    state.setdefault("known", {})[args.name] = version
    save_state(state)
    result.update({"installed": True, "backup": backup})
    out(result)


def cmd_adopt(args):
    state = load_state()
    commit = resolve_commit()
    tree = source_tree(commit)
    _, items = load_catalog(tree)
    names = args.names or sorted(items)
    adopted, skipped = [], []
    for name in names:
        entry = _find(items, name)
        rels = [rel for _, rel in entry_files(tree, entry)]
        if not all((ROOT / r).exists() for r in rels):
            skipped.append({"name": name, "reason": "не все файлы установлены"})
            continue
        state.setdefault("installed", {})[name] = {
            "version": str(entry.get("version")), "commit": commit, "installed_at": now_iso(),
            "files": {str(r): sha256_file(ROOT / r) for r in rels}}
        state.setdefault("known", {})[name] = str(entry.get("version"))
        adopted.append(name)
    state["last_checked"] = state.get("last_checked") or now_iso()
    state["last_seen_commit"] = commit
    save_state(state)
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
    args = p.parse_args(argv)
    try:
        args.fn(args)
    except (OSError, RuntimeError, ValueError, yaml.YAMLError, StopIteration) as e:
        out({"error": f"{type(e).__name__}: {e}"}, 1)


if __name__ == "__main__":
    main()
