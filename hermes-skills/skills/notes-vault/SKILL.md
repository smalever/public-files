---
name: notes-vault
description: "Use when working in the owner's /notes vault: structure, frontmatter, cleanup."
version: 2.0.0
author: Viktor Filatov (smalever), Hermes Agent
license: CC-BY-4.0
metadata:
  hermes:
    tags: [obsidian, notes, vault, conventions]
    related_skills: [obsidian, ideas-inbox, health-tracker, bot-help]
---

# Vault владельца (`/notes`)

Механика чтения/поиска/правки заметок — встроенный навык `obsidian` (vault = `/notes`, `OBSIDIAN_VAULT_PATH` в `.env`). Здесь — только соглашения этого vault.

## Структура

- `Главная.md` — корневой индекс. Каждый раздел имеет свой индекс `<Раздел>/<Раздел>.md` (`type: index`).
- Состав разделов — по факту vault (`Главная.md`), не по памяти. Фиксированные разделы, которые читают скрипты: `Моё здоровье/` (пути и имена заметок не переименовывать), `Идеи/` (навык `ideas-inbox`), `Шаблоны/`.
- `Справка — что я умею.md` — справка бота для владельца (навык `bot-help`).
- `.kilo/`, `.obsidian/` — служебные, не трогать и не индексировать.

## Frontmatter

Каждая заметка начинается с YAML. Порядок и обязательность полей:

```yaml
type: guide                    # обязательно
status: active                 # обязательно
created: 2026-09-27T09:12:00   # обязательно, никогда не меняется
updated: 2026-09-27T09:12:00   # обязательно, при КАЖДОЙ правке содержимого — текущее время
verified: 2026-09-27T09:12:00  # только после фактической проверки в системе
tags: [hermes]                 # необязательно, только сквозные темы; пустой `tags: []` не ставить
aliases / sensitivity / project / handoff   # необязательно
# далее специфические поля заметки (owner, telegram_bot, deployed, period, medications, targets, …)
```

- Даты — `YYYY-MM-DDTHH:mm:ss`, московское время, без кавычек и часового пояса (только день → `T00:00:00`).
- Новая заметка: `created` = `updated` = текущее время. Правка: обновить только `updated`.
- Устаревшие имена не использовать: `last_verified` → `verified`, `updated_at`/`last_updated` → `updated`.
- `type`: `index | guide | system | reference | tracker | project | plan | spec | architecture | research | idea | note | schedule | result | log | presentation`. `tracker` — заметки-данные, которые ведут скрипты (`Схема препаратов`, `Цели и мониторинг`, `Фокус недели`, `Динамика анализов`).
- `status`: `draft | planned | in-progress | testing | review | active | paused | implemented | rejected | someday | deprecated | archived`.
- Полная схема — `/notes/AGENTS.md`.

Шаблоны — папка `Шаблоны/` (минимум `Идея.md`); для здоровья — `Моё здоровье/Исследования/Шаблон исследования метода.md`, если есть. Состав шаблонов проверять по факту. В шаблонах `{{date:...}}` — плейсхолдер Obsidian: при создании заметки по шаблону агентом подставить текущее время.

## Правила

- Новая заметка → сразу строка `- [[Название]] — суть` в индексе своего раздела. Сирот не оставлять.
- Связи — только wikilinks `[[...]]`.
- В `Моё здоровье/*.md` YAML-блоки `medications:`, `targets:` и `monitoring:` парсятся скриптами: менять аккуратно, после правки прогнать `/opt/data/home/.venv/bin/python /opt/data/scripts/health_sync.py` и убедиться, что нет `sync_warnings`.
- Секреты в заметках допустимы (vault приватный), но не выводить их в чат без необходимости.
- Наведение порядка в заметке — `references/note-cleanup.md`.
