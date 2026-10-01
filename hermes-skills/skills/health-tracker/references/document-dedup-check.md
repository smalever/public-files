# Проверка дубля медицинского PDF

Короткий рабочий алгоритм для случая, когда пользователь прислал PDF и хочет понять, уже есть ли он в базе.

## Что проверять

1. `documents.path` — совпадение по полному пути.
2. Фрагменты имени файла / даты / номера протокола в `documents.path`.
3. Если точного совпадения нет — сравнить **содержимое PDF**.

## Практика

- Извлекать текст всех страниц PDF.
- Нормализовать пробелы:
  - заменить `\s+` на один пробел
  - `strip()`
- Сравнивать:
  - полный нормализованный текст
  - SHA-256 от нормализованного текста

Если hash и текст совпадают — это тот же документ, даже если имя файла другое.

## Когда считать «не тот же»

- Текст отличается хотя бы в одном существенном фрагменте.
- Это другая дата консультации/другой врач/другая клиника.

## Полезный SQL

```sql
SELECT id, report_date, lab_name, path, uploaded_at
FROM documents
WHERE path LIKE '%Протокол%' OR path LIKE '%consultation%' OR path LIKE '%protocol%'
ORDER BY id DESC;
```

## Полезная проверка текста

```python
import fitz, re, hashlib

def normalized_pdf_text(path: str) -> str:
    doc = fitz.open(path)
    text = "\n".join(page.get_text('text') for page in doc)
    return re.sub(r'\s+', ' ', text).strip()

text = normalized_pdf_text(pdf_path)
print(hashlib.sha256(text.encode()).hexdigest())
```

## Сигнал для ответа пользователю

- `path` не найден, но hash/текст совпал с уже сохранённым документом → сказать, что документ уже есть в базе.
- Совпадения по path нет, hash отличается → это новая запись.
