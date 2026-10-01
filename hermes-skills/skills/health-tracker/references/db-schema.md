# Health DB Schema

База: `/opt/data/personal/health/health.db`

## Таблица `medications`

Справочник принимаемых препаратов.

| Поле | Тип | Описание |
|------|-----|----------|
| id | TEXT PK | Например `med_1775925099440` |
| name | TEXT NOT NULL | Название препарата |
| dosage | TEXT | Дозировка, например `50 мг` |
| frequency | TEXT | Частота приёма |
| timing | TEXT | JSON-массив времён из поля `time` схемы (например `["20:00"]`); если времени в схеме нет — `[]`. Заполняет `health_sync.py`, на напоминания не влияет |
| when_text | TEXT | Слот приёма: `morning`, `lunch`, `evening`, `wednesday_morning`, `morning_evening` (препарат в двух слотах) |
| note | TEXT | Заметка |
| active | BOOLEAN DEFAULT 1 | Активен (1) или скрыт (0) |
| added | DATE | Дата добавления |
| food | TEXT | Приём относительно еды: `before` / `with` / `after` / `any` |

Таблица — реплика схемы из Obsidian (`Моё здоровье/Схема препаратов.md`); править её вручную не нужно, `health_sync.py` перезаписывает её из заметки.

**Значения `when_text`:**
- `morning` — утренний слот
- `lunch` — дневной слот
- `evening` — вечерний слот
- `wednesday_morning` — только по средам утром
- `morning_evening` — один препарат и в утреннем, и в вечернем слоте

### SQL-паттерны

```sql
-- Все утренние лекарства (включая morning_evening)
SELECT name, dosage FROM medications
WHERE active=1 AND when_text IN ('morning', 'morning_evening');

-- Все вечерние лекарства (включая morning_evening)
SELECT name, dosage FROM medications
WHERE active=1 AND when_text IN ('evening', 'morning_evening');

-- Конкретный день недели (среда)
SELECT name, dosage FROM medications
WHERE active=1 AND when_text='wednesday_morning';

-- Все активные
SELECT id, name, dosage, when_text, food FROM medications WHERE active=1;
```

> ⚠️ **Столбец `active`**, не `is_active`.
> ⚠️ Сопоставление по слотам для приёма и напоминаний делает `medication_reminder_helper.py` по схеме из Obsidian, а не по этой таблице; таблица нужна как запасной вариант.

## Таблицы приёма препаратов

Пишутся только через `health.py take-meds` / `nutrition.py take-meds`.

**`med_intake_log`** — отметка по каждому препарату:

| Поле | Тип | Описание |
|------|-----|----------|
| id | INTEGER PK AUTOINCREMENT | |
| date | TEXT NOT NULL | Дата `YYYY-MM-DD` |
| slot | TEXT NOT NULL | `morning` / `lunch` / `evening` — слот препарата из схемы |
| medication | TEXT NOT NULL | Название |
| med_key | TEXT NOT NULL | Название в нижнем регистре (`casefold`) |
| ts | TEXT NOT NULL | Время `DD.MM.YYYY HH:MM:SS` |
| confirmed_text | TEXT | Кто отметил (`confirmed via health.py` / `confirmed via cli`) |

Уникально по `(date, slot, med_key)`: повторная отметка дубль не создаёт.

**`medication_slot_log`** — слот закрыт целиком (`date`, `slot`, `ts`, `confirmed_text`, уникально по `(date, slot)`). Пишется, когда принят весь слот; старые записи слота считаются «все препараты приняты».

## Таблица `med_log`

Лог приёмов лекарств.

| Поле | Тип | Описание |
|------|-----|----------|
| id | INTEGER PK AUTOINCREMENT | |
| date | TEXT NOT NULL | Дата `YYYY-MM-DD` |
| ts | TEXT NOT NULL | Время `DD.MM.YYYY HH:MM:SS` |
| medication | TEXT NOT NULL | Название |
| dosage | TEXT | Дозировка |
| method | TEXT | Способ/время (копируется из medications.when_text) |
| med_id TEXT | FK → medications.id | |

**Запись:** только через `scripts/health.py medication`, не вручную.

```sql
-- Последние приёмы
SELECT * FROM med_log ORDER BY date DESC, ts DESC LIMIT 10;

-- Приёмы за день
SELECT * FROM med_log WHERE date='YYYY-MM-DD' ORDER BY ts;
```

## Таблица `metrics`

Метрики здоровья: давление, пульс, вес, глюкоза и т.д.

| Поле | Тип | Описание |
|------|-----|----------|
| id | INTEGER PK AUTOINCREMENT | |
| date | TEXT NOT NULL | Дата `YYYY-MM-DD` |
| ts | TEXT NOT NULL | Время `DD.MM.YYYY HH:MM:SS` |
| type | TEXT NOT NULL | Тип метрики (см. ниже) |
| value | REAL | Основное значение |
| unit | TEXT | Единица измерения |
| systolic | INTEGER | Систолическое (только для bp) |
| diastolic | INTEGER | Диастолическое (только для bp) |
| note | TEXT | Заметка |
| source | TEXT | Источник |

**Допустимые `type`:** `weight`, `glucose`, `pulse`, `waist`, `temp`, `water`, `sleep`, `steps`, `active_cal`, `bp`

Для `bp` (давление): value=NULL, используй systolic + diastolic.

```sql
-- Метрики за день
SELECT * FROM metrics WHERE type='glucose' AND date='YYYY-MM-DD' ORDER BY ts DESC;

-- Давление за период
SELECT * FROM metrics WHERE type='bp' AND date BETWEEN 'YYYY-MM-DD' AND 'YYYY-MM-DD' ORDER BY date, ts;
```

## Таблица `profile`

Профиль пользователя (одна запись, id=1).

| Поле | Тип | Описание |
|------|-----|----------|
| id | INTEGER PK CHECK (id=1) | Всегда 1 |
| name | TEXT | Имя |
| dob | TEXT | Дата рождения |
| height_cm | INTEGER | Рост |
| gender | TEXT | Пол |
| daily_target_kcal | INTEGER | Калории в день |
| target_protein | REAL | Целевой белок (г) |
| target_fat | REAL | Целевой жир (г) |
| target_carbs | REAL | Целевые углеводы (г) |
| snils | TEXT | СНИЛС |
| oms | TEXT | Полис ОМС |

## Таблицы анализов

**`lab_results`** — результаты анализов (числовые):
```sql
CREATE TABLE lab_results (
    id INTEGER PK AUTOINCREMENT, date TEXT NOT NULL, lab_name TEXT,
    document_id INTEGER REFERENCES documents(id),
    test_type TEXT NOT NULL, parameter TEXT NOT NULL, value REAL, unit TEXT,
    ref_min REAL, ref_max REAL, is_critical BOOLEAN DEFAULT 0
);
```
`document_id` — FK → `documents.id`. Связывает результат с исходным файлом (PDF/скан). При импорте из конкретного документа заполнять обязательно. Может быть NULL для ручного ввода.

**`lab_parser_cache`** — кэш распознавания: `(file_hash, lab_name, raw_content, parsed_json, updated_at)`.

**`documents`** — загруженные файлы:
```sql
CREATE TABLE documents (
    id INTEGER PK AUTOINCREMENT, path TEXT NOT NULL UNIQUE,
    lab_name TEXT, report_date TEXT, uploaded_at TEXT DEFAULT CURRENT_TIMESTAMP
);
```

**`doctor_notes`** — заключения врачей:
```sql
CREATE TABLE doctor_notes (
    id INTEGER PK AUTOINCREMENT, date TEXT NOT NULL,
    doctor_name TEXT, specialization TEXT,
    conclusion TEXT NOT NULL, document_id INTEGER REFERENCES documents(id)
);
```

## Формат дат — ВАЖНО

- `date` в формате `YYYY-MM-DD` — сортируется корректно
- `ts` в формате `DD.MM.YYYY HH:MM:SS` — **НЕ сортируется** лексикографически
- Для сортировки: `ORDER BY date, ts` или парсить в Python

## Кодирование

Все текстовые поля в UTF-8. Не использовать ASCII-encoding.
