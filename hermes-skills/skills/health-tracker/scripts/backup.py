#!/opt/data/home/.venv/bin/python
"""
DEPRECATED / DISABLED: Raw SQLite file copy is unsafe for live databases.
"""
import sys

def backup():
    sys.stderr.write(
        "ERROR: Direct execution of backup.py is disabled (fail-closed).\n"
        "Automated live-database copy via shutil.copy is unsafe and prohibited.\n"
        "Database backups and snapshots must be performed by the host administrator\n"
        "using the verified unified backup-system or SQLite backup API.\n"
    )
    sys.exit(1)

if __name__ == '__main__':
    backup()
