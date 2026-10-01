#!/opt/data/home/.venv/bin/python
"""
Health DB Integrity Check
Проверяет целостность связей между documents, lab_results, doctor_notes
и файлами на диске. Запускать после массового импорта или периодически.

Использование:
    /opt/data/home/.venv/bin/python /opt/data/skills-custom/health/health-tracker/scripts/check_integrity.py
"""

import sqlite3
import os

DB = os.path.expanduser('/opt/data/personal/health/health.db')
DOCS_DIR = os.path.expanduser('/opt/data/personal/health/documents')


def check():
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    ok = True

    # 1. lab_results без document_id
    c.execute('SELECT COUNT(*) FROM lab_results WHERE document_id IS NULL')
    null_lab = c.fetchone()[0]
    if null_lab:
        ok = False
        print(f'problem: lab_results without document_id: {null_lab}')
        c.execute('SELECT id, date, lab_name, parameter FROM lab_results '
                  'WHERE document_id IS NULL ORDER BY date, id LIMIT 10')
        for r in c.fetchall():
            print(f'   id={r[0]} date={r[1]} lab={r[2]} param={r[3]}')
    else:
        print('ok: all lab_results have document_id')

    # 2. doctor_notes без document_id
    c.execute('SELECT COUNT(*) FROM doctor_notes WHERE document_id IS NULL')
    null_notes = c.fetchone()[0]
    if null_notes:
        ok = False
        print(f'problem: doctor_notes without document_id: {null_notes}')
    else:
        print('ok: all doctor_notes have document_id')

    # 3. Documents without files on disk
    c.execute('SELECT id, path FROM documents')
    dead = []
    for row in c.fetchall():
        if not os.path.exists(row[1]):
            dead.append(row)
    if dead:
        ok = False
        print(f'problem: dead files in documents: {len(dead)}')
        for d in dead:
            print(f'   id={d[0]} path={d[1]}')
    else:
        print('ok: all files exist on disk')

    # 4. Files on disk without documents entry
    known_paths = set()
    c.execute('SELECT path FROM documents')
    for row in c.fetchall():
        known_paths.add(row[0])
    missing = []
    for root, dirs, files in os.walk(DOCS_DIR):
        for f in files:
            fp = os.path.join(root, f)
            if fp not in known_paths and not fp.endswith('.png'):
                missing.append(fp)
    if missing:
        ok = False
        print(f'problem: files not in documents: {len(missing)}')
        for m in missing:
            print(f'   {m}')
    else:
        print('ok: all files registered in documents')

    # 5. Orphan references
    c.execute('SELECT DISTINCT lr.document_id FROM lab_results lr '
              'LEFT JOIN documents d ON d.id=lr.document_id '
              'WHERE lr.document_id IS NOT NULL AND d.id IS NULL')
    orphan_lab = c.fetchall()
    if orphan_lab:
        ok = False
        print(f'problem: orphan lab_results.document_id: {[r[0] for r in orphan_lab]}')
    else:
        print('ok: no orphan lab_results references')

    c.execute('SELECT DISTINCT dn.document_id FROM doctor_notes dn '
              'LEFT JOIN documents d ON d.id=dn.document_id '
              'WHERE dn.document_id IS NOT NULL AND d.id IS NULL')
    orphan_notes = c.fetchall()
    if orphan_notes:
        ok = False
        print(f'problem: orphan doctor_notes.document_id: {[r[0] for r in orphan_notes]}')
    else:
        print('ok: no orphan doctor_notes references')

    # 6. Summary
    print()
    print('=== Summary ===')
    for table in ['documents', 'lab_results', 'doctor_notes',
                  'metrics', 'medications', 'med_log']:
        c.execute(f'SELECT COUNT(*) FROM {table}')
        print(f'  {table}: {c.fetchone()[0]}')

    conn.close()
    print()
    if ok:
        print('ok: all checks passed')
    else:
        print('problem: issues found, fix required')
    return ok


if __name__ == '__main__':
    check()
