#!/opt/data/home/.venv/bin/python
"""
Health Tracker CLI (Hermes Agent Skill)
Миграция функционала из OpenClaw (doctor.js) на Python.
"""
import sqlite3
import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
import difflib
from pathlib import Path

DB_PATH = '/opt/data/personal/health/health.db'

def get_conn(*, write=False):
    try:
        conn = sqlite3.connect(Path(DB_PATH).resolve().as_uri() + ('?mode=rw' if write else '?mode=ro'), uri=True)
    except sqlite3.OperationalError as exc:
        raise sqlite3.OperationalError('Cannot open existing health database; initialization must be a separate explicit operation.') from exc
    conn.row_factory = sqlite3.Row
    return conn

def find_closest_match(query, choices, cutoff=0.4, limit=5):
    return difflib.get_close_matches(query, choices, n=limit, cutoff=cutoff)

def add_metric(args):
    conn = get_conn(write=True)
    c = conn.cursor()
    msk_time = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=3)))
    ts = msk_time.strftime('%d.%m.%Y %H:%M:%S')
    date = args.date or msk_time.strftime('%Y-%m-%d')

    unit_defaults = {
        'weight': 'kg',
        'glucose': 'mmol/L',
        'pulse': 'bpm',
        'waist': 'cm',
        'temp': '°C',
        'water': 'L',
        'sleep': 'h',
        'steps': 'steps',
        'active_cal': 'kcal',
    }

    if args.type == 'bp':
        if args.systolic is None or args.diastolic is None:
            error = {"status": "error", "message": "For blood pressure (bp), both --systolic and --diastolic are required."}
            print(json.dumps(error, ensure_ascii=False))
            sys.exit(1)
        c.execute("INSERT INTO metrics (date, ts, type, systolic, diastolic, note) VALUES (?,?,?,?,?,?)",
                  (date, ts, 'bp', args.systolic, args.diastolic, args.note))
    else:
        if args.value is None:
            error = {"status": "error", "message": f"For metric type '{args.type}', --value is required."}
            print(json.dumps(error, ensure_ascii=False))
            sys.exit(1)
        unit = args.unit if args.unit is not None else unit_defaults.get(args.type)
        c.execute("INSERT INTO metrics (date, ts, type, value, unit, note) VALUES (?,?,?,?,?,?)",
                  (date, ts, args.type, args.value, unit, args.note))
    conn.commit()
    conn.close()

def _record_scheduled_medication(name, args, date, now):
    """Route scheduled doses through the idempotent slot-aware writer."""
    sys.path.append("/opt/data/scripts")
    import medication_reminder_helper as med_helper
    entry = next((e for e in med_helper.get_medication_catalog()
                  if med_helper.med_key(e["name"]) == med_helper.med_key(name)), None)
    if not entry or not any(s["slot"] != "as_needed" for s in entry["schedule"]):
        return False
    explicit_slot = getattr(args, "slot", None)
    if not explicit_slot and getattr(args, "method", None) in med_helper.SLOTS:
        explicit_slot = args.method
    payload = med_helper.take_meds(
        date_str=date, slot=explicit_slot, names=[entry["name"]],
        confirmed_text="confirmed via health.py medication", now=now,
    )
    print(json.dumps(payload, ensure_ascii=False))
    return True


def add_medication(args):
    msk_time = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=3)))
    date = args.date or msk_time.strftime('%Y-%m-%d')
    if _record_scheduled_medication(args.name.strip(), args, date, msk_time):
        return
    conn = get_conn(write=True)
    c = conn.cursor()
    msk_time = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=3)))
    ts = msk_time.strftime('%d.%m.%Y %H:%M:%S')
    date = args.date or msk_time.strftime('%Y-%m-%d')
    med_name_input = args.name.strip()
    c.execute("SELECT id, name, dosage, when_text AS method FROM medications WHERE active=1")
    active_meds = c.fetchall()
    med_dict = {row['name']: row for row in active_meds}
    lowered = {row['name'].lower(): row for row in active_meds}
    exact = lowered.get(med_name_input.lower())
    if exact:
        med_id = exact['id']
        med_name = exact['name']
        dosage = args.dosage or exact['dosage']
        method = args.method or exact['method']
        print(f"Найдено лекарство: {med_name} (дозировка: {dosage}, способ: {method})")
    else:
        choices = list(med_dict.keys())
        matches = find_closest_match(med_name_input, choices, cutoff=0.4)
        if matches:
            print(f"Лекарство '{med_name_input}' не найдено точно. Ближайшие варианты:")
            for i, m in enumerate(matches, 1):
                print(f"  {i}. {m}")
            print(f"  0. Записать как новое лекарство '{med_name_input}'")
            try:
                choice = input("Выберите номер (Enter для отмены): ").strip()
                if not choice:
                    print("Отмена.")
                    sys.exit(0)
                idx = int(choice)
                if idx == 0:
                    selected_name = med_name_input
                    med_id = None
                    exact = None
                elif 1 <= idx <= len(matches):
                    selected_name = matches[idx - 1]
                    exact = med_dict[selected_name]
                    med_id = exact['id']
                else:
                    print("Неверный номер. Отмена.")
                    sys.exit(1)
            except (EOFError, KeyboardInterrupt):
                print("\nОтмена.")
                sys.exit(0)
            except ValueError:
                print("Неверный ввод. Отмена.")
                sys.exit(1)
        else:
            print(f"Лекарство '{med_name_input}' не найдено в списке принимаемых. Будет записано как новое.")
            selected_name = med_name_input
            med_id = None
            exact = None
        med_name = selected_name
        if exact:
            dosage = args.dosage or exact['dosage']
            method = args.method or exact['method']
        else:
            if not args.dosage:
                try:
                    dosage = input("Введите дозировку (или Enter чтобы пропустить): ").strip() or None
                except (EOFError, KeyboardInterrupt):
                    print("\nОтмена.")
                    sys.exit(0)
            else:
                dosage = args.dosage
            if not args.method:
                try:
                    method = input("Введите способ/время приёма (или Enter чтобы пропустить): ").strip() or None
                except (EOFError, KeyboardInterrupt):
                    print("\nОтмена.")
                    sys.exit(0)
            else:
                method = args.method
    if exact and _record_scheduled_medication(med_name, args, date, msk_time):
        conn.close()
        return
    c.execute("INSERT INTO med_log (date, ts, medication, dosage, method, med_id) VALUES (?,?,?,?,?,?)",
              (date, ts, med_name, dosage, method, med_id))
    conn.commit()
    conn.close()
    print(f"Прием лекарства '{med_name}' записан в лог.")
    print(f"Дата: {date}, время: {ts}")

def list_metrics(args):
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT * FROM metrics WHERE date=? ORDER BY ts DESC", (args.date,))
    rows = c.fetchall()
    if getattr(args, 'summary', False) or getattr(args, 'json', False):
        has_records = len(rows) > 0
        payload = {
            "status": "ok",
            "has_records": has_records,
            "count": len(rows),
            "date": args.date,
        }
        print(json.dumps(payload, ensure_ascii=False))
    else:
        for row in rows:
            print(dict(row))
    conn.close()

def take_meds_cmd(args):
    """Отметка приёма: все препараты слота или только названные (--names)."""
    try:
        sys.path.append("/opt/data/scripts")
        import medication_reminder_helper as med_helper
        names = [n for n in (getattr(args, "names", None) or "").split(",") if n.strip()]
        payload = med_helper.take_meds(
            date_str=args.date, slot=args.slot, names=names,
            confirmed_text="confirmed via health.py",
        )
        print(json.dumps(payload, ensure_ascii=False))
    except Exception as e:
        print(json.dumps({"status": "error", "message": str(e)}, ensure_ascii=False))

def meds_catalog_cmd(args):
    """Справочник препаратов и время приёма (JSON, либо --text — короткий список)."""
    try:
        sys.path.append("/opt/data/scripts")
        import medication_reminder_helper as med_helper
        catalog = med_helper.get_medication_catalog()
        if getattr(args, "text", False):
            print(med_helper.format_catalog_text(catalog))
        else:
            print(json.dumps({"status": "ok", "count": len(catalog), "medications": catalog},
                             ensure_ascii=False))
    except Exception as e:
        print(json.dumps({"status": "error", "message": str(e)}, ensure_ascii=False))

def meds_resolve_cmd(args):
    """Сопоставить искажённое название с препаратом из справочника (ничего не записывает)."""
    try:
        sys.path.append("/opt/data/scripts")
        import medication_reminder_helper as med_helper
        cands = med_helper.resolve_medication_name(args.query)
        print(json.dumps({"status": "ok", "query": args.query, "candidates": cands},
                         ensure_ascii=False))
    except Exception as e:
        print(json.dumps({"status": "error", "message": str(e)}, ensure_ascii=False))

def main():
    parser = argparse.ArgumentParser(description='Health Tracker')
    subparsers = parser.add_subparsers(dest='command')
    cat_parser = subparsers.add_parser('meds-catalog', help='Справочник препаратов и время приёма')
    cat_parser.add_argument('--text', action='store_true', help='Короткий текстовый список вместо JSON')
    cat_parser.set_defaults(func=meds_catalog_cmd)
    res_parser = subparsers.add_parser('meds-resolve', help='Найти препарат по неточному названию (без записи)')
    res_parser.add_argument('--query', required=True, help='Название, как его написал или продиктовал владелец')
    res_parser.set_defaults(func=meds_resolve_cmd)
    take_meds_parser = subparsers.add_parser('take-meds', help='Mark medications as taken for current or specified slot')
    take_meds_parser.add_argument('--slot', choices=['morning', 'lunch', 'evening'], help='Slot to mark as taken')
    take_meds_parser.add_argument('--date', help='Date in YYYY-MM-DD format (default: today)')
    take_meds_parser.add_argument('--names', help='Только эти препараты, через запятую (без флага — все препараты слота)')
    take_meds_parser.set_defaults(func=take_meds_cmd)
    metric_parser = subparsers.add_parser('metric', help='Add metric')
    metric_parser.add_argument('type', choices=['weight', 'glucose', 'bp', 'pulse', 'waist', 'temp', 'water', 'sleep', 'steps', 'active_cal'])
    metric_parser.add_argument('--value', type=float)
    metric_parser.add_argument('--unit')
    metric_parser.add_argument('--systolic', type=int)
    metric_parser.add_argument('--diastolic', type=int)
    metric_parser.add_argument('--note')
    metric_parser.add_argument('--date')
    metric_parser.set_defaults(func=add_metric)
    med_parser = subparsers.add_parser('medication', help='Record medication intake')
    med_parser.add_argument('--name', required=True, help='Название лекарства')
    med_parser.add_argument('--dosage', help='Дозировка (если не указана, возмётся из справочника)')
    med_parser.add_argument('--method', help='Способ/время приёма (если не указано, возмётся из справочника)')
    med_parser.add_argument('--slot', choices=['morning', 'lunch', 'evening'], help='Явный слот планового приёма')
    med_parser.add_argument('--date')
    med_parser.set_defaults(func=add_medication)
    list_parser = subparsers.add_parser('list', help='List metrics')
    list_parser.add_argument('--date', required=True)
    list_parser.add_argument('--json', action='store_true', help='Вывод в JSON')
    list_parser.add_argument('--summary', action='store_true', help='Машинная сводка: has_records, count, status (без raw записей)')
    list_parser.set_defaults(func=list_metrics)
    args = parser.parse_args()
    if args.command:
        try:
            args.func(args)
        except sqlite3.DatabaseError as exc:
            print(json.dumps({'status': 'error', 'message': f'Database/schema error: {exc}. Schema changes require explicit initialization/migration.'}, ensure_ascii=False))
            sys.exit(1)
    else:
        parser.print_help()

if __name__ == '__main__':
    main()
