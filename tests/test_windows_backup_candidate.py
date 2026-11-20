import os
from dataclasses import replace
from unittest.mock import patch
import pytest
from test_adapter_input import fixture, page, wire, AT
from aegis.windows_adapter_candidate import WindowsAdapterCandidate as Store
from aegis import windows_backup_candidate as backup
from aegis.windows_journal_candidate import JournalError
pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Windows native handles')


def prepare(tmp_path):
    with Store(tmp_path/'source', fixture=fixture(page())) as source:
        source.commit_page(wire(page()), AT)
        return backup.prepare_backup(source, tmp_path/'stage', tmp_path/'final')


def test_publish_and_reopen(tmp_path):
    ticket = prepare(tmp_path)
    assert backup.inspect_publication(ticket)['status'] == 'REQUIRES_REVIEW'
    assert backup.publish_backup(ticket)['status'] == 'PUBLISHED_READBACK_CONFIRMED'
    assert backup.inspect_publication(ticket)['status'] == 'PUBLISHED_OBSERVED'
    with Store(ticket.destination) as restored:
        assert restored.committed_cursor == 'delivery-1'
    assert not (tmp_path/'stage').exists()


def test_existing_destination_and_race_never_overwritten(tmp_path):
    ticket = prepare(tmp_path)
    (tmp_path/'final').write_bytes(b'preserve')
    with pytest.raises(JournalError, match='PUBLICATION_RESULT_UNKNOWN'):
        backup.publish_backup(ticket)
    assert (tmp_path/'final').read_bytes() == b'preserve'
    assert backup.inspect_publication(ticket)['staging'] == 'MATCH'


def test_move_success_then_exception_retains_destination(tmp_path):
    ticket = prepare(tmp_path)
    move = backup._move_no_replace
    def ambiguous(a, b):
        move(a, b)
        raise OSError('after rename')
    with patch.object(backup, '_move_no_replace', ambiguous):
        with pytest.raises(JournalError, match='PUBLICATION_RESULT_UNKNOWN'):
            backup.publish_backup(ticket)
    assert backup.inspect_publication(ticket)['status'] == 'PUBLISHED_OBSERVED'


def test_readback_failure_is_unknown_not_cleanup(tmp_path):
    ticket = prepare(tmp_path)
    verify = backup._verify
    def fail_final(path, digest):
        if path == ticket.destination:
            raise OSError('readback failure')
        verify(path, digest)
    with patch.object(backup, '_verify', fail_final):
        with pytest.raises(JournalError, match='PUBLICATION_RESULT_UNKNOWN'):
            backup.publish_backup(ticket)
    assert backup.inspect_publication(ticket)['status'] == 'PUBLISHED_OBSERVED'


def test_digest_and_path_validation_before_move(tmp_path):
    ticket = prepare(tmp_path)
    with pytest.raises(JournalError, match='SNAPSHOT_MISMATCH'):
        backup.publish_backup(replace(ticket, snapshot_sha256='0'*64))
    with pytest.raises(JournalError, match='DISTINCT_SAME_DIRECTORY_REQUIRED'):
        backup.publish_backup(replace(ticket, destination=ticket.staging))
    with pytest.raises(JournalError, match='DISTINCT_SAME_DIRECTORY_REQUIRED'):
        backup.publish_backup(replace(ticket, destination=str(tmp_path/'other'/'final')))
    assert backup.inspect_publication(ticket)['staging'] == 'MATCH'


def test_sharing_violation_preserves_stage(tmp_path):
    ticket = prepare(tmp_path)
    with Store(ticket.staging):
        with pytest.raises(OSError):
            backup.publish_backup(ticket)
    assert backup.inspect_publication(ticket)['staging'] == 'MATCH'

def test_invalid_application_schema_is_unverified(tmp_path):
    from aegis.windows_journal_candidate import WindowsJournalCandidate
    ticket = prepare(tmp_path)
    with WindowsJournalCandidate(ticket.destination, create=True) as journal:
        journal.append(b'{"version":"windows-adapter-snapshot-1","fixture":{},"pages":[]}')
    assert backup.inspect_publication(ticket)['destination'] == 'UNVERIFIED'
