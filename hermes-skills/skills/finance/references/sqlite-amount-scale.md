# SQLite: суммы в базе

В `finance.db` поле `transactions.amount` хранится **в копейках** (INTEGER).

## Практика
- Если в CLI сумма выглядит как `-400.00 руб`, то в SQL это `-40000`.
- Для прямых запросов, UPDATE и WHERE всегда переводить рубли в копейки: `рубли * 100`.
- Для чтения через `finance.py list` конвертация уже делается самим CLI.

## Типовой поиск
```sql
SELECT * FROM transactions WHERE date='2026-05-30' AND amount=-40000;
```

## Типовой UPDATE
```sql
UPDATE transactions SET account='sber_credit' WHERE id='...';
```
