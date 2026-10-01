#!/opt/data/home/.venv/bin/python
"""Импорт выписки Озон Банка (TXT) в finance.db."""
import argparse
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from category_map import IMPORT_FILES, map_ozon_category, normalize_vendor

DB = Path("/opt/data/personal/finance/finance.db")
DATA = Path("/opt/data/personal/finance")

rx_date_only = re.compile(r"^(\d{2}\.\d{2}\.\d{4})$")
rx_date_time = re.compile(r"^(\d{2}\.\d{2}\.\d{4})\s+(\d{2}:\d{2}:\d{2})")
rx_time_only = re.compile(r"^(\d{2}:\d{2}:\d{2})")
rx_amount = re.compile(r"([+-])\s+(\d[\d\s]*)\.(\d{2})\s*₽")



def clean_description(desc):
    value = re.sub(r"^\d{6,}\s+", "", desc).strip()
    value = re.sub(r"\bдата\s+\d{4}-\d{2}-\d{2}\b", "", value).strip()
    value = re.sub(r"\bвремя\s+\d{2}:\d{2}:\d{2}\b", "", value).strip()
    value = re.sub(r"\bдата\s*$", "", value).strip()
    value = re.sub(r"\s+", " ", value).strip()
    return value



def should_import_ozon(desc: str, amount: int) -> bool:
    if amount >= 0:
        return False
    lowered = desc.lower()
    if lowered.startswith("перевод собственных средств"):
        return False
    if lowered.startswith("сбп. получатель:"):
        return False
    if lowered.startswith("перевод "):
        return False
    return True



def parse_ozon_file(filepath: Path, account: str):
    with open(filepath, encoding="utf-8-sig") as f:
        raw_lines = f.read().replace("\xa0", " ").splitlines()

    rows = []
    cur_date = None
    cur_time = None
    cur_desc = ""
    seen_ids = {}
    skipped_non_expense = 0
    skipped_transfers = 0

    def make_tx(date, time_str, sign, whole, cents, desc):
        nonlocal skipped_non_expense, skipped_transfers

        whole_c = whole.replace(" ", "")
        try:
            amount = int(whole_c) * 100 + int(cents)
        except ValueError:
            return None

        if sign == "-":
            amount = -amount

        description = clean_description(desc)

        if amount >= 0:
            skipped_non_expense += 1
            return None
        if not should_import_ozon(description, amount):
            skipped_transfers += 1
            return None

        t = time_str if time_str else "00:00:00"
        try:
            dt = datetime.strptime(f"{date} {t[:8]}", "%d.%m.%Y %H:%M:%S")
        except ValueError:
            return None

        ts_ms = int(dt.timestamp() * 1000)
        base_id = f"ozon_{account}_{ts_ms}_{abs(amount)}"
        if base_id in seen_ids:
            seen_ids[base_id] += 1
            tx_id = f"{base_id}_{seen_ids[base_id]}"
        else:
            seen_ids[base_id] = 0
            tx_id = base_id

        category = map_ozon_category(description)
        vendor = normalize_vendor(description)
        note = description[:200]
        return (tx_id, ts_ms, dt.strftime("%Y-%m-%d"), amount, account, category, vendor, note)

    def flush(sign, whole, cents, desc):
        nonlocal cur_time, cur_desc
        if cur_date is None:
            return
        tx = make_tx(cur_date, cur_time, sign, whole, cents, desc)
        if tx:
            rows.append(tx)
        cur_time = None
        cur_desc = ""

    i = 0
    while i < len(raw_lines):
        line = raw_lines[i].strip()
        if not line:
            i += 1
            continue

        m_dt = rx_date_time.match(line)
        if m_dt:
            cur_date = m_dt.group(1)
            cur_time = m_dt.group(2)
            cur_desc = ""
            amt = rx_amount.search(line)
            if amt:
                desc = line[m_dt.end():amt.start()].strip()
                flush(amt.group(1), amt.group(2), amt.group(3), desc)
            i += 1
            continue

        m_do = rx_date_only.match(line)
        if m_do:
            cur_date = m_do.group(1)
            cur_time = None
            cur_desc = ""
            i += 1
            continue

        m_to = rx_time_only.match(line)
        if m_to:
            cur_time = m_to.group(1)
            i += 1
            continue

        amt = rx_amount.search(line)
        if amt:
            desc = line[:amt.start()].strip()
            m_d = rx_date_only.match(line)
            if m_d:
                cur_date = m_d.group(1)
                desc = line[m_d.end():amt.start()].strip()
            if not desc:
                desc = cur_desc
            flush(amt.group(1), amt.group(2), amt.group(3), desc)
            i += 1
            continue

        cur_desc = f"{cur_desc} {line}".strip() if cur_desc else line
        i += 1

    return rows, skipped_non_expense, skipped_transfers



def ensure_schema(conn):
    conn.execute(
        """CREATE TABLE IF NOT EXISTS transactions (
        id TEXT PRIMARY KEY, ts INTEGER, date TEXT, amount INTEGER,
        currency TEXT DEFAULT 'RUB', category TEXT DEFAULT 'other_expense',
        account TEXT, vendor TEXT, note TEXT)"""
    )



def import_account(conn, path: Path, account: str):
    rows, skipped_non_expense, skipped_transfers = parse_ozon_file(path, account)

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
        description="Импорт расходов из TXT-выписки Озон Банка в finance.db"
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

    files = IMPORT_FILES.get("ozon") or {}
    if not files:
        print("  Нет настроенных выписок Озона: добавьте import.ozon в finance-settings.yaml")
        conn.close()
        return 0

    for account, fname in files.items():
        path = DATA / fname
        if not path.exists():
            print(f"  {fname} not found")
            continue
        inserted, skipped_non_expense, skipped_transfers, replaced = import_account(conn, path, account)
        status = "заменено" if replaced else "пропущено"
        print(f"  {account}: {inserted} | +/не расход: {skipped_non_expense} | переводы: {skipped_transfers} | {status}")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
