# Интеграция с cron-заданиями Hermes

Актуальный список джобов, расписание и id — только `hermes cron list`.

## Как устроен запуск
1. Cron вызывает скрипт по имени из `/opt/data/scripts/` (`script: "health_reminder_morning.py"`).
2. Скрипт читает `/opt/data/personal/health/health.db` и `/opt/data/personal/nutrition/nutrition.db`, печатает JSON в stdout.
3. Cron подгружает навык `health-reminder` и передаёт JSON модели; формат сообщения задан в SKILL.md.

Вечерний скрипт гасит запуск модели: последняя строка `{"wakeAgent": false}` — LLM не вызывается, сообщение не доставляется.

## Промпты джоб
Промпт короткий и только ссылается на раздел навыка (единый источник формата). Правка — через CLI, не руками `jobs.json`:

```bash
hermes cron edit <job_id> --prompt "$(cat /opt/data/cache/scratch/prompt.txt)"
hermes cron doctor
```

## Правила
- Скрипты правим в `/opt/data/scripts/` (тесты — `/opt/data/scripts/tests/`).
- Cron игнорирует shebang. У пяти health jobs задан `interpreter: /opt/data/home/.venv/bin/python`; шесть self-reexec блоков удалены. Общая справка: `/opt/data/home/python-runtime.md`.
- Interpreter менять только штатным `hermes cron edit <id> --interpreter ...`, без ручной перезаписи `jobs.json` и без `cron run`. Прямой запуск через чужой Python больше не переключает окружение автоматически.
