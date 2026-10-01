#!/opt/data/home/.venv/bin/python
"""
Nutrition Tracker CLI (Hermes Agent Skill)
Логика питания, вынесенная из health.py.
"""
import sqlite3
import argparse
import json
import os
import sys
import re
from datetime import datetime, timedelta, timezone

DB_PATH = '/opt/data/personal/nutrition/nutrition.db'

def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def levenshtein(s1, s2):
    """Расстояние Левенштейна между двумя строками."""
    if len(s1) < len(s2):
        return levenshtein(s2, s1)
    if len(s2) == 0:
        return len(s1)
    prev_row = range(len(s2) + 1)
    for i, c1 in enumerate(s1):
        curr_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = prev_row[j + 1] + 1
            deletions = curr_row[j] + 1
            substitutions = prev_row[j] + (c1 != c2)
            curr_row.append(min(insertions, deletions, substitutions))
        prev_row = curr_row
    return prev_row[-1]

def fuzzy_threshold(word_len):
    """Адаптивный порог для fuzzy search: короткие слова — строже."""
    return max(1, word_len // 3)

def norm_text(s):
    """Нормализация для кириллицы без SQLite LOWER()."""
    return (s or '').strip().lower().replace('ё', 'е')

def _dedupe_rows(rows):
    seen = set()
    out = []
    for r in rows:
        key = r['id']
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out

def _product_rows_for_aliases(c, alias_rows):
    rows = []
    for ar in alias_rows:
        found = c.execute(
            "SELECT id, product, kcal, protein, fat, carbs FROM food_db WHERE product=?",
            (ar['product'],)
        ).fetchall()
        rows.extend(found)
    return _dedupe_rows(rows)

def find_product(c, search):
    """
    Поиск продукта по базе. Возвращает (rows, match_type).
    Приоритет согласован со skill:
    1) точное совпадение food_db, 2) точный алиас,
    3) похожее по смыслу в food_db/food_aliases, 4) fuzzy.
    """
    search_norm = norm_text(search)

    all_products = c.execute(
        "SELECT id, product, normalized_name, kcal, protein, fat, carbs FROM food_db"
    ).fetchall()
    all_aliases = c.execute("SELECT alias, product FROM food_aliases").fetchall()

    # 1. Точное совпадение в food_db через Python lower для кириллицы.
    rows = [p for p in all_products
            if search_norm in (norm_text(p['product']), norm_text(p['normalized_name']))]
    if rows:
        return rows, 'exact'

    # 2. Точный алиас.
    alias_rows = [a for a in all_aliases if norm_text(a['alias']) == search_norm]
    rows = _product_rows_for_aliases(c, alias_rows)
    if rows:
        return rows, 'alias'

    # 3. Похожее по смыслу в food_db.
    # Для фраз из нескольких слов автосовпадение только если совпали все части.
    search_parts = [p for p in search_norm.split() if p]
    rows = []
    for p in all_products:
        text_norm = norm_text(f"{p['product']} {p['normalized_name'] or ''}")
        full_match = search_norm in text_norm
        parts_match = bool(search_parts) and all(part in text_norm for part in search_parts)
        if full_match or parts_match:
            rows.append(p)
    if rows:
        return _dedupe_rows(rows), 'similar'

    # 4. Похожее по смыслу в алиасах — та же строгая логика.
    alias_rows = []
    for a in all_aliases:
        text_norm = norm_text(f"{a['alias']} {a['product']}")
        full_match = search_norm in text_norm
        parts_match = bool(search_parts) and all(part in text_norm for part in search_parts)
        if full_match or parts_match:
            alias_rows.append(a)
    rows = _product_rows_for_aliases(c, alias_rows)
    if rows:
        return rows, 'similar_alias'

    # 5. Fuzzy search по food_db и alias.
    threshold = fuzzy_threshold(len(search_norm))
    candidates = []
    for p in all_products:
        for name in [p['product'], p['normalized_name']]:
            if name and levenshtein(search_norm, norm_text(name)) <= threshold:
                candidates.append(p)
                break
    if candidates:
        return _dedupe_rows(candidates), 'fuzzy'

    alias_rows = [a for a in all_aliases if levenshtein(search_norm, norm_text(a['alias'])) <= threshold]
    rows = _product_rows_for_aliases(c, alias_rows)
    if rows:
        return rows, 'fuzzy_alias'

    return [], None

def load_medication_reminder_helper(loader=None):
    """Load the optional medication helper without invalidating a saved meal."""
    try:
        sys.path.append("/opt/data/scripts")
        if loader is not None:
            return loader()
        import medication_reminder_helper
        return medication_reminder_helper
    except Exception as exc:
        print(
            "⚠️ Не удалось загрузить модуль напоминаний о лекарствах: "
            f"{type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return None


def render_medication_reminder(
    *, med_helper, date_str, current_msk_dt, total_meals_today, meal_info
):
    """Build a meal-time medication reminder; report helper failures to stderr."""
    try:
        reminder_res = med_helper.determine_reminder(
            date_str, current_msk_dt, total_meals_today
        )
        if not reminder_res:
            return None
        slot, meds = reminder_res
        return med_helper.format_reminder(slot, meds, meal_info=meal_info)
    except Exception as exc:
        print(
            "⚠️ Не удалось проверить напоминание о лекарствах: "
            f"{type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return None


def window_meal_totals(c, now_msk, window_minutes=60):
    """Суммирует БЖУ всех записей еды за последние window_minutes (по полю ts, МСК).

    Возвращает dict(kcal, protein, fat, carbs, meals_in_window, window_minutes).
    """
    since = now_msk - timedelta(minutes=window_minutes)
    dates = {now_msk.strftime('%Y-%m-%d'), since.strftime('%Y-%m-%d')}
    placeholders = ','.join('?' * len(dates))
    rows = c.execute(
        f"SELECT ts, total_kcal, total_protein, total_fat, total_carbs "
        f"FROM nutrition WHERE date IN ({placeholders})",
        tuple(dates),
    ).fetchall()
    tot = {'kcal': 0.0, 'protein': 0.0, 'fat': 0.0, 'carbs': 0.0}
    n = 0
    naive_now = now_msk.replace(tzinfo=None)
    naive_since = since.replace(tzinfo=None)
    for r in rows:
        try:
            t = datetime.strptime(r[0], '%d.%m.%Y %H:%M:%S')
        except (TypeError, ValueError):
            continue
        if naive_since <= t <= naive_now:
            n += 1
            tot['kcal'] += r[1] or 0
            tot['protein'] += r[2] or 0
            tot['fat'] += r[3] or 0
            tot['carbs'] += r[4] or 0
    tot = {k: round(v, 1) for k, v in tot.items()}
    tot['meals_in_window'] = n
    tot['window_minutes'] = window_minutes
    return tot


def add_meal(args):
    conn = get_conn()
    c = conn.cursor()
    msk_time = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=3)))
    ts = msk_time.strftime('%d.%m.%Y %H:%M:%S')
    date = args.date or msk_time.strftime('%Y-%m-%d')

    items = []
    if ';' in args.items:
        print(json.dumps({
            "status": "error",
            "type": "bad_items_format",
            "message": "Используй запятые для разделения продуктов, не ';'. Формат: продукт 100г, продукт 200г"
        }, ensure_ascii=False))
        conn.close()
        return

    # Используем regex findall вместо split, чтобы запятые в числах (2,5%)
    # и разделителях не ломали парсинг. Ищем паттерн "название число + г".
    matches = re.findall(r'(.+?)\s+(\d+(?:[.,]\d+)?)\s*г', args.items)
    for match in matches:
        product = match[0].strip(' ,')
        grams = float(match[1].replace(',', '.'))
        items.append({'product': product, 'grams': grams})

    if not items:
        print("Не удалось распознать продукты.")
        conn.close()
        return

    total_kcal, total_p, total_f, total_c = 0, 0, 0, 0
    nutrition_items = []
    not_found_products = []

    for item in items:
        search = item['product']
        rows, match_type = find_product(c, search)

        if rows:
            row = rows[0]
            if len(rows) > 1:
                variants = ', '.join(dict(r)['product'] for r in rows[:5])
                print(json.dumps({
                    "status": "warning", "type": "multiple_matches",
                    "product": search,
                    "message": f"Найдено несколько вариантов для '{search}': {variants}. Выбран: {row['product']}"
                }, ensure_ascii=False))
            elif match_type in ('fuzzy', 'fuzzy_alias', 'similar', 'similar_alias'):
                print(json.dumps({
                    "status": "warning", "type": "fuzzy_match",
                    "product": search,
                    "selected": row['product'],
                    "match_type": match_type,
                    "message": f"Продукт '{search}' не найден точно. Подобран: '{row['product']}'"
                }, ensure_ascii=False))
            kcal_100, p_100, f_100, c_100 = row['kcal'], row['protein'], row['fat'], row['carbs']
            ratio = item['grams'] / 100.0
            kcal = int(kcal_100 * ratio)
            p = round(p_100 * ratio, 1)
            f = round(f_100 * ratio, 1)
            c_val = round(c_100 * ratio, 1)

            total_kcal += kcal
            total_p += p
            total_f += f
            total_c += c_val

            nutrition_items.append({
                'product': row['product'], 'grams': item['grams'],
                'kcal': kcal, 'protein': p, 'fat': f, 'carbs': c_val
            })
        else:
            # Ничего не найдено — ищем похожие для подсказки
            suggestions = _find_suggestions(c, search)
            not_found_products.append({
                'product': item['product'],
                'grams': item['grams'],
                'suggestions': suggestions
            })

    if not_found_products:
        for item in not_found_products:
            msg = f"Продукт '{item['product']}' не найден в базе."
            if item['suggestions']:
                variants = ', '.join(item['suggestions'][:5])
                msg += f" Похожие: {variants}"
            print(json.dumps({
                "status": "error", "type": "product_not_found",
                "product": item['product'], "grams": item['grams'],
                "suggestions": item['suggestions'],
                "message": msg
            }, ensure_ascii=False))
        conn.close()
        return

    c.execute("INSERT INTO nutrition (date, ts, meal, total_kcal, total_protein, total_fat, total_carbs) VALUES (?,?,?,?,?,?,?)",
              (date, ts, args.meal, total_kcal, total_p, total_f, total_c))
    nutrition_id = c.lastrowid

    for ni in nutrition_items:
        c.execute("INSERT INTO nutrition_items (nutrition_id, product, grams, kcal, protein, fat, carbs) VALUES (?,?,?,?,?,?,?)",
                  (nutrition_id, ni['product'], ni['grams'], ni['kcal'], ni['protein'], ni['fat'], ni['carbs']))

    total_meals_today = c.execute("SELECT count(*) FROM nutrition WHERE date=?", (date,)).fetchone()[0]
    conn.commit()
    window_totals = None
    if date == msk_time.strftime('%Y-%m-%d'):
        try:
            window_totals = window_meal_totals(c, msk_time, 60)
        except Exception as exc:
            print(f"⚠️ Не удалось посчитать еду за последний час: {type(exc).__name__}: {exc}",
                  file=sys.stderr)
    conn.close()
    print(f"Прием пищи '{args.meal}' записан. Калории: {total_kcal}")

    # Check medication reminder for the meal (only for today's meals).
    med_helper = load_medication_reminder_helper()
    msk_dt = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=3)))
    today_str = msk_dt.strftime("%Y-%m-%d")
    if med_helper is not None and date == today_str:
        meal_info = {
            "meal": args.meal,
            "kcal": total_kcal,
            "fat": total_f,
            "protein": total_p,
            "carbs": total_c,
            "meals_in_window": 1,
        }
        if window_totals and window_totals.get("meals_in_window", 0) >= 1:
            meal_info.update(window_totals)
            meal_info["meal"] = args.meal
        reminder = render_medication_reminder(
            med_helper=med_helper,
            date_str=date,
            current_msk_dt=msk_dt,
            total_meals_today=total_meals_today,
            meal_info=meal_info,
        )
        if reminder:
            print()
            print(reminder)

def _find_suggestions(c, search, max_suggestions=5):
    """Находит похожие продукты и алиасы для подсказки при product_not_found."""
    search_norm = norm_text(search)
    search_parts = [p for p in search_norm.split() if p]
    threshold = fuzzy_threshold(len(search_norm)) + 1
    candidates = []

    products = c.execute("SELECT product, normalized_name FROM food_db").fetchall()
    for p in products:
        names = [p['product'], p['normalized_name']]
        text_norm = norm_text(' '.join(n for n in names if n))
        score = None
        if search_norm in text_norm or any(part in text_norm for part in search_parts):
            score = 0
        else:
            best_dist = min((levenshtein(search_norm, norm_text(n)) for n in names if n), default=999)
            if best_dist <= threshold:
                score = best_dist
        if score is not None:
            candidates.append((score, p['product']))

    aliases = c.execute("SELECT alias, product FROM food_aliases").fetchall()
    for a in aliases:
        text_norm = norm_text(f"{a['alias']} {a['product']}")
        score = None
        if search_norm in text_norm or any(part in text_norm for part in search_parts):
            score = 0
        else:
            best_dist = levenshtein(search_norm, norm_text(a['alias']))
            if best_dist <= threshold:
                score = best_dist
        if score is not None:
            candidates.append((score, a['product']))

    candidates.sort(key=lambda x: (x[0], x[1]))
    out = []
    seen = set()
    for _, name in candidates:
        if name not in seen:
            seen.add(name)
            out.append(name)
        if len(out) >= max_suggestions:
            break
    return out

def delete_meal(args):
    """Удаление приёма пищи по ID с предварительным показом."""
    conn = get_conn()
    c = conn.cursor()

    # Показываем запись перед удалением
    c.execute("SELECT n.id, n.date, n.meal, n.total_kcal, n.total_protein, n.total_fat, n.total_carbs, "
              "GROUP_CONCAT(ni.product || ' ' || ni.grams || 'г', ', ') as items "
              "FROM nutrition n LEFT JOIN nutrition_items ni ON n.id = ni.nutrition_id "
              "WHERE n.id = ? GROUP BY n.id", (args.id,))
    row = c.fetchone()

    if not row:
        print(json.dumps({
            "status": "error", "type": "not_found",
            "message": f"Запись с ID {args.id} не найдена."
        }, ensure_ascii=False))
        conn.close()
        return

    print(json.dumps({
        "status": "info", "type": "preview",
        "id": row['id'], "date": row['date'], "meal": row['meal'],
        "kcal": row['total_kcal'], "protein": row['total_protein'],
        "fat": row['total_fat'], "carbs": row['total_carbs'],
        "items": row['items'],
        "message": f"ID {row['id']}: {row['date']} {row['meal']} — {row['total_kcal']} ккал ({row['items']})"
    }, ensure_ascii=False))

    if not args.yes:
        confirm = input("Удалить эту запись? (да/нет): ").strip().lower()
        if confirm not in ('да', 'д', 'yes', 'y'):
            print(json.dumps({"status": "ok", "type": "cancelled", "message": "Удаление отменено."}))
            conn.close()
            return

    # Удаляем
    c.execute("DELETE FROM nutrition_items WHERE nutrition_id = ?", (args.id,))
    c.execute("DELETE FROM nutrition WHERE id = ?", (args.id,))
    conn.commit()
    conn.close()
    print(json.dumps({
        "status": "ok", "type": "deleted",
        "id": args.id,
        "message": f"Запись ID {args.id} удалена."
    }, ensure_ascii=False))

def summary_meal(args):
    """Просмотр съеденных калорий и БЖУ за день или период."""
    conn = get_conn()
    c = conn.cursor()
    msk_time = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=3)))

    if args.days and args.days > 1:
        end_date = msk_time.date()
        start_date = end_date - timedelta(days=args.days - 1)
        start_str = start_date.strftime('%Y-%m-%d')
        end_str = end_date.strftime('%Y-%m-%d')

        rows = c.execute(
            "SELECT date, SUM(total_kcal) as kcal, SUM(total_protein) as p, "
            "SUM(total_fat) as f, SUM(total_carbs) as c, COUNT(id) as meals_cnt "
            "FROM nutrition WHERE date >= ? AND date <= ? "
            "GROUP BY date ORDER BY date ASC",
            (start_str, end_str)
        ).fetchall()

        if not rows:
            print(f"Нет записей за период {start_str} — {end_str}.")
            conn.close()
            return

        total_kcal = sum(r['kcal'] or 0 for r in rows)
        total_p = sum(r['p'] or 0 for r in rows)
        total_f = sum(r['f'] or 0 for r in rows)
        total_c = sum(r['c'] or 0 for r in rows)
        days_count = len(rows)

        print(f"Статистика питания за {args.days} дн. ({start_str} — {end_str}):")
        print(f"Дней с записями: {days_count}")
        print(f"Всего: {total_kcal:.0f} ккал (Б: {total_p:.1f}г, Ж: {total_f:.1f}г, У: {total_c:.1f}г)")
        print(f"В среднем в день: {total_kcal/days_count:.0f} ккал (Б: {total_p/days_count:.1f}г, Ж: {total_f/days_count:.1f}г, У: {total_c/days_count:.1f}г)")
        print("\nПо дням:")
        for r in rows:
            print(f"  {r['date']}: {r['kcal']:.0f} ккал (Б: {r['p']:.1f}г, Ж: {r['f']:.1f}г, У: {r['c']:.1f}г, приёмов: {r['meals_cnt']})")
    else:
        target_date = args.date or msk_time.strftime('%Y-%m-%d')
        rows = c.execute(
            "SELECT id, date, ts, meal, total_kcal, total_protein, total_fat, total_carbs "
            "FROM nutrition WHERE date = ? ORDER BY id ASC",
            (target_date,)
        ).fetchall()

        if not rows:
            print(f"Нет записей за {target_date}.")
            conn.close()
            return

        meal_names = {
            'breakfast': 'Завтрак',
            'lunch': 'Обед',
            'dinner': 'Ужин',
            'snack': 'Перекус'
        }

        total_kcal = sum(r['total_kcal'] or 0 for r in rows)
        total_p = sum(r['total_protein'] or 0 for r in rows)
        total_f = sum(r['total_fat'] or 0 for r in rows)
        total_c = sum(r['total_carbs'] or 0 for r in rows)

        print(f"Питание за {target_date}:")
        print(f"Итого: {total_kcal:.0f} ккал (Б: {total_p:.1f}г, Ж: {total_f:.1f}г, У: {total_c:.1f}г)")
        print("\nПриёмы пищи:")
        for r in rows:
            meal_label = meal_names.get(r['meal'], r['meal'])
            print(f"- {meal_label} (ID {r['id']}) — {r['total_kcal']} ккал (Б: {r['total_protein']}г, Ж: {r['total_fat']}г, У: {r['total_carbs']}г):")
            items = c.execute(
                "SELECT product, grams, kcal, protein, fat, carbs FROM nutrition_items WHERE nutrition_id = ? ORDER BY id ASC",
                (r['id'],)
            ).fetchall()
            for it in items:
                print(f"  • {it['product']} — {it['grams']}г ({it['kcal']} ккал)")
    conn.close()

def take_meds_cmd(args):
    """Отметка приёма: все препараты слота или только названные (--names)."""
    try:
        sys.path.append("/opt/data/scripts")
        import medication_reminder_helper as med_helper
        names = [n for n in (getattr(args, "names", None) or "").split(",") if n.strip()]
        payload = med_helper.take_meds(
            date_str=args.date, slot=args.slot, names=names,
            confirmed_text="confirmed via cli",
        )
        print(json.dumps(payload, ensure_ascii=False))
    except Exception as e:
        print(json.dumps({"status": "error", "message": str(e)}, ensure_ascii=False))

def main():
    parser = argparse.ArgumentParser(description='Nutrition Tracker')
    subparsers = parser.add_subparsers(dest='command')

    meal_parser = subparsers.add_parser('meal', help='Add meal')
    meal_parser.add_argument('--meal', required=True, choices=['breakfast', 'lunch', 'dinner', 'snack'])
    meal_parser.add_argument('--items', required=True, help='e.g., "говядина 200г, гречка 150г"')
    meal_parser.add_argument('--date')
    meal_parser.set_defaults(func=add_meal)

    meds_parser = subparsers.add_parser('take-meds', help='Mark medications as taken')
    meds_parser.add_argument('--slot', choices=['morning', 'lunch', 'evening'], help='Slot name')
    meds_parser.add_argument('--date', help='Date in YYYY-MM-DD format')
    meds_parser.add_argument('--names', help='Только эти препараты, через запятую (без флага — все препараты слота)')
    meds_parser.set_defaults(func=take_meds_cmd)

    del_parser = subparsers.add_parser('delete', help='Delete meal by ID')
    del_parser.add_argument('--id', type=int, required=True, help='ID записи для удаления')
    del_parser.add_argument('--yes', action='store_true', help='Удалить без интерактивного подтверждения после внешнего подтверждения пользователя')
    del_parser.set_defaults(func=delete_meal)

    summary_parser = subparsers.add_parser('summary', help='Show nutrition summary')
    summary_parser.add_argument('--date', help='Date in YYYY-MM-DD format (default: today)')
    summary_parser.add_argument('--days', type=int, default=1, help='Number of days (default: 1)')
    summary_parser.set_defaults(func=summary_meal)

    args = parser.parse_args()
    if args.command:
        args.func(args)
    else:
        parser.print_help()

if __name__ == '__main__':
    main()
