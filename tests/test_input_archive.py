"""Generic archive integrity tests use synthetic payloads, including origin declarations."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import errno
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys

import pytest

from test_adapter_input import AT, RUN, fixture, page as adapter_page, record, wire


HEADER=('adapterId','adapterVersion','streamId','declaredOrigin','originEvidence',
        'acquisitionMethod','sourceAccessMode')
PROVENANCE=('declaredOrigin','originEvidence','acquisitionMethod','sourceAccessMode')


def api():
    from aegis import input_archive
    return input_archive


def test_input_archive_api_exists():
    assert callable(api().InputArchive)
    assert callable(api().validate_write_page)
    assert callable(api().validate_input_archive_snapshot)


def page(records=None,**kwargs):
    return adapter_page(records,**{'schemaVersion':'archive-write-1',**kwargs})


def descriptor(incoming=None,**changes):
    p=incoming if incoming is not None else page()
    return {**{key:p[key] for key in HEADER},**changes}


def first():return page(end=False)
def second():return page([record('r1',provided=True)],after='delivery-1',next='delivery-2')


def rows(connection):
    return tuple((name,connection.execute(f'SELECT * FROM {name} ORDER BY 1').fetchall())
        for name in ('stream_meta','archive_pages','archive_records','archive_state'))


@pytest.mark.parametrize('origin',['DEVICE','SYNTHETIC','UNKNOWN'])
@pytest.mark.parametrize('method',['SOURCE_BUFFER_REPLAY','SNAPSHOT_READ'])
def test_nullable_original_metadata_and_declared_provenance_survive_commit_and_reopen(tmp_path,origin,method):
    a=api();p=page([record(),record('provided',provided=True,value=2**60)],
        declaredOrigin=origin,acquisitionMethod=method);bound=descriptor(p);path=tmp_path/'archive.sqlite'
    receipt=datetime(2026,11,9,15,tzinfo=timezone(timedelta(hours=3)))
    with a.InputArchive(path,bound) as archive:
        result=archive.append_page(wire(p),receipt)
        assert result['pageCommitStatus']=='LOCAL_ARCHIVE_COMMITTED'
        assert result['newRecords']==2 and result['sourceAcknowledgement']=='NOT_SENT'
        assert archive.committed_cursor=='delivery-1' and archive.ended
        entries=archive.records
        assert [entry['record'] for entry in entries]==p['records']
        assert all(entry['firstReceivedAtUtc']=='2026-11-09T12:00:00Z' for entry in entries)
        assert all(entry['provenance']=={key:p[key] for key in PROVENANCE} for entry in entries)
        assert entries[0]['record']['sourceMetadata']==record()['sourceMetadata']
        assert entries[1]['record']['sourceMetadata']['observedAtUtc']=='2026-11-09T13:00:00+01:00'
        assert entries[1]['record']['sourceMetadata']['sequence']==2**60
        assert entries[1]['record']['sourceMetadata']['runId']==RUN
        entries[0]['record']['signals'][0]['value']=999
        entries[0]['provenance']['declaredOrigin']='tampered-copy'
        assert archive.records[0]['record']['signals'][0]['value']==-2.5
        assert archive.records[0]['provenance']['declaredOrigin']==origin
        assert archive.report()['sourceAcknowledgement']=='NOT_SENT'
        assert stat.S_IMODE(path.stat().st_mode)==0o600
    with a.InputArchive(path,bound) as reopened:
        assert reopened.committed_cursor=='delivery-1' and reopened.ended
        assert [entry['record'] for entry in reopened.records]==p['records']


def test_write_page_validation_keeps_original_wire_and_canonical_content():
    a=api();p=page([record('provided',provided=True)],declaredOrigin='DEVICE')
    payload=json.dumps(p,indent=3).encode();validated=a.validate_write_page(payload)
    assert validated.model.schemaVersion=='archive-write-1'
    assert validated.model.declaredOrigin=='DEVICE'
    assert validated.wire==payload
    assert json.loads(validated.canonical)==p
    assert validated.canonical==json.dumps(p,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()


@pytest.mark.parametrize('change',[
    {'schemaVersion':'adapter-input-1'}, {'adapterVersion':'2.0'},
    {'originEvidence':'HARDWARE_VERIFIED'}, {'sourceAccessMode':'READ_WRITE'},
    {'declaredOrigin':'REAL_DEVICE_VERIFIED'}, {'acquisitionMethod':'DEVICE_CONTROL'},
    {'streamId':'invalid'}, {'endOfStream':1},
])
def test_invalid_page_contract_does_not_create_partial_records(tmp_path,change):
    a=api();path=tmp_path/'archive.sqlite'
    with a.InputArchive(path,descriptor()) as archive:
        with pytest.raises(a.StoreError):archive.append_page(wire(page(**change)),AT)
        assert archive.records==() and archive.committed_cursor is None and not archive.ended


@pytest.mark.parametrize('field,status',[('observedAtUtc','timestampStatus'),('sequence','sequenceStatus'),('runId','runIdStatus')])
@pytest.mark.parametrize('mutation',['missing','contradiction'])
def test_original_metadata_presence_is_validated_without_fabrication(tmp_path,field,status,mutation):
    a=api();p=page([record('valid'),record('bad')]);metadata=p['records'][1]['sourceMetadata']
    if mutation=='missing':del metadata[field]
    else:metadata[status]='PROVIDED'
    with a.InputArchive(tmp_path/'archive.sqlite',descriptor()) as archive:
        with pytest.raises(a.StoreError):archive.append_page(wire(p),AT)
        assert archive.records==() and archive.committed_cursor is None


@pytest.mark.parametrize('bound',[
    {}, {'extra':'value'}, {'schemaVersion':'archive-write-1'},
    {'sourceAccessMode':'READ_WRITE'}, {'declaredOrigin':'VERIFIED'}, {'adapterVersion':'2.0'},
])
def test_descriptor_requires_exact_supported_binding_fields(tmp_path,bound):
    a=api();value=descriptor();value.update(bound)
    if bound=={}:value.pop('streamId')
    with pytest.raises(a.StoreError):a.InputArchive(tmp_path/'archive.sqlite',value)


@pytest.mark.parametrize('change',[{'adapterId':'other'},{'streamId':RUN},
    {'declaredOrigin':'DEVICE'},{'acquisitionMethod':'SNAPSHOT_READ'}])
def test_reopen_and_page_binding_mismatches_preserve_existing_state(tmp_path,change):
    a=api();path=tmp_path/'archive.sqlite';bound=descriptor()
    with a.InputArchive(path,bound) as archive:
        archive.append_page(wire(first()),AT);before=archive.records
        bad=page([record('r1')],after='delivery-1',next='delivery-2',**change)
        with pytest.raises(a.StoreError):archive.append_page(wire(bad),AT)
        assert archive.records==before and archive.committed_cursor=='delivery-1'
    before=path.read_bytes()
    with pytest.raises(a.StoreError) as error:a.InputArchive(path,descriptor(**change))
    assert error.value.code=='STORE_BINDING_MISMATCH' and path.read_bytes()==before


def test_descriptor_input_is_copied_and_cannot_change_committed_binding(tmp_path):
    a=api();bound=descriptor();path=tmp_path/'archive.sqlite'
    with a.InputArchive(path,bound) as archive:
        bound['declaredOrigin']='DEVICE'
        archive.append_page(wire(page()),AT)
        assert archive.records[0]['provenance']['declaredOrigin']=='SYNTHETIC'
    with a.InputArchive(path,descriptor()) as reopened:assert reopened.ended


def test_canonical_retransmission_preserves_first_wire_receipt_and_cursor(tmp_path):
    a=api();path=tmp_path/'archive.sqlite'
    with a.InputArchive(path,descriptor()) as archive:
        original=wire(first());archive.append_page(original,AT)
        archive.append_page(wire(second()),AT+timedelta(seconds=1));before=archive.records
        differently_formatted=json.dumps(dict(reversed(list(first().items()))),indent=4).encode()
        result=archive.append_page(differently_formatted,AT+timedelta(seconds=99))
        assert result['pageCommitStatus']=='ALREADY_LOCAL_ARCHIVE_COMMITTED' and result['newRecords']==0
        assert archive.records==before and archive.committed_cursor=='delivery-2' and archive.ended
        with sqlite3.connect(path) as connection:
            stored=connection.execute('SELECT wire,received_at_utc FROM archive_pages WHERE ordinal=0').fetchone()
        assert stored==(original,'2026-11-09T12:00:00Z')
        with pytest.raises(a.StoreError):archive.append_page(wire(page([record(value=99)],end=False)),AT)
        assert archive.records==before


def test_record_deduplication_keeps_first_receipt_and_conflict_rolls_back_whole_page(tmp_path):
    a=api();path=tmp_path/'archive.sqlite'
    with a.InputArchive(path,descriptor()) as archive:
        archive.append_page(wire(first()),AT)
        repeated=page([record(),record('r1')],after='delivery-1',next='delivery-2',end=False)
        result=archive.append_page(wire(repeated),AT+timedelta(seconds=5))
        assert result['newRecords']==1 and len(archive.records)==2
        assert archive.records[0]['firstReceivedAtUtc']=='2026-11-09T12:00:00Z'
        before=deepcopy(archive.records)
        conflicting=page([record('new'),record(value=99)],after='delivery-2',next='delivery-3')
        with pytest.raises(a.StoreError):archive.append_page(wire(conflicting),AT+timedelta(seconds=6))
        assert archive.records==before and archive.committed_cursor=='delivery-2' and not archive.ended


@pytest.mark.parametrize('kind',['out_of_order','loop','after_eof'])
def test_token_order_loops_and_eof_never_advance_invalid_delivery(tmp_path,kind):
    a=api();path=tmp_path/'archive.sqlite'
    with a.InputArchive(path,descriptor()) as archive:
        archive.append_page(wire(first()),AT)
        if kind=='out_of_order':bad=page([record('r1')],after='missing',next='delivery-2')
        elif kind=='loop':
            archive.append_page(wire(page([record('r1')],after='delivery-1',next='delivery-2',end=False)),AT)
            bad=page([record('r2')],after='delivery-2',next='delivery-1')
        else:
            archive.append_page(wire(second()),AT)
            bad=page([record('r2')],after='delivery-2',next='delivery-3')
        before=archive.records;cursor=archive.committed_cursor;ended=archive.ended
        with pytest.raises(a.StoreError):archive.append_page(wire(bad),AT)
        assert archive.records==before and archive.committed_cursor==cursor and archive.ended==ended


@pytest.mark.parametrize('end',[False,True])
def test_empty_nonterminal_has_no_commit_but_empty_terminal_is_durable(tmp_path,end):
    a=api();path=tmp_path/'archive.sqlite';p=page([],next=None,end=end)
    with a.InputArchive(path,descriptor()) as archive:
        result=archive.append_page(wire(p),AT)
        assert result['pageCommitStatus']==('LOCAL_ARCHIVE_COMMITTED' if end else 'NO_NEW_PAGE')
        assert result['newRecords']==0 and result['sourceAcknowledgement']=='NOT_SENT'
        assert archive.committed_cursor is None and archive.ended==end and archive.records==()
        with sqlite3.connect(path) as connection:
            assert connection.execute('SELECT count(*) FROM archive_pages').fetchone()[0]==int(end)
    with a.InputArchive(path,descriptor()) as archive:assert archive.ended==end and archive.records==()


def test_failure_after_page_and_record_inserts_rolls_back_cursor_and_can_retry(tmp_path,monkeypatch):
    a=api();path=tmp_path/'archive.sqlite'
    with a.InputArchive(path,descriptor()) as archive:
        write=archive._write_candidate
        def injected_failure(*args):
            write(*args)
            raise OSError(errno.ENOSPC,'injected no space after inserts')
        with monkeypatch.context() as patch:
            patch.setattr(archive,'_write_candidate',injected_failure)
            with pytest.raises(a.StoreError):archive.append_page(wire(page()),AT)
        assert archive.committed_cursor is None and archive.records==() and not archive.ended
        archive.append_page(wire(page()),AT)
        assert archive.committed_cursor=='delivery-1' and len(archive.records)==1


@pytest.mark.parametrize('changed_evidence',['wire','receipt'])
def test_precommit_write_hook_cannot_rewrite_preexisting_prefix_and_rolls_back_whole_page(tmp_path,monkeypatch,changed_evidence):
    a=api();path=tmp_path/'archive.sqlite'
    with a.InputArchive(path,descriptor()) as archive:
        archive.append_page(wire(first()),AT)
        before=rows(archive._connection);records_before=archive.records;write=archive._write_candidate;rewritten=[]
        def write_then_rewrite_prefix(*args):
            write(*args)
            if changed_evidence=='wire':
                changed=json.dumps(first(),indent=4).encode()
                archive._connection.execute('UPDATE archive_pages SET wire=?,wire_sha=? WHERE ordinal=0',
                    (changed,hashlib.sha256(changed).hexdigest()))
            else:
                receipt='2026-11-09T12:00:05Z'
                archive._connection.execute('UPDATE archive_pages SET received_at_utc=? WHERE ordinal=0',(receipt,))
                archive._connection.execute('UPDATE archive_records SET first_received_at_utc=? WHERE record_id=?',
                    (receipt,'r0'))
            rewritten.append(True)
        with monkeypatch.context() as patch:
            patch.setattr(archive,'_write_candidate',write_then_rewrite_prefix)
            with pytest.raises(a.StoreError) as error:archive.append_page(wire(second()),AT+timedelta(seconds=1))
        assert error.value.code=='STORE_INTEGRITY' and rewritten==[True]
        assert rows(archive._connection)==before
        assert archive.records==records_before and archive.committed_cursor=='delivery-1' and not archive.ended
        archive.append_page(wire(second()),AT+timedelta(seconds=1))
        assert archive.committed_cursor=='delivery-2' and archive.ended


def test_sqlite_busy_and_full_leave_no_records_or_cursor_and_retry_succeeds(tmp_path):
    a=api();path=tmp_path/'archive.sqlite';p=page([record('large')])
    p['records'][0]['signals']=[{'signalKey':f'synthetic.tag{n:04d}','value':n,'unit':'count','quality':'GOOD'} for n in range(300)]
    with a.InputArchive(path,descriptor()) as archive:
        with sqlite3.connect(path) as blocker:
            blocker.execute('BEGIN IMMEDIATE')
            with pytest.raises(a.StoreError):archive.append_page(wire(p),AT)
            blocker.rollback()
        current=archive._connection.execute('PRAGMA page_count').fetchone()[0]
        archive._connection.execute(f'PRAGMA max_page_count={current}')
        with pytest.raises(a.StoreError):archive.append_page(wire(p),AT)
        assert archive.committed_cursor is None and archive.records==()
        archive._connection.execute('PRAGMA max_page_count=8192')
        archive.append_page(wire(p),AT)
        assert len(archive.records)==1 and archive.ended


def test_uncommitted_proposal_is_never_exposed_by_public_properties(tmp_path,monkeypatch):
    a=api();path=tmp_path/'archive.sqlite'
    with a.InputArchive(path,descriptor()) as archive:
        commit=archive._commit;observed=[]
        def inspect_then_commit():
            with pytest.raises(a.StoreError) as error:_=archive.records
            assert error.value.code=='STORE_OPERATION_IN_PROGRESS'
            with sqlite3.connect(path) as connection:
                observed.append(connection.execute('SELECT page_count FROM archive_state').fetchone()[0])
            commit()
        monkeypatch.setattr(archive,'_commit',inspect_then_commit)
        archive.append_page(wire(page()),AT)
        assert observed==[0] and archive.committed_cursor=='delivery-1'


@pytest.mark.parametrize('after_real_commit',[False,True])
def test_commit_exception_is_unknown_and_fenced_until_verified_reopen(tmp_path,monkeypatch,after_real_commit):
    a=api();path=tmp_path/'archive.sqlite';archive=a.InputArchive(path,descriptor());commit=archive._commit
    def uncertain():
        if after_real_commit:commit()
        raise OSError(errno.EIO,'injected uncertain commit outcome')
    monkeypatch.setattr(archive,'_commit',uncertain)
    with pytest.raises(a.StoreError) as error:archive.append_page(wire(page()),AT)
    assert error.value.code=='STORE_RESULT_UNKNOWN'
    with pytest.raises(a.StoreError) as error:_=archive.committed_cursor
    assert error.value.code=='STORE_RESULT_UNKNOWN';archive.close()
    with a.InputArchive(path,descriptor()) as reopened:
        assert len(reopened.records)==int(after_real_commit)
        assert (reopened.committed_cursor=='delivery-1')==after_real_commit
        reopened.append_page(wire(page()),AT+timedelta(seconds=1))
        assert len(reopened.records)==1


def test_postcommit_readback_failure_cannot_return_success(tmp_path,monkeypatch):
    a=api();path=tmp_path/'archive.sqlite';archive=a.InputArchive(path,descriptor())
    def fail(*_args):raise OSError(errno.EIO,'injected confirmation failure')
    monkeypatch.setattr(archive,'_confirm_after_commit',fail)
    with pytest.raises(a.StoreError) as error:archive.append_page(wire(page()),AT)
    assert error.value.code=='STORE_RESULT_UNKNOWN';archive.close()
    with a.InputArchive(path,descriptor()) as reopened:assert reopened.ended and len(reopened.records)==1


@pytest.mark.parametrize('target_ordinal',[0,1])
@pytest.mark.parametrize('changed_evidence',['wire','receipt'])
def test_confirmation_pins_requested_wire_receipt_and_precommit_page_prefix(tmp_path,monkeypatch,target_ordinal,changed_evidence):
    a=api();path=tmp_path/'archive.sqlite';archive=a.InputArchive(path,descriptor())
    archive.append_page(wire(first()),AT);confirm=archive._confirm_after_commit;replaced=[]
    def replace_valid_wire_then_confirm(*args):
        changed=json.dumps(first() if target_ordinal==0 else second(),indent=4).encode()
        with sqlite3.connect(path) as tamper:
            if changed_evidence=='wire':
                tamper.execute('UPDATE archive_pages SET wire=?,wire_sha=? WHERE ordinal=?',
                    (changed,hashlib.sha256(changed).hexdigest(),target_ordinal))
            else:
                receipt='2026-11-09T12:00:05Z'
                tamper.execute('UPDATE archive_pages SET received_at_utc=? WHERE ordinal=?',(receipt,target_ordinal))
                tamper.execute('UPDATE archive_records SET first_received_at_utc=? WHERE record_id=?',
                    (receipt,'r0' if target_ordinal==0 else 'r1'))
        replaced.append(True)
        return confirm(*args)
    monkeypatch.setattr(archive,'_confirm_after_commit',replace_valid_wire_then_confirm)
    with pytest.raises(a.StoreError) as error:archive.append_page(wire(second()),AT+timedelta(seconds=1))
    assert error.value.code=='STORE_RESULT_UNKNOWN' and replaced==[True];archive.close()
    # These self-consistent alterations are valid; request/prefix confirmation must reject them.
    with a.InputArchive(path,descriptor()) as reopened:assert reopened.ended and len(reopened.records)==2


@pytest.mark.parametrize('limit',['MAX_ACCEPTED_PAGES','MAX_ADMITTED_RECORDS'])
def test_admission_limits_leave_preexisting_page_and_cursor_unchanged(tmp_path,monkeypatch,limit):
    a=api();path=tmp_path/'archive.sqlite'
    with a.InputArchive(path,descriptor()) as archive:
        archive.append_page(wire(first()),AT);before=archive.records
        with monkeypatch.context() as patch:
            patch.setattr(a,limit,1)
            with pytest.raises(a.StoreError):archive.append_page(wire(second()),AT)
        assert archive.records==before and archive.committed_cursor=='delivery-1' and not archive.ended


@pytest.mark.parametrize('receipt',[
    datetime(2026,11,9,12),
    datetime(1,1,1,0,tzinfo=timezone(timedelta(hours=1))),
])
def test_naive_or_utc_overflow_receipt_does_not_change_archive(tmp_path,receipt):
    a=api();path=tmp_path/'archive.sqlite'
    with a.InputArchive(path,descriptor()) as archive:
        archive.append_page(wire(first()),AT);before=rows(archive._connection)
        with pytest.raises(a.StoreError) as error:archive.append_page(wire(second()),receipt)
        assert error.value.code=='INVALID_RECEIPT'
        assert rows(archive._connection)==before
        assert archive.committed_cursor=='delivery-1' and len(archive.records)==1 and not archive.ended


def test_expired_write_budget_rolls_back_and_retry_after_restoring_budget_succeeds(tmp_path,monkeypatch):
    a=api();path=tmp_path/'archive.sqlite'
    with a.InputArchive(path,descriptor()) as archive:
        archive.append_page(wire(first()),AT);before=rows(archive._connection)
        with monkeypatch.context() as patch:
            patch.setattr(a,'MAX_SECONDS',0)
            with pytest.raises(a.StoreError) as error:archive.append_page(wire(second()),AT+timedelta(seconds=1))
        assert error.value.code=='STORE_LIMIT' and rows(archive._connection)==before
        assert archive.committed_cursor=='delivery-1' and len(archive.records)==1 and not archive.ended
        archive.append_page(wire(second()),AT+timedelta(seconds=1))
        assert archive.committed_cursor=='delivery-2' and archive.ended


@pytest.mark.parametrize('sql',[
    "UPDATE archive_state SET cursor_key='\"wrong\"'", 'UPDATE archive_state SET ended=0',
    'UPDATE archive_state SET page_count=0', 'DELETE FROM archive_records',
    'DELETE FROM archive_pages', "UPDATE archive_pages SET wire_sha='wrong'",
    "UPDATE archive_pages SET canonical_sha='wrong'",
    "UPDATE archive_pages SET received_at_utc='2026-11-10T00:00:00Z'",
    "UPDATE archive_records SET first_received_at_utc='2026-11-10T00:00:00Z'",
    "UPDATE stream_meta SET descriptor_sha='wrong'",
])
def test_restart_rejects_state_record_page_receipt_and_descriptor_tampering(tmp_path,sql):
    a=api();path=tmp_path/'archive.sqlite'
    with a.InputArchive(path,descriptor()) as archive:archive.append_page(wire(page()),AT)
    with sqlite3.connect(path) as tamper:tamper.execute(sql)
    before=path.read_bytes()
    with pytest.raises(a.StoreError):a.InputArchive(path,descriptor())
    assert path.read_bytes()==before


@pytest.mark.parametrize('after_commit',[False,True])
def test_actual_process_exit_before_or_after_commit_recovers_atomic_state(tmp_path,after_commit):
    a=api();path=tmp_path/'archive.sqlite'
    with a.InputArchive(path,descriptor()):pass
    payload=tmp_path/'synthetic-page.json';payload.write_bytes(wire(page()))
    script='''import json,os,sys
from datetime import datetime,timezone
from aegis.input_archive import InputArchive
p=json.loads(open(sys.argv[1],"rb").read())
keys=("adapterId","adapterVersion","streamId","declaredOrigin","originEvidence","acquisitionMethod","sourceAccessMode")
s=InputArchive(sys.argv[2],{k:p[k] for k in keys})
commit=s._commit
def crash():
 if sys.argv[3]=="after":commit()
 os._exit(17)
s._commit=crash
s.append_page(json.dumps(p).encode(),datetime(2026,11,9,12,tzinfo=timezone.utc))
'''
    root=Path(__file__).resolve().parents[1]
    result=subprocess.run([sys.executable,'-c',script,str(payload),str(path),'after' if after_commit else 'before'],
        env=dict(os.environ,PYTHONPATH=str(root/'src')),capture_output=True,timeout=10)
    assert result.returncode==17
    with a.InputArchive(path,descriptor()) as reopened:
        assert len(reopened.records)==int(after_commit)
        assert (reopened.committed_cursor=='delivery-1')==after_commit
        reopened.append_page(wire(page()),AT+timedelta(seconds=1))
        assert len(reopened.records)==1


class ExecuteOnly:
    def __init__(self,connection):self.connection=connection
    def execute(self,*args):return self.connection.execute(*args)


def deny_writes(action,first,second,*_args):
    if action in (sqlite3.SQLITE_SELECT,sqlite3.SQLITE_READ,sqlite3.SQLITE_FUNCTION):return sqlite3.SQLITE_OK
    if action==sqlite3.SQLITE_PRAGMA and (first,second) in (
            ('application_id',None),('user_version',None),('page_count',None),('page_size',None),('integrity_check','1')):
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


@pytest.mark.parametrize('live_wal',[False,True])
def test_snapshot_helper_uses_readonly_connection_without_writes_or_lifecycle_ownership(tmp_path,monkeypatch,live_wal):
    a=api();path=tmp_path/'archive.sqlite';p=page([record(),record('provided',provided=True)],declaredOrigin='DEVICE')
    bound=descriptor(p);archive=a.InputArchive(path,bound);archive.append_page(wire(p),AT)
    if live_wal:assert path.with_name(path.name+'-wal').stat().st_size>32
    else:archive.close()
    reader=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,isolation_level=None)
    reader.execute('PRAGMA query_only=ON');reader.execute('PRAGMA trusted_schema=OFF');reader.execute('BEGIN')
    try:
        before=rows(reader);main=path.read_bytes();progress=[]
        reader.set_progress_handler(lambda:progress.append(1) or 0,1);reader.set_authorizer(deny_writes)
        with monkeypatch.context() as patch:
            patch.setattr(a.sqlite3,'connect',lambda *_a,**_k:pytest.fail('Helper opened a connection'))
            patch.setattr(a,'InputArchive',lambda *_a,**_k:pytest.fail('Helper constructed a writer'))
            snapshot=a.validate_input_archive_snapshot(ExecuteOnly(reader),a._Budget())
        assert reader.in_transaction and progress
        progress.clear();reader.execute('SELECT 1').fetchone();assert progress
        assert snapshot.descriptor==bound and snapshot.cursor=='delivery-1' and snapshot.ended
        assert len(snapshot.pages)==1 and [entry['record'] for entry in snapshot.records]==p['records']
        snapshot.records[0]['record']['signals'][0]['value']=999
        assert snapshot.records[0]['record']['signals'][0]['value']==-2.5
        assert rows(reader)==before and path.read_bytes()==main
        with pytest.raises(a.StoreError) as error:
            a.validate_input_archive_snapshot(reader,a._Budget(),expected_descriptor=descriptor(declaredOrigin='UNKNOWN'))
        assert error.value.code=='STORE_BINDING_MISMATCH' and reader.in_transaction
    finally:reader.set_authorizer(None);reader.close();archive.close()


def test_unexpected_sqlite_prefixed_trigger_is_rejected_by_readonly_schema_and_snapshot_helpers(tmp_path):
    a=api();path=tmp_path/'archive.sqlite'
    with a.InputArchive(path,descriptor()) as archive:archive.append_page(wire(page()),AT)
    with sqlite3.connect(path) as tamper:
        tamper.execute('CREATE TRIGGER sqliteXhidden AFTER INSERT ON archive_records BEGIN SELECT 1; END')
    reader=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,isolation_level=None)
    reader.execute('PRAGMA query_only=ON');reader.execute('PRAGMA trusted_schema=OFF');reader.execute('BEGIN')
    try:
        before=rows(reader);main=path.read_bytes();reader.set_authorizer(deny_writes)
        for check in (lambda:a.validate_input_archive_schema(ExecuteOnly(reader)),
                lambda:a.validate_input_archive_snapshot(ExecuteOnly(reader),a._Budget())):
            with pytest.raises(a.StoreError) as error:check()
            assert error.value.code=='STORE_INTEGRITY' and reader.in_transaction
            assert rows(reader)==before and path.read_bytes()==main
    finally:reader.set_authorizer(None);reader.close()


@pytest.mark.parametrize('kind',['adapter','other_sqlite','empty'])
def test_foreign_store_is_refused_before_sqlite_without_changing_files_or_companions(tmp_path,kind):
    a=api();path=tmp_path/'foreign.sqlite';writer=None
    if kind=='adapter':
        from aegis.adapter_input import SyntheticReplayAdapter
        from aegis.adapter_store import AdapterStore
        writer=AdapterStore(path,SyntheticReplayAdapter(fixture(adapter_page())))
        writer.commit_page(wire(adapter_page()),AT)
    elif kind=='other_sqlite':
        with sqlite3.connect(path) as connection:connection.execute('CREATE TABLE preserve(value TEXT)')
    else:path.write_bytes(b'')
    before={p.name:p.read_bytes() for p in tmp_path.iterdir()}
    try:
        with pytest.raises(a.StoreError):a.InputArchive(path,descriptor())
        assert {p.name:p.read_bytes() for p in tmp_path.iterdir()}==before
    finally:
        if writer is not None:writer.close()


@pytest.mark.parametrize('kind',['symlink','hardlink'])
def test_unsafe_archive_links_are_refused_and_original_is_unchanged(tmp_path,kind):
    a=api();target=tmp_path/'original.sqlite'
    with a.InputArchive(target,descriptor()) as archive:archive.append_page(wire(page()),AT)
    linked=tmp_path/'linked.sqlite'
    if kind=='symlink':linked.symlink_to(target)
    else:os.link(target,linked)
    before=target.read_bytes()
    with pytest.raises(a.StoreError):a.InputArchive(linked,descriptor())
    assert target.read_bytes()==before
