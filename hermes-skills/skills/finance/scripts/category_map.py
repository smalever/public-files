#!/opt/data/home/.venv/bin/python
"""Общие маппинги и константы для финансовых скиллов.

Счета, счёт по умолчанию, файлы выписок и личные шаблоны магазинов — данные
владельца, а не код: они читаются из `finance-settings.yaml` рядом с базой
(`/opt/data/personal/finance/`, путь можно переопределить `FINANCE_SETTINGS`).
Если файла нет — работают счета по умолчанию «Наличные» и «Карта».
Формат файла — `references/settings.md`.
"""

import os
import re
from pathlib import Path

import yaml

SETTINGS_PATH = Path(os.environ.get(
    "FINANCE_SETTINGS", "/opt/data/personal/finance/finance-settings.yaml"
))

DEFAULT_SETTINGS = {
    "default_account": "card",
    "accounts": {
        "card": {"name": "Карта"},
        "cash": {"name": "Наличные", "aliases": ["наличные", "наличными", "кэш"]},
    },
}


def load_settings(path=None):
    """Настройки владельца; при отсутствии/ошибке файла — DEFAULT_SETTINGS."""
    path = Path(path) if path else SETTINGS_PATH
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return DEFAULT_SETTINGS
    if not isinstance(data, dict) or not isinstance(data.get("accounts"), dict) or not data["accounts"]:
        return DEFAULT_SETTINGS
    return data


SETTINGS = load_settings()

# ── Счета: техническое имя → русское (из настроек владельца) ───────
ACCOUNT_NAMES = {
    key: str((value or {}).get("name") or key)
    for key, value in SETTINGS["accounts"].items()
}

# Валюта счёта. По умолчанию RUB; валютные указываются в настройках (`currency: USD`).
ACCOUNT_CURRENCIES = {
    key: str(value["currency"]).upper()
    for key, value in SETTINGS["accounts"].items()
    if isinstance(value, dict) and value.get("currency")
}

DEFAULT_ACCOUNT = SETTINGS.get("default_account") or next(iter(ACCOUNT_NAMES))
FUEL_DEFAULT_ACCOUNT = SETTINGS.get("fuel_default_account") or DEFAULT_ACCOUNT

# Файлы выписок для импортёров: {"sber": {account: file}, "ozon": {account: file}}
IMPORT_FILES = SETTINGS.get("import") or {}


def _compile_pairs(items):
    pairs = []
    for item in items or []:
        if isinstance(item, (list, tuple)) and len(item) == 2:
            try:
                pairs.append((re.compile(str(item[0]), re.I), str(item[1])))
            except re.error:
                continue
    return pairs


# ── Категории: техническое имя → русское ──────────────────────────
# Единственный источник правды для импортёров и CLI.
# Добавлять новые категории ТОЛЬКО сюда и в references/categories.md.
CATEGORY_NAMES = {
    "groceries": "Продукты",
    "health": "Здоровье",
    "transport": "Транспорт",
    "restaurants": "Рестораны",
    "clothing": "Одежда",
    "travel": "Путешествия",
    "entertainment": "Развлечения",
    "home": "Дом/ЖКХ",
    "household": "Товары для дома",
    "it_services": "IT-сервисы",
    "subscriptions": "Подписки",
    "electronics": "Техника",
    "gifts": "Подарки",
    "auto": "Автомобиль",
    "fuel": "Топливо",
    "smoking": "Сигареты",
    "legal": "Юридические расходы",
    "other_expense": "Прочие расходы",
    "marketplace": "Маркетплейсы",
}

# ── Маппинг Сбера: начало строки → единая категория ──────────────
SBER_CATEGORY_MAP = {
    "Супермаркеты": "groceries",
    "Здоровье и красота": "health",
    "Транспорт": "transport",
    "Коммунальные платежи": "home",
    "Автомобиль": "auto",
    "Рестораны и кафе": "restaurants",
    "Путешествия": "travel",
    "Одежда и аксессуары": "clothing",
    "Отдых и развлечения": "entertainment",
    "Прочие расходы": "other_expense",
    "Прочие операции": "other_expense",
}

# ── Маппинг Озона: (regex, категория), порядок важен! ───────────
# Общие шаблоны: служебные строки банка и федеральные сети. Личные магазины
# владельца — `ozon_category_patterns` в настройках, проверяются после служебных строк.
OZON_CATEGORY_MAP = [
    (re.compile(r"^через СБП\. Оплата"), "home"),
    (re.compile(r"^через СБП\.\d"), "home"),
    (re.compile(r"Premium\), заказ"), "marketplace"),
    (re.compile(r"^Оплата товаров/услуг на Платформе Ozon"), "marketplace"),
    (re.compile(r"^\d{10,11}$"), "marketplace"),
    (re.compile(r"^Оплата товаров по карте"), "groceries"),
] + _compile_pairs(SETTINGS.get("ozon_category_patterns")) + [
    (re.compile(r"MAGAZIN PRODUKTOV"), "groceries"),
    (re.compile(r"FIXPRICE"), "groceries"),
    (re.compile(r"VKUSNOITOCHKA"), "groceries"),
    (re.compile(r"PRODUKTOV"), "groceries"),
    (re.compile(r"PYATEROCHKA"), "groceries"),
    (re.compile(r"MAGNIT"), "groceries"),
    (re.compile(r"NOTARIUS"), "legal"),
    (re.compile(r"^\d{6,9}$"), "other_expense"),
]

# ── Нормализация vendor: regex → чистое название ─────────────────
# Личные шаблоны владельца (`vendor_patterns` в настройках) проверяются первыми.
VENDOR_PATTERNS = _compile_pairs(SETTINGS.get("vendor_patterns")) + [
    (re.compile(r"^PYATEROCHKA\s+\d+", re.I), "Пятёрочка"),
    (re.compile(r"^MAGNIT\s+(MM|GM|MK|DM)", re.I), "Магнит"),
    (re.compile(r"^FIXPRICE\s+\d*", re.I), "ФиксПрайс"),
    (re.compile(r"^VKUSNOITOCHKA", re.I), "Вкусно и точка"),
    (re.compile(r"^MAGAZIN\s+PRODUKTOV", re.I), "Магазин продуктов"),
    (re.compile(r"^\d{10,11}$"), "Ozon"),
    (re.compile(r"^Premium\), заказ", re.I), "Ozon"),
    (re.compile(r"^Супермаркеты$"), "Супермаркет"),
    (re.compile(r"^Транспорт$"), "Транспорт"),
    (re.compile(r"^Рестораны и кафе$"), "Ресторан"),
    (re.compile(r"^Автомобиль$"), "Авто"),
    (re.compile(r"^Здоровье и красота"), "Здоровье"),
    (re.compile(r"^Одежда и аксессуары$"), "Одежда"),
    (re.compile(r"^Путешествия$"), "Путешествия"),
    (re.compile(r"^Отдых и развлечения$"), "Развлечения"),
    (re.compile(r"^Коммунальные платежи"), "ЖКХ"),
    (re.compile(r"^Прочие расходы$"), "Прочее"),
    (re.compile(r"^Прочие операции$"), "Прочее"),
    (re.compile(r"^NOTARIUS", re.I), "Нотариус"),
]


def map_sber_category(raw: str) -> str:
    """Маппинг категории Сбера на единый справочник."""
    for prefix, cat in SBER_CATEGORY_MAP.items():
        if raw.startswith(prefix):
            return cat
    return "other_expense"



def map_ozon_category(raw: str) -> str:
    """Маппинг категории Озона на единый справочник."""
    for pattern, cat in OZON_CATEGORY_MAP:
        if pattern.search(raw):
            return cat
    return "other_expense"



def normalize_vendor(raw: str | None) -> str | None:
    """Приводит сырое описание к чистому названию магазина/места."""
    if not raw:
        return None
    value = re.sub(r"\s+", " ", raw).strip()
    if not value:
        return None
    for pattern, label in VENDOR_PATTERNS:
        if pattern.search(value):
            return label
    return None
