import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import sqlite3

import pytest


AT=datetime(2026,11,9,12,tzinfo=timezone.utc)
STREAM='11111111-1111-4111-8111-111111111111'
RUN='22222222-2222-4222-8222-222222222222'


def api():
    from aegis import adapter_input
    return adapter_input


def record(key='r0',*,provided=False,value=-2.5):
    return {'recordId':key,'deviceId':'SYNTHETIC-01','sourceMetadata':{
        'observedAtUtc':'2026-11-09T13:00:00+01:00' if provided else None,
        'timestampStatus':'PROVIDED' if provided else 'NOT_PROVIDED',
        'sequence':2**60 if provided else None,'sequenceStatus':'PROVIDED' if provided else 'NOT_PROVIDED',
        'runId':RUN if provided else None,'runIdStatus':'PROVIDED' if provided else 'NOT_PROVIDED'},
        'signals':[{'signalKey':'synthetic.temperature','value':value,'unit':'degC','quality':'GOOD'}]}


def page(records=None,*,after=None,next='delivery-1',end=True,**kwargs):
    return {'schemaVersion':'adapter-input-1','adapterId':'synthetic-01','adapterVersion':'1.0',
        'streamId':STREAM,'declaredOrigin':'SYNTHETIC','originEvidence':'DECLARED_ONLY',
        'acquisitionMethod':'SOURCE_BUFFER_REPLAY','sourceAccessMode':'READ_ONLY',
        'afterToken':after,'nextToken':next,'endOfStream':end,'records':[record()] if records is None else records,**kwargs}


def wire(value):return json.dumps(value,ensure_ascii=False).encode()


def fixture(*pages,delays=None):
    return {'schemaVersion':'synthetic-adapter-fixture-1','adapterId':'synthetic-01','adapterVersion':'1.0',
        'streamId':STREAM,'pages':[{'afterToken':p['afterToken'],'response':p,'delayMs':(delays or [0]*len(pages))[i]} for i,p in enumerate(pages)]}


def admission():return api().AdapterAdmission('synthetic-01','1.0',STREAM)


def test_nullable_source_metadata_stays_unknown_and_host_receipt_is_separate():
    a=api();incoming=page([record(),record('provided',provided=True,value=2**60)])
    state=admission();r=state.admit(wire(incoming),AT)
    assert r['status']=='ADMITTED' and r['persistenceStatus']=='NOT_PERSISTED'
    originals=[x['record'] for x in state.records]
    assert originals==incoming['records']
    assert originals[0]['sourceMetadata']['observedAtUtc'] is None
    assert originals[0]['sourceMetadata']['sequence'] is None
    assert originals[1]['sourceMetadata']['sequence']==2**60
    assert originals[1]['sourceMetadata']['observedAtUtc']=='2026-11-09T13:00:00+01:00'
    assert all(x['firstReceivedAtUtc']=='2026-11-09T12:00:00Z' for x in state.records)
    assert state.cursor=='delivery-1'
    report=state.report()
    assert report['persistenceStatus']=='NOT_PERSISTED' and report['legacyArchiveIntegration']=='DISABLED'
    assert report['realEquipmentSupported'] is False and report['hardRealtimeGuaranteed'] is False
    assert report['counts']['missingSourceTimestampRecords']==report['counts']['missingSourceSequenceRecords']==1


@pytest.mark.parametrize('field,status', [('observedAtUtc','timestampStatus'),('sequence','sequenceStatus'),('runId','runIdStatus')])
def test_missing_keys_and_presence_contradictions_fail_without_progress(field,status):
    a=api()
    for mutation in ('missing','contradiction'):
        p=page();m=p['records'][0]['sourceMetadata']
        if mutation=='missing':del m[field]
        else:m[status]='PROVIDED'
        state=admission()
        with pytest.raises(a.AdapterError):state.admit(wire(p),AT)
        assert state.cursor is None and state.records==()


@pytest.mark.parametrize('change',[{'schemaVersion':'adapter-input-2'},{'adapterVersion':'2.0'},
    {'declaredOrigin':'DEVICE'},{'declaredOrigin':'UNKNOWN'},{'originEvidence':'HARDWARE_VERIFIED'},
    {'acquisitionMethod':'SNAPSHOT_READ'},{'sourceAccessMode':'READ_WRITE'},
    {'adapterId':'other'},{'streamId':RUN}])
def test_unsupported_or_mismatched_descriptor_never_enters_synthetic_admission(change):
    a=api();state=admission()
    with pytest.raises(a.AdapterError):state.admit(wire(page(**change)),AT)
    assert state.cursor is None and not state.records


@pytest.mark.parametrize('kind',['bool_sequence','negative_sequence','bad_time','naive_time','bool_value','lossy_integer','duplicate_signal','duplicate_record'])
def test_invalid_whole_page_cannot_partially_admit_the_good_record(kind):
    a=api();good=record('good');bad=record('bad',provided=True);p=page([good,bad])
    if kind=='bool_sequence':bad['sourceMetadata']['sequence']=True
    elif kind=='negative_sequence':bad['sourceMetadata']['sequence']=-1
    elif kind=='bad_time':bad['sourceMetadata']['observedAtUtc']='2026-11-09T99:00:00Z'
    elif kind=='naive_time':bad['sourceMetadata']['observedAtUtc']='2026-11-09T12:00:00'
    elif kind=='bool_value':bad['signals'][0]['value']=True
    elif kind=='lossy_integer':bad['signals'][0]['value']=2**53+1
    elif kind=='duplicate_signal':bad['signals']*=2
    else:bad['recordId']='good'
    state=admission()
    with pytest.raises(a.AdapterError):state.admit(wire(p),AT)
    assert state.cursor is None and state.records==()


def test_retransmission_canonicalizes_keys_and_preserves_first_receipt_without_rewinding():
    state=admission();first=page(end=False);state.admit(wire(first),AT)
    state.admit(wire(page([record('r1')],after='delivery-1',next='delivery-2')),AT+timedelta(seconds=1))
    before=deepcopy(state.records)
    again=state.admit(json.dumps(dict(reversed(list(first.items()))),indent=4).encode(),AT+timedelta(seconds=99))
    assert again['status']=='RETRANSMISSION' and again['acceptedRecords']==0
    assert state.cursor=='delivery-2' and state.records==before


def test_changed_retransmission_and_cross_page_record_conflict_are_atomic():
    a=api();state=admission();state.admit(wire(page(end=False)),AT);before=deepcopy(state.records)
    changed=page([record(value=10)],end=False)
    with pytest.raises(a.AdapterError):state.admit(wire(changed),AT)
    conflicting=page([record('new'),record(value=10)],after='delivery-1',next='delivery-2')
    with pytest.raises(a.AdapterError):state.admit(wire(conflicting),AT)
    assert state.cursor=='delivery-1' and state.records==before


def test_cross_page_exact_overlap_is_not_a_second_admission():
    state=admission();state.admit(wire(page(end=False)),AT)
    r=state.admit(wire(page([record(),record('new')],after='delivery-1',next='delivery-2')),AT+timedelta(seconds=1))
    assert r['acceptedRecords']==1 and r['duplicateRecords']==1
    assert state.records[0]['firstReceivedAtUtc']=='2026-11-09T12:00:00Z'


def test_input_and_returned_evidence_mutation_cannot_change_admitted_state():
    state=admission();p=page();state.admit(wire(p),AT);p['records'][0]['signals'][0]['value']=999
    exposed=state.records;exposed[0]['record']['sourceMetadata']['sequence']=999
    assert state.records[0]['record']==record()


@pytest.mark.parametrize('kind',['stationary_nonempty','empty_advances','token_loop','record_limit','page_limit'])
def test_cursor_and_capacity_errors_preserve_previous_state(monkeypatch,kind):
    a=api();state=admission();state.admit(wire(page(end=False)),AT);before=deepcopy(state.records)
    p=page([record('r1')],after='delivery-1',next='delivery-2')
    if kind=='stationary_nonempty':p['nextToken']='delivery-1'
    elif kind=='empty_advances':p['records']=[]
    elif kind=='token_loop':
        state.admit(wire(p),AT);before=deepcopy(state.records)
        p=page([record('r2')],after='delivery-2',next='delivery-1')
    elif kind=='record_limit':monkeypatch.setattr(a,'MAX_ADMITTED_RECORDS',1)
    else:monkeypatch.setattr(a,'MAX_ACCEPTED_PAGES',1)
    cursor=state.cursor
    with pytest.raises(a.AdapterError):state.admit(wire(p),AT)
    assert state.cursor==cursor and state.records==before


def test_synthetic_no_data_and_eof_remain_distinct_from_timeout():
    async def check():
        a=api();source=a.SyntheticReplayAdapter(fixture(page([],next=None,end=False)))
        session=a.AdapterSession(source)
        assert (await session.poll_once())['status']=='NO_NEW_DATA'
        assert session.cursor is None
        eof=a.AdapterSession(a.SyntheticReplayAdapter(fixture(page([],next=None,end=True))))
        assert (await eof.poll_once())['status']=='END_OF_SYNTHETIC_INPUT'
    asyncio.run(check())


def test_timeout_retries_the_same_delivery_cursor_and_cancels_the_builtin_read():
    async def check():
        a=api();source=a.SyntheticReplayAdapter(fixture(page(),delays=[50]));session=a.AdapterSession(source,timeout_ms=1,max_attempts=3)
        before=set(asyncio.all_tasks())
        with pytest.raises(a.AdapterError) as error:await session.poll_once()
        assert error.value.code=='ADAPTER_TIMEOUT' and source.read_calls==3
        assert session.cursor is None and not session.admission.records
        assert set(asyncio.all_tasks())==before
    asyncio.run(check())


def test_external_cancellation_leaves_no_admission_or_synthetic_read_task():
    async def check():
        a=api();source=a.SyntheticReplayAdapter(fixture(page(),delays=[1000]));session=a.AdapterSession(source,timeout_ms=1000)
        task=asyncio.create_task(session.poll_once());await asyncio.sleep(.01);task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
        assert session.cursor is None and not session.admission.records
    asyncio.run(check())


def test_only_builtin_adapter_executes_and_concurrent_polls_are_serialized():
    a=api()
    class Unknown:
        async def read(self,*args):raise AssertionError('must not execute')
    with pytest.raises(a.AdapterError):a.AdapterSession(Unknown())
    async def check():
        source=a.SyntheticReplayAdapter(fixture(page(end=False),page([record('r1')],after='delivery-1',next='delivery-2')))
        session=a.AdapterSession(source)
        await asyncio.gather(session.poll_once(),session.poll_once())
        assert session.cursor=='delivery-2' and len(session.admission.records)==2
    asyncio.run(check())


def test_legacy_simulator_contract_and_sqlite_original_cursor_gap_remain_separate(tmp_path):
    a=api()
    from aegis.archive import RawArchive
    from aegis.collector import RawSnapshot
    from test_archive import snapshot as legacy_snapshot,AT as LEGACY_AT
    async def seed():
        store=RawArchive(tmp_path/'legacy.sqlite');await store.start()
        await store.append([legacy_snapshot(0)],LEGACY_AT)
        await store.append([legacy_snapshot(4)],LEGACY_AT,gap=(1,3));await store.close()
    asyncio.run(seed());path=tmp_path/'legacy.sqlite';before=path.read_bytes()
    with sqlite3.connect(path) as c:rows={t:c.execute('select * from '+t).fetchall() for t in ('raw_batches','ingest_cursors','ingest_gaps')}
    state=admission();state.admit(wire(page()),AT)
    assert path.read_bytes()==before
    with sqlite3.connect(path) as c:assert rows=={t:c.execute('select * from '+t).fetchall() for t in rows}
    old=legacy_snapshot(0);old['source']='DEVICE'
    with pytest.raises(ValueError):RawSnapshot.model_validate(old)
    old=legacy_snapshot(0);del old['sequence']
    with pytest.raises(ValueError):RawSnapshot.model_validate(old)


def test_empty_terminal_page_retransmission_is_idempotent_and_conflicts_are_rejected():
    a=api();state=admission();empty=wire(page([],next=None,end=True))
    assert state.admit(empty,AT)['status']=='END_OF_SYNTHETIC_INPUT'
    assert state.admit(empty,AT+timedelta(seconds=1))['status']=='RETRANSMISSION'
    assert state.cursor is None and state.records==()
    with pytest.raises(a.AdapterError):state.admit(wire(page()),AT)


@pytest.mark.parametrize('bad_request',[object(),None,False])
def test_invalid_read_request_never_executes_or_exposes_an_unhandled_exception(bad_request):
    async def check():
        a=api();source=a.SyntheticReplayAdapter(fixture(page()))
        with pytest.raises(a.AdapterError):await source.read(bad_request)
        assert source.read_calls==0
    asyncio.run(check())


def test_wire_evidence_is_retained_separately_from_canonical_parsed_json():
    a=api();state=admission();original=json.dumps(page([record(provided=True)]),indent=3).encode()
    state.admit(original,AT)
    assert state.wire_pages==(original,)
    parsed=a.validate_page(original)
    assert parsed.original['records'][0]==record(provided=True)
    with pytest.raises(ValueError):parsed.model.records[0].sourceMetadata.sequence=0


@pytest.mark.parametrize('kind',['unreachable_origin','unreachable_page','missing_chain','token_loop'])
def test_entire_fixture_is_pinned_and_forms_one_reachable_chain(kind):
    a=api();first=page(end=False);second=page([record('r1')],after='delivery-1',next='delivery-2')
    if kind=='unreachable_origin':
        first['endOfStream']=True;second['declaredOrigin']='DEVICE'
    elif kind=='unreachable_page':first['endOfStream']=True
    elif kind=='missing_chain':first['nextToken']='not-present'
    else:second['endOfStream']=False;second['nextToken']='delivery-1'
    with pytest.raises(a.AdapterError):a.SyntheticReplayAdapter(fixture(first,second))


def test_synthetic_fixture_bytes_are_retained_separately_from_generated_response_wire():
    a=api();original=wire(fixture(page())).replace(b'-2.5',b'1.0000000000000000000000000000000000000001')
    adapter=a.SyntheticReplayAdapter(original)
    assert adapter.fixture_wire==original
    async def check():
        session=a.AdapterSession(adapter);await session.poll_once()
        assert session.admission.records[0]['record']['signals'][0]['value']==1.0
        assert session.report()['fixtureSha256']==__import__('hashlib').sha256(original).hexdigest()
        assert session.report()['responseEncoding']=='CANONICAL_PARSED_JSON_FLOAT64_SIGNALS'
    asyncio.run(check())


@pytest.mark.parametrize('cursor,limit',[(True,64),([],64),('',64),(None,True),(None,0),(None,65)])
def test_read_request_contract_is_strict_before_execution(cursor,limit):
    a=api()
    with pytest.raises(a.AdapterError):a.AdapterReadRequest(cursor,limit)


def test_host_receipt_is_captured_before_response_validation(monkeypatch):
    async def check():
        a=api();adapter=a.SyntheticReplayAdapter(fixture(page()));clock=[AT]
        original=a.validate_page
        def slow_validation(payload):
            clock[0]+=timedelta(seconds=1)
            return original(payload)
        monkeypatch.setattr(a,'validate_page',slow_validation)
        session=a.AdapterSession(adapter,utc_now=lambda:clock[0]);await session.poll_once()
        assert session.admission.records[0]['firstReceivedAtUtc']=='2026-11-09T12:00:00Z'
    asyncio.run(check())


def test_cooperative_timeout_can_retry_successfully_without_duplicate_admission(monkeypatch):
    async def check():
        a=api();adapter=a.SyntheticReplayAdapter(fixture(page()));sleep=asyncio.sleep;calls=[]
        async def delays(_seconds):
            calls.append(True)
            await sleep(.05 if len(calls)==1 else 0)
        monkeypatch.setattr(a.asyncio,'sleep',delays)
        session=a.AdapterSession(adapter,timeout_ms=10,max_attempts=2,utc_now=lambda:AT)
        result=await session.poll_once()
        assert result['acceptedRecords']==1 and adapter.read_calls==2
        assert session.cursor=='delivery-1' and len(session.admission.records)==1
    asyncio.run(check())
