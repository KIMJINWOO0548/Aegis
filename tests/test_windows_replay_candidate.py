import asyncio
import os
from unittest.mock import patch
import pytest
from test_adapter_input import fixture, page, record, wire, AT
from aegis.adapter_input import SyntheticReplayAdapter, AdapterError
from aegis.windows_adapter_candidate import WindowsAdapterCandidate as Store
from aegis.windows_replay_candidate import WindowsReplaySessionCandidate as Session
pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Windows native handles')


def test_restart_view_and_resume_from_committed_cursor(tmp_path):
    first = page(end=False)
    second = page([record('r1',provided=True)],after='delivery-1',next='delivery-2')
    f = fixture(first,second)
    p=tmp_path/'source'
    with Store(p, fixture=f) as store:
        assert store.state_view()['stateSource']=='EMPTY'
        store.commit_page(wire(first),AT)
        assert store.state_view()['stateSource']=='SESSION_SYNTHETIC'
    with Store(p) as store:
        restored = store.state_view()
        assert restored['stateSource']=='RESTORED_LAST_KNOWN'
        assert restored['lastReceiptUtc']=='2026-11-09T12:00:00Z'
        assert not restored['isLive']
        source=SyntheticReplayAdapter(f)
        session=Session(source,store,utc_now=lambda:AT)
        async def run():
            result=await session.poll_once()
            assert result['status']=='LOCAL_FLUSH_CONFIRMED'
            assert session.state_view()['committedCursor']=='delivery-2'
            assert len(session.state_view()['records'])==2
            await session.poll_once()
            assert source.read_calls==1
        asyncio.run(run())
        assert not session.state_view()['isLive']
        assert session.state_view()['stateSource']=='SESSION_SYNTHETIC'
    with Store(p) as store:
        assert store.state_view()['stateSource']=='RESTORED_LAST_KNOWN'
        assert len(store.state_view()['records'])==2


def test_retransmission_does_not_mark_restored_state_as_new(tmp_path):
    p=tmp_path/'source'
    with Store(p,fixture=fixture(page())) as store:
        store.commit_page(wire(page()),AT)
    with Store(p) as store:
        before=store.state_view()
        store.commit_page(wire(page()),AT)
        assert store.state_view()==before


def test_binding_mismatch_rejected_without_mutation(tmp_path):
    with Store(tmp_path/'source',fixture=fixture(page())) as store:
        with pytest.raises(AdapterError,match='STORE_BINDING_MISMATCH'):
            Session(SyntheticReplayAdapter(fixture(page([record(value=99)]))),store)
        assert store.committed_cursor is None


def test_timeout_and_cancel_preserve_last_known_state(tmp_path):
    first=page(end=False)
    second=page([record('r1',provided=True)],after='delivery-1',next='delivery-2')
    f=fixture(first,second,delays=[0,1000]);p=tmp_path/'source'
    with Store(p,fixture=f) as store: store.commit_page(wire(first),AT)
    with Store(p) as store:
        before=store.state_view()
        session=Session(SyntheticReplayAdapter(f),store,timeout_ms=5)
        async def run():
            with pytest.raises(AdapterError,match='ADAPTER_TIMEOUT'): await session.poll_once()
            assert session.state_view()['lastReadError']=='ADAPTER_TIMEOUT'
            assert store.state_view()==before
            task=asyncio.create_task(session.poll_once())
            await asyncio.sleep(0)
            task.cancel()
            with pytest.raises(asyncio.CancelledError): await task
            assert session.state_view()['lastReadError']=='READ_CANCELLED'
        asyncio.run(run())
        assert store.state_view()==before

def test_ambiguous_commit_never_exposes_a_success_view(tmp_path):
    from aegis.windows_journal_candidate import JournalError
    with Store(tmp_path/'source',fixture=fixture(page())) as store:
        with patch.object(store._journal._file,'flush',side_effect=OSError('uncertain flush')):
            with pytest.raises(JournalError,match='RESULT_UNKNOWN'):
                store.commit_page(wire(page()),AT)
        with pytest.raises(JournalError,match='RESULT_UNKNOWN'):
            store.state_view()
    with Store(tmp_path/'source') as recovered:
        assert recovered.state_view()['stateSource']=='RESTORED_LAST_KNOWN'
        assert not recovered.state_view()['isLive']
