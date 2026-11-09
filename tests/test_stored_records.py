import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from test_archive import AT, RUN, snapshot

ROOT = Path(__file__).resolve().parents[1]
START = '2026-10-18T00:00:00Z'
END = '2026-10-19T00:00:00Z'


def api():
    from aegis import stored_records
    return stored_records


def raw(path, *, gap=False):
    from aegis.archive import RawArchive
    async def write():
        s=RawArchive(path); await s.start()
        await s.append([snapshot(0),snapshot(1,value=None,quality='MISSING')],AT,
                       equipment_id='CMP-01',module_id='PMC-01')
        if gap:await s.append([snapshot(4)],AT,gap=(2,3))
        await s.close()
    asyncio.run(write())


def adapter(path, *, both=True, fixture=None):
    from aegis.adapter_input import SyntheticReplayAdapter, _encode
    from aegis.adapter_store import AdapterStore
    f=fixture or json.loads((ROOT/'examples/adapter-input.synthetic.json').read_bytes())
    a=SyntheticReplayAdapter(_encode(f)); s=AdapterStore(path,a)
    s.commit_page(_encode(f['pages'][0]['response']),AT)
    if both:s.commit_page(_encode(f['pages'][1]['response']),AT+timedelta(seconds=1))
    s.close();return f


def sources(tmp_path, *, both=True, gap=False):
    r=tmp_path/'raw.db';a=tmp_path/'adapter.db';raw(r,gap=gap);adapter(a,both=both)
    return [{'storeId':'legacy','kind':'RAW_V1','path':r},
            {'storeId':'nullable','kind':'ADAPTER_STORE_V1','path':a}]


def query(s, **kw):return api().query_stored_records(s,START,END,**kw)


def test_dual_projection_preserves_null_originals_receipt_and_namespaced_evidence(tmp_path):
    ss=sources(tmp_path,gap=True);before=[s['path'].read_bytes() for s in ss]
    r=query(ss); assert r['schemaVersion']=='stored-record-query-1'
    assert r['timeBasis']=='FIRST_RECEIPT' and len(r['records'])==5
    assert r['adapterToRawV1WriteDelivery']=='NOT_CONNECTED'
    assert r['sourceAcknowledgement']=='NOT_SENT' and not r['schemaMigrationPerformed']
    assert r['snapshotConsistency']=='PER_STORE_SNAPSHOT_NO_DISTRIBUTED_ATOMICITY'
    assert r['sourceCoverage']=='NOT_ASSERTED' and r['selectionComplete']
    aa=[x for x in r['records'] if x['identity']['storeId']=='nullable']
    n=aa[0]; assert n['sourceMetadata']==dict(observedAtUtc=None,timestampStatus='NOT_PROVIDED',
        sequence=None,sequenceStatus='NOT_PROVIDED',runId=None,runIdStatus='NOT_PROVIDED')
    assert n['firstReceivedAtUtc']=='2026-10-18T00:00:00Z'
    assert n['provenance']['acquisitionMethod']=='SOURCE_BUFFER_REPLAY'
    assert n['provenance']['originEvidence']=='DECLARED_ONLY'
    assert n['provenance']['hardwareIdentityVerified'] is False
    assert n['hierarchy']=={'equipmentId':None,'moduleId':None,'evidence':'NOT_RECORDED'}
    assert aa[1]['sourceMetadata']['sequence']==2**60
    assert aa[1]['sourceMetadata']['observedAtUtc']=='2026-11-09T13:00:00+01:00'
    legacy=[x for x in r['records'] if x['identity']['storeId']=='legacy'][0]
    assert legacy['sourceMetadata']['runId']==RUN
    assert legacy['provenance']['producerDeclaration']=='SIMULATOR'
    assert legacy['provenance']['declaredOrigin']=='SYNTHETIC'
    assert legacy['provenance']['acquisitionMethod']=='NOT_RECORDED'
    assert legacy['provenance']['sourceAccessMode']=='NOT_RECORDED'
    assert legacy['hierarchy']['equipmentId']=='CMP-01'
    assert r['sources'][0]['gapEvidence']['count']==1
    assert r['sources'][1]['gapEvidence']['count'] is None
    assert [s['path'].read_bytes() for s in ss]==before
    encoded=json.dumps(r);assert str(tmp_path) not in encoded and 'synthetic-token' not in encoded


def test_missing_tag_and_present_missing_are_distinct(tmp_path):
    ss=sources(tmp_path)
    r=query(ss,signals='synthetic.optional,not.here')
    n=next(x for x in r['records'] if x['identity']['storeId']=='nullable')
    assert n['signals']==[{'signalKey':'synthetic.optional','present':True,
                          'reading':{'value':None,'unit':'count','quality':'MISSING'}},
                         {'signalKey':'not.here','present':False,'reading':None}]


def test_window_uses_receipt_utc_half_open_and_not_original_time(tmp_path):
    ss=sources(tmp_path)
    r=api().query_stored_records(ss,'2026-10-18T02:00:00+02:00','2026-10-18T00:00:01Z')
    assert len(r['records'])==3
    r=api().query_stored_records(ss,'2026-10-18T00:00:01Z','2026-10-18T00:00:02Z')
    assert len(r['records'])==1 and r['records'][0]['sourceMetadata']['sequence']==2**60


def test_namespaces_do_not_collapse_equal_physical_looking_samples(tmp_path):
    ss=sources(tmp_path,both=False)
    f=json.loads((ROOT/'examples/adapter-input.synthetic.json').read_bytes())
    rec=f['pages'][0]['response']['records'][0];rec['deviceId']='CMP-01.PMC-01'
    rec['sourceMetadata']=dict(observedAtUtc=snapshot(0)['observedAtUtc'],timestampStatus='PROVIDED',
        sequence=0,sequenceStatus='PROVIDED',runId=RUN,runIdStatus='PROVIDED')
    rec['signals']=snapshot(0)['signals'];p=tmp_path/'other.db';adapter(p,both=False,fixture=f)
    ss[1]['path']=p;r=query(ss,device_id='CMP-01.PMC-01')
    assert len(r['records'])==3 and len({json.dumps(x['identity'],sort_keys=True) for x in r['records']})==3


def test_long_colon_adapter_device_selector(tmp_path):
    f=json.loads((ROOT/'examples/adapter-input.synthetic.json').read_bytes());device='x:'+'a'*126
    f['pages'][0]['response']['records'][0]['deviceId']=device
    p=tmp_path/'a.db';adapter(p,both=False,fixture=f)
    assert len(query([dict(storeId='a',kind='ADAPTER_STORE_V1',path=p)],device_id=device)['records'])==1


@pytest.mark.parametrize('sql',[
    "update store_state set cursor_key='null'",
    "update adapter_records set first_received_at_utc='2026-10-18T00:00:03Z'",
    "update adapter_records set record_json='{}'",
    "update adapter_pages set wire_sha='invalid'",
    'create table sqliteXhidden(x)',
])
def test_adapter_corruption_fails_whole_query_without_writes(tmp_path,sql):
    ss=sources(tmp_path)
    with sqlite3.connect(ss[1]['path']) as c:c.execute(sql)
    before=[s['path'].read_bytes() for s in ss]
    with pytest.raises(api().QueryError) as e:query(ss)
    assert e.value.code=='SOURCE_INVALID'
    assert [s['path'].read_bytes() for s in ss]==before


@pytest.mark.parametrize('sql',[
    'update ingest_cursors set sequence=999',
    "update raw_batches set received_at_utc='invalid'",
    'create table sqliteXhidden(x)',
    'create view hidden as select 1',
])
def test_raw_corruption_and_hidden_objects_fail_closed(tmp_path,sql):
    ss=sources(tmp_path)
    with sqlite3.connect(ss[0]['path']) as c:c.execute(sql)
    with pytest.raises(api().QueryError) as e:query(ss)
    assert e.value.code=='SOURCE_INVALID'


def test_binding_pin_and_duplicate_physical_source(tmp_path):
    ss=sources(tmp_path)
    ss[1]['expectedFixtureSha256']='0'*64
    with pytest.raises(api().QueryError) as e:query(ss)
    assert e.value.code=='SOURCE_BINDING_MISMATCH'
    del ss[1]['expectedFixtureSha256'];ss.append(dict(ss[0],storeId='alias'))
    with pytest.raises(api().QueryError) as e:query(ss)
    assert e.value.code=='DUPLICATE_SOURCE'


@pytest.mark.parametrize('start,end',[
    ('2026-10-18','2026-10-19T00:00:00Z'),
    ('2026-10-18T00:00:00Z','2026-10-18T00:00:00Z'),
    ('2026-10-18T00:00:00Z','2026-10-20T00:00:00Z'),
    ('0001-01-01T00:00:00+23:00','0001-01-02T00:00:00Z'),
    ('9999-12-31T00:00:00Z','9999-12-31T23:59:59-23:00'),
])
def test_invalid_window_before_sources_open(tmp_path,start,end):
    with pytest.raises(api().QueryError) as e:
        api().query_stored_records([dict(storeId='a',kind='RAW_V1',path=tmp_path/'missing')],start,end)
    assert e.value.code=='INVALID_QUERY'


@pytest.mark.parametrize('kw',[{'max_records':True},{'max_records':0},{'max_records':65},
    {'signals':''},{'signals':'x,x'},{'signals':'x,'},{'device_id':'bad/path'}])
def test_invalid_selection(tmp_path,kw):
    with pytest.raises(api().QueryError) as e:query([],**kw)
    assert e.value.code=='INVALID_QUERY'


def test_limits_fail_instead_of_truncating(tmp_path,monkeypatch):
    ss=sources(tmp_path)
    with pytest.raises(api().QueryError) as e:query(ss,max_records=1)
    assert e.value.code=='QUERY_LIMIT'
    monkeypatch.setattr(api(),'MAX_RAW_ROWS',1)
    with pytest.raises(api().QueryError) as e:query(ss)
    assert e.value.code=='QUERY_LIMIT'


def test_reader_does_not_construct_writer_and_keeps_pinned_wal_snapshot(tmp_path,monkeypatch):
    from aegis.adapter_input import SyntheticReplayAdapter,_encode
    from aegis.adapter_store import AdapterStore
    p=tmp_path/'a.db';f=adapter(p,both=False)
    writer=AdapterStore(p,SyntheticReplayAdapter(_encode(f)))
    original=api().validate_adapter_snapshot;calls=[]
    def validate(c,b,**kw):
        result=original(c,b,**kw)
        writer.commit_page(_encode(f['pages'][1]['response']),AT+timedelta(seconds=1));calls.append(1)
        return result
    monkeypatch.setattr(api(),'validate_adapter_snapshot',validate)
    monkeypatch.setattr(AdapterStore,'__init__',lambda *a,**kw:(_ for _ in ()).throw(AssertionError('writer constructed')))
    try:
        r=query([dict(storeId='a',kind='ADAPTER_STORE_V1',path=p)])
        assert len(r['records'])==1 and calls==[1] and len(writer.records)==2
    finally:writer.close()


def test_rollback_restart_and_retry_keep_first_receipt_and_provenance(tmp_path,monkeypatch):
    from aegis.adapter_input import SyntheticReplayAdapter,_encode
    from aegis.adapter_store import AdapterStore,StoreError
    p=tmp_path/'a.db';f=adapter(p,both=False);a=SyntheticReplayAdapter(_encode(f));s=AdapterStore(p,a)
    original=s._write_candidate
    def fail(*args,**kw):original(*args,**kw);raise sqlite3.OperationalError('injected full')
    monkeypatch.setattr(s,'_write_candidate',fail)
    with pytest.raises(StoreError):s.commit_page(_encode(f['pages'][1]['response']),AT+timedelta(seconds=1))
    s.close();ss=[dict(storeId='a',kind='ADAPTER_STORE_V1',path=p)]
    before=query(ss);assert len(before['records'])==1
    s=AdapterStore(p,a);s.commit_page(_encode(f['pages'][1]['response']),AT+timedelta(seconds=2))
    s.commit_page(_encode(f['pages'][1]['response']),AT+timedelta(seconds=5));s.close()
    after=query(ss);assert len(after['records'])==2
    assert before['records'][0]==after['records'][0]
    assert after['records'][1]['firstReceivedAtUtc']=='2026-10-18T00:00:02Z'


def test_read_authorizer_observes_no_dml_and_inherited_records_exact_hash(tmp_path,monkeypatch):
    ss=sources(tmp_path);original=api().sqlite3.connect;denied=[]
    def connect(*a,**kw):
        assert 'mode=ro' in a[0]
        c=original(*a,**kw)
        def auth(action,*args):
            if action in {sqlite3.SQLITE_INSERT,sqlite3.SQLITE_UPDATE,sqlite3.SQLITE_DELETE,
                          sqlite3.SQLITE_CREATE_TABLE,sqlite3.SQLITE_DROP_TABLE}:
                denied.append(action);return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK
        c.set_authorizer(auth);return c
    monkeypatch.setattr(api().sqlite3,'connect',connect)
    r=query(ss);assert not denied
    # Original payload representation, not the normalized common projection.
    c=original(ss[0]['path']);encoded=c.execute('select payload_json from raw_batches order by sequence').fetchone()[0];c.close()
    n=next(x for x in r['records'] if x['identity']['storeId']=='legacy')
    assert n['evidence']['recordSha256']==hashlib.sha256(encoded.encode()).hexdigest()


def test_empty_adapter_snapshot_is_not_source_coverage_or_ack(tmp_path):
    from aegis.adapter_input import SyntheticReplayAdapter
    from aegis.adapter_store import AdapterStore
    p=tmp_path/'empty.db'
    s=AdapterStore(p,SyntheticReplayAdapter((ROOT/'examples/adapter-input.synthetic.json').read_bytes()));s.close()
    r=query([dict(storeId='empty',kind='ADAPTER_STORE_V1',path=p)])
    assert r['records']==[] and r['selectionComplete']
    assert r['sourceCoverage']=='NOT_ASSERTED' and r['sourceAcknowledgement']=='NOT_SENT'
    assert r['sources'][0]['gapEvidence']['count'] is None


def test_valid_fixture_pin_is_reported_as_binding_not_authentication(tmp_path):
    from aegis.adapter_input import _encode
    ss=sources(tmp_path);f=json.loads((ROOT/'examples/adapter-input.synthetic.json').read_bytes())
    ss[1]['expectedFixtureSha256']=hashlib.sha256(_encode(f)).hexdigest()
    r=query(ss)
    assert r['sources'][1]['bindingEvidence']=='EXPECTED_FIXTURE_SHA_MATCHED'
    assert all(x['provenance']['hardwareIdentityVerified'] is False for x in r['records'])


@pytest.mark.parametrize('constant,value',[('MAX_OUTPUT_BYTES',1),('MAX_SECONDS',0),('MAX_SQL_STEPS',0)])
def test_resource_limits_fail_whole_query(tmp_path,monkeypatch,constant,value):
    ss=sources(tmp_path);monkeypatch.setattr(api(),constant,value)
    with pytest.raises(api().QueryError) as e:query(ss)
    assert e.value.code=='QUERY_LIMIT'


@pytest.mark.parametrize('kind',['fifo','symlink','hardlink','directory'])
def test_unsafe_source_is_rejected_before_sqlite_open(tmp_path,monkeypatch,kind):
    import os
    p=tmp_path/'unsafe'
    if kind=='fifo':os.mkfifo(p)
    elif kind=='directory':p.mkdir()
    else:
        target=tmp_path/'regular';target.write_bytes(b'unchanged')
        if kind=='symlink':p.symlink_to(target)
        else:p.hardlink_to(target)
    monkeypatch.setattr(api().sqlite3,'connect',lambda *a,**k:pytest.fail('unsafe SQLite open'))
    with pytest.raises(api().QueryError) as e:query([dict(storeId='a',kind='RAW_V1',path=p)])
    assert e.value.code=='SOURCE_UNAVAILABLE'


def test_legacy_wal_snapshot_pins_rows_cursor_gap_together(tmp_path,monkeypatch):
    ss=sources(tmp_path);writer=sqlite3.connect(ss[0]['path'],isolation_level=None)
    writer.execute('pragma journal_mode=wal')
    inspect=api()._inspect;calls=[]
    def interleave(c,b):
        result=inspect(c,b);row=snapshot(4)
        writer.execute('begin immediate')
        writer.execute('insert into raw_batches values(?,?,?,?,?,?,?,?)',
            (row['deviceId'],RUN,4,row['observedAtUtc'],AT.isoformat(),None,None,json.dumps(row)))
        writer.execute('update ingest_cursors set sequence=4')
        writer.execute("insert into ingest_gaps values(?,?,2,3,'REPLAY_OVERFLOW')",(row['deviceId'],RUN))
        writer.commit();calls.append(1);return result
    monkeypatch.setattr(api(),'_inspect',interleave)
    try:
        r=query(ss);assert calls==[1] and r['sources'][0]['scannedRecords']==2
        assert r['sources'][0]['gapEvidence']['count']==0
        assert writer.execute('select count(*) from raw_batches').fetchone()==(3,)
        assert writer.execute('select sequence from ingest_cursors').fetchone()==(4,)
    finally:writer.close()


def test_gap_evidence_is_not_inferred_as_window_completeness(tmp_path):
    ss=sources(tmp_path,gap=True)
    r=api().query_stored_records(ss,'2026-10-18T01:00:00Z','2026-10-18T02:00:00Z')
    assert r['records']==[]
    assert r['sources'][0]['gapEvidence']==dict(kind='LEGACY_RECORDED_SEQUENCE_GAPS',
        count=1,scope='ENTIRE_STORE',timeCoverage='NOT_INFERRED')
    assert r['sourceCoverage']=='NOT_ASSERTED'
