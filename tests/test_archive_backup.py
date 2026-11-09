import asyncio
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest

from test_archive import AT, RUN, snapshot


def api():
    from aegis import archive_backup
    return archive_backup


def seed(path, *, gap=False):
    from aegis.archive import RawArchive
    async def run():
        store = RawArchive(path)
        await store.start()
        await store.append([snapshot(0), snapshot(1, value=None, quality='MISSING')], AT,
                           equipment_id='CMP-01', module_id='PMC-01')
        if gap:
            await store.append([snapshot(4), snapshot(5)], AT, gap=(2, 3))
        await store.close()
    asyncio.run(run())


def rows(path):
    with sqlite3.connect(path) as c:
        return {t: c.execute(f'SELECT * FROM {t} ORDER BY 1,2,3').fetchall()
                for t in ('raw_batches', 'ingest_cursors', 'ingest_gaps')}


def test_roundtrip_preserves_original_text_receipt_cursor_gap_and_no_source_mutation(tmp_path):
    a = api(); source = tmp_path/'raw.db'; seed(source, gap=True)
    before = source.read_bytes(); original = rows(source)
    backup = tmp_path/'backup.db'; restored = tmp_path/'restored.db'
    manifest = a.backup_archive(source, backup)
    assert manifest['artifactSha256'] == hashlib.sha256(backup.read_bytes()).hexdigest()
    assert a.verify_archive(backup, manifest)['status'] == 'VERIFIED'
    restored_manifest = a.restore_archive(backup, restored, manifest)
    assert restored_manifest['snapshot'] == manifest['snapshot']
    assert rows(backup) == rows(restored) == original
    assert source.read_bytes() == before
    assert not list(tmp_path.glob('.aegis-backup-*'))
    with sqlite3.connect(backup) as c:
        assert c.execute('pragma journal_mode').fetchone() == ('delete',)


def test_fixed_wal_snapshot_has_same_raw_cursor_gap_cutoff_and_writer_progresses(tmp_path, monkeypatch):
    a = api(); source = tmp_path/'raw.db'; seed(source)
    writer = sqlite3.connect(source, isolation_level=None)
    writer.execute('pragma journal_mode=wal')
    writer.execute('pragma wal_autocheckpoint=0')
    before = rows(source); original_copy = a._copy
    def commit_later():
        writer.execute('begin immediate')
        for n in (4, 5):
            item = snapshot(n)
            writer.execute('insert into raw_batches values(?,?,?,?,?,?,?,?)',
                           (item['deviceId'], RUN, n, item['observedAtUtc'], AT.isoformat(),
                            None, None, json.dumps(item)))
        writer.execute('update ingest_cursors set sequence=5')
        writer.execute("insert into ingest_gaps values(?,?,2,3,'REPLAY_OVERFLOW')",
                       ('CMP-01.PMC-01', RUN))
        writer.commit()
    copied_steps = []
    def copy(pinned, target, budget):
        class DuringCopy:
            def backup(self, destination, **kwargs):
                progress = kwargs['progress']
                def interleave(status, remaining, total):
                    if not copied_steps:
                        assert remaining > 0
                        commit_later()
                    copied_steps.append(remaining)
                    progress(status, remaining, total)
                pinned.backup(destination, pages=1, progress=interleave, sleep=.01)
        original_copy(DuringCopy(), target, budget)
    monkeypatch.setattr(a, '_copy', copy)
    try:
        backup = tmp_path/'backup.db'; a.backup_archive(source, backup)
        assert rows(backup) == before
        assert len(rows(source)['raw_batches']) == 4
        assert rows(source)['ingest_cursors'][0][2] == 5
        assert len(rows(source)['ingest_gaps']) == 1
        assert len(copied_steps) > 1 and copied_steps[-1] == 0
    finally:
        writer.close()


def test_uncommitted_transaction_is_excluded_and_reader_releases(tmp_path):
    a = api(); source = tmp_path/'raw.db'; seed(source)
    with sqlite3.connect(source) as writer:
        writer.execute('pragma journal_mode=wal')
        before = rows(source)
        writer.execute('update ingest_cursors set sequence=99')
        a.backup_archive(source, tmp_path/'backup.db')
        assert rows(tmp_path/'backup.db') == before
        writer.rollback()
        writer.execute('begin immediate'); writer.rollback()


def test_sparse_retired_run_and_empty_archive_are_supported(tmp_path):
    a = api()
    from aegis.archive import RawArchive
    async def build(path, empty):
        s = RawArchive(path); await s.start()
        if not empty:
            await s.append([snapshot(100)], AT)
            await s.append([snapshot(0, run=str(uuid4()))], AT)
        await s.close()
    for empty in (False, True):
        source = tmp_path/f'{empty}.db'; asyncio.run(build(source, empty))
        a.backup_archive(source, tmp_path/f'{empty}-copy.db')


@pytest.mark.parametrize('sql', [
    'update ingest_cursors set sequence=0',
    'delete from ingest_cursors',
    "update ingest_cursors set run_id='missing'",
    "update raw_batches set payload_json='{}'",
    "update raw_batches set received_at_utc='invalid'",
    "update raw_batches set observed_at_utc='invalid'",
    "insert into ingest_gaps values('CMP-01.PMC-01','missing',2,3,'REPLAY_OVERFLOW')",
    f"insert into ingest_gaps values('CMP-01.PMC-01','{RUN}',0,0,'REPLAY_OVERFLOW')",
    f"insert into ingest_gaps values('CMP-01.PMC-01','{RUN}',2,9,'REPLAY_OVERFLOW')",
    'pragma user_version=2',
])
def test_inconsistent_archive_is_rejected_without_publication(tmp_path, sql):
    a = api(); source = tmp_path/'raw.db'; seed(source)
    with sqlite3.connect(source) as c: c.execute(sql)
    before = source.read_bytes()
    with pytest.raises(a.BackupError): a.backup_archive(source, tmp_path/'out.db')
    assert not (tmp_path/'out.db').exists() and source.read_bytes() == before
    assert not list(tmp_path.glob('.aegis-backup-*'))


@pytest.mark.parametrize('kind', ['file','directory','symlink','dangling','hardlink','-wal','-shm','-journal'])
def test_existing_destinations_and_sidecars_are_preserved(tmp_path, kind):
    a = api(); source = tmp_path/'raw.db'; seed(source)
    dest = tmp_path/'out.db'; protected = dest
    if kind == 'directory': dest.mkdir()
    elif kind == 'symlink': dest.symlink_to(source)
    elif kind == 'dangling': dest.symlink_to(tmp_path/'missing')
    elif kind == 'hardlink': dest.hardlink_to(source)
    elif kind.startswith('-'): protected = Path(str(dest)+kind); protected.write_bytes(b'keep')
    else: dest.write_bytes(b'keep')
    before = source.read_bytes()
    with pytest.raises(a.BackupError): a.backup_archive(source, dest)
    assert protected.exists() or protected.is_symlink()
    assert source.read_bytes() == before
    if kind in ('file','-wal','-shm','-journal'): assert protected.read_bytes() == b'keep'


@pytest.mark.parametrize('suffix', ['', '-wal','-shm','-journal'])
def test_source_and_absent_companion_names_are_protected(tmp_path, suffix):
    a = api(); source = tmp_path/'raw.db'; seed(source)
    before = source.read_bytes()
    with pytest.raises(a.BackupError): a.backup_archive(source, Path(str(source)+suffix))
    assert source.read_bytes() == before


@pytest.mark.parametrize('stage', ['copy','validate','file_sync','publish'])
def test_failures_before_publication_preserve_source(tmp_path, monkeypatch, stage):
    a = api(); source = tmp_path/'raw.db'; seed(source); before = source.read_bytes()
    def fail(*args, **kwargs): raise OSError(28, 'injected capacity failure')
    if stage == 'copy': monkeypatch.setattr(a, '_copy', fail)
    elif stage == 'validate': monkeypatch.setattr(a, '_inspect', fail)
    elif stage == 'file_sync': monkeypatch.setattr(a, '_sync_file', fail)
    else: monkeypatch.setattr(a.os, 'link', fail)
    with pytest.raises(a.BackupError) as e: a.backup_archive(source, tmp_path/'out.db')
    assert not e.value.published
    assert not (tmp_path/'out.db').exists() and source.read_bytes() == before
    assert not list(tmp_path.glob('.aegis-backup-*'))


def test_publish_race_preserves_competing_file(tmp_path, monkeypatch):
    a = api(); source = tmp_path/'raw.db'; seed(source); dest = tmp_path/'out.db'
    link = a.os.link
    def racing_link(src, dst):
        Path(dst).write_bytes(b'competing')
        link(src, dst)
    monkeypatch.setattr(a.os, 'link', racing_link)
    with pytest.raises(a.BackupError): a.backup_archive(source, dest)
    assert dest.read_bytes() == b'competing'


def test_post_publication_sync_failure_preserves_valid_file(tmp_path, monkeypatch):
    a = api(); source = tmp_path/'raw.db'; seed(source); dest = tmp_path/'out.db'
    def fail(*args): raise OSError('injected sync')
    monkeypatch.setattr(a, '_sync_directory', fail)
    with pytest.raises(a.BackupError) as e: a.backup_archive(source, dest)
    assert e.value.published and e.value.code == 'PUBLISHED_DURABILITY_UNKNOWN'
    assert rows(dest) == rows(source)
    assert not list(tmp_path.glob('.aegis-backup-*'))


@pytest.mark.parametrize('limit,value', [('MAX_DATABASE_BYTES',1),('MAX_RAW_ROWS',1),
    ('MAX_PAYLOAD_BYTES',1),('MAX_TOTAL_PAYLOAD_BYTES',1),('MAX_SECONDS',0),('MAX_SQL_STEPS',0)])
def test_resource_limits_fail_without_destination_and_release_source(tmp_path, monkeypatch, limit, value):
    a = api(); source = tmp_path/'raw.db'; seed(source)
    monkeypatch.setattr(a, limit, value)
    with pytest.raises(a.BackupError): a.backup_archive(source, tmp_path/'out.db')
    assert not (tmp_path/'out.db').exists()
    with sqlite3.connect(source) as c: c.execute('begin immediate'); c.rollback()


@pytest.mark.parametrize('mutation', ['raw','receipt','cursor','gap','manifest','truncate'])
def test_expected_manifest_rejects_changed_backup_and_preserves_restore_target(tmp_path, mutation):
    a = api(); source = tmp_path/'raw.db'; seed(source, gap=True)
    backup = tmp_path/'backup.db'; m = a.backup_archive(source, backup)
    if mutation == 'manifest': m['artifactSha256'] = '0'*64
    elif mutation == 'truncate': backup.write_bytes(backup.read_bytes()[:100])
    else:
        sql = {'raw':"update raw_batches set payload_json=replace(payload_json,'10.0','11.0')",
               'receipt':"update raw_batches set received_at_utc='2026-10-19T00:00:00Z'",
               'cursor':'update ingest_cursors set sequence=4',
               'gap':'delete from ingest_gaps'}[mutation]
        with sqlite3.connect(backup) as c: c.execute(sql)
    with pytest.raises(a.BackupError): a.verify_archive(backup, m)
    with pytest.raises(a.BackupError): a.restore_archive(backup, tmp_path/'restore.db', m)
    assert not (tmp_path/'restore.db').exists()


@pytest.mark.parametrize('operation', ['backup','restore'])
def test_stdout_failure_reports_already_published_file(tmp_path, monkeypatch, capsys, operation):
    a = api(); source = tmp_path/'raw.db'; seed(source)
    args = ['backup','--source',str(source),'--destination',str(tmp_path/'out.db')]
    if operation == 'restore':
        backup = tmp_path/'backup.db'; manifest = a.backup_archive(source, backup)
        reference = tmp_path/'manifest.json'; reference.write_text(json.dumps(manifest))
        args = ['restore','--source',str(backup),'--destination',str(tmp_path/'out.db'),
                '--manifest',str(reference)]
    class FailedOutput:
        def write(self, value): raise OSError(28, 'injected full output')
        def flush(self): pass
    monkeypatch.setattr(a.sys, 'stdout', FailedOutput())
    assert a.main(args) == 2
    result = json.loads(capsys.readouterr().err)
    assert result['publication'] == 'PUBLISHED_DURABILITY_UNKNOWN'
    assert rows(tmp_path/'out.db') == rows(source)


@pytest.mark.skipif(not Path('/dev/full').exists(), reason='Linux full output device required')
def test_actual_cli_full_stdout_has_structured_error_and_exit_two(tmp_path):
    source = tmp_path/'raw.db'; seed(source); dest = tmp_path/'out.db'
    root = Path(__file__).resolve().parents[1]
    with open('/dev/full', 'wb') as full:
        result = subprocess.run([sys.executable,'-m','aegis.archive_backup','backup',
            '--source',str(source),'--destination',str(dest)], stdout=full, stderr=subprocess.PIPE,
            env=dict(os.environ, PYTHONPATH=str(root/'src')), timeout=10)
    assert result.returncode == 2
    error = json.loads(result.stderr)
    assert error == {'error':'PUBLISHED_REPORT_FAILED','publication':'PUBLISHED_DURABILITY_UNKNOWN'}
    assert rows(dest) == rows(source)


@pytest.mark.parametrize('reference', [None, {}, [], 'invalid', True, float('nan')])
def test_restore_api_requires_valid_reference_before_any_copy(tmp_path, monkeypatch, reference):
    a = api(); source = tmp_path/'raw.db'; seed(source)
    before = source.read_bytes(); copied = []
    original_copy = a._copy
    def observe(*args):
        copied.append(True)
        return original_copy(*args)
    monkeypatch.setattr(a, '_copy', observe)
    with pytest.raises(a.BackupError):
        a.restore_archive(source, tmp_path/'restored.db', reference)
    assert not copied and not (tmp_path/'restored.db').exists()
    assert source.read_bytes() == before


@pytest.mark.parametrize('after_publish', [False, True])
def test_cleanup_failure_is_explicit_and_preserves_published_destination(tmp_path, monkeypatch, after_publish):
    a = api(); source = tmp_path/'raw.db'; seed(source)
    before = source.read_bytes(); dest = tmp_path/'out.db'; unlink = Path.unlink
    def blocked_unlink(path, *args, **kwargs):
        if path.parent.name.startswith('.aegis-backup-'):
            raise PermissionError('injected staging cleanup failure')
        return unlink(path, *args, **kwargs)
    def failed_copy(*args): raise OSError(28, 'injected copy failure')
    with monkeypatch.context() as patch:
        patch.setattr(Path, 'unlink', blocked_unlink)
        if not after_publish: patch.setattr(a, '_copy', failed_copy)
        with pytest.raises(a.BackupError) as error: a.backup_archive(source, dest)
        assert error.value.published == after_publish
        assert error.value.code == ('PUBLISHED_CLEANUP_REQUIRED' if after_publish else 'STAGING_CLEANUP_REQUIRED')
        assert dest.exists() == after_publish
        assert source.read_bytes() == before
        if after_publish: assert rows(dest) == rows(source)
    # Explicit operator-style cleanup of this test's own retained alias only.
    for directory in tmp_path.glob('.aegis-backup-*'):
        for item in directory.iterdir(): item.unlink()
        directory.rmdir()


@pytest.mark.parametrize('limit', ['MAX_CURSORS','MAX_GAPS','MAX_MANIFEST_BYTES'])
def test_remaining_reference_and_table_limits(tmp_path, monkeypatch, limit):
    a = api(); source = tmp_path/'raw.db'; seed(source, gap=True)
    if limit == 'MAX_MANIFEST_BYTES':
        backup = tmp_path/'backup.db'; manifest = a.backup_archive(source, backup)
        monkeypatch.setattr(a, limit, 1)
        with pytest.raises(a.BackupError): a.restore_archive(backup, tmp_path/'out.db', manifest)
    else:
        monkeypatch.setattr(a, limit, 0)
        with pytest.raises(a.BackupError): a.backup_archive(source, tmp_path/'out.db')
    assert not (tmp_path/'out.db').exists()


def test_nonzero_sql_progress_budget_interrupts_and_releases_reader(tmp_path, monkeypatch):
    a = api(); source = tmp_path/'raw.db'
    from aegis.archive import RawArchive
    async def build():
        store = RawArchive(source); await store.start()
        await store.append([snapshot(n) for n in range(500)], AT)
        await store.close()
    asyncio.run(build())
    invoked = []; progress = a._Budget.progress
    def observed(budget):
        invoked.append(True)
        return progress(budget)
    monkeypatch.setattr(a._Budget, 'progress', observed)
    monkeypatch.setattr(a, 'MAX_SQL_STEPS', 1000)
    with pytest.raises(a.BackupError): a.backup_archive(source, tmp_path/'out.db')
    assert invoked and not (tmp_path/'out.db').exists()
    with sqlite3.connect(source) as writer: writer.execute('begin immediate'); writer.rollback()
