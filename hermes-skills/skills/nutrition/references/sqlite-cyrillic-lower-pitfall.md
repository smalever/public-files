# SQLite LOWER() Cyrillic Pitfall

## Проблема

SQLite `LOWER()` переводит в нижний регистр **только ASCII** (A-Z → a-z). Для кириллицы (и любого multi-byte Unicode) это no-op:

```sql
-- В БД: "Кофе с молоком" (К = U+041A)
SELECT LOWER('Кофе с молоком');  -- Возвращает "Кофе с молоком" — К НЕ изменилась!
```

Из-за этого `LOWER(product) LIKE '%кофе%'` не находит запись, где product = "Кофе с молоком" (U+041A ≠ U+043A).

## Диагностика

Проверить, что `LOWER()` не сработал:

```bash
# Сравнить hex product и hex LOWER(product)
sqlite3 food.db "SELECT hex(product), hex(LOWER(product)) FROM food_db WHERE id = N;"
# Если hex совпадает — LOWER ничего не сделал
```

## Рабочее решение

1. **Не используй `LOWER()` для кириллицы** — ищи напрямую `product LIKE '%слово%'`
2. Если не нашло — попробуй с заглавной первой буквой: `product LIKE '%Слово%'` (продукты в БД с заглавной)
3. Если не нашло — попробуй сочетание: часть слова с заглавной, часть со строчной
4. Как крайняя мера — ищи через Python с корректным `str.lower()` (Python обрабатывает Unicode правильно)

## Пример из практики

Запрос: найти "Кофе с молоком"

```bash
# ❌ НЕ РАБОТАЕТ — LOWER не меняет кириллицу
sqlite3 nutrition.db "SELECT * FROM food_db WHERE LOWER(product) LIKE '%кофе%';"

# ❌ НЕ РАБОТАЕТ — регистрозависимый LIKE
sqlite3 nutrition.db "SELECT * FROM food_db WHERE product LIKE '%кофе%';"

# ✅ РАБОТАЕТ — часть слова без заглавной буквы
sqlite3 nutrition.db "SELECT * FROM food_db WHERE product LIKE '%офе%';"

# ✅ РАБОТАЕТ — продукт целиком
sqlite3 nutrition.db "SELECT * FROM food_db WHERE product = 'Кофе с молоком';"
```