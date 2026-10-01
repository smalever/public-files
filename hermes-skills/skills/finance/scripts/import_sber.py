#!/opt/data/home/.venv/bin/python
"""Импорт выписок СберБанка (TXT) в finance.db."""
import argparse
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from category_map import IMPORT_FILES, map_sber_category, normalize_vendor

DB = Path("/opt/data/personal/finance/finance.db")
DATA = Path("/opt/data/personal/finance")

# Счета и файлы выписок задаёт владелец: `import.sber` в finance-settings.yaml.
FILES = [(fname, account) for account, fname in (IMPORT_FILES.get("sber") or {}).items()]

rx_dt = re.compile(r"^(\d{2}\.\d{2}\.\d{4})\s+(\d{2}:\d{2})")
rx_amount = re.compile(r"([+-]?)\s*(\d[\d\s]*),(\d{2})")
TRANSFER_PREFIXES = (
    "Перевод на карту",
    "Перевод с карты",
    "Перевод СБП",
    "Перевод по СБП",
    "Перевод",
)



def should_import_sber(raw_category: str, amount: int) -> bool:
    if amount >= 0:
        return False
    cleaned = raw_category.strip()
    return not cleaned.startswith(TRANSFER_PREFIXES)



def parse_sber_file(filepath: Path, account: str):
    with open(filepath, encoding="utf-8-sig") as f:
        content = f.read().replace("\xa0", " ")

    rows = []
    seen_ids = {}
    skipped_non_expense = 0
    skipped_transfers = 0

    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue
        m = rx_dt.match(line)
        if not m:
            continue

        date_str, time_str = m.group(1), m.group(2)
        amounts = list(rx_amount.finditer(line))
        if not amounts:
            continue

        amt_match = amounts[-2] if len(amounts) >= 2 else amounts[0]
        sign_str = amt_match.group(1)
        whole = amt_match.group(2).replace(" ", "")
        cents = amt_match.group(3)
        try:
            amount = int(whole) * 100 + int(cents)
        except ValueError:
            continue

        if sign_str == "-":
            amount = -amount
        elif sign_str != "+":
            amount = -amount

        cat_start, cat_end = m.end(), amt_match.start()
        raw_category = line[cat_start:cat_end].strip()
        raw_category = re.sub(r"[\s•–—\-]+$", "", raw_category).strip()
        raw_category = re.sub(r",?\s+в т\.ч\..*$", "", raw_category).strip()

        if amount >= 0:
            skipped_non_expense += 1
            continue
        if not should_import_sber(raw_category, amount):
            skipped_transfers += 1
            continue

        category = map_sber_category(raw_category)
        vendor = normalize_vendor(raw_category)
        note = raw_category[:200]

        try:
            dt = datetime.strptime(f"{date_str} {time_str}", "%d.%m.%Y %H:%M")
        except ValueError:
            continue

        ts_ms = int(dt.timestamp() * 1000)
        base_id = f"sber_{account}_{ts_ms}_{abs(amount)}"
        if base_id in seen_ids:
            seen_ids[base_id] += 1
            tx_id = f"{base_id}_{seen_ids[base_id]}"
        else:
            seen_ids[base_id] = 0
            tx_id = base_id

        rows.append((tx_id, ts_ms, dt.strftime("%Y-%m-%d"), amount, account, category, vendor, note))

    return rows, skipped_non_expense, skipped_transfers



def ensure_schema(conn):
    conn.execute(
        """CREATE TABLE IF NOT EXISTS transactions (
        id TEXT PRIMARY KEY, ts INTEGER, date TEXT, amount INTEGER,
        currency TEXT DEFAULT 'RUB', category TEXT DEFAULT 'other_expense',
        account TEXT, vendor TEXT, note TEXT)"""
    )



def import_account(conn, path: Path, account: str):
    rows, skipped_non_expense, skipped_transfers = parse_sber_file(path, account)

    if not rows:
        print(f"  {account}: 0 строк для импорта, замена пропущена")
        return 0, skipped_non_expense, skipped_transfers, False

    with conn:
        conn.execute("DELETE FROM transactions WHERE account=?", (account,))
        conn.executemany(
            "INSERT OR IGNORE INTO transactions (id,ts,date,amount,account,category,vendor,note) VALUES (?,?,?,?,?,?,?,?)",
            rows,
        )
    return len(rows), skipped_non_expense, skipped_transfers, True



def build_parser():
    parser = argparse.ArgumentParser(
        description="Импорт расходов из TXT-выписок Сбера в finance.db"
    )
    parser.add_argument(
        "--run",
        action="store_true",
        help="выполнить импорт; без аргументов показывается справка",
    )
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.run:
        parser.print_help()
        return 0

    conn = sqlite3.connect(DB)
    ensure_schema(conn)

    total = 0
    total_non_expense = 0
    total_transfers = 0

    if not FILES:
        print("  Нет настроенных выписок Сбера: добавьте import.sber в finance-settings.yaml")
        conn.close()
        return 0

    for fname, account in FILES:
        path = DATA / fname
        if not path.exists():
            print(f"  {fname} - not found")
            continue
        inserted, skipped_non_expense, skipped_transfers, replaced = import_account(conn, path, account)
        status = "заменено" if replaced else "пропущено"
        print(
            f"  {account}: {inserted} | +/не расход: {skipped_non_expense} | переводы: {skipped_transfers} | {status}"
        )
        total += inserted
        total_non_expense += skipped_non_expense
        total_transfers += skipped_transfers

    conn.close()
    print(f"Total imported: {total}")
    print(f"Skipped non-expense: {total_non_expense}")
    print(f"Skipped transfers: {total_transfers}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
