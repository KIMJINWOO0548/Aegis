"""Connection-only adapter-store checks use synthetic evidence and caller-owned reads."""

import hashlib
import sqlite3

import pytest

from test_adapter_input import AT, RUN, fixture, page, record, wire


def api():
    from aegis import adapter_store
    return adapter_store


def source(*pages):
    from aegis.adapter_input import SyntheticReplayAdapter
    return SyntheticReplayAdapter(fixture(*(pages or (page(),))))


def readonly(path):
    connection=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,isolation_level=None)
    connection.execute('PRAGMA query_only=ON')
    connection.execute('PRAGMA trusted_schema=OFF')
    connection.execute('BEGIN')
    return connection


def test_connection_validation_api_exists():
    assert callable(api().validate_adapter_schema)
    assert callable(api().validate_adapter_snapshot)


class ExecuteOnly:
    """A validator can query, but cannot own connection or transaction lifecycle."""
    def __init__(self,connection):self.connection=connection
    def execute(self,*args):return self.connection.execute(*args)


def deny_writes(action,first,second,*_args):
    if action in (sqlite3.SQLITE_SELECT,sqlite3.SQLITE_READ,sqlite3.SQLITE_FUNCTION):
        return sqlite3.SQLITE_OK
    if action==sqlite3.SQLITE_PRAGMA and (first,second) in (
            ('application_id',None),('user_version',None),('page_count',None),
            ('page_size',None),('integrity_check','1')):
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


def evidence(connection):
    return tuple((name,connection.execute(f'SELECT * FROM {name} ORDER BY 1').fetchall())
                 for name in api().SCHEMA)


@pytest.mark.parametrize('live_wal',[False,True])
def test_validates_on_readonly_connection_without_lifecycle_or_write_access(tmp_path,monkeypatch,live_wal):
    a=api();path=tmp_path/'store.sqlite';incoming=page([record(),record('provided',provided=True)])
    writer=a.AdapterStore(path,source(incoming))
    writer.commit_page(wire(incoming),AT)
    if live_wal:
        assert path.with_name(path.name+'-wal').stat().st_size>32
    else:writer.close()
    reader=readonly(path)
    try:
        before=evidence(reader);main=path.read_bytes();progress=[]
        reader.set_progress_handler(lambda:progress.append(1) or 0,1)
        reader.set_authorizer(deny_writes)
        with monkeypatch.context() as patch:
            patch.setattr(a.sqlite3,'connect',lambda *_a,**_k:pytest.fail('Validator opened a connection'))
            patch.setattr(a,'AdapterStore',lambda *_a,**_k:pytest.fail('Validator constructed a writer'))
            a.validate_adapter_schema(ExecuteOnly(reader))
            admitted=a.validate_adapter_snapshot(ExecuteOnly(reader),a._Budget())
        assert reader.in_transaction
        assert progress
        progress.clear();reader.execute('SELECT 1').fetchone();assert progress
        assert admitted.descriptor==source(incoming).descriptor
        assert admitted.cursor=='delivery-1' and admitted.ended
        assert admitted.wire_pages==(wire(incoming),)
        assert admitted.records[0]['record']['sourceMetadata']['observedAtUtc'] is None
        assert admitted.records[1]['record']['sourceMetadata']['sequence']==2**60
        copy=admitted.records;copy[0]['record']['signals'][0]['value']=999
        assert admitted.records[0]['record']['signals'][0]['value']==-2.5
        assert evidence(reader)==before and path.read_bytes()==main
    finally:
        reader.set_authorizer(None);reader.close();writer.close()


@pytest.mark.parametrize('binding',['descriptor','fixture','both'])
def test_expected_binding_mismatch_keeps_writer_error_contract(tmp_path,binding):
    a=api();path=tmp_path/'store.sqlite';adapter=source()
    with a.AdapterStore(path,adapter) as writer:writer.commit_page(wire(page()),AT)
    descriptor=adapter.descriptor.model_copy(update={'adapterId':'other'})
    kwargs={}
    if binding in ('descriptor','both'):kwargs['expected_descriptor']=descriptor
    if binding in ('fixture','both'):kwargs['expected_fixture_sha']='0'*64
    reader=readonly(path)
    try:
        with pytest.raises(a.StoreError) as error:
            a.validate_adapter_snapshot(reader,a._Budget(),**kwargs)
        assert error.value.code=='STORE_BINDING_MISMATCH' and reader.in_transaction
    finally:reader.close()


def test_matching_expected_binding_validates_and_descriptor_bootstrap_is_independent(tmp_path):
    a=api();path=tmp_path/'store.sqlite';adapter=source()
    with a.AdapterStore(path,adapter) as writer:writer.commit_page(wire(page()),AT)
    reader=readonly(path)
    try:
        admitted=a.validate_adapter_snapshot(reader,a._Budget(),expected_descriptor=adapter.descriptor,
            expected_fixture_sha=a._sha(a._encode(a._decode(adapter.fixture_wire,a.MAX_FIXTURE_BYTES))))
        assert admitted.descriptor==adapter.descriptor and admitted.cursor=='delivery-1'
        assert reader.in_transaction
    finally:reader.close()


@pytest.mark.parametrize('sql',[
    "UPDATE store_meta SET adapter_id='other'",
    "UPDATE store_meta SET stream_id='"+RUN+"'",
    "UPDATE store_meta SET fixture_sha='wrong'",
    "UPDATE store_meta SET fixture_wire_sha='wrong'",
    "UPDATE store_state SET cursor_key='\"wrong\"'",
    'UPDATE store_state SET page_count=0',
    'UPDATE store_state SET ended=0',
    "UPDATE adapter_pages SET canonical_sha='wrong'",
    "UPDATE adapter_pages SET wire_sha='wrong'",
    "UPDATE adapter_pages SET received_at_utc='2026-11-09T12:01:00Z'",
    "UPDATE adapter_records SET first_received_at_utc='2026-11-09T12:01:00Z'",
    'DELETE FROM adapter_records',
])
def test_stored_fixture_binding_and_evidence_tampering_are_rejected_without_changes(tmp_path,sql):
    a=api();path=tmp_path/'store.sqlite'
    with a.AdapterStore(path,source()) as writer:writer.commit_page(wire(page()),AT)
    with sqlite3.connect(path) as tamper:tamper.execute(sql)
    reader=readonly(path)
    try:
        before=evidence(reader);reader.set_authorizer(deny_writes)
        with pytest.raises(a.StoreError) as error:a.validate_adapter_snapshot(ExecuteOnly(reader),a._Budget())
        assert error.value.code=='STORE_INTEGRITY'
        assert reader.in_transaction and evidence(reader)==before
    finally:reader.set_authorizer(None);reader.close()


def test_self_consistent_changed_page_and_record_cannot_replace_fixture_response(tmp_path):
    a=api();path=tmp_path/'store.sqlite'
    with a.AdapterStore(path,source()) as writer:writer.commit_page(wire(page()),AT)
    changed=page([record(value=99)]);changed_wire=wire(changed)
    canonical=a._encode(changed);encoded=a._encode(changed['records'][0])
    with sqlite3.connect(path) as tamper:
        tamper.execute('UPDATE adapter_pages SET wire=?,wire_sha=?,canonical_sha=?',
            (changed_wire,hashlib.sha256(changed_wire).hexdigest(),hashlib.sha256(canonical).hexdigest()))
        tamper.execute('UPDATE adapter_records SET record_json=?,record_sha=?',
            (encoded.decode(),hashlib.sha256(encoded).hexdigest()))
    reader=readonly(path)
    try:
        with pytest.raises(a.StoreError) as error:a.validate_adapter_snapshot(reader,a._Budget())
        assert error.value.code=='STORE_INTEGRITY' and reader.in_transaction
    finally:reader.close()


def test_unexpected_schema_object_is_rejected_by_both_connection_helpers(tmp_path):
    a=api();path=tmp_path/'store.sqlite'
    with a.AdapterStore(path,source()):pass
    with sqlite3.connect(path) as tamper:
        tamper.execute('CREATE TRIGGER sqliteXhidden AFTER INSERT ON adapter_records BEGIN SELECT 1; END')
    reader=readonly(path)
    try:
        for check in (lambda:a.validate_adapter_schema(reader),
                lambda:a.validate_adapter_snapshot(reader,a._Budget())):
            with pytest.raises(a.StoreError) as error:check()
            assert error.value.code=='STORE_INTEGRITY' and reader.in_transaction
    finally:reader.close()
