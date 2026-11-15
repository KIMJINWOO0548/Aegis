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
