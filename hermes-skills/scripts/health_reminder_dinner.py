#!/usr/bin/env python3
"""🌆 18:00 — бриф «до ужина». Логика — health_reminder_premeal.py."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import health_reminder_premeal

if __name__ == "__main__":
    health_reminder_premeal.main("evening")
