#!/usr/bin/env python3
"""
Helper module for meal-time medication reminders and intake tracking.
Synchronized with Obsidian vault (/notes/Моё здоровье/Схема препаратов.md)
and SQLite health.db.
"""

import os
import re
import json
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path
import yaml

MSK = timezone(timedelta(hours=3))
HEALTH_DB_PATH = Path("/opt/data/personal/health/health.db")
MEDS_NOTE_PATH = Path("/notes/Моё здоровье/Схема препаратов.md")

SLOT_TITLES = {
    "morning": "💊 Прими утренние лекарства:",
    "lunch": "💊 Прими дневные лекарства:",
    "evening": "💊 Прими вечерние лекарства:",
    "morning_lunch": "💊 Прими утренние и дневные лекарства:",
    "lunch_evening": "💊 Прими дневные и вечерние лекарства:",
}

def get_health_conn(*, write=False):
    try:
        conn = sqlite3.connect(Path(HEALTH_DB_PATH).resolve().as_uri() + ('?mode=rw' if write else '?mode=ro'), uri=True, timeout=20.0)
    except sqlite3.OperationalError as exc:
        raise sqlite3.OperationalError('Cannot open existing health database; initialization must be a separate explicit operation.') from exc
    conn.row_factory = sqlite3.Row
    try:
        for table in ('medication_slot_log', 'med_intake_log'):
            conn.execute(f'SELECT * FROM {table} LIMIT 0')
    except sqlite3.DatabaseError as exc:
        conn.close()
        raise sqlite3.OperationalError('Missing/invalid medication tracking schema; run medication_reminder_helper.py migrate explicitly before use.') from exc
    return conn

def init_db(conn: sqlite3.Connection):
    """Explicit migration only; never called by read or intake operations."""
    with conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS medication_slot_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                slot TEXT NOT NULL,
                ts TEXT NOT NULL,
                confirmed_text TEXT,
                UNIQUE(date, slot)
            );
        """)
        # Отметка по каждому препарату (с 2026-09-29).
        conn.execute("""
            CREATE TABLE IF NOT EXISTS med_intake_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                slot TEXT NOT NULL,
                medication TEXT NOT NULL,
                med_key TEXT NOT NULL,
                ts TEXT NOT NULL,
                confirmed_text TEXT,
                UNIQUE(date, slot, med_key)
            );
        """)

def _parse_frontmatter(file_path: Path):
    if not file_path.exists():
        return None
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
        if not content.startswith("---"):
            return None
        parts = content.split("---", 2)
        if len(parts) < 3:
            return None
        return yaml.safe_load(parts[1])
    except Exception:
        return None

FOOD_TIMINGS = ("before", "with", "after", "any")
FOOD_TIMING_TAGS = {
    "before": "⏰ до еды",
    "with": "🍽 во время еды",
    "after": "🍽 после еды",
}
SLOT_NAMES_RU = {"morning": "утро", "lunch": "обед", "evening": "вечер"}
SLOTS = ("morning", "lunch", "evening")


def infer_food_timing(note: str) -> str:
    """Запасной вариант, если в схеме нет поля food: угадать по тексту note."""
    n = (note or "").casefold()
    if "до еды" in n or "натощак" in n or "перед едой" in n or "до завтрака" in n:
        return "before"
    if "после" in n:
        return "after"
    if "во время" in n or "с едой" in n:
        return "with"
    return "any"


def normalize_food_timing(value, note: str = "") -> str:
    v = str(value or "").strip().casefold()
    if v in FOOD_TIMINGS:
        return v
    return infer_food_timing(note)


def _med_from_note(m: dict, default_note: str = "") -> dict:
    note = str(m.get("note", default_note) or "").strip()
    return {
        "name": str(m.get("name", "") or "").strip(),
        "dosage": str(m.get("dosage", "") or "").strip(),
        "note": note,
        "food": normalize_food_timing(m.get("food"), note),
    }


def get_active_meds_for_slot(slot: str, date_str: str = None):
    """
    Returns list of dicts: [{'name', 'dosage', 'note', 'food'}]
    food: before | with | after | any (признак приёма относительно еды).
    Reads from Obsidian first, fallback to SQLite medications table.
    """
    if date_str is None:
        date_str = datetime.now(MSK).strftime("%Y-%m-%d")

    dt = datetime.strptime(date_str, "%Y-%m-%d")
    is_wednesday = (dt.weekday() == 2)

    # 1. Try Obsidian
    frontmatter = _parse_frontmatter(MEDS_NOTE_PATH)
    if frontmatter and isinstance(frontmatter, dict) and "medications" in frontmatter:
        meds_section = frontmatter["medications"] or {}
        res = []
        if slot in SLOTS:
            for m in meds_section.get(slot, []) or []:
                res.append(_med_from_note(m))
        if slot == "morning" and is_wednesday:
            for m in meds_section.get("wednesday_morning", []) or []:
                res.append(_med_from_note(m, "по средам"))
        return [m for m in res if m["name"]]

    # 2. Fallback to SQLite medications table
    conn = get_health_conn()
    cur = conn.cursor()
    cols = {r[1] for r in cur.execute("PRAGMA table_info(medications)")}
    food_col = ", food" if "food" in cols else ""
    rows = cur.execute(
        f"SELECT name, dosage, when_text, note{food_col} FROM medications WHERE active = 1"
    ).fetchall()
    conn.close()

    res = []
    for r in rows:
        when = (r["when_text"] or "").lower()
        note = r["note"] or ""
        med = {
            "name": r["name"],
            "dosage": r["dosage"] or "",
            "note": note,
            "food": normalize_food_timing(r["food"] if food_col else None, note),
        }
        atoms = when.replace("wednesday_morning", "weekly").split("_")
        slots = {"wednesday_morning" if atom == "weekly" else atom for atom in atoms}
        if slots - {"morning", "lunch", "evening", "wednesday_morning"}:
            continue
        if slot in SLOTS and (slot in slots or
                (slot == "morning" and is_wednesday and "wednesday_morning" in slots)):
            res.append(med)

    return res

def med_key(name: str) -> str:
    return (name or "").strip().casefold()


def get_slot_status(slot: str, date_str: str = None, meds: list = None) -> list:
    """Препараты слота с признаком taken (True/False).

    Старые записи (до поштучных отметок) хранятся в medication_slot_log целым слотом —
    такой слот считается принятым полностью.
    """
    if date_str is None:
        date_str = datetime.now(MSK).strftime("%Y-%m-%d")
    if meds is None:
        meds = get_active_meds_for_slot(slot, date_str)
    conn = get_health_conn()
    try:
        slot_closed = conn.execute(
            "SELECT 1 FROM medication_slot_log WHERE date = ? AND slot = ?", (date_str, slot)
        ).fetchone() is not None
        keys = {
            r[0] for r in conn.execute(
                "SELECT med_key FROM med_intake_log WHERE date = ? AND slot = ?", (date_str, slot)
            )
        }
    finally:
        conn.close()
    return [dict(m, taken=slot_closed or med_key(m["name"]) in keys) for m in meds]


def is_slot_taken(slot: str, date_str: str) -> bool:
    """Слот закрыт, если приняты все его препараты (пустой слот — закрыт)."""
    return all(m["taken"] for m in get_slot_status(slot, date_str))


CATALOG_SLOT_RU = {
    "morning": "утро",
    "wednesday_morning": "утро по средам",
    "lunch": "день",
    "evening": "вечер",
    "as_needed": "по потребности",
}
CATALOG_SLOT_ORDER = ("morning", "wednesday_morning", "lunch", "evening", "as_needed")


def get_medication_catalog() -> list:
    """Полный справочник препаратов из схемы (Obsidian) — для сопоставления названий.

    Один препарат = одна запись; все его приёмы — в `schedule`
    (slot, slot_ru, time, food, dosage, note, condition). Только активная схема.
    Если заметки нет — запасной вариант: активные препараты из SQLite.
    """
    fm = _parse_frontmatter(MEDS_NOTE_PATH)
    section = fm.get("medications") if isinstance(fm, dict) else None
    catalog = {}

    def add(name, slot, m):
        name = str(name or "").strip()
        if not name:
            return
        entry = catalog.setdefault(med_key(name), {"name": name, "schedule": []})
        note = str(m.get("note", "") or "").strip()
        food = None
        if slot != "as_needed":
            food = normalize_food_timing(m.get("food"), note)
        entry["schedule"].append({
            "slot": slot,
            "slot_ru": CATALOG_SLOT_RU.get(slot, slot),
            "time": str(m.get("time", "") or "") or None,
            "food": food,
            "dosage": str(m.get("dosage", "") or "").strip(),
            "note": note,
            "condition": str(m.get("condition", "") or "").strip() or None,
        })

    if isinstance(section, dict):
        for slot in CATALOG_SLOT_ORDER:
            for m in section.get(slot) or []:
                add(m.get("name"), slot, m)
        for slot, items in section.items():
            if slot not in CATALOG_SLOT_ORDER:
                for m in items or []:
                    add(m.get("name"), slot, m)
    else:
        conn = get_health_conn()
        try:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(medications)")}
            optional = ", food" if "food" in cols else ""
            optional += ", timing" if "timing" in cols else ""
            for r in conn.execute(f"SELECT name, dosage, when_text, note{optional} FROM medications WHERE active = 1"):
                # Protect the weekly atom before decoding legacy underscore combinations.
                when = (r["when_text"] or "").strip().casefold()
                atoms = when.replace("wednesday_morning", "weekly").split("_")
                slots = ["wednesday_morning" if atom == "weekly" else atom for atom in atoms]
                if any(s not in CATALOG_SLOT_ORDER for s in slots):
                    continue
                times = []
                if "timing" in cols:
                    try:
                        times = json.loads(r["timing"] or "[]")
                    except (ValueError, TypeError):
                        times = []
                if not isinstance(times, list) or any(not isinstance(t, str) for t in times):
                    times = []
                # Combined legacy rows have no reliable time-to-slot mapping: do not invent one.
                medication = {"dosage": r["dosage"], "note": r["note"],
                    "food": r["food"] if "food" in cols else None}
                for slot in dict.fromkeys(slots):
                    for at in times or [None]:
                        add(r["name"], slot, dict(medication, time=at))
        finally:
            conn.close()
    return list(catalog.values())


def _compact(text: str) -> str:
    """Для сравнения: регистр, ё=е, без пробелов/дефисов/скобок («Ко-Пренесса» ~ «ко пренесса»)."""
    return re.sub(r"[\W_]+", "", (text or "").casefold().replace("ё", "е"))


def _name_variants(name: str) -> list:
    """Полное имя, имя без скобок, то, что в скобках (бренд), и отдельные слова."""
    variants = {name, med_short_name(name)}
    for inner in re.findall(r"\(([^)]*)\)", name):
        variants.add(inner)
    for w in _word_stems(name):
        if len(w) >= 4:
            variants.add(w)
    return [v for v in {_compact(v) for v in variants} if v]


def resolve_medication_name(query: str, limit: int = 3) -> list:
    """Сопоставить искажённое название (автозамена, опечатка, падеж) с препаратами схемы.

    Возвращает до `limit` кандидатов по убыванию похожести:
    [{name, score, confident, schedule}]. confident=True — один явный лидер
    (score >= 0.8 и отрыв от второго >= 0.15) либо точное совпадение.
    Ничего не записывает: решение о записи принимает вызывающий.
    """
    import difflib

    q = _compact(query)
    if len(q) < 2:
        return []
    scored = []
    for entry in get_medication_catalog():
        best = 0.0
        for v in _name_variants(entry["name"]):
            if q == v:
                score = 1.0
            elif len(q) >= 3 and (q in v or v in q) and min(len(q), len(v)) >= 3:
                score = max(0.85, difflib.SequenceMatcher(None, q, v).ratio())
            else:
                score = difflib.SequenceMatcher(None, q, v).ratio()
            best = max(best, score)
        scored.append((best, entry))
    scored.sort(key=lambda t: -t[0])
    scored = [(s, e) for s, e in scored if s >= 0.55][:limit]
    out = []
    for i, (s, e) in enumerate(scored):
        nxt = scored[i + 1][0] if i + 1 < len(scored) else 0.0
        out.append({
            "name": e["name"],
            "score": round(s, 2),
            "confident": i == 0 and (s >= 0.999 or (s >= 0.8 and s - nxt >= 0.15)),
            "schedule": e["schedule"],
        })
    return out


def format_catalog_text(catalog: list = None) -> str:
    """Короткий текстовый справочник: препарат — когда принимать."""
    catalog = catalog if catalog is not None else get_medication_catalog()
    lines = []
    for e in catalog:
        parts = []
        for s in e["schedule"]:
            when = s["slot_ru"] + (f" {s['time']}" if s["time"] else "")
            tag = FOOD_TIMING_TAGS.get(s["food"], "")
            extra = s["condition"] or ""
            bits = [b for b in (s["dosage"], tag.split(" ", 1)[-1] if tag else "", extra) if b]
            parts.append(when + (f" ({', '.join(bits)})" if bits else ""))
        lines.append(f"- {e['name']}: " + "; ".join(parts))
    return "\n".join(lines)


FOCUS_FOOD_ORDER = (
    ("before", "до еды"),
    ("with", "во время еды"),
    ("after", "после еды"),
    ("any", "независимо от еды"),
)
FOCUS_DAILY_SLOTS = (("morning", "утром"), ("lunch", "днём"), ("evening", "вечером"))


def format_week_focus_lines(catalog: list = None) -> list:
    """Строки чек-листа «Фокус недели» по актуальной схеме (без хардкода препаратов).

    Еженедельные инъекции (`wednesday_morning`) — отдельными строками, затем
    ежедневные слоты: препараты сгруппированы по признаку приёма относительно еды.
    Препараты «по потребности» в чек-лист не входят.
    """
    catalog = catalog if catalog is not None else get_medication_catalog()
    by_slot = {}
    for e in catalog:
        for s in e["schedule"]:
            by_slot.setdefault(s["slot"], []).append((e["name"], s))

    lines = []
    for name, s in by_slot.get("wednesday_morning", []):
        tag = dict(FOCUS_FOOD_ORDER).get(s["food"]) if s["food"] != "any" else None
        when = "утром" + (f", {tag}" if tag else "")
        extra = f" ({s['note']})" if s["note"] else ""
        dose = f" — {s['dosage']}" if s["dosage"] else ""
        lines.append(f"- [ ] **Среда ({when}):** {med_short_name(name)}{dose}{extra}.")

    for slot, title in FOCUS_DAILY_SLOTS:
        items = by_slot.get(slot, [])
        if not items:
            continue
        times = {s["time"] for _, s in items if s["time"]}
        when = f"{title} ({times.pop()})" if len(times) == 1 else title
        groups = []
        for food, label in FOCUS_FOOD_ORDER:
            names = [med_short_name(n) for n, s in items if s["food"] == food]
            if names:
                groups.append(f"{label} — " + ", ".join(names))
        lines.append(f"- [ ] Ежедневно, {when}: " + "; ".join(groups) + ".")
    return lines


def get_pending_meds(slot: str, date_str: str = None, food: tuple = None) -> list:
    """Непринятые препараты слота; food — фильтр по признаку приёма."""
    return [
        m for m in get_slot_status(slot, date_str)
        if not m["taken"] and (food is None or m["food"] in food)
    ]


def _word_stems(text: str) -> list:
    return [w for w in re.split(r"[^\w]+", (text or "").casefold()) if w]


def med_matches(query: str, name: str) -> bool:
    """Нестрогое совпадение названия: «магний» ~ «Магния цитрат», бренд в скобках: «глюкофаж» ~ «Метформин (Глюкофаж)»."""
    q = (query or "").strip().casefold()
    n = (name or "").casefold()
    if not q:
        return False
    if q in n or med_short_name(name).casefold() in q:
        return True
    name_words = _word_stems(name)
    q_words = [w for w in _word_stems(query) if len(w) >= 3]
    if not q_words:
        return False
    for qw in q_words:
        stem = qw[:max(3, min(len(qw) - 2, 6))] if len(qw) > 4 else qw
        if not any(nw.startswith(stem) for nw in name_words):
            return False
    return True


def mark_meds_taken(
    slot: str,
    date_str: str = None,
    names: list = None,
    food: tuple = None,
    confirmed_text: str = "confirmed",
    now: datetime = None,
) -> dict:
    """Отметить приём препаратов слота.

    names=None — все препараты слота (с учётом фильтра food); иначе только названные.
    Возвращает {slot, date, marked, already, unknown, remaining, slot_closed}.
    """
    now_dt = now or datetime.now(MSK)
    today_str = now_dt.strftime("%Y-%m-%d")
    if date_str is None:
        date_str = today_str
    ts_str = now_dt.strftime("%d.%m.%Y %H:%M:%S") if date_str == today_str else f"{date_str} 12:00:00"

    status = get_slot_status(slot, date_str)
    targets = [m for m in status if food is None or m["food"] in food]
    unknown = []
    if names:
        selected = []
        for q in names:
            hits = [m for m in status if med_matches(q, m["name"])]
            if not hits:
                unknown.append(q)
            for h in hits:
                if h not in selected:
                    selected.append(h)
        targets = selected

    to_mark = [m for m in targets if not m["taken"]]
    already = [m for m in targets if m["taken"]]

    conn = get_health_conn(write=True)
    try:
        with conn:
            medication_rows = conn.execute(
                "SELECT id, name, dosage, when_text FROM medications WHERE active = 1"
            ).fetchall()
            medication_ids = {}
            for m in to_mark:
                candidates = [r for r in medication_rows
                              if med_key(r["name"]) == med_key(m["name"])
                              and slot in (r["when_text"] or "").casefold()
                              and ("wednesday" not in (r["when_text"] or "").casefold()
                                   or datetime.strptime(date_str, "%Y-%m-%d").weekday() == 2)]
                candidates.sort(key=lambda r: (
                    (r["dosage"] or "") != (m.get("dosage") or ""),
                    (r["when_text"] or "").casefold() != slot,
                    str(r["id"]),
                ))
                if candidates:
                    medication_ids[med_key(m["name"])] = candidates[0]["id"]
            marked = []
            for m in to_mark:
                cur = conn.execute("""
                    INSERT OR IGNORE INTO med_intake_log (date, slot, medication, med_key, ts, confirmed_text)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (date_str, slot, m["name"], med_key(m["name"]), ts_str, confirmed_text))
                if cur.rowcount > 0:
                    marked.append(m)
                    conn.execute("""
                        INSERT INTO med_log (date, ts, medication, dosage, method, med_id)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, (date_str, ts_str, m["name"], m["dosage"], slot,
                          medication_ids.get(med_key(m["name"]), f"slot_{slot}")))
                else:
                    already.append(m)
            taken_keys = {med_key(m["name"]) for m in status if m["taken"]} | {med_key(m["name"]) for m in marked}
            remaining = [m for m in status if med_key(m["name"]) not in taken_keys]
            slot_closed = bool(status) and not remaining
            if slot_closed:
                # Слот целиком: для совместимости со старыми отчётами.
                conn.execute("""
                    INSERT OR IGNORE INTO medication_slot_log (date, slot, ts, confirmed_text)
                    VALUES (?, ?, ?, ?)
                """, (date_str, slot, ts_str, confirmed_text))
    finally:
        conn.close()

    return {
        "slot": slot,
        "date": date_str,
        "marked": [m["name"] for m in marked],
        "already": [m["name"] for m in already],
        "unknown": unknown,
        "remaining": [m["name"] for m in remaining],
        "slot_closed": slot_closed,
    }


def mark_slot_taken(
    slot: str,
    date_str: str = None,
    confirmed_text: str = "confirmed",
    now: datetime = None,
):
    """Отметить весь слот; False, если всё уже было отмечено."""
    res = mark_meds_taken(slot, date_str, confirmed_text=confirmed_text, now=now)
    return bool(res["marked"])

def determine_confirmation_slot(
    current_msk_dt: datetime,
    *,
    morning_taken: bool,
    lunch_taken: bool,
    active_lunch: list,
) -> str:
    """Choose the current confirmation slot without closing a future slot."""
    hour = current_msk_dt.hour
    if hour < 12:
        return "morning"
    if hour < 18:
        if not morning_taken:
            return "morning"
        if active_lunch:
            return "lunch"
        return "morning"
    return "evening"


def build_slot_confirmation_payload(slot: str, date_str: str, created) -> dict:
    """CLI-ответ на отметку. created — bool (старый вызов) или dict от mark_meds_taken."""
    slot_names = {"morning": "утренние", "lunch": "дневные", "evening": "вечерние"}
    slot_ru = slot_names.get(slot, slot)
    if isinstance(created, dict):
        res = created
        parts = []
        if res["marked"]:
            parts.append("Отмечено (" + SLOT_NAMES_RU.get(slot, slot) + "): " + ", ".join(res["marked"]) + ".")
        if res["already"]:
            parts.append("Уже было отмечено (" + SLOT_NAMES_RU.get(slot, slot) + "): " + ", ".join(res["already"]) + ".")
        if res["unknown"]:
            parts.append("Не нашёл в слоте «" + SLOT_NAMES_RU.get(slot, slot) + "»: " + ", ".join(res["unknown"]) + ".")
        if res["remaining"]:
            parts.append("Ещё не приняты (" + SLOT_NAMES_RU.get(slot, slot) + "): " + ", ".join(res["remaining"]) + ".")
        elif res["slot_closed"]:
            parts.append(f"{slot_ru.capitalize()} препараты приняты полностью.")
        if res["unknown"] and not res["marked"]:
            event_type = "meds_not_found"
        elif res["marked"]:
            event_type = "meds_taken"
        else:
            event_type = "meds_already_taken"
        return {
            "status": "ok",
            "type": event_type,
            "slot": slot,
            "date": date_str,
            "marked": res["marked"],
            "already": res["already"],
            "unknown": res["unknown"],
            "remaining": res["remaining"],
            "message": " ".join(parts) or "Нечего отмечать.",
        }
    if created:
        event_type = "meds_taken"
        message = f"{slot_ru.capitalize()} таблетки отмечены как принятые за {date_str}."
    else:
        event_type = "meds_already_taken"
        message = f"{slot_ru.capitalize()} таблетки уже были отмечены как принятые за {date_str}."
    return {"status": "ok", "type": event_type, "slot": slot, "message": message}


def current_slot_order(current_msk_dt: datetime) -> list:
    """Текущий слот и более ранние (будущие слоты не закрываем)."""
    hour = current_msk_dt.hour
    if hour < 12:
        return ["morning"]
    if hour < 18:
        return ["lunch", "morning"]
    return ["evening", "lunch", "morning"]


def take_meds(date_str: str = None, slot: str = None, names: list = None,
              confirmed_text: str = "confirmed", now: datetime = None) -> dict:
    """Единая точка для CLI «принял …».

    - names пусто, slot не задан → «принял всё»: всё непринятое в текущем окне
      напоминаний (reminder_slots: до 12 утро, 12–16 утро+день, 16–18 день, после 18 день+вечер).
    - names пусто, slot задан → все препараты этого слота.
    - names заданы → только названные, В ЛЮБОЕ ВРЕМЯ суток, независимо от окна
      (например, утренний препарат, названный вечером, — запишется как утренний).
      Без slot ищем в текущем и более ранних слотах; если совпало несколько слотов
      (включая уже принятые), требуется явный slot, весь запрос остаётся без записи.
    """
    now_dt = now or datetime.now(MSK)
    date_str = date_str or now_dt.strftime("%Y-%m-%d")
    names = [n.strip() for n in (names or []) if n and n.strip()]

    if not names:
        if slot:
            res = mark_meds_taken(slot, date_str, confirmed_text=confirmed_text, now=now_dt)
            return build_slot_confirmation_payload(slot, date_str, res)
        payloads = []
        for s in reminder_slots(now_dt):
            if not get_slot_status(s, date_str):
                continue  # в слоте нет препаратов по схеме
            res = mark_meds_taken(s, date_str, confirmed_text=confirmed_text, now=now_dt)
            payloads.append(build_slot_confirmation_payload(s, date_str, res))
        if not payloads:
            return {
                "status": "ok",
                "type": "meds_already_taken",
                "date": date_str,
                "slots": [],
                "marked": [],
                "already": [],
                "unknown": [],
                "remaining": {},
                "message": "Нечего отмечать: в текущем окне препаратов по схеме нет. "
                           "Отдельный препарат можно записать по названию.",
            }
        return _combine_payloads(date_str, payloads, [])

    slots = [slot] if slot else current_slot_order(now_dt)
    # Resolve the entire request before any writes. Taken doses remain candidates:
    # otherwise repeating the same confirmation silently consumes another dose.
    if not slot:
        ambiguous = {}
        for q in names:
            candidates = [
                dict(m, slot=s, slot_ru=SLOT_NAMES_RU.get(s, s))
                for s in slots for m in get_slot_status(s, date_str)
                if med_matches(q, m["name"])
            ]
            if len({m["slot"] for m in candidates}) > 1:
                ambiguous[q] = candidates
        if ambiguous:
            return {
                "status": "requires_confirmation", "type": "meds_slot_ambiguous",
                "date": date_str, "marked": [], "already": [], "unknown": [],
                "slots": [], "remaining": {}, "candidates": ambiguous,
                "message": "Уточни слот для препарата: " + "; ".join(
                    q + " — " + ", ".join(
                        m["slot_ru"] + (f" ({m['dosage']})" if m.get("dosage") else "")
                        + (" — уже отмечено" if m["taken"] else "")
                        for m in candidates
                    ) for q, candidates in ambiguous.items()
                ) + ". Ничего не записано; укажи --slot.",
            }
    by_slot = {}
    unknown = []
    for q in names:
        chosen = None
        for pending_only in (True, False):
            for s in slots:
                for m in get_slot_status(s, date_str):
                    if med_matches(q, m["name"]) and (not pending_only or not m["taken"]):
                        chosen = s
                        break
                if chosen:
                    break
            if chosen:
                break
        if chosen:
            by_slot.setdefault(chosen, []).append(q)
        else:
            unknown.append(q)

    payloads = []
    for s, qs in by_slot.items():
        res = mark_meds_taken(s, date_str, names=qs, confirmed_text=confirmed_text, now=now_dt)
        payloads.append(build_slot_confirmation_payload(s, date_str, res))
    result = _combine_payloads(date_str, payloads, unknown)
    if unknown:
        # Подсказки для бота: возможно, опечатка/автозамена. Сами ничего не записываем.
        result["suggestions"] = {
            q: [{"name": c["name"], "score": c["score"], "confident": c["confident"]}
                for c in resolve_medication_name(q)]
            for q in unknown
        }
    return result


def _combine_payloads(date_str: str, payloads: list, unknown: list) -> dict:
    """Свести ответы по нескольким слотам в один CLI-ответ."""
    marked = [n for p in payloads for n in p["marked"]]
    already = [n for p in payloads for n in p["already"]]
    remaining = {p["slot"]: p["remaining"] for p in payloads if p["remaining"]}
    parts = [p["message"] for p in payloads]
    if unknown:
        parts.append("Не нашёл среди сегодняшних препаратов: " + ", ".join(unknown) + ".")
    if unknown and not marked:
        event_type = "meds_not_found"
    elif marked:
        event_type = "meds_taken"
    else:
        event_type = "meds_already_taken"
    return {
        "status": "ok",
        "type": event_type,
        "date": date_str,
        "slots": [p["slot"] for p in payloads],
        "marked": marked,
        "already": already,
        "unknown": unknown,
        "remaining": remaining,
        "message": " ".join(parts),
    }


def get_day_pending(date_str: str = None) -> dict:
    """Непринятые за день препараты по слотам (для «Итогов дня»)."""
    date_str = date_str or datetime.now(MSK).strftime("%Y-%m-%d")
    out = {}
    for s in SLOTS:
        pending = get_pending_meds(s, date_str)
        if pending:
            out[s] = [{"name": m["name"], "dosage": m["dosage"], "food": m["food"]} for m in pending]
    return out


MEAL_FOOD_TIMINGS = ("with", "after", "any")

# Окна напоминаний при записи еды (решение владельца 2026-10-01).
# Не принятое в окно считается пропущенным: «догонять» или нет — решает владелец
# с врачом, бот не навязывает и по названиям препаратов исключений не делает.
LUNCH_FROM_HOUR = 12      # с этого часа напоминаем про дневные препараты
MORNING_UNTIL_HOUR = 16   # утренние напоминаем до 16:00
LUNCH_UNTIL_HOUR = 18     # с этого часа к дневным добавляются вечерние (дневные остаются до конца суток)


def reminder_slots(current_msk_dt: datetime) -> list:
    """Слоты, о которых напоминаем в этот час (только по времени суток)."""
    hour = current_msk_dt.hour
    if hour < LUNCH_FROM_HOUR:
        return ["morning"]
    if hour < MORNING_UNTIL_HOUR:
        return ["morning", "lunch"]
    if hour < LUNCH_UNTIL_HOUR:
        return ["lunch"]
    return ["lunch", "evening"]  # с 18:00 дневные и вечерние вместе до конца суток


def determine_reminder(date_str: str, current_msk_dt: datetime, meals_count_today: int):
    """
    Напоминание при записи еды: непринятые препараты «во время/после еды/не важно».
    Препараты «до еды» напоминают брифы (7:00/12:00/18:00); здесь они только
    упоминаются, если ещё не отмечены.
    Слот выбирается только по времени (см. reminder_slots); meals_count_today
    оставлен для совместимости вызова и не используется.
    Returns: (slot_name, meds_list) or None; у meds_list элементы с food == 'before'
    означают «до еды — не отмечено». slot_name — слоты с непринятым, через «_»
    (например 'morning_lunch' в 12:00–16:00 или 'lunch_evening' после 18:00).
    """
    pending = []
    seen = set()
    slots_with_pending = []
    for s in reminder_slots(current_msk_dt):
        slot_pending = get_pending_meds(s, date_str)
        if slot_pending:
            slots_with_pending.append(s)
        for m in slot_pending:
            dose_key = (s, med_key(m["name"]), m.get("food"), m.get("dosage"))
            if dose_key in seen:
                continue
            seen.add(dose_key)
            pending.append(dict(m, slot=s))

    meal_meds = [m for m in pending if m["food"] in MEAL_FOOD_TIMINGS]
    if not meal_meds:
        return None
    before = [m for m in pending if m["food"] == "before"]
    title_slot = "_".join(slots_with_pending)  # morning / lunch / evening / morning_lunch / lunch_evening
    return title_slot, meal_meds + before


# Пороги по еде (пересмотрены 2026-09-29 по инструкциям и исследованиям).
# В инструкциях норм в граммах нет — только «во время еды». Поэтому пороги мягкие:
# вердикт — подсказка («лучше с…»), а не запрет.
#  - Метформин: инструкция — «во время еды», цель — переносимость ЖКТ;
#    FDA (Glucophage XR): с едой AUC +50%, жирность еды не влияет → нужна просто еда.
#  - Омега-3 / Q10: жирорастворимы, с жирной едой всасываются лучше
#    (Lawson & Hughes 1988: 8 г vs 44 г жира) → желательно ≥10 г жиров.
#  - Магний: «рекомендуется во время еды» → хоть какая-то еда.
#  - Остальные препараты: требований к еде нет → не проверяем.
MEAL_WINDOW_MINUTES = 60          # еда, записанная за последние N минут, считается одним приёмом
# По умолчанию персональные эвристики отключены (возвращаются только факты из схемы).
# При необходимости настраиваемые правила могут быть заданы через словарь FOOD_RULES.
MEAL_MIN_KCAL = 200.0
FAT_MIN_FAT = 10.0
FOOD_MIN_KCAL = 100.0

_MEAL = {"req": "meal"}
_FAT = {"req": "fat"}
_FOOD = {"req": "food"}

FOOD_RULES = {}


def check_meal_requirement(req: str, meal_info: dict) -> list:
    """Возвращает список замечаний (пустой — еда подходит) для типа требования."""
    f = float(meal_info.get("fat") or 0)
    kcal = float(meal_info.get("kcal") or 0)
    problems = []
    if req == "meal":
        if kcal < MEAL_MIN_KCAL:
            problems.append(f"сейчас {kcal:.0f} ккал, лучше от {MEAL_MIN_KCAL:.0f}")
    elif req == "fat":
        if f < FAT_MIN_FAT:
            problems.append(f"жиров {f:.1f} г, лучше от {FAT_MIN_FAT:.0f}")
    elif req == "food":
        if kcal < FOOD_MIN_KCAL:
            problems.append(f"сейчас {kcal:.0f} ккал, лучше от {FOOD_MIN_KCAL:.0f}")
    return problems


_SOFT_ADVICE = {
    "meal": "лучше принять с полноценной едой",
    "fat": "усвоится лучше с едой пожирнее (яйца, сыр, рыба, масло)",
    "food": "лучше принять с нормальной едой",
}
_REQ_HINT = {
    "meal": f"с полноценной едой (от {MEAL_MIN_KCAL:.0f} ккал)",
    "fat": f"с едой, где есть жиры (от {FAT_MIN_FAT:.0f} г)",
    "food": "во время еды",
}


def med_short_name(name: str) -> str:
    """Короткое имя препарата для вердикта: без уточнений в скобках."""
    short = re.sub(r"\s*\([^)]*\)", "", name).strip()
    return short or name


def med_food_rule(name: str):
    """Правило по еде для препарата (первое совпавшее) или None."""
    low = name.lower()
    for rule_key, rule_val in FOOD_RULES.items():
        if rule_key in low:
            return rule_val
    return None


def _med_line(m: dict) -> str:
    line = f"- {m['name']}"
    if m.get("dosage"):
        line += f" ({m['dosage']})"
    if m.get("slot"):
        line += " — " + SLOT_NAMES_RU.get(m["slot"], m["slot"])
    tag = FOOD_TIMING_TAGS.get(m.get("food"))
    if tag:
        line += f" [{tag}]"
    return line


def format_reminder(slot: str, meds: list, meal_info: dict = None) -> str:
    title = SLOT_TITLES.get(slot, "💊 Прими лекарства:")
    meal_meds = [m for m in meds if m.get("food", "any") != "before"]
    before = [m for m in meds if m.get("food") == "before"]
    lines = [title]
    lines += [_med_line(m) for m in meal_meds]

    if before:
        lines.append("")
        lines.append("⏰ До еды, ещё не отмечено: " + ", ".join(_med_line(m)[2:] for m in before)
                     + ". Если выпил — скажи, отмечу.")

    if meal_info:
        p = float(meal_info.get("protein") or 0)
        f = float(meal_info.get("fat") or 0)
        c = float(meal_info.get("carbs") or 0)
        kcal = float(meal_info.get("kcal") or 0)
        window = meal_info.get("window_minutes")
        n = meal_info.get("meals_in_window")
        scope = "в этом приёме"
        if window and n and n > 1:
            scope = f"за последние {window} мин ({n} записи)"
        lines.append("")
        lines.append(f"🍽 Съедено {scope}: {kcal:.0f} ккал — Б {p:.1f} / Ж {f:.1f} / У {c:.1f} г")

    checked = []
    for m in meal_meds:
        rule = med_food_rule(m["name"])
        if rule:
            checked.append((med_short_name(m["name"]), rule))
    if not checked:
        return "\n".join(lines)

    lines.append("")
    if not meal_info:
        lines.append("💡 По еде:")
        for name, r in checked:
            lines.append(f"• {name}: {_REQ_HINT[r['req']]}.")
        return "\n".join(lines)

    notes = []
    for name, r in checked:
        problems = check_meal_requirement(r["req"], meal_info)
        if problems:
            notes.append(f"💡 {name}: {_SOFT_ADVICE[r['req']]} — " + "; ".join(problems) + ".")
    if not notes:
        lines.append("✅ Еда подходит под все препараты.")
    else:
        lines += notes
    return "\n".join(lines)

def calculate_weekly_adherence(start_date: str, end_date: str) -> dict:
    """
    Регулярность приёма по каждому препарату за период.
    Приём засчитывается по med_intake_log (поштучно), medication_slot_log (слот целиком,
    старые записи) или med_log (запасной вариант).
    """
    start_dt = datetime.strptime(start_date, "%Y-%m-%d")
    end_dt = datetime.strptime(end_date, "%Y-%m-%d")

    total_scheduled = 0
    total_taken = 0
    missed_list = []

    conn = get_health_conn()
    cur = conn.cursor()
    curr = start_dt
    while curr <= end_dt:
        d_str = curr.strftime("%Y-%m-%d")
        for slot in SLOTS:
            status = get_slot_status(slot, d_str)
            if not status:
                continue
            logged = {
                (r[0] or "").casefold() for r in cur.execute(
                    "SELECT medication FROM med_log WHERE date = ? AND (method = ? OR method = ?)",
                    (d_str, slot, f"slot_{slot}"),
                )
            }
            missed = [m["name"] for m in status if not (m["taken"] or med_key(m["name"]) in logged)]
            total_scheduled += len(status)
            total_taken += len(status) - len(missed)
            if missed:
                missed_list.append({
                    "date": d_str,
                    "slot": slot,
                    "slot_ru": SLOT_NAMES_RU.get(slot, slot),
                    "medications": missed,
                })
        curr += timedelta(days=1)
    conn.close()

    missed_count = total_scheduled - total_taken
    adherence_pct = round((total_taken / total_scheduled) * 100) if total_scheduled > 0 else 100

    if missed_count == 0:
        msg = "Все приёмы лекарств за неделю выполнены на 100%! Ни одного пропуска."
    else:
        msg = f"Ай-яй-яй! Пропущено доз: {missed_count} из {total_scheduled}. Таблеточки надо не забывать принимать — непрерывность терапии критична!"

    return {
        "start_date": start_date,
        "end_date": end_date,
        "total_scheduled": total_scheduled,
        "total_taken": total_taken,
        "missed_count": missed_count,
        "adherence_pct": adherence_pct,
        "missed_list": missed_list,
        "message": msg
    }


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Explicit medication tracking schema migration (existing health database only).')
    parser.add_argument('command', choices=['migrate'])
    parser.parse_args()
    conn = sqlite3.connect(Path(HEALTH_DB_PATH).resolve().as_uri() + '?mode=rw', uri=True)
    try:
        init_db(conn)
    finally:
        conn.close()
