import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx

from aegis.api import create_app
from aegis.adapter_input import SyntheticReplayAdapter
from aegis.adapter_store import AdapterStore
from test_archive import AT, RUN, snapshot

TOKEN = 'local-test-' + 'x' * 32
WINDOW = dict(receivedFromUtc='2026-11-20T00:00:00Z', receivedToUtc='2026-11-21T00:00:00Z',
              signals='synthetic.temperature,synthetic.optional,absent')


def test_configured_history_preserves_nullable_records_and_rejects_path_input(tmp_path):
    fixture = json.loads((Path(__file__).parents[1] / 'examples/adapter-input.synthetic.json').read_bytes())
    fixture['pages'][0]['response']['records'][0]['signals'][0]['value'] = 0
    path = tmp_path / 'adapter.sqlite'
    with AdapterStore(path, SyntheticReplayAdapter(json.dumps(fixture).encode())) as store:
        store.commit_page(json.dumps(fixture['pages'][0]['response']).encode(), datetime(2026,11,20,tzinfo=timezone.utc))
    before = path.read_bytes()
    app = create_app(TOKEN, stored_sources=[dict(storeId='nullable',kind='ADAPTER_STORE_V1',path=path)])

    async def check():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1',
                                     headers={'X-Aegis-Token':TOKEN}) as client:
            response = await client.get('/v1/stored-records', params=WINDOW)
            assert response.status_code == 200
            result = response.json(); row = result['records'][0]
            assert row['sourceMetadata']['observedAtUtc'] is None
            assert row['identity']['nativeKeyText'] == json.dumps(row['identity']['nativeKey'],separators=(',', ':'))
            assert row['signals'][0]['reading']['value'] == 0
            assert row['signals'][0]['reading']['valueText'] == '0'
            assert row['signals'][1]['reading']['value'] is None
            assert row['signals'][2] == dict(signalKey='absent',present=False,reading=None)
            assert result['adapterToRawV1WriteDelivery'] == 'NOT_CONNECTED'
            assert not result['schemaMigrationPerformed']
            assert str(path) not in response.text
            assert (await client.get('/v1/stored-records',params={**WINDOW,'path':str(path)})).status_code == 422
            assert (await client.get('/v1/stored-records',params={**WINDOW,'receivedToUtc':'2026-11-22T00:00:00Z'})).status_code == 422
            assert (await client.get('/v1/stored-records',params=WINDOW,headers={'X-Aegis-Token':'wrong'})).status_code == 401
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(TOKEN)),base_url='http://127.0.0.1',
                                     headers={'X-Aegis-Token':TOKEN}) as client:
            assert (await client.get('/v1/stored-records',params=WINDOW)).status_code == 404
    asyncio.run(check())
    assert path.read_bytes() == before


def test_selected_run_evidence_is_scoped_to_the_store_and_not_the_receipt_window(tmp_path):
    from aegis.archive import RawArchive
    from uuid import uuid4

    device = 'CMP-01.PMC-01'
    path = tmp_path / 'raw.sqlite'
    other = tmp_path / 'other.sqlite'
    next_run = str(uuid4())

    async def check():
        for database in (path,other):
            raw = RawArchive(database)
            await raw.start()
            await raw.append([snapshot(1,value=0),snapshot(2)],AT)
            await raw.append([snapshot(4)],AT,gap=(3,3))
            await raw.append([snapshot(1,run=next_run)],AT)
            await raw.close()
        hashes = [p.read_bytes() for p in (path,other)]
        app = create_app(TOKEN,stored_sources=[dict(storeId='selected',kind='RAW_V1',path=path),
                                              dict(storeId='other',kind='RAW_V1',path=other)])
        params = dict(receivedFromUtc='2026-10-18T00:00:00Z',receivedToUtc='2026-10-19T00:00:00Z',
                      deviceId=device,storeId='selected',runId=RUN,signals='actualFlowLpm,absent')
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1',
                                     headers={'X-Aegis-Token':TOKEN}) as client:
            async def request(**selection):
                return await client.get('/v1/stored-records',params={**params,**selection})
            response = await request()
            assert response.status_code == 200
            result = response.json()
            assert [r['identity']['nativeKey'][-1] for r in result['records']] == [1,2,4]
            first = result['records'][0]
            assert first['signals'][0]['reading']['valueText'] in ('0','0.0')
            assert not first['signals'][1]['present']
            assert result['runEvidence'] == dict(
                state='recorded',scope='SELECTED_RUN_SEQUENCE_ONLY',storeId='selected',deviceId=device,runId=RUN,
                storedRowsInReceivedWindow=3,recordedGaps=[dict(firstSequence='3', lastSequence='3', reason='REPLAY_OVERFLOW')],
                resendCount=None,ordinalGapState='not_assessed',ordinalGaps=None)
            empty = (await request(receivedFromUtc='2026-10-18T00:01:00Z')).json()
            assert empty['records']==[] and empty['runEvidence']['storedRowsInReceivedWindow']==0
            assert empty['runEvidence']['recordedGaps']==result['runEvidence']['recordedGaps']
            later = (await request(runId=next_run)).json()
            assert len(later['records'])==1 and later['runEvidence']['recordedGaps']==[]
            missing = (await request(storeId='absent')).json()
            assert missing['records']==[] and missing['runEvidence']['state']=='not_found'
            assert missing['runEvidence']['recordedGaps'] is None
            assert (await request(runId='not-a-run')).status_code==422
            assert (await client.get('/v1/stored-records',params={k:v for k,v in params.items() if k!='deviceId'})).status_code==422
        assert [p.read_bytes() for p in (path,other)]==hashes

    asyncio.run(check())
