import os
from datetime import timedelta
from unittest.mock import patch
import pytest
from test_adapter_input import AT, fixture, page, record, wire
from aegis.windows_adapter_candidate import WindowsAdapterCandidate as Store
from aegis.windows_journal_candidate import WindowsJournalCandidate as Journal, JournalError

pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Windows native handles')

def pages():
    return page(end=False), page([record('r1', provided=True)], after='delivery-1', next='delivery-2')

def test_commit_reopen_backup_resume(tmp_path):
    first, second = pages()
    p, b = tmp_path/'source', tmp_path/'backup'
    with Store(p, fixture=fixture(first, second)) as s:
        s.commit_page(wire(first), AT)
        assert s.committed_cursor == 'delivery-1'
        before = s.records
        report = s.backup_to_new(b)
        assert report['durablePublication'] == 'NOT_VERIFIED'
    with Store(b) as restored:
        assert restored.records == before
        restored.commit_page(wire(second), AT + timedelta(seconds=1))
        assert restored.committed_cursor == 'delivery-2'
        assert len(restored.records) == 2
    with Store(p) as source:
        assert source.records == before
        assert source.committed_cursor == 'delivery-1'

def test_retransmission_and_conflict_do_not_change_file(tmp_path):
    p = tmp_path/'source'
    with Store(p, fixture=fixture(page())) as s:
        s.commit_page(wire(page()), AT)
        count = len(s._journal.records)
        assert s.commit_page(wire(page()), AT)['status'] == 'RETRANSMISSION'
        with pytest.raises(JournalError, match='FIXTURE_MISMATCH'):
            s.commit_page(wire(page([record(value=99)])), AT)
        assert len(s._journal.records) == count

def test_failed_append_fences_cursor_and_reopen_resolves(tmp_path):
    p = tmp_path/'source'
    with Store(p, fixture=fixture(page())) as s:
        with patch.object(s._journal._file, 'flush', side_effect=OSError('injected')):
            with pytest.raises(JournalError, match='RESULT_UNKNOWN'):
                s.commit_page(wire(page()), AT)
        with pytest.raises(JournalError, match='RESULT_UNKNOWN'):
            _ = s.committed_cursor
    with Store(p) as reopened:
        assert reopened.committed_cursor == 'delivery-1'

def test_backup_no_overwrite(tmp_path):
    p, b = tmp_path/'source', tmp_path/'existing'
    b.write_bytes(b'preserve')
    with Store(p, fixture=fixture(page())) as s:
        with pytest.raises(OSError):
            s.backup_to_new(b)
        assert s.committed_cursor is None
    assert b.read_bytes() == b'preserve'

def test_incomplete_backup_rejected(tmp_path):
    p = tmp_path/'empty'
    with Journal(p, create=True):
        pass
    with pytest.raises(JournalError, match='INCOMPLETE_SNAPSHOT'):
        Store(p)

def test_partial_backup_preserved_and_rejected(tmp_path):
    p, b = tmp_path/'source', tmp_path/'backup'
    from aegis.windows_journal_candidate import _File
    write = _File.write
    def partial(self, payload):
        if payload.startswith(b'{'):
            raise AssertionError('framed payload expected')
        if len(payload) > 100:
            write(self, payload[:50])
            raise OSError('partial write')
        write(self, payload)
    with Store(p, fixture=fixture(page())) as s:
        with patch.object(_File, 'write', partial):
            with pytest.raises(JournalError, match='RESULT_UNKNOWN'):
                s.backup_to_new(b)
        assert s.committed_cursor is None
    before = b.read_bytes()
    with pytest.raises(JournalError):
        Store(b)
    assert b.read_bytes() == before

def test_invalid_schema_and_cursor_transition_rejected(tmp_path):
    from aegis.adapter_input import _encode, _decode
    p = tmp_path/'source'
    with Store(p, fixture=fixture(page())) as s:
        s.commit_page(wire(page()), AT)
        snapshot = _decode(s._journal.records[-1], 1024*1024)
    with Journal(p) as journal:
        journal.append(_encode(snapshot))  # Duplicate full state is not an append transition.
    with pytest.raises(JournalError, match='INVALID_SNAPSHOT_TRANSITION'):
        Store(p)
    other = tmp_path/'unknown-schema'
    snapshot['version'] = 'unsupported'
    with Journal(other, create=True) as journal:
        journal.append(_encode(snapshot))
    with pytest.raises(JournalError, match='INVALID_SNAPSHOT'):
        Store(other)

def test_backup_flush_unknown_does_not_fence_source(tmp_path):
    from aegis.windows_journal_candidate import _File
    p, b = tmp_path/'source', tmp_path/'backup'
    original = _File.flush
    calls = []
    def fail_second(handle):
        calls.append(1)
        if len(calls) == 2:
            raise OSError('backup snapshot flush failed')
        return original(handle)
    with Store(p, fixture=fixture(page())) as s:
        s.commit_page(wire(page()), AT)
        with patch.object(_File, 'flush', fail_second):
            with pytest.raises(JournalError, match='RESULT_UNKNOWN'):
                s.backup_to_new(b)
        assert s.committed_cursor == 'delivery-1'
    with Store(b) as recovered:
        assert recovered.committed_cursor == 'delivery-1'

def test_exact_wire_evidence_survives_backup_and_retransmission(tmp_path):
    import json
    original_fixture = json.dumps(fixture(page()), indent=3).encode() + b'\n'
    original_page = json.dumps(page(), indent=4).encode() + b'\n\n'
    with Store(tmp_path/'source', fixture=original_fixture) as source:
        source.commit_page(original_page, AT)
        source.commit_page(wire(page()), AT + timedelta(seconds=1))
        assert source.wire_pages == (original_page,)
        assert source.fixture_wire == original_fixture
        source.backup_to_new(tmp_path/'backup')
    with Store(tmp_path/'backup') as restored:
        assert restored.wire_pages == (original_page,)
        assert restored.fixture_wire == original_fixture
        assert restored.records[0]['firstReceivedAtUtc'] == '2026-11-09T12:00:00Z'


def test_wire_hash_and_canonical_mismatch_rejected(tmp_path):
    import copy, base64, hashlib
    from aegis.adapter_input import _encode, _decode
    with Store(tmp_path/'source', fixture=fixture(page())) as source:
        source.commit_page(wire(page()), AT)
        snapshot = _decode(source._journal.records[-1], 1024*1024)
    for label in ('hash', 'canonical'):
        invalid = copy.deepcopy(snapshot)
        different = wire(page([record(value=99)]))
        invalid['pages'][0]['wire'] = base64.b64encode(different).decode()
        if label == 'canonical':
            invalid['pages'][0]['wireSha256'] = hashlib.sha256(different).hexdigest()
        with Journal(tmp_path/label, create=True) as journal:
            journal.append(_encode(invalid))
        with pytest.raises(JournalError, match='WIRE_'):
            Store(tmp_path/label)


def test_legacy_snapshot_preserved_read_only(tmp_path):
    import json
    from aegis.adapter_input import _encode
    snapshot = {'version':'windows-adapter-snapshot-1', 'fixture':fixture(page()),
                'pages':[{'page':page(), 'received':'2026-11-09T12:00:00Z'}]}
    p = tmp_path/'legacy'
    with Journal(p, create=True) as journal:
        journal.append(_encode(snapshot))
    before = p.read_bytes()
    with Store(p) as old:
        assert old.committed_cursor == 'delivery-1'
        with pytest.raises(JournalError, match='WIRE_EVIDENCE_UNAVAILABLE'):
            _ = old.wire_pages
        with pytest.raises(JournalError, match='LEGACY_SNAPSHOT_READ_ONLY'):
            old.commit_page(wire(page()), AT)
        old.backup_to_new(tmp_path/'legacy-copy')
    assert p.read_bytes() == before
    with Store(tmp_path/'legacy-copy') as copy:
        assert copy.committed_cursor == 'delivery-1'


@pytest.mark.parametrize('boundary', ['before', 'after'])
def test_real_process_exit_at_snapshot_commit_boundary(tmp_path, boundary):
    import subprocess, sys, json
    from pathlib import Path
    p = tmp_path/'source'
    raw = json.dumps(page(), indent=2).encode() + b'\n'
    payload = tmp_path/'page.json'
    payload.write_bytes(raw)
    with Store(p, fixture=fixture(page())):
        pass
    code = '''import os,sys
from pathlib import Path
from datetime import datetime,timezone
from aegis.windows_adapter_candidate import WindowsAdapterCandidate as S
s=S(sys.argv[1]); append=s._journal.append
def interrupted(payload):
    if sys.argv[3]=='before': os._exit(71)
    append(payload)
    os._exit(72)
s._journal.append=interrupted
s.commit_page(Path(sys.argv[2]).read_bytes(),datetime(2026,11,9,12,tzinfo=timezone.utc))
'''
    # Worker exits itself: venv launcher waits and propagates worker exit status.
    child_env = {**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[1] / 'src')}
    result = subprocess.run([sys.executable, '-c', code, str(p), str(payload), boundary],
                            timeout=20, creationflags=subprocess.CREATE_NO_WINDOW,
                            env=child_env, capture_output=True, text=True)
    assert result.returncode == (71 if boundary == 'before' else 72), result.stderr
    with Store(p) as recovered:
        assert recovered.committed_cursor == (None if boundary == 'before' else 'delivery-1')
        assert recovered.wire_pages == (() if boundary == 'before' else (raw,))
        outcome = recovered.commit_page(raw, AT)
        assert outcome['status'] == ('LOCAL_FLUSH_CONFIRMED' if boundary == 'before' else 'RETRANSMISSION')
        assert len(recovered.records) == 1


def test_fixture_canonical_type_confusion_rejected(tmp_path):
    from aegis.adapter_input import _encode, _decode
    with Store(tmp_path/'source', fixture=fixture(page())) as source:
        snapshot = _decode(source._journal.records[-1], 1024*1024)
    snapshot['fixture']['pages'][0]['delayMs'] = False  # False == 0 in Python, not JSON.
    with Journal(tmp_path/'invalid', create=True) as journal:
        journal.append(_encode(snapshot))
    with pytest.raises(JournalError, match='WIRE_CANONICAL_MISMATCH'):
        Store(tmp_path/'invalid')
