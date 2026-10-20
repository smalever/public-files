#!/usr/bin/env python3
"""🍽 12:00 — бриф «до обеда». Логика — health_reminder_premeal.py."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import health_reminder_premeal

if __name__ == "__main__":
    health_reminder_premeal.main("lunch")
