#!/usr/bin/env python3
"""🌆 18:00 — бриф «до ужина». Логика — health_reminder_premeal.py."""
# Run under the scripts venv, not Hermes' interpreter (cron ignores the shebang).
import os as _os, sys as _sys
_VENV = "/opt/data/home/.venv"
if _os.path.realpath(_sys.prefix) != _os.path.realpath(_VENV) and _os.path.exists(_VENV + "/bin/python"):
    for _k in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"):
        _os.environ.pop(_k, None)
    _os.execv(_VENV + "/bin/python", [_VENV + "/bin/python", _os.path.abspath(__file__), *_sys.argv[1:]])

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import health_reminder_premeal

if __name__ == "__main__":
    health_reminder_premeal.main("evening")
