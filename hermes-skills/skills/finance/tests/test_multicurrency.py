import argparse
import importlib.util
import io
import sqlite3
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

# Тест не зависит от счетов конкретного владельца: свой файл настроек.
_SETTINGS_DIR = tempfile.mkdtemp()
_SETTINGS = Path(_SETTINGS_DIR) / "finance-settings.yaml"
_SETTINGS.write_text(
    "default_account: card\n"
    "accounts:\n"
    "  card: {name: Карта}\n"
    "  usd_account: {name: Долларовый счёт, currency: USD}\n",
    encoding="utf-8",
)
import os  # noqa: E402
os.environ["FINANCE_SETTINGS"] = str(_SETTINGS)

spec = importlib.util.spec_from_file_location("finance_cli", SCRIPTS / "finance.py")
finance = importlib.util.module_from_spec(spec)
spec.loader.exec_module(finance)


def make_db(path):
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            """CREATE TABLE transactions (
                id TEXT PRIMARY KEY,
                ts INTEGER,
                date TEXT,
                amount INTEGER,
                currency TEXT DEFAULT 'RUB',
                category TEXT DEFAULT 'other_expense',
                account TEXT,
                vendor TEXT,
                note TEXT
            )"""
        )
        conn.commit()
    finally:
        conn.close()


class MulticurrencyTest(unittest.TestCase):
    def test_usd_account_adds_usd_transaction(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "finance.db"
            make_db(db)
            finance.DB = db
            args = argparse.Namespace(
                amount=10.0,
                cat="subscriptions",
                account="usd_account",
                sign="neg",
                vendor="Сервис",
                note="подписка",
                on="2026-08-27",
            )

            with redirect_stdout(io.StringIO()):
                finance.cmd_add(args)

            conn = sqlite3.connect(db)
            try:
                row = conn.execute(
                    "SELECT amount, currency, category, account, vendor, note FROM transactions"
                ).fetchone()
            finally:
                conn.close()

        self.assertEqual(
            row,
            (
                -1000,
                "USD",
                "subscriptions",
                "usd_account",
                "Сервис",
                "подписка",
            ),
        )

    def test_balance_reports_totals_separately_by_currency(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "finance.db"
            make_db(db)
            conn = sqlite3.connect(db)
            try:
                conn.executemany(
                    """INSERT INTO transactions
                    (id, amount, currency, category, account)
                    VALUES (?, ?, ?, 'subscriptions', ?)""",
                    [
                        ("rub", -10000, "RUB", "card"),
                        ("usd", -1000, "USD", "usd_account"),
                    ],
                )
                conn.commit()
            finally:
                conn.close()
            finance.DB = db

            output = io.StringIO()
            with redirect_stdout(output):
                finance.cmd_balance(argparse.Namespace(account=None))

        text = output.getvalue()
        self.assertIn("ИТОГО RUB", text)
        self.assertIn("-100.00 руб", text)
        self.assertIn("ИТОГО USD", text)
        self.assertIn("-10.00 USD", text)
        self.assertNotIn("-110.00", text)

    def test_list_reports_totals_separately_by_currency(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "finance.db"
            make_db(db)
            conn = sqlite3.connect(db)
            try:
                conn.executemany(
                    """INSERT INTO transactions
                    (id, ts, date, amount, currency, category, account)
                    VALUES (?, 1, '2026-08-27', ?, ?, 'subscriptions', ?)""",
                    [
                        ("rub", -10000, "RUB", "card"),
                        ("usd", -1000, "USD", "usd_account"),
                    ],
                )
                conn.commit()
            finally:
                conn.close()
            finance.DB = db
            args = argparse.Namespace(
                from_=None, to=None, cat=None, account=None, limit=None, json=False
            )

            output = io.StringIO()
            with redirect_stdout(output):
                finance.cmd_list(args)

        text = output.getvalue()
        self.assertIn("ИТОГО RUB", text)
        self.assertIn("ИТОГО USD", text)
        self.assertNotIn("-110.00", text)

    def test_category_stats_reports_totals_separately_by_currency(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "finance.db"
            make_db(db)
            conn = sqlite3.connect(db)
            try:
                conn.executemany(
                    """INSERT INTO transactions
                    (id, amount, currency, category, account)
                    VALUES (?, ?, ?, 'subscriptions', ?)""",
                    [
                        ("rub", -10000, "RUB", "card"),
                        ("usd", -1000, "USD", "usd_account"),
                    ],
                )
                conn.commit()
            finally:
                conn.close()
            finance.DB = db
            args = argparse.Namespace(period="cat", from_=None, to=None)

            output = io.StringIO()
            with redirect_stdout(output):
                finance.cmd_stats(args)

        text = output.getvalue()
        self.assertIn("ИТОГО RUB", text)
        self.assertIn("ИТОГО USD", text)
        self.assertNotIn("-110.00", text)


class SettingsTest(unittest.TestCase):
    def test_default_settings_without_file(self):
        import category_map
        data = category_map.load_settings(Path(_SETTINGS_DIR) / "missing.yaml")
        self.assertEqual(data["default_account"], "card")
        self.assertIn("cash", data["accounts"])

    def test_owner_settings_are_used(self):
        self.assertEqual(finance.DEFAULT_ACCOUNT, "card")
        self.assertEqual(finance.ACCOUNT_CURRENCIES.get("usd_account"), "USD")


if __name__ == "__main__":
    unittest.main()
