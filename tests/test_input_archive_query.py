"""All payloads here are synthetic, including simulated DEVICE declarations."""

from datetime import timedelta
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from test_stored_records import AT, START, END, sources as old_sources

ROOT=Path(__file__).resolve().parents[1]
HEADERS=('adapterId','adapterVersion','streamId','declaredOrigin','originEvidence','acquisitionMethod','sourceAccessMode')


def api():
    from aegis import stored_records
    return stored_records


def case(origin='SYNTHETIC',method='SOURCE_BUFFER_REPLAY'):
    f=json.loads((ROOT/'examples/adapter-input.synthetic.json').read_bytes())
    pages=[entry['response'] for entry in f['pages']]
    for page in pages:
        page['schemaVersion']='archive-write-1';page['declaredOrigin']=origin;page['acquisitionMethod']=method
    return {key:pages[0][key] for key in HEADERS},pages


def encode(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()


def seed(path,origin='SYNTHETIC',method='SOURCE_BUFFER_REPLAY',both=True):
    from aegis.input_archive import InputArchive
    d,pages=case(origin,method)
    with InputArchive(path,d) as store:
        store.append_page(encode(pages[0]),AT)
        if both:store.append_page(encode(pages[1]),AT+timedelta(seconds=1))
    return d,pages


def spec(path,label='new'):
    return dict(storeId=label,kind='INPUT_ARCHIVE_V1',path=path)


def query(s,**kw):return api().query_stored_records(s,START,END,**kw)


def test_new_kind_is_an_explicit_accepted_read_contract():
    assert 'INPUT_ARCHIVE_V1' in api().KINDS


@pytest.mark.parametrize('origin',['SYNTHETIC','DEVICE','UNKNOWN'])
@pytest.mark.parametrize('method',['SOURCE_BUFFER_REPLAY','SNAPSHOT_READ'])
def test_write_requery_preserves_original_nulls_source_declaration_and_receipt(tmp_path,origin,method):
    path=tmp_path/'input.db';d,pages=seed(path,origin,method);before=path.read_bytes()
    r=query([spec(path)],signals='synthetic.optional,absent')
    assert r['inputArchiveReadSupport']=='INPUT_ARCHIVE_V1'
    assert r['sourceAcknowledgement']=='NOT_SENT' and not r['schemaMigrationPerformed']
    assert r['adapterToRawV1WriteDelivery']=='NOT_CONNECTED'
    assert len(r['records'])==2 and r['sourceCoverage']=='NOT_ASSERTED'
    for index,item in enumerate(r['records']):
        original=pages[index]['records'][0]
        assert item['sourceMetadata']==original['sourceMetadata']
        assert item['provenance']==dict(declaredOrigin=origin,originEvidence='DECLARED_ONLY',
            declarationSource='ARCHIVE_WRITE_PAGE',producerDeclaration=origin,
            acquisitionMethod=method,sourceAccessMode='READ_ONLY',hardwareIdentityVerified=False)
        assert item['identity']['nativeKeyKind']=='ARCHIVE_DELIVERY_STREAM_RECORD'
        assert item['identity']['nativeKey']==[d['adapterId'],d['adapterVersion'],d['streamId'],original['recordId']]
        assert item['evidence']['recordSha256']==hashlib.sha256(encode(original)).hexdigest()
        assert item['evidence']['pageWireSha256']==hashlib.sha256(encode(pages[index])).hexdigest()
        assert item['evidence']['representation']=='ARCHIVE_CANONICAL_RECORD_JSON'
        assert item['hierarchy']==dict(equipmentId=None,moduleId=None,evidence='NOT_RECORDED')
    assert r['records'][0]['firstReceivedAtUtc']=='2026-10-18T00:00:00Z'
    assert r['records'][1]['sourceMetadata']['sequence']==2**60
    assert r['records'][1]['sourceMetadata']['observedAtUtc']=='2026-11-09T13:00:00+01:00'
    assert r['records'][0]['signals'][0]['reading']==dict(value=None,unit='count',quality='MISSING')
    assert r['records'][0]['signals'][1]==dict(signalKey='absent',present=False,reading=None)
    assert r['sources'][0]['writeContractVersion']=='archive-write-1'
    assert r['sources'][0]['gapEvidence']['count'] is None
    assert path.read_bytes()==before and str(path) not in json.dumps(r)
    assert 'synthetic-token' not in json.dumps(r)


def test_three_formats_share_query_and_keep_separate_identities(tmp_path):
    ss=old_sources(tmp_path);new=tmp_path/'input.db';seed(new)
    r=query([*ss,spec(new)])
    assert len(r['records'])==6
    assert {x['identity']['storeKind'] for x in r['records']}=={'RAW_V1','ADAPTER_STORE_V1','INPUT_ARCHIVE_V1'}
    assert len({json.dumps(x['identity'],sort_keys=True) for x in r['records']})==6


@pytest.mark.parametrize('sql',[
    "update archive_state set cursor_key='null'",
    "update archive_records set record_json='{}'",
    "update archive_records set first_received_at_utc='2026-10-18T00:00:09Z'",
    "update archive_pages set wire_sha='invalid'",
    "update stream_meta set descriptor_json='{}'",
    'create table sqliteXhidden(value)',
])
def test_invalid_new_store_fails_entire_query_and_preserves_files(tmp_path,sql):
    ss=old_sources(tmp_path);new=tmp_path/'input.db';seed(new)
    with sqlite3.connect(new) as c:c.execute(sql)
    before=new.read_bytes()
    with pytest.raises(api().QueryError) as e:query([*ss,spec(new)])
    assert e.value.code=='SOURCE_INVALID' and new.read_bytes()==before


def test_new_kind_does_not_accept_fixture_binding_option(tmp_path):
    with pytest.raises(api().QueryError) as e:query([dict(spec(tmp_path/'missing'),expectedFixtureSha256='0'*64)])
    assert e.value.code=='INVALID_QUERY'


def test_query_reader_uses_no_writer_and_pins_new_archive_wal_prefix(tmp_path,monkeypatch):
    from aegis.input_archive import InputArchive
    path=tmp_path/'input.db';d,pages=seed(path,both=False);writer=InputArchive(path,d)
    original=api().validate_input_archive_snapshot;calls=[]
    def interleave(c,b,**kw):
        result=original(c,b,**kw)
        writer.append_page(encode(pages[1]),AT+timedelta(seconds=1));calls.append(1);return result
    monkeypatch.setattr(api(),'validate_input_archive_snapshot',interleave)
    monkeypatch.setattr(InputArchive,'__init__',lambda *a,**kw:pytest.fail('reader constructed writer'))
    try:
        r=query([spec(path)]);assert len(r['records'])==1 and calls==[1]
        assert len(writer.records)==2
    finally:writer.close()


def test_write_failure_reopen_retry_then_actual_common_query_preserves_first_receipt(tmp_path,monkeypatch):
    from aegis.input_archive import InputArchive,StoreError
    path=tmp_path/'input.db';d,pages=seed(path,both=False);writer=InputArchive(path,d)
    original=writer._write_candidate
    def fail(*a,**kw):original(*a,**kw);raise sqlite3.OperationalError('injected write fault')
    monkeypatch.setattr(writer,'_write_candidate',fail)
    with pytest.raises(StoreError):writer.append_page(encode(pages[1]),AT+timedelta(seconds=1))
    writer.close();first=query([spec(path)]);assert len(first['records'])==1
    with InputArchive(path,d) as reopened:
        reopened.append_page(encode(pages[1]),AT+timedelta(seconds=2))
        reopened.append_page(encode(pages[1]),AT+timedelta(seconds=5))
    result=query([spec(path)])
    assert result['records'][0]==first['records'][0]
    assert len(result['records'])==2 and result['records'][1]['firstReceivedAtUtc']=='2026-10-18T00:00:02Z'


def test_new_write_target_refuses_old_raw_and_adapter_without_mutation(tmp_path):
    from aegis.input_archive import InputArchive,StoreError
    ss=old_sources(tmp_path);d,_=case()
    for source in ss:
        before=source['path'].read_bytes();companions={p.name:p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}
        with pytest.raises(StoreError):InputArchive(source['path'],d)
        assert source['path'].read_bytes()==before
        assert {p.name:p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}==companions
    assert len(query(ss)['records'])==4
