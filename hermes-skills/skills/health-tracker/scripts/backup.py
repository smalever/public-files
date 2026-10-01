#!/opt/data/home/.venv/bin/python
"""
Backup health.db to timestamped file.
"""
import shutil
import os
from datetime import datetime

DB_PATH = '/opt/data/personal/health/health.db'
BACKUP_DIR = '/opt/data/personal/health/backups'

def backup():
    if not os.path.exists(BACKUP_DIR):
        os.makedirs(BACKUP_DIR)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_path = os.path.join(BACKUP_DIR, f'health_{timestamp}.db')
    shutil.copy2(DB_PATH, backup_path)
    print(f"Backup created: {backup_path}")

if __name__ == '__main__':
    backup()
