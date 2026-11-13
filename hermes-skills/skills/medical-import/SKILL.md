---
name: medical-import
description: "Use when the owner sends a medical PDF or photo to import."
version: 4.2.0
author: Viktor Filatov (smalever), Hermes Agent
license: CC-BY-4.0
metadata:
  hermes:
    tags: [medical, vision, sqlite, confirmation]
    related_skills: [health-tracker]
---

# Импорт медицинского документа

Единственный допустимый путь записи анализов и заключений в `health.db`. Контекст БД и Obsidian — в `health-tracker`.

Перед первым запуском собственного Python, диагностикой или тестами прочитай файловым инструментом `/opt/data/home/python-runtime.md`. До работы с БД загрузи `health-tracker`; до изменения Obsidian — `notes-vault` и `/notes/AGENTS.md`. Cross-skill references читай файловым инструментом из `/opt/data/skills-custom/health/health-tracker/references/`: `document-dedup-check.md` для шага 1, `vision-ocr-pitfalls.md` только для фотографий.

## Workflow

1. **Дубль?** Проверь по `health-tracker/references/document-dedup-check.md`.
2. **Распознай.** PDF передай целиком через `$PY /opt/data/skills-custom/health/health-tracker/scripts/medical_extract.py lab --source /workspace/file.pdf --source-root /workspace` (где `PY=/opt/data/home/.venv/bin/python`; для заключения `doctor`). Убедись, что `--source` лежит внутри `--source-root` (при необходимости скопируй входящий файл из кэша в `/workspace`). CLI использует native `gemini-3.8-flash-high`, `/v1beta/models/...:generateContent`, `inlineData` исходного PDF, существующий `providers.newapi.api` в `${HERMES_HOME:-/opt/data}/config.yaml` и ключ `NEWAPI_API_KEY`. URL из env не используется; redirects запрещены. HTTP разрешён только для loopback (localhost/127.0.0.1), остальным нужен TLS. Без OCR и PDF → PNG, без доступа к БД. Для фотографии остаётся `vision_analyze` и правила фотографий.
3. **Черновик.** Покажи: дата, лаборатория/врач, все значения с единицами и референсами либо полный текст заключения; отдельно отметь неуверенные поля. Ничего не угадывай. Доверяем извлечению модели: не требуй ручной сверки каждого результата, не используй старую БД как эталон. Если `date_required`, покажи filename и recognized `document_title`, запроси дату у владельца; до получения даты импорт блокирован.
4. Сохрани объект `draft` (не весь ответ CLI) в `/workspace/draft.json`. Это ещё не импорт и не подтверждение.
5. **Preview:**

```bash
PY=/opt/data/home/.venv/bin/python
CLI=/opt/data/skills-custom/health/health-tracker/scripts/medical_import.py
$PY $CLI lab --source /workspace/file.pdf --metadata-json /workspace/draft.json --dry-run
```

   Для заключения — `doctor` вместо `lab`.
6. **Подтверждение черновика.** Покажи черновик и preview владельцу; после его явного одобрения выполни ту же команду с `--confirm`. Это содержательное подтверждение импорта, не повторный вопрос о разрешении записи. Для нового документа бэкап и хостовый снимок не нужны; исправление/замена истории — отдельная процедура из `health-tracker`.
7. Успех — только статус `created` или `duplicate`. Для `created` достаточно receipt, если CLI гарантирует проверку document ID и всех связанных строк после commit; повторные SELECT и запуск общего integrity-check тогда не нужны. Если такой гарантии нет, receipt неполон или результат неизвестен — проверь точный document ID и связанные строки read-only; не повторяй импорт вслепую.
8. Автоматическая синхронизация Obsidian отключена: существующий sync повреждает history и «Фокус недели». Данные остаются в `health.db`; отдельный sync пока disabled. Не запускай его после импорта.

## JSON анализов

```json
{"report_date": "YYYY-MM-DD", "lab_name": "Лаборатория", "source_message_id": "optional",
 "results": [{"test_type": "blood", "parameter": "Показатель", "value": 1.2, "unit": "ед",
              "ref_min": 1.0, "ref_max": 2.0, "is_critical": false}]}
```

## JSON заключения

```json
{"report_date": "YYYY-MM-DD", "doctor_name": "Врач", "specialization": "Специализация",
 "conclusion": "Полный подтверждённый текст", "source_message_id": "optional"}
```

## Ограничения

- Никаких ручных INSERT/UPDATE/DELETE из текста модели — только CLI (валидация, SHA-256, дубли, транзакция, read-back).
- Originals хранит CLI в `/opt/data/personal/health/documents/`.
- Исправление/удаление медицинских записей — отдельный точный scope и подтверждение (см. `health-tracker` → «Безопасность данных»).
