"""Bounded raw1.1 SQLite snapshot backup; fresh destinations only, no activation."""

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import tempfile
import time

from aegis.archive import COLUMNS
from aegis.raw_history import RawQueryError, _batch, _constant, _object, _schema


FORMAT = 'aegis-archive-backup-1'
MAX_DATABASE_BYTES = 256 * 1024 * 1024
MAX_RAW_ROWS = 100_000
MAX_CURSORS = 4096
MAX_GAPS = 100_000
MAX_PAYLOAD_BYTES = 256 * 1024
MAX_TOTAL_PAYLOAD_BYTES = 128 * 1024 * 1024
MAX_MANIFEST_BYTES = 64 * 1024
MAX_SECONDS = 30.0
MAX_SQL_STEPS = 20_000_000
SUFFIXES = ('', '-wal', '-shm', '-journal')
HEX = re.compile(r'[0-9a-f]{64}\Z')


class BackupError(Exception):
    def __init__(self, code, *, published=False):
        super().__init__(code)
        self.code, self.published = code, published


class _Budget:
    def __init__(self):
        self.deadline = time.monotonic() + MAX_SECONDS
        self.steps = 0

    def check(self):
        if time.monotonic() >= self.deadline or self.steps >= MAX_SQL_STEPS:
            raise BackupError('RESOURCE_LIMIT')

    def progress(self):
        self.steps += 1000
        self.check()
        return 0


def _encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=False, allow_nan=False).encode('utf-8')


def _family(path):
    return {Path(str(path) + suffix) for suffix in SUFFIXES}


def _source(path):
    path = Path(path).absolute()
    if path.is_symlink() or not path.is_file():
        raise BackupError('INVALID_SOURCE')
    return path.resolve(strict=True)


def _destination(source, path):
    path = Path(path).absolute()
    parent = path.parent.resolve(strict=True)
    path = parent / path.name
    if not parent.is_dir() or _family(source) & _family(path):
        raise BackupError('UNSAFE_DESTINATION')
    _absent(path)
    return path


def _absent(path):
    if any(os.path.lexists(p) for p in _family(path)):
        raise BackupError('DESTINATION_EXISTS')


@contextmanager
def _reader(path, budget):
    connection = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True,
                                 timeout=.2, isolation_level=None)
    try:
        connection.set_progress_handler(budget.progress, 1000)
        connection.execute('PRAGMA query_only=ON')
        connection.execute('PRAGMA trusted_schema=OFF')
        connection.execute('BEGIN')
        connection.execute('SELECT count(*) FROM sqlite_master').fetchone()
        yield connection
    finally:
        connection.close()


def _inspect(connection, budget):
    budget.check()
    size = connection.execute('PRAGMA page_size').fetchone()[0]
    pages = connection.execute('PRAGMA page_count').fetchone()[0]
    if size * pages > MAX_DATABASE_BYTES:
        raise BackupError('RESOURCE_LIMIT')
    _schema(connection)
    schema = connection.execute(
        'SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name').fetchall()
    if any(row[0] not in ('table', 'index') for row in schema):
        raise BackupError('UNSUPPORTED_SCHEMA')
    if connection.execute('PRAGMA integrity_check(1)').fetchall() != [('ok',)]:
        raise BackupError('ARCHIVE_INVALID')
    limits = {'raw_batches': MAX_RAW_ROWS, 'ingest_cursors': MAX_CURSORS, 'ingest_gaps': MAX_GAPS}
    counts = {}
    for table, limit in limits.items():
        count = connection.execute(f'SELECT count(*) FROM {table}').fetchone()[0]
        if count > limit:
            raise BackupError('RESOURCE_LIMIT')
        counts[table] = count
    max_payload, total_payload = connection.execute(
        'SELECT coalesce(max(length(CAST(payload_json AS BLOB))),0),'
        'coalesce(sum(length(CAST(payload_json AS BLOB))),0) FROM raw_batches').fetchone()
    if max_payload > MAX_PAYLOAD_BYTES or total_payload > MAX_TOTAL_PAYLOAD_BYTES:
        raise BackupError('RESOURCE_LIMIT')
    hashes = {}
    for table, columns in COLUMNS.items():
        digest = hashlib.sha256()
        order = ','.join(columns[:4] if table == 'ingest_gaps' else columns[:3])
        for row in connection.execute(f'SELECT * FROM {table} ORDER BY {order}'):
            budget.check()
            if table == 'raw_batches':
                _batch(row, row[0], row[1])
            elif table == 'ingest_cursors':
                device, run, sequence = row
                if (not isinstance(device, str) or not isinstance(run, str)
                        or type(sequence) is not int or sequence < 0):
                    raise BackupError('CURSOR_INVALID')
                maximum = connection.execute('SELECT max(sequence) FROM raw_batches '
                    'WHERE device_id=? AND run_id=?', (device, run)).fetchone()[0]
                if maximum != sequence:
                    raise BackupError('CURSOR_INVALID')
            else:
                device, run, first, last, reason = row
                if (not isinstance(device, str) or not isinstance(run, str)
                        or type(first) is not int or type(last) is not int
                        or not 0 <= first <= last <= 2**63-1 or reason != 'REPLAY_OVERFLOW'):
                    raise BackupError('GAP_INVALID')
                maximum = connection.execute('SELECT max(sequence) FROM raw_batches '
                    'WHERE device_id=? AND run_id=?', (device, run)).fetchone()[0]
                overlaps = connection.execute('SELECT 1 FROM raw_batches WHERE device_id=? '
                    'AND run_id=? AND sequence BETWEEN ? AND ? LIMIT 1', (device, run, first, last)).fetchone()
                if maximum is None or last >= maximum or overlaps:
                    raise BackupError('GAP_INVALID')
            digest.update(_encoded(row) + b'\n')
        hashes[table] = {'rows': counts[table], 'sha256': digest.hexdigest()}
    if connection.execute('SELECT 1 FROM raw_batches b LEFT JOIN ingest_cursors c '
        'ON b.device_id=c.device_id WHERE c.device_id IS NULL LIMIT 1').fetchone():
        raise BackupError('CURSOR_INVALID')
    budget.check()
    return {'schemaVersion': 1, 'schemaSha256': hashlib.sha256(_encoded(schema)).hexdigest(),
            'tables': hashes, 'coverage': 'NOT_ASSERTED'}


def _copy(source, destination, budget):
    def progress(_status, _remaining, _total):
        budget.check()
    source.backup(destination, pages=64, progress=progress, sleep=.01)
    budget.check()


def _file_sha(path, budget):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        while chunk := stream.read(64 * 1024):
            budget.check()
            digest.update(chunk)
    return digest.hexdigest()


def _sync_file(path):
    with path.open('rb') as stream:
        os.fsync(stream.fileno())


def _sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _reference(value):
    # Freeze one bounded reference; no later reads of caller-owned mutable data.
    if len(_encoded(value)) > MAX_MANIFEST_BYTES:
        raise BackupError('MANIFEST_INVALID')
    value = json.loads(_encoded(value))
    if (not isinstance(value, dict) or set(value) != {'format', 'artifactSha256', 'snapshot'}
            or value['format'] != FORMAT or not isinstance(value['artifactSha256'], str)
            or not HEX.fullmatch(value['artifactSha256'])):
        raise BackupError('MANIFEST_INVALID')
    snap = value['snapshot']
    if (not isinstance(snap, dict) or set(snap) != {'schemaVersion','schemaSha256','tables','coverage'}
            or type(snap['schemaVersion']) is not int or snap['schemaVersion'] != 1
            or snap['coverage'] != 'NOT_ASSERTED' or not isinstance(snap['schemaSha256'], str)
            or not HEX.fullmatch(snap['schemaSha256'])
            or not isinstance(snap['tables'], dict) or set(snap['tables']) != set(COLUMNS)):
        raise BackupError('MANIFEST_INVALID')
    for row in snap['tables'].values():
        if (not isinstance(row, dict) or set(row) != {'rows','sha256'}
                or type(row['rows']) is not int or row['rows'] < 0
                or not isinstance(row['sha256'], str) or not HEX.fullmatch(row['sha256'])):
            raise BackupError('MANIFEST_INVALID')
    return value


def _standalone(path):
    if path.stat().st_size > MAX_DATABASE_BYTES or any(
            os.path.lexists(Path(str(path)+suffix)) for suffix in SUFFIXES[1:]):
        raise BackupError('ARTIFACT_NOT_STANDALONE')


def _verify(connection, path, reference, budget):
    _standalone(path)
    if connection.execute('PRAGMA journal_mode').fetchone()[0] != 'delete':
        raise BackupError('ARTIFACT_NOT_STANDALONE')
    if _file_sha(path, budget) != reference['artifactSha256']:
        raise BackupError('MANIFEST_MISMATCH')
    snapshot = _inspect(connection, budget)
    if snapshot != reference['snapshot']:
        raise BackupError('MANIFEST_MISMATCH')
    return snapshot


def _create(source, destination, reference=None, *, require_reference=False):
    budget = _Budget()
    published = False
    directory = stage = None
    try:
        if os.name != 'posix' or not hasattr(os, 'O_DIRECTORY'):
            raise BackupError('PLATFORM_UNVERIFIED')
        if require_reference:
            reference = _reference(reference)
        source = _source(source)
        destination = _destination(source, destination)
        if reference is not None:
            reference = _reference(reference)
            _standalone(source)
        with _reader(source, budget) as reader:
            expected = (_verify(reader, source, reference, budget) if reference is not None
                        else _inspect(reader, budget))
            directory = Path(tempfile.mkdtemp(prefix='.aegis-backup-', dir=destination.parent))
            stage = directory / 'archive.sqlite'
            target = sqlite3.connect(stage, timeout=.2, isolation_level=None)
            try:
                os.chmod(stage, 0o600)
                target.execute('PRAGMA synchronous=FULL')
                _copy(reader, target, budget)
                if target.execute('PRAGMA journal_mode=DELETE').fetchone()[0] != 'delete':
                    raise BackupError('ARTIFACT_NOT_STANDALONE')
            finally:
                target.close()
            with _reader(stage, budget) as copied:
                if _inspect(copied, budget) != expected:
                    raise BackupError('SNAPSHOT_MISMATCH')
            _standalone(stage)
            if reference is not None:
                # A restore input must remain the exact referenced quiescent artifact.
                _standalone(source)
                if _file_sha(source, budget) != reference['artifactSha256']:
                    raise BackupError('MANIFEST_MISMATCH')
        manifest = {'format': FORMAT, 'artifactSha256': _file_sha(stage, budget), 'snapshot': expected}
        _sync_file(stage)
        budget.check()
        _absent(destination)
        os.link(stage, destination)  # Atomic no-replace; never use replace/rename-overwrite.
        published = True
        stage.unlink()  # Remove only our own alias, preventing divergent SQLite WAL names.
        directory.rmdir()
        directory = stage = None
        _sync_directory(destination.parent)
        return manifest
    except BackupError:
        raise
    except (OSError, sqlite3.Error, RawQueryError, ValueError, TypeError, OverflowError,
            RecursionError, KeyError):
        raise BackupError('PUBLISHED_DURABILITY_UNKNOWN' if published else 'BACKUP_FAILED',
                          published=published) from None
    finally:
        if directory is not None:
            # Only names in the private directory created by this invocation are eligible.
            # Never remove source, destination, user sidecars, or completed backups.
            try:
                for name in SUFFIXES:
                    candidate = directory / ('archive.sqlite' + name)
                    candidate.unlink(missing_ok=True)
                directory.rmdir()
            except OSError:
                raise BackupError('PUBLISHED_CLEANUP_REQUIRED' if published else 'STAGING_CLEANUP_REQUIRED',
                                  published=published) from None


def backup_archive(source, destination):
    return _create(source, destination)


def restore_archive(source, destination, expected_manifest):
    return _create(source, destination, expected_manifest, require_reference=True)


def verify_archive(source, expected_manifest):
    try:
        budget = _Budget()
        reference = _reference(expected_manifest)
        source = _source(source)
        _standalone(source)
        with _reader(source, budget) as connection:
            _verify(connection, source, reference, budget)
            _standalone(source)
            if _file_sha(source, budget) != reference['artifactSha256']:
                raise BackupError('MANIFEST_MISMATCH')
        return {'status': 'VERIFIED', 'manifest': reference}
    except BackupError:
        raise
    except (OSError, sqlite3.Error, RawQueryError, ValueError, TypeError, OverflowError,
            RecursionError, KeyError):
        raise BackupError('VERIFICATION_FAILED') from None


def _load_manifest(path):
    with Path(path).open('rb') as stream:
        raw = stream.read(MAX_MANIFEST_BYTES + 1)
    if len(raw) > MAX_MANIFEST_BYTES:
        raise BackupError('MANIFEST_INVALID')
    return _reference(json.loads(raw, object_pairs_hook=_object, parse_constant=_constant))


def _discard_failed_stdout():
    # A failed buffered flush must not be retried at interpreter shutdown.
    # Synthetic/library streams may not expose a file descriptor.
    try:
        output_fd = sys.stdout.fileno()
        sink = os.open(os.devnull, os.O_WRONLY)
        try:
            os.dup2(sink, output_fd)
        finally:
            os.close(sink)
    except (AttributeError, OSError, ValueError):
        pass


def main(argv=None):
    class Parser(argparse.ArgumentParser):
        def error(self, _message):
            raise BackupError('INVALID_ARGUMENTS')

    parser = Parser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('backup','verify','restore'):
        command = commands.add_parser(name)
        command.add_argument('--source', required=True)
        if name != 'verify':
            command.add_argument('--destination', required=True)
        if name != 'backup':
            command.add_argument('--manifest', required=True)
    published = False
    reporting = False
    try:
        args = parser.parse_args(argv)
        if args.command == 'backup':
            result = backup_archive(args.source, args.destination)
            published = True
        elif args.command == 'verify':
            result = verify_archive(args.source, _load_manifest(args.manifest))
        else:
            result = restore_archive(args.source, args.destination, _load_manifest(args.manifest))
            published = True
        reporting = True
        print(_encoded(result).decode('utf-8'))
        sys.stdout.flush()
        return 0
    except (BackupError, OSError, ValueError, TypeError, OverflowError, RecursionError):
        error = sys.exception()
        if reporting and isinstance(error, OSError):
            _discard_failed_stdout()
        published = published or (isinstance(error, BackupError) and error.published)
        print(json.dumps({'error': error.code if isinstance(error, BackupError) else (
                          'PUBLISHED_REPORT_FAILED' if published else 'INVALID_INPUT'),
                          'publication': 'PUBLISHED_DURABILITY_UNKNOWN' if published else 'NOT_PUBLISHED'}),
              file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
