# Настройки финансов владельца

Файл `/opt/data/personal/finance/finance-settings.yaml` (переопределяется `FINANCE_SETTINGS`). Это данные владельца: в навык и репозиторий не попадает. Нет файла — счета `card` (Карта, по умолчанию) и `cash` (Наличные).

```yaml
default_account: card            # счёт, если пользователь не назвал другой
fuel_default_account: card       # необязательно: счёт для fuel-add
accounts:
  card:
    name: Карта
    aliases: [карта, картой]
  cash:
    name: Наличные
    aliases: [наличные, наличными, кэш]
  usd_account:
    name: Долларовый счёт
    currency: USD                # по умолчанию RUB
    aliases: [доллары]
import:                          # необязательно: выписки для импортёров
  sber:
    card: sber_card.txt          # счёт: файл в /opt/data/personal/finance/
  ozon:
    ozon_card: ozon.txt
ozon_category_patterns:          # необязательно: личные магазины → категория
  - ['MY SHOP', groceries]
vendor_patterns:                 # необязательно: regex → чистое название магазина
  - ['^MY\s+SHOP', 'Мой магазин']
```

Правила:

- id счёта — латиница/цифры/`_`; после первой транзакции не переименовывать (он хранится в базе). Менять можно `name` и `aliases`.
- Правка файла — показать изменение владельцу, получить подтверждение, затем `finance.py accounts` для проверки.
- Категории в шаблонах — только технические имена из `CATEGORY_NAMES`.
