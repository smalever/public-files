---
name: medical-import
description: "Use when the owner sends a medical PDF or photo to import into health.db."
version: 4.0.0
author: Viktor Filatov (smalever), Hermes Agent
license: CC-BY-4.0
metadata:
  hermes:
    tags: [medical, vision, sqlite, confirmation]
    related_skills: [health-tracker]
---

# Импорт медицинского документа

Единственный допустимый путь записи анализов и заключений в `health.db`. Контекст БД и Obsidian — в `health-tracker`.

## Workflow

1. **Дубль?** Проверь по `health-tracker/references/document-dedup-check.md`.
2. **Распознай.** PDF → PNG (PyMuPDF, dpi 200–300, все страницы) → `vision_analyze`. Типовые ошибки OCR — `health-tracker/references/vision-ocr-pitfalls.md`; рукопись не доверять Vision — нарезать и показать владельцу.
3. **Черновик.** Покажи: дата, лаборатория/врач, все значения с единицами и референсами либо полный текст заключения; отдельно отметь неуверенные поля. Ничего не угадывай.
4. **Подтверждение №1** черновика → сохрани JSON в `/workspace/draft.json`.
5. **Preview:**

```bash
PY=/opt/data/home/.venv/bin/python
CLI=/opt/data/skills-custom/health/health-tracker/scripts/medical_import.py
$PY $CLI lab --source /workspace/file.pdf --metadata-json /workspace/draft.json --dry-run
```

   Для заключения — `doctor` вместо `lab`.
6. **Подтверждение №2** → та же команда с `--confirm`.
7. Успех — только статус `created` или `duplicate`. После `created` проверь read-only document ID и связанные строки.
8. Для анализов синхронизируй Obsidian (если владелец ведёт таблицу `/notes/Моё здоровье/Динамика анализов.md`; нет заметки — скрипт пропускает шаг, данные остаются в `health.db`):

```bash
$PY /opt/data/scripts/sync_lab_results_to_obsidian.py --date <report_date>
```

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
