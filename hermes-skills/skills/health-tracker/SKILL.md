---
name: health-tracker
description: "Use when the owner logs or asks about health: metrics, meds, labs, doctor docs."
version: 3.0.0
author: Viktor Filatov (smalever), Hermes Agent
license: CC-BY-4.0
metadata:
  hermes:
    tags: [health, metrics, medications, labs, sqlite, obsidian]
    related_skills: [nutrition, health-reminder, medical-import, obsidian]
---

# Здоровье: метрики, лекарства, анализы

Хаб по здоровью. Питание — `nutrition`, cron-брифы — `health-reminder`, импорт медицинских PDF/фото — `medical-import`.

## Где что лежит

```bash
PY=/opt/data/home/.venv/bin/python
CLI=/opt/data/skills-custom/health/health-tracker/scripts/health.py
DB=/opt/data/personal/health/health.db          # данные, НЕ внутри скилла
DOCS=/opt/data/personal/health/documents        # originals документов
```

Obsidian (`/notes/Моё здоровье/`) — источник правды для схемы и целей; SQLite — реплика/fallback:
- `Схема препаратов.md` — frontmatter `medications:` (слоты `morning`, `lunch`, `wednesday_morning`, `evening`, `as_needed`);
- `Цели и мониторинг.md` — frontmatter `targets:` (ккал/БЖУ) и необязательный `monitoring:` (личные коридоры веса/сахара/давления, вода, замеры, панель анализов — читает `/opt/data/scripts/health_monitoring.py`);
- `Расписание и план обследований.md`, `Динамика анализов.md`, `Фокус недели.md`, `Исследования/`.

`/opt/data/scripts/health_sync.py` реплицирует Obsidian → `health.db` (`medications`, `profile`) и при ошибке разметки отдаёт `sync_warnings`. Работа с заметками — по навыку `obsidian`.

Схема БД: `references/db-schema.md`.

## Метрики

Записывать только через CLI (время MSK ставится само):

```bash
$PY $CLI metric weight --value 82.4               # weight|glucose|pulse|waist|temp|water|sleep|steps|active_cal
$PY $CLI metric bp --systolic 128 --diastolic 82   # давление: только systolic/diastolic, без --value
$PY $CLI list --date YYYY-MM-DD
```

«Сахар» → `type='glucose'`. Единицы по умолчанию проставляет CLI.

Исправление: показать строки `SELECT id, date, ts, type, value, unit, systolic, diastolic FROM metrics ...`, получить подтверждение, `UPDATE` только по точным `id`, проверить результат.

## Лекарства

Схему меняем в Obsidian (`Схема препаратов.md`), не в БД.

Отметка приёма — **по каждому препарату**:

```bash
$PY $CLI take-meds                                   # «принял всё / таблетки выпил» — все препараты текущего слота
$PY $CLI take-meds --names "Магний"                 # «принял магний» — только названные (через запятую)
$PY $CLI take-meds --slot morning [--date YYYY-MM-DD] # явный слот/дата
$PY $CLI medication --name "Название"              # разовый приём препарата вне схемы (по потребности)
```

- Владелец назвал конкретные препараты → только `--names`, без «всех». Названия можно разговорные («магний», «омегу») — CLI сопоставит. Без `--slot` ищет в текущем и более ранних слотах (сначала непринятые).
- «Принял всё» (без `--names` и `--slot`) работает по тем же окнам, что и напоминания (ниже): отмечает всё непринятое в текущем окне; пропущенное раньше не трогает. Пустое окно — не ошибка, в ответе подсказка про запись по названию.
- Препарат, названный владельцем (`--names`), записывается в любое время суток, без ограничений окна, в своём слоте из схемы (утренний препарат, отмеченный вечером, → слот morning). Позднюю дозу владелец решает сам после консультации с врачом; бот не спорит и не отказывает. Препарат, которого нет в схеме на сегодня (например, еженедельный `wednesday_morning` не в среду), `take-meds --names` не найдёт (`meds_not_found`). Для разового приёма вне схемы есть `medication --name`, но при неточном названии он ждёт интерактивный ввод (`input()`): в боте запускать только с точным названием из справочника `medications`.
- **Окна напоминаний при записи еды** (`reminder_slots()` в `medication_reminder_helper.py`): до 12:00 — утренние; 12:00–16:00 — утренние + дневные вместе; 16:00–18:00 — только дневные; после 18:00 — дневные + вечерние вместе (до конца суток). Утренние после 16:00 считаются пропущенными (в «Итогах дня» и статистике видны), на вечерние не переносятся и дозу не удваиваем. Непринятые дневные дописываются к вечерним после 18:00. Слот зависит только от времени, не от числа приёмов пищи за день. Исключений по названиям препаратов нет.
- Отвечать по JSON CLI: `marked` — отмечено сейчас; `already` — уже было; `unknown` — не найдено (уточнить у владельца); `remaining` — что ещё не принято в слоте. Использовать `message`.
- Пишется в `med_intake_log` (поштучно, без дублей) и `med_log`; когда слот принят целиком — ещё и в `medication_slot_log` (старые записи слота считаются «все приняты»).
- У каждого препарата в схеме поле `food`: `before` / `with` / `after` / `any`. Новый препарат добавлять с `food`, иначе признак угадывается по `note`. `before` напоминают брифы 07:00 / 12:00 / 18:00, остальные — запись еды (`nutrition.py meal`). Логика — `/opt/data/scripts/medication_reminder_helper.py`.

Pitfalls:
- столбец `active`, не `is_active`;
- `when_text='morning_evening'` попадает в оба слота;
- кириллицу сравнивать через Python `casefold()`, не SQLite `LOWER()`.

## Справочник препаратов и опечатки

Речь и автозамена искажают названия («магнезия», «омегу», бренд вместо действующего вещества). Чтобы не угадывать, берём реальные названия из схемы (Obsidian `Схема препаратов.md`) через скрипты, только чтение:

```bash
$PY $CLI meds-catalog --text                    # все препараты + когда принимать (утро/утро по средам/день/вечер + время/по потребности)
$PY $CLI meds-catalog                           # то же JSON (slot, time, food, dosage, note, condition)
$PY $CLI meds-resolve --query "магнезия"      # до 3 кандидатов {name, score, confident, schedule}
```

Правила:
- `take-meds --names` записывает только при надёжном совпадении с сегодняшними препаратами (`med_matches`). Если вернулся `meds_not_found`, в ответе есть `suggestions` {запрос: [{name, score, confident}]}. Сама команда по неточному названию ничего не пишет.
- `confident: true` и ясный лидер → повторить `take-meds --names "<точное название из name>"` и в ответе кратко сказать, как понял («Понял «магнезия» как Магний B6»).
- несколько кандидатов без лидера, или `confident: false`, или пусто → не записывать, переспросить владельца со списком вариантов. Дозу и время приёма не выдумывать, брать из справочника.
- Вопросы «когда принимать X», «что пить вечером» → отвечать по `meds-catalog`, не по памяти.
- Справочник читает актуальную схему из Obsidian (включая `as_needed` — по потребности). Неактивные препараты из SQLite в нём не появляются.
- Схема пустая (новый владелец) — справочник пуст, напоминаний нет; предложить заполнить `Схема препаратов.md`, если владелец упоминает лекарства.

## Анализы и документы

- Импорт нового PDF/фото — только по навыку `medical-import`.
- Не дубль ли документ: `references/document-dedup-check.md`.
- Ошибки OCR (HbA1c vs гемоглобин, единицы, рукопись): `references/vision-ocr-pitfalls.md`.
- Целостность связей после импорта: `$PY scripts/check_integrity.py` (read-only).

## Как отвечать на вопросы

Читать `health.db` read-only, выводить кратким списком, SQL не показывать:
- «анализы» → `lab_results` по дате убыв., критические (`is_critical=1`) с ⚠️;
- «что сказал врач» → `doctor_notes` (врач, дата, заключение);
- «метрики» → `metrics ORDER BY date DESC, ts DESC` (`ts` в формате `DD.MM.YYYY` — по нему одному не сортировать);
- «документы» / «что за документ» → `documents` + связанные `lab_results`/`doctor_notes` по `document_id`.

## Графики

```bash
$PY /opt/data/skills-custom/health/health-tracker/scripts/plot_health.py --metrics pressure,pulse,sugar,weight --days 62 --output /opt/data/cache/charts/health.png
```

`--metrics` из pressure,pulse,sugar,weight; `--days` (9999 — всё время). Метрики с разными единицами — отдельными подграфиками. Отправлять `MEDIA:/path.png` в тексте ответа.

## Исследование новых препаратов/добавок

1. Только доказательные источники (PubMed, ClinicalTrials, Cochrane, FDA/EMA, Vidal, ГРЛС) со ссылками.
2. Показатели и текущую схему брать из `Цели и мониторинг.md` и `Схема препаратов.md`, не из памяти.
3. Проверять взаимодействия с текущими препаратами.
4. Итог — заметка в `/notes/Моё здоровье/Исследования/` по шаблону `Шаблон исследования метода.md` со статусом (Изучается / Оценка совместимости / Тестируется / Внедрено / Отклонено).

Назначений не менять и эффект не обещать — это информация для обсуждения с врачом.

## Безопасность данных

- Любой DELETE/UPDATE медицинских записей: показать точные строки → явное подтверждение → бэкап (`$PY scripts/backup.py`, копия в `/opt/data/personal/health/backups`) → изменение → read-back.
- Скрипты без `argparse` не запускать «для проверки» — сначала читать код; безопасная проверка — `python -m py_compile`.
- Python только `/opt/data/home/.venv/bin/python` (см. `nutrition/references/python-runtime.md`).
