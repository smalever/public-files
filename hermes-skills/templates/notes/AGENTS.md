# Правила vault

- Каждая заметка (кроме этого файла) начинается с YAML: `type`, `status`, `created`, `updated`, далее необязательные `verified`, `tags`, `aliases`, затем поля заметки.
- Даты — `YYYY-MM-DDTHH:mm:ss`, московское время, без кавычек. Новая заметка: `created` = `updated` = сейчас. Правка — только `updated`.
- `type`: index, guide, reference, tracker, project, plan, idea, note, schedule, result, log.
- `status`: draft, planned, in-progress, active, paused, implemented, rejected, someday, archived.
- Новая заметка — сразу строка `- [[Название]] — суть` в индексе раздела.
- `Моё здоровье/`: YAML-блоки `medications:`, `targets:`, `monitoring:` читают скрипты бота — структуру не менять, имена заметок не переименовывать. `Фокус недели.md` перезаписывается скриптом.
