#!/opt/data/home/.venv/bin/python
"""CLI для управления личными финансами (finance.db)."""
import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from category_map import (
    ACCOUNT_CURRENCIES, ACCOUNT_NAMES, CATEGORY_NAMES, DEFAULT_ACCOUNT,
    FUEL_DEFAULT_ACCOUNT, SETTINGS, SETTINGS_PATH,
)

DB = Path("/opt/data/personal/finance/finance.db")



def get_conn():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn



def category_label(name):
    return CATEGORY_NAMES.get(name, name)



def account_label(name):
    return ACCOUNT_NAMES.get(name, name)


def account_currency(name):
    return ACCOUNT_CURRENCIES.get(name, "RUB")


def currency_label(currency):
    return {"RUB": "руб", "USD": "USD"}.get(currency, currency)



def accounts_help():
    return (
        "Счета: " + ", ".join(f"{name} ({label})" for name, label in ACCOUNT_NAMES.items())
        + f". По умолчанию: {DEFAULT_ACCOUNT}."
    )



def validate_category(conn, category):
    valid_cats = set(CATEGORY_NAMES.keys())
    if category not in valid_cats:
        print(f"ОШИБКА: неизвестная категория '{category}'")
        print(f"Допустимые: {', '.join(sorted(valid_cats))}")
        conn.close()
        sys.exit(1)



def validate_account(conn, account):
    valid_accounts = set(ACCOUNT_NAMES.keys())
    if account not in valid_accounts:
        print(f"ОШИБКА: неизвестный счёт '{account}'")
        print(f"Допустимые: {', '.join(sorted(valid_accounts))}")
        conn.close()
        sys.exit(1)



def make_manual_tx_id(conn, account, ts_ms, amount_abs):
    base_id = f"manual_{account}_{ts_ms}_{amount_abs}"
    tx_id = base_id
    suffix = 1
    while conn.execute("SELECT 1 FROM transactions WHERE id=?", (tx_id,)).fetchone():
        tx_id = f"{base_id}_{suffix}"
        suffix += 1
    return tx_id



def render_text_cell(*parts, limit=44):
    text = " | ".join(part.strip() for part in parts if part and part.strip())
    if len(text) > limit:
        return text[: limit - 1] + "…"
    return text



def cmd_accounts(args):
    """Счета владельца, счёт по умолчанию и разговорные названия (из настроек)."""
    data = {
        "settings_file": str(SETTINGS_PATH),
        "settings_exists": SETTINGS_PATH.exists(),
        "default_account": DEFAULT_ACCOUNT,
        "fuel_default_account": FUEL_DEFAULT_ACCOUNT,
        "accounts": [
            {
                "id": key,
                "name": ACCOUNT_NAMES[key],
                "currency": ACCOUNT_CURRENCIES.get(key, "RUB"),
                "aliases": list(((SETTINGS["accounts"].get(key) or {}).get("aliases") or [])),
            }
            for key in ACCOUNT_NAMES
        ],
    }
    if getattr(args, "json", False):
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return
    for acc in data["accounts"]:
        mark = " (по умолчанию)" if acc["id"] == DEFAULT_ACCOUNT else ""
        aliases = f" — {', '.join(acc['aliases'])}" if acc["aliases"] else ""
        print(f"{acc['id']}: {acc['name']} [{acc['currency']}]{mark}{aliases}")
    if not data["settings_exists"]:
        print(f"Настройки не найдены ({SETTINGS_PATH}): используются счета по умолчанию.")


def cmd_list(args):
    conn = get_conn()
    c = conn.cursor()

    query = "SELECT * FROM transactions WHERE 1=1"
    params = []

    if args.from_:
        query += " AND date >= ?"
        params.append(args.from_)
    if args.to:
        query += " AND date <= ?"
        params.append(args.to)
    if args.cat:
        if args.cat == "auto_all":
            query += " AND category IN ('auto', 'fuel')"
        else:
            query += " AND category = ?"
            params.append(args.cat)
    if args.account:
        query += " AND account = ?"
        params.append(args.account)

    query += " ORDER BY ts DESC"

    if args.limit:
        query += f" LIMIT {int(args.limit)}"

    c.execute(query, params)
    rows = c.fetchall()

    if args.json:
        result = []
        for r in rows:
            result.append({
                "id": r["id"],
                "date": r["date"],
                "amount": r["amount"] / 100,
                "currency": r["currency"],
                "category": r["category"],
                "account": r["account"],
                "vendor": r["vendor"],
                "note": r["note"],
            })
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"{'Дата':<12} {'Сумма':>12} {'Вал.':<5} {'Категория':<20} {'Счёт':<15} {'Магазин / note'}")
        print("─" * 114)
        totals = {}
        for r in rows:
            amount = r["amount"] / 100
            totals[r["currency"]] = totals.get(r["currency"], 0) + amount
            sign = "+" if amount > 0 else ""
            details = render_text_cell(r["vendor"], r["note"])
            print(
                f"{r['date']:<12} {sign}{amount:>11,.2f} {r['currency']:<5} {category_label(r['category']):<20} "
                f"{account_label(r['account']):<15} {details}"
            )
        print("─" * 114)
        for currency, total in sorted(totals.items()):
            sign = "+" if total > 0 else ""
            print(f"{('ИТОГО ' + currency):<12} {sign}{total:>11,.2f} {currency:<5}")

    conn.close()



def cmd_balance(args):
    conn = get_conn()
    c = conn.cursor()

    if args.account:
        validate_account(conn, args.account)
        c.execute(
            "SELECT currency, SUM(amount)/100.0 FROM transactions WHERE account=? GROUP BY currency",
            (args.account,),
        )
        row = c.fetchone()
        currency = row[0] if row else account_currency(args.account)
        total = row[1] if row else 0
        print(
            f"Баланс {account_label(args.account)}: {total:,.2f} "
            f"{currency_label(currency)}"
        )
    else:
        c.execute(
            """SELECT account, currency, SUM(amount)/100.0
            FROM transactions
            GROUP BY account, currency
            ORDER BY currency, account"""
        )
        totals = {}
        for r in c.fetchall():
            print(
                f"  {account_label(r[0]):<20} {r[2]:>12,.2f} "
                f"{currency_label(r[1])}"
            )
            totals[r[1]] = totals.get(r[1], 0) + r[2]
        print(f"  {'─' * 35}")
        for currency, total in sorted(totals.items()):
            print(
                f"  {('ИТОГО ' + currency):<20} {total:>12,.2f} "
                f"{currency_label(currency)}"
            )

    conn.close()



def cmd_add(args):
    conn = get_conn()
    c = conn.cursor()

    validate_category(conn, args.cat)
    validate_account(conn, args.account)

    amount = int(args.amount * 100)
    if args.sign == "neg":
        amount = -abs(amount)
    else:
        amount = abs(amount)

    date = args.on if args.on else datetime.now().strftime("%Y-%m-%d")
    ts = int(datetime.strptime(date, "%Y-%m-%d").timestamp() * 1000)
    tx_id = make_manual_tx_id(conn, args.account, ts, abs(amount))

    currency = account_currency(args.account)
    c.execute(
        """INSERT INTO transactions
        (id, ts, date, amount, currency, category, account, vendor, note)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (tx_id, ts, date, amount, currency, args.cat, args.account, args.vendor, args.note),
    )

    conn.commit()
    conn.close()
    print(
        f"Добавлено: {date} | {amount / 100:+.2f} {currency_label(currency)} | "
        f"{category_label(args.cat)} | {args.account}"
    )



def cmd_delete(args):
    conn = get_conn()
    c = conn.cursor()

    if args.index:
        c.execute("SELECT id, date, amount, category FROM transactions ORDER BY ts DESC LIMIT 1 OFFSET ?", (args.index - 1,))
        row = c.fetchone()
        if row:
            c.execute("DELETE FROM transactions WHERE id=?", (row["id"],))
            conn.commit()
            print(f"Удалено: {row['date']} | {row['amount'] / 100:+.2f} руб | {category_label(row['category'])}")
        else:
            print("Транзакция не найдена")
    elif args.last_cat:
        validate_category(conn, args.last_cat)
        c.execute("SELECT id, date, amount, category FROM transactions WHERE category=? ORDER BY ts DESC LIMIT 1", (args.last_cat,))
        row = c.fetchone()
        if row:
            c.execute("DELETE FROM transactions WHERE id=?", (row["id"],))
            conn.commit()
            print(f"Удалено: {row['date']} | {row['amount'] / 100:+.2f} руб | {category_label(row['category'])}")
        else:
            print(f"Транзакции с категорией '{args.last_cat}' не найдены")

    conn.close()



def cmd_stats(args):
    conn = get_conn()
    c = conn.cursor()

    period = args.period or "month"

    where = ""
    params = []
    if args.from_:
        where += " AND date >= ?"
        params.append(args.from_)
    if args.to:
        where += " AND date <= ?"
        params.append(args.to)

    if period == "month":
        c.execute(
            f"""
            SELECT substr(date, 1, 7) as period, currency,
                   SUM(CASE WHEN amount < 0 THEN amount ELSE 0 END)/100.0 as expenses,
                   SUM(CASE WHEN amount > 0 THEN amount ELSE 0 END)/100.0 as income,
                   COUNT(*) as cnt
            FROM transactions
            WHERE 1=1{where}
            GROUP BY substr(date, 1, 7), currency
            ORDER BY substr(date, 1, 7), currency
        """,
            params,
        )
        print(f"{'Период':<10} {'Вал.':<5} {'Расходы':>12} {'Доходы':>12} {'Баланс':>12} {'Транз.'}")
        print("─" * 66)
        for r in c.fetchall():
            balance = (r["income"] or 0) + (r["expenses"] or 0)
            print(f"{r['period']:<10} {r['currency']:<5} {(r['expenses'] or 0):>12,.2f} {(r['income'] or 0):>12,.2f} {balance:>12,.2f} {r['cnt']:>5}")

    elif period == "cat":
        c.execute(
            f"""
            SELECT category, currency,
                   SUM(amount)/100.0 as total,
                   COUNT(*) as cnt
            FROM transactions
            WHERE amount < 0{where}
            GROUP BY category, currency
            ORDER BY currency, SUM(amount)
        """,
            params,
        )
        rows = c.fetchall()
        print(f"{'Категория':<25} {'Вал.':<5} {'Сумма':>12} {'Транз.'}")
        print("─" * 51)
        totals = {}
        totals_by_cat = {}
        counts_by_cat = {}
        for r in rows:
            currency = r["currency"]
            print(f"{category_label(r['category']):<25} {currency:<5} {r['total']:>12,.2f} {r['cnt']:>5}")
            totals[currency] = totals.get(currency, 0) + r["total"]
            totals_by_cat[(r["category"], currency)] = r["total"]
            counts_by_cat[(r["category"], currency)] = r["cnt"]

        for currency in sorted(totals):
            auto_all_total = (totals_by_cat.get(("auto", currency), 0) or 0) + (totals_by_cat.get(("fuel", currency), 0) or 0)
            auto_all_cnt = (counts_by_cat.get(("auto", currency), 0) or 0) + (counts_by_cat.get(("fuel", currency), 0) or 0)
            print(f"{'Автомобиль (с топливом)':<25} {currency:<5} {auto_all_total:>12,.2f} {auto_all_cnt:>5}")

        print("─" * 51)
        for currency, total in sorted(totals.items()):
            print(f"{('ИТОГО ' + currency):<25} {currency:<5} {total:>12,.2f}")

    conn.close()




def cmd_fuel_add(args):
    conn = get_conn()
    c = conn.cursor()

    validate_account(conn, args.account)

    amount = int(args.amount * 100)
    tx_amount = -abs(amount)

    date = args.on if args.on else datetime.now().strftime("%Y-%m-%d")
    ts = int(datetime.strptime(date, "%Y-%m-%d").timestamp() * 1000)
    tx_id = make_manual_tx_id(conn, args.account, ts, abs(amount))

    is_full = 1 if args.full else 0
    full_str = "полный бак" if is_full else "частично"
    note_parts = [f"{args.liters:.1f} л", f"{args.odo:.0f} км", full_str]
    if args.note:
        note_parts.append(args.note)
    tx_note = " | ".join(note_parts)

    vendor = args.vendor if args.vendor else "АЗС"

    c.execute(
        """INSERT INTO transactions
        (id, ts, date, amount, currency, category, account, vendor, note)
        VALUES (?, ?, ?, ?, 'RUB', 'fuel', ?, ?, ?)""",
        (tx_id, ts, date, tx_amount, args.account, vendor, tx_note),
    )

    price_per_liter = args.amount / args.liters if args.liters > 0 else 0.0
    c.execute(
        """INSERT INTO fuel_log
        (transaction_id, date, odo_km, liters, price_per_liter, total_cost, is_full, account, vendor, note)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            tx_id,
            date,
            args.odo,
            args.liters,
            price_per_liter,
            args.amount,
            is_full,
            args.account,
            vendor,
            args.note,
        ),
    )

    conn.commit()
    conn.close()
    print(
        f"Заправка добавлена: {date} | {args.amount:,.2f} руб | {args.liters:.1f} л | "
        f"{args.odo:,.0f} км | {'До полного' if is_full else 'Частично'} | {account_label(args.account)}"
    )


def cmd_fuel_list(args):
    conn = get_conn()
    c = conn.cursor()

    query = "SELECT * FROM fuel_log WHERE 1=1"
    params = []
    if args.from_:
        query += " AND date >= ?"
        params.append(args.from_)
    if args.to:
        query += " AND date <= ?"
        params.append(args.to)

    query += " ORDER BY odo_km DESC, date DESC"

    if args.limit:
        query += f" LIMIT {int(args.limit)}"

    c.execute(query, params)
    rows = c.fetchall()

    if args.json:
        result = []
        for r in rows:
            result.append({
                "id": r["id"],
                "date": r["date"],
                "odo_km": r["odo_km"],
                "liters": r["liters"],
                "total_cost": r["total_cost"],
                "price_per_liter": r["price_per_liter"],
                "is_full": bool(r["is_full"]),
                "account": r["account"],
                "vendor": r["vendor"],
                "note": r["note"],
            })
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(
            f"{'Дата':<12} {'Одометр':>10} {'Литры':>8} {'Сумма (руб)':>12} {'Цена/л':>8} {'Бак':<10} {'Счёт':<15} {'АЗС / Заметка'}"
        )
        print("─" * 105)
        for r in rows:
            full_txt = "Полный" if r["is_full"] else "Частично"
            price_txt = f"{r['price_per_liter']:.2f}" if r["price_per_liter"] else "-"
            vendor_note = render_text_cell(r["vendor"], r["note"])
            print(
                f"{r['date']:<12} {r['odo_km']:>10,.0f} {r['liters']:>8.1f} {r['total_cost']:>12,.2f} "
                f"{price_txt:>8} {full_txt:<10} {account_label(r['account']):<15} {vendor_note}"
            )
        print("─" * 105)

    conn.close()


def cmd_fuel_stats(args):
    conn = get_conn()
    c = conn.cursor()

    query = "SELECT * FROM fuel_log WHERE 1=1"
    params = []
    if args.from_:
        query += " AND date >= ?"
        params.append(args.from_)
    if args.to:
        query += " AND date <= ?"
        params.append(args.to)

    query += " ORDER BY odo_km ASC, date ASC"

    c.execute(query, params)
    rows = c.fetchall()

    if not rows:
        print("Записей о заправках за выбранный период не найдено.")
        conn.close()
        return

    total_cost = sum(r["total_cost"] for r in rows)
    total_liters = sum(r["liters"] for r in rows)
    avg_price_per_liter = total_cost / total_liters if total_liters > 0 else 0.0

    print("════════════════════════════════════════════════════════════")
    print("СТАТИСТИКА ЗАПРАВОК И РАСХОДА ТОПЛИВА")
    if args.from_ or args.to:
        print(f"Период: {args.from_ or 'начало'} — {args.to or 'конец'}")
    print("════════════════════════════════════════════════════════════")
    print(f"Всего заправок:         {len(rows)}")
    print(f"Потрачено денег:        {total_cost:,.2f} руб")
    print(f"Залито топлива:         {total_liters:,.1f} л")
    print(f"Средняя цена литра:     {avg_price_per_liter:,.2f} руб/л")

    full_refills = [r for r in rows if r["is_full"]]

    if len(full_refills) >= 2:
        first_full = full_refills[0]
        last_full = full_refills[-1]

        total_dist = last_full["odo_km"] - first_full["odo_km"]

        liters_consumed = sum(
            r["liters"] for r in rows
            if r["odo_km"] > first_full["odo_km"] and r["odo_km"] <= last_full["odo_km"]
        )
        cost_consumed = sum(
            r["total_cost"] for r in rows
            if r["odo_km"] > first_full["odo_km"] and r["odo_km"] <= last_full["odo_km"]
        )

        if total_dist > 0:
            avg_consumption = (liters_consumed / total_dist) * 100
            cost_per_km = cost_consumed / total_dist
            print("────────────────────────────────────────────────────────────")
            print(f"Интервал расчёта (до полного): {first_full['date']} ({first_full['odo_km']:,.0f} км) → {last_full['date']} ({last_full['odo_km']:,.0f} км)")
            print(f"Пройденный пробег:      {total_dist:,.0f} км")
            print(f"Израсходовано топлива:  {liters_consumed:,.1f} л")
            print(f"СРЕДНИЙ РАСХОД:         {avg_consumption:.2f} л / 100 км")
            print(f"Стоимость 1 км пробега: {cost_per_km:.2f} руб/км")
        else:
            print("────────────────────────────────────────────────────────────")
            print("Одометр не изменился между первой и последней заправкой.")
    else:
        print("────────────────────────────────────────────────────────────")
        print("Для точного расчёта расхода (л/100 км) нужно минимум 2 заправки 'до полного бака'.")

    print("════════════════════════════════════════════════════════════")
    conn.close()


def main():
    parser = argparse.ArgumentParser(
        description="Управление личными финансами",
        epilog=accounts_help(),
    )
    subparsers = parser.add_subparsers(dest="command")

    p_accounts = subparsers.add_parser("accounts", help="Счета владельца и счёт по умолчанию")
    p_accounts.add_argument("--json", action="store_true", help="Вывод в JSON")

    p_list = subparsers.add_parser("list", help="Список транзакций")
    p_list.add_argument("--from", dest="from_", help="Дата начала (YYYY-MM-DD)")
    p_list.add_argument("--to", help="Дата конца (YYYY-MM-DD)")
    p_list.add_argument("--cat", help="Фильтр по категории (или auto_all = auto+fuel)")
    p_list.add_argument("--account", help="Фильтр по счёту")
    p_list.add_argument("--limit", type=int, help="Лимит записей")
    p_list.add_argument("--json", action="store_true", help="Вывод в JSON")

    p_balance = subparsers.add_parser("balance", help="Баланс")
    p_balance.add_argument("--account", help="Баланс по конкретному счёту")

    p_add = subparsers.add_parser("add", help="Добавить транзакцию")
    p_add.add_argument("--amount", type=float, required=True, help="Сумма в валюте счёта")
    p_add.add_argument("--cat", required=True, help="Категория")
    p_add.add_argument("--account", default=DEFAULT_ACCOUNT, help="Счёт (по умолчанию — из настроек владельца)")
    p_add.add_argument("--sign", choices=["neg", "pos"], default="neg", help="Знак (neg=расход, pos=доход)")
    p_add.add_argument("--vendor", help="Название магазина/места")
    p_add.add_argument("--note", help="Детали покупки / заметка")
    p_add.add_argument("--on", help="Дата (YYYY-MM-DD)")

    p_delete = subparsers.add_parser("delete", help="Удалить транзакцию")
    p_delete.add_argument("--index", type=int, help="Номер от конца (1=последняя)")
    p_delete.add_argument("--last-cat", help="Удалить последнюю по категории")

    p_stats = subparsers.add_parser("stats", help="Статистика")
    p_stats.add_argument("--period", choices=["month", "cat"], default="month", help="Тип статистики")
    p_stats.add_argument("--from", dest="from_", help="Дата начала (YYYY-MM-DD)")
    p_stats.add_argument("--to", help="Дата конца (YYYY-MM-DD)")

    # Fuel subparsers
    p_fuel_add = subparsers.add_parser("fuel-add", help="Записать заправку с пробегом")
    p_fuel_add.add_argument("--odo", type=float, required=True, help="Пробег (одометр), км")
    p_fuel_add.add_argument("--liters", type=float, required=True, help="Количество литров")
    p_fuel_add.add_argument("--amount", type=float, required=True, help="Сумма заправки в рублях")
    p_fuel_add.add_argument("--account", default=FUEL_DEFAULT_ACCOUNT, help="Счёт оплаты")
    p_fuel_add.add_argument("--full", action="store_true", default=True, help="Заправка до полного бака (отсечка)")
    p_fuel_add.add_argument("--partial", dest="full", action="store_false", help="Частичная заправка")
    p_fuel_add.add_argument("--vendor", help="АЗС / Магазин")
    p_fuel_add.add_argument("--note", help="Дополнительный комментарий")
    p_fuel_add.add_argument("--on", help="Дата (YYYY-MM-DD)")

    p_fuel_list = subparsers.add_parser("fuel-list", help="Список заправок")
    p_fuel_list.add_argument("--from", dest="from_", help="Дата начала (YYYY-MM-DD)")
    p_fuel_list.add_argument("--to", help="Дата конца (YYYY-MM-DD)")
    p_fuel_list.add_argument("--limit", type=int, help="Лимит записей")
    p_fuel_list.add_argument("--json", action="store_true", help="Вывод в JSON")

    p_fuel_stats = subparsers.add_parser("fuel-stats", help="Статистика заправок и расхода л/100км")
    p_fuel_stats.add_argument("--from", dest="from_", help="Дата начала (YYYY-MM-DD)")
    p_fuel_stats.add_argument("--to", help="Дата конца (YYYY-MM-DD)")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    commands = {
        "accounts": cmd_accounts,
        "list": cmd_list,
        "balance": cmd_balance,
        "add": cmd_add,
        "delete": cmd_delete,
        "stats": cmd_stats,
        "fuel-add": cmd_fuel_add,
        "fuel-list": cmd_fuel_list,
        "fuel-stats": cmd_fuel_stats,
    }

    commands[args.command](args)


if __name__ == "__main__":
    main()
