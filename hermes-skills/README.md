# hermes-skills

Навыки для персонального Telegram-бота на [Hermes Agent](https://hermes-agent.nousresearch.com/): финансы, здоровье, питание, рецепты, импорт анализов, брифы, идеи, заметки, поиск, справка и обновления.

Список с датами и версиями — [CATALOG.md](CATALOG.md).

## Раскладка в боте

```text
/opt/data/skills-custom/<категория>/<навык>/      навыки (skills.external_dirs + skills.create_dir)
/opt/data/scripts/                               общие скрипты брифов и здоровья
/opt/data/personal/{finance,health,nutrition}/   данные владельца: SQLite, документы, finance-settings.yaml
/opt/data/home/.venv                             Python для скриптов: pyyaml matplotlib numpy pillow pymupdf
/notes                                           Obsidian vault владельца (стартовые заметки — templates/notes)
```

## Установка и обновление

В боте — навык `bot-help`: «что нового?» показывает новые и обновлённые навыки с прошлой проверки; установка — только после согласия владельца, с бэкапом; если владелец менял навык, бот показывает разницу и предлагает объединить правки.

```bash
PY=/opt/data/home/.venv/bin/python
$PY /opt/data/skills-custom/notes/bot-help/scripts/skills_update.py check
$PY /opt/data/skills-custom/notes/bot-help/scripts/skills_update.py install <name> --yes
```

## Данные

В навыках нет личных данных. Счета, цели, личные ориентиры и схема лекарств — у владельца: `finance-settings.yaml`, заметки `Моё здоровье/` (`targets`, `monitoring`, `medications`).

## Лицензия

[CC BY 4.0](../LICENSE). При использовании и переработке укажите автора — **Виктор Филатов** — и ссылку на источник: https://github.com/smalever/public-files
