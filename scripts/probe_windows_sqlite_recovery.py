"""Synthetic subprocess-crash probe; never opens a caller-supplied database.

This probes SQLite process-crash recovery, NOT power-loss durability or Aegis
storage support. Production platform guards remain unchanged.
"""
import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time


def worker(root):
    root = Path(root)
    connection = sqlite3.connect(root / 'synthetic.sqlite', isolation_level=None)
    connection.execute('PRAGMA journal_mode=WAL')
    connection.execute('PRAGMA synchronous=FULL')
    connection.execute('CREATE TABLE evidence(id INTEGER PRIMARY KEY, value TEXT NOT NULL)')
    connection.execute('BEGIN IMMEDIATE')
    connection.execute("INSERT INTO evidence VALUES(1,'committed synthetic record')")
    connection.execute('COMMIT')
    connection.execute('BEGIN IMMEDIATE')
    connection.execute("INSERT INTO evidence VALUES(2,'uncommitted synthetic record')")
    (root / 'ready').write_text('transaction open', encoding='ascii')
    time.sleep(120)


def probe(timeout=20):
    if os.name != 'nt':
        raise RuntimeError('This probe requires Windows')
    with tempfile.TemporaryDirectory(prefix='aegis-synthetic-crash-') as directory:
        root = Path(directory)
        process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()),
                                    '--worker', directory], stdout=subprocess.DEVNULL,
                                   stderr=subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            deadline = time.monotonic() + timeout
            while not (root / 'ready').exists():
                if process.poll() is not None:
                    raise RuntimeError('Synthetic worker exited before open transaction')
                if time.monotonic() >= deadline:
                    raise TimeoutError('Synthetic transaction readiness timeout')
                time.sleep(.02)
            # Terminate only the child process created above, with an open write txn.
            process.kill()
            process.wait(timeout=timeout)
            with closing(sqlite3.connect(root / 'synthetic.sqlite')) as connection:
                integrity = connection.execute('PRAGMA integrity_check').fetchall()
                rows = connection.execute('SELECT id,value FROM evidence ORDER BY id').fetchall()
                if integrity != [('ok',)] or rows != [(1, 'committed synthetic record')]:
                    raise AssertionError('Crash recovery did not preserve exact committed prefix')
                connection.execute("INSERT INTO evidence VALUES(2,'resumed synthetic record')")
                connection.commit()
            with closing(sqlite3.connect(root / 'synthetic.sqlite')) as connection:
                if connection.execute('SELECT COUNT(*) FROM evidence').fetchone() != (2,):
                    raise AssertionError('Post-recovery commit was not retained')
            return {'scope': 'synthetic SQLite WAL process termination only',
                    'committedPrefix': 'PASS', 'uncommittedRollback': 'PASS',
                    'reopenAndResume': 'PASS', 'powerLoss': 'NOT_RUN',
                    'aegisProductionStorage': 'BLOCKED'}
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=timeout)
            if process.stderr:
                process.stderr.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--worker', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        worker(args.worker)
    else:
        print(json.dumps(probe(), ensure_ascii=True))
