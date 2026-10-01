#!/usr/bin/env python3
"""Личные ориентиры мониторинга владельца из заметки «Цели и мониторинг».

Код брифов не содержит личных цифр: коридоры веса, сахара, давления, воды,
панель плановых анализов и список ежедневных замеров берутся из необязательного
YAML-блока `monitoring:` в frontmatter заметки. Отсутствующий ключ — блок брифа
не выводится. Пример блока — в шаблоне заметки и `health-reminder/SKILL.md`.
"""

import os
from pathlib import Path

import yaml

NOTES_DIR = Path(os.environ.get("BOT_NOTES_DIR", "/notes"))
TARGETS_NOTE = NOTES_DIR / "Моё здоровье/Цели и мониторинг.md"

RANGE_KEYS = (
    "protein_range",
    "kcal_range",
    "water_l",
    "glucose_range",
    "weight_corridor",
    "bp_systolic_range",
    "bp_diastolic_range",
)
NUMBER_KEYS = ("weight_target", "weight_start", "lab_interval_days", "bp_systolic_spike")
LIST_KEYS = ("measure", "lab_panel")


def _range(value):
    if isinstance(value, (list, tuple)) and len(value) == 2:
        try:
            lo, hi = float(value[0]), float(value[1])
        except (TypeError, ValueError):
            return None
        if lo <= hi:
            return [lo, hi]
    return None


def _number(value):
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def load_monitoring(note_path=None):
    """Return normalized monitoring settings; unknown/broken values become None/[]."""
    path = Path(note_path) if note_path else TARGETS_NOTE
    raw = {}
    try:
        content = path.read_text(encoding="utf-8")
        if content.startswith("---"):
            frontmatter = yaml.safe_load(content.split("---", 2)[1]) or {}
            raw = frontmatter.get("monitoring") or {}
    except (OSError, ValueError, yaml.YAMLError):
        raw = {}
    if not isinstance(raw, dict):
        raw = {}

    result = {key: _range(raw.get(key)) for key in RANGE_KEYS}
    result.update({key: _number(raw.get(key)) for key in NUMBER_KEYS})
    for key in LIST_KEYS:
        value = raw.get(key) or []
        result[key] = [str(v) for v in value] if isinstance(value, list) else []
    if result["lab_interval_days"] is None:
        result["lab_interval_days"] = 90.0
    return result


def fmt_range(rng, unit="", sep=" – "):
    """[120.0, 140.0] -> '120 – 140 г'; None -> None."""
    if not rng:
        return None

    def _f(x):
        return f"{x:g}"

    text = f"{_f(rng[0])}{sep}{_f(rng[1])}"
    return f"{text} {unit}".strip() if unit else text
