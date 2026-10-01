# Python runtime для личных скриптов

Скрипты skills (`nutrition`, `health-tracker`, `finance`, `health-reminder`) запускаются
отдельным окружением, которое не зависит от Hermes:

```text
Окружение:   /opt/data/home/.venv            (Python 3.14, uv-managed)
Интерпретатор: /opt/data/home/.local/share/uv/python/
Пакеты:      /opt/data/home/requirements-scripts.txt
Зафиксировано: /opt/data/home/requirements-scripts.lock
```

Окружение Hermes (`/opt/data/installs/...`, `/opt/data/runtime/hermes-agent/.venv`) для скриптов
не использовать и пакеты туда не ставить: Hermes пересобирает его при `hermes update`.

Добавить пакет — дописать в `requirements-scripts.txt`, затем:

```bash
uv pip install --python /opt/data/home/.venv/bin/python -r /opt/data/home/requirements-scripts.txt
uv pip freeze --python /opt/data/home/.venv/bin/python > /opt/data/home/requirements-scripts.lock
```

Пересоздать окружение с нуля:

```bash
export UV_PYTHON_INSTALL_DIR=/opt/data/home/.local/share/uv/python
uv venv --python 3.14 --managed-python --clear /opt/data/home/.venv
uv pip install --python /opt/data/home/.venv/bin/python -r /opt/data/home/requirements-scripts.lock
```

Проверки:

```bash
/opt/data/home/.venv/bin/python -c "import matplotlib, numpy, yaml, PIL, fitz, sqlite3; print(sqlite3.sqlite_version)"
/opt/data/home/.venv/bin/python /opt/data/skills-custom/health/nutrition/scripts/nutrition.py --help
/opt/data/home/.venv/bin/python /opt/data/skills-custom/health/nutrition/scripts/plot_nutrition.py --help
```

Cron-скрипты из `/opt/data/scripts` Hermes запускает своим интерпретатором (shebang игнорируется);
тяжёлые зависимости они получают через этот venv.
