import asyncio
from datetime import timedelta
import errno
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import os

import pytest

from test_adapter_input import AT, STREAM, fixture, page, record, wire


def api():
    from aegis import adapter_store
    return adapter_store


def adapter(*pages):
    from aegis.adapter_input import SyntheticReplayAdapter
    return SyntheticReplayAdapter(fixture(*(pages or (page(),))))


def first():return page(end=False)
def second():return page([record('r1',provided=True)],after='delivery-1',next='delivery-2')


def test_page_record_receipt_and_cursor_commit_together_and_resume_after_reopen(tmp_path):
    a=api();source=adapter(first(),second());path=tmp_path/'store.sqlite'
    with a.AdapterStore(path,source) as store:
        result=store.commit_page(wire(first()),AT)
        assert result['pageCommitStatus']=='LOCAL_STORE_COMMITTED'
        assert store.committed_cursor=='delivery-1' and not store.ended
        assert store.records[0]['record']==record()
        assert store.records[0]['firstReceivedAtUtc']=='2026-11-09T12:00:00Z'
    with a.AdapterStore(path,source) as reopened:
        assert reopened.committed_cursor=='delivery-1'
        reopened.commit_page(wire(second()),AT+timedelta(seconds=1))
        assert reopened.committed_cursor=='delivery-2' and reopened.ended
        report=reopened.report()
        assert report['persistenceStatus']=='LOCAL_STORE_COMMITTED'
        assert report['legacyArchiveDelivery']=='NOT_CONNECTED' and report['sourceAcknowledgement']=='NOT_SENT'
        assert report['counts']['committedPages']==report['counts']['committedRecords']==2
        assert reopened.records[0]['record']['sourceMetadata']['sequence'] is None
        assert reopened.records[1]['record']['sourceMetadata']['sequence']==2**60


def test_identical_retransmission_preserves_first_receipt_and_changed_page_fails(tmp_path):
    a=api();source=adapter();path=tmp_path/'store.sqlite'
    with a.AdapterStore(path,source) as s:s.commit_page(wire(page()),AT);before=s.records
    with a.AdapterStore(path,source) as s:
        r=s.commit_page(wire(page()),AT+timedelta(seconds=10))
        assert r['pageCommitStatus']=='ALREADY_LOCAL_STORE_COMMITTED'
        assert s.records==before
        with pytest.raises(a.StoreError):s.commit_page(wire(page([record(value=99)])),AT)
        assert s.records==before and s.committed_cursor=='delivery-1'


def test_conflict_rejects_whole_page_including_valid_new_records(tmp_path):
    a=api();source=adapter(first(),second());path=tmp_path/'store.sqlite'
    with a.AdapterStore(path,source) as s:
        s.commit_page(wire(first()),AT);before=s.records
        bad=page([record('new'),record(value=99)],after='delivery-1',next='delivery-2')
        with pytest.raises(a.StoreError):s.commit_page(wire(bad),AT)
        assert s.records==before and s.committed_cursor=='delivery-1'


def test_empty_eof_is_durable_but_transient_no_data_has_no_page_commit(tmp_path):
    a=api()
    for end in (False,True):
        p=page([],next=None,end=end);source=adapter(p);path=tmp_path/f'{end}.sqlite'
        with a.AdapterStore(path,source) as s:
            r=s.commit_page(wire(p),AT)
            assert s.committed_cursor is None and s.ended==end
            assert s.report()['counts']['committedPages']==int(end)
            assert r['pageCommitStatus']==('LOCAL_STORE_COMMITTED' if end else 'NO_NEW_PAGE')
        with a.AdapterStore(path,source) as s:assert s.ended==end and not s.records


def test_no_uncommitted_proposal_is_exposed_before_commit_confirmation(tmp_path,monkeypatch):
    a=api();source=adapter();path=tmp_path/'store.sqlite'
    with a.AdapterStore(path,source) as s:
        commit=s._commit;observed=[]
        def check_then_commit():
            with pytest.raises(a.StoreError) as error:_=s.committed_cursor
            assert error.value.code=='STORE_OPERATION_IN_PROGRESS'
            with sqlite3.connect(path) as reader:
                observed.append(reader.execute('SELECT page_count FROM store_state').fetchone()[0])
            commit()
        monkeypatch.setattr(s,'_commit',check_then_commit)
        s.commit_page(wire(page()),AT)
        assert observed==[0] and s.committed_cursor=='delivery-1'


def test_failure_after_inserts_rolls_back_page_records_and_cursor_then_can_retry(tmp_path,monkeypatch):
    a=api();source=adapter();path=tmp_path/'store.sqlite'
    with a.AdapterStore(path,source) as s:
        write=s._write_candidate
        def fail(*args):write(*args);raise OSError(errno.ENOSPC,'injected full store')
        with monkeypatch.context() as patch:
            patch.setattr(s,'_write_candidate',fail)
            with pytest.raises(a.StoreError):s.commit_page(wire(page()),AT)
        assert s.committed_cursor is None and s.records==()
        s.commit_page(wire(page()),AT)
        assert s.committed_cursor=='delivery-1'


@pytest.mark.parametrize('after_real_commit',[False,True])
def test_commit_exception_is_unknown_and_fenced_until_reopen(tmp_path,monkeypatch,after_real_commit):
    a=api();source=adapter();path=tmp_path/'store.sqlite';s=a.AdapterStore(path,source);commit=s._commit
    def uncertain():
        if after_real_commit:commit()
        raise OSError(errno.EIO,'injected uncertain commit result')
    monkeypatch.setattr(s,'_commit',uncertain)
    with pytest.raises(a.StoreError) as error:s.commit_page(wire(page()),AT)
    assert error.value.code=='STORE_RESULT_UNKNOWN'
    with pytest.raises(a.StoreError):_=s.committed_cursor
    s.close()
    with a.AdapterStore(path,source) as recovered:
        assert (recovered.committed_cursor=='delivery-1')==after_real_commit
        assert len(recovered.records)==int(after_real_commit)
        recovered.commit_page(wire(page()),AT+timedelta(seconds=1))
        assert len(recovered.records)==1


def test_failed_postcommit_confirmation_never_returns_success(tmp_path,monkeypatch):
    a=api();source=adapter();path=tmp_path/'store.sqlite';s=a.AdapterStore(path,source)
    def fail(*_args):raise OSError(errno.EIO,'injected readback failure')
    monkeypatch.setattr(s,'_confirm_after_commit',fail)
    with pytest.raises(a.StoreError) as error:s.commit_page(wire(page()),AT)
    assert error.value.code=='STORE_RESULT_UNKNOWN';s.close()
    with a.AdapterStore(path,source) as s:assert s.committed_cursor=='delivery-1' and len(s.records)==1


def test_sqlite_busy_and_full_fail_without_cursor_progress(tmp_path):
    a=api();p=page([record('large')]);p['records'][0]['signals']=[
        {'signalKey':f'synthetic.tag{n:04d}','value':n,'unit':'count','quality':'GOOD'} for n in range(300)]
    source=adapter(p);path=tmp_path/'store.sqlite'
    with a.AdapterStore(path,source) as s:
        with sqlite3.connect(path) as blocker:
            blocker.execute('begin immediate')
            with pytest.raises(a.StoreError):s.commit_page(wire(p),AT)
            blocker.rollback()
        count=s._connection.execute('pragma page_count').fetchone()[0]
        s._connection.execute(f'pragma max_page_count={count}')
        with pytest.raises(a.StoreError):s.commit_page(wire(p),AT)
        assert s.committed_cursor is None and s.records==()
        s._connection.execute('pragma max_page_count=8192')
        s.commit_page(wire(p),AT);assert len(s.records)==1


@pytest.mark.parametrize('sql',["UPDATE store_state SET cursor_key='\"wrong\"'",'DELETE FROM adapter_records',
    'DELETE FROM adapter_pages',"UPDATE adapter_records SET first_received_at_utc='2026-11-10T00:00:00Z'",
    "UPDATE adapter_pages SET wire_sha='0000'",'UPDATE store_state SET ended=0'])
def test_restart_rejects_cursor_record_page_receipt_or_hash_inconsistency(tmp_path,sql):
    a=api();source=adapter();path=tmp_path/'store.sqlite'
    with a.AdapterStore(path,source) as s:s.commit_page(wire(page()),AT)
    with sqlite3.connect(path) as c:c.execute(sql)
    before=path.read_bytes()
    with pytest.raises(a.StoreError):a.AdapterStore(path,source)
    assert path.read_bytes()==before


@pytest.mark.parametrize('after_commit',[False,True])
def test_actual_process_crash_before_or_after_commit_recovers_atomic_state(tmp_path,after_commit):
    a=api();source=adapter();path=tmp_path/'store.sqlite'
    with a.AdapterStore(path,source):pass
    f=tmp_path/'fixture.json';f.write_text(json.dumps(fixture(page())))
    root=Path(__file__).resolve().parents[1]
    script='''import os,sys,json\nfrom datetime import datetime,timezone\nfrom aegis.adapter_input import SyntheticReplayAdapter\nfrom aegis.adapter_store import AdapterStore\na=SyntheticReplayAdapter(open(sys.argv[1],"rb").read())\ns=AdapterStore(sys.argv[2],a)\ncommit=s._commit\ndef crash():\n if sys.argv[3]=="after":commit()\n os._exit(17)\ns._commit=crash\ns.commit_page(json.dumps(json.loads(a.fixture_wire)["pages"][0]["response"]).encode(),datetime(2026,11,9,12,tzinfo=timezone.utc))\n'''
    result=subprocess.run([sys.executable,'-c',script,str(f),str(path),'after' if after_commit else 'before'],
        env=dict(os.environ,PYTHONPATH=str(root/'src')),capture_output=True,timeout=10)
    assert result.returncode==17
    with a.AdapterStore(path,source) as s:
        assert len(s.records)==int(after_commit)
        assert (s.committed_cursor=='delivery-1')==after_commit
        s.commit_page(wire(page()),AT+timedelta(seconds=1))
        assert len(s.records)==1


def test_caller_cancellation_after_commit_resumes_from_durable_state_without_duplicate(tmp_path,monkeypatch):
    async def check():
        a=api();source=adapter();path=tmp_path/'store.sqlite'
        with a.AdapterStore(path,source) as s:
            session=a.DurableAdapterSession(source,s,utc_now=lambda:AT);commit=s._commit
            def commit_then_cancel():commit();asyncio.current_task().cancel()
            with monkeypatch.context() as patch:
                patch.setattr(s,'_commit',commit_then_cancel)
                task=asyncio.create_task(session.poll_once())
                with pytest.raises(asyncio.CancelledError):await task
            assert s.committed_cursor=='delivery-1' and len(s.records)==1
            assert (await session.poll_once())['sourceAcknowledgement']=='NOT_SENT'
            assert len(s.records)==1
    asyncio.run(check())


def test_raw_v1_is_refused_before_sqlite_can_change_files_or_companions(tmp_path):
    a=api()
    from aegis.archive import RawArchive
    from test_archive import snapshot,AT as OLD_AT
    path=tmp_path/'legacy.sqlite'
    async def seed():
        s=RawArchive(path);await s.start();await s.append([snapshot(0)],OLD_AT);await s.close()
    asyncio.run(seed());before={p.name:p.read_bytes() for p in tmp_path.iterdir()}
    with pytest.raises(a.StoreError):a.AdapterStore(path,adapter())
    assert before=={p.name:p.read_bytes() for p in tmp_path.iterdir()}


def test_sqlite_like_wildcard_cannot_hide_an_unexpected_trigger(tmp_path):
    a=api();source=adapter();path=tmp_path/'store.sqlite'
    with a.AdapterStore(path,source):pass
    with sqlite3.connect(path) as c:
        c.execute("CREATE TRIGGER sqliteXeraser AFTER UPDATE ON store_state WHEN NEW.page_count>0 BEGIN DELETE FROM adapter_records; DELETE FROM adapter_pages; UPDATE store_state SET cursor_key='null',ended=0,page_count=0,record_count=0 WHERE id=1; END")
    before=path.read_bytes()
    with pytest.raises(a.StoreError):a.AdapterStore(path,source)
    assert path.read_bytes()==before


def test_confirmation_requires_the_requested_page_to_still_exist(tmp_path,monkeypatch):
    a=api();source=adapter();path=tmp_path/'store.sqlite';s=a.AdapterStore(path,source);commit=s._commit
    def erase_after_commit():
        commit()
        with sqlite3.connect(path) as c:
            c.execute('DELETE FROM adapter_records');c.execute('DELETE FROM adapter_pages')
            c.execute("UPDATE store_state SET cursor_key='null',ended=0,page_count=0,record_count=0")
    monkeypatch.setattr(s,'_commit',erase_after_commit)
    with pytest.raises(a.StoreError) as error:s.commit_page(wire(page()),AT)
    assert error.value.code=='STORE_RESULT_UNKNOWN'
    with pytest.raises(a.StoreError):_=s.committed_cursor
    s.close()


def test_this_run_count_does_not_include_another_valid_writer_commit(tmp_path,monkeypatch):
    async def check():
        a=api();source=adapter(first(),second());path=tmp_path/'store.sqlite'
        with a.AdapterStore(path,source) as s:
            commit=s._commit
            def interleave():
                commit()
                with a.AdapterStore(path,source) as other:other.commit_page(wire(second()),AT+timedelta(seconds=1))
            monkeypatch.setattr(s,'_commit',interleave)
            report=await a._run(source,s,100,2,64)
            assert report['counts']['committedPages']==2
            assert report['counts']['thisRunCommittedPages']==1
    asyncio.run(check())


def test_failed_rollback_fences_without_a_success_acknowledgement(tmp_path,monkeypatch):
    a=api();source=adapter();path=tmp_path/'store.sqlite';s=a.AdapterStore(path,source);write=s._write_candidate
    def fail_write(*args):write(*args);raise OSError(errno.EIO,'synthetic write fault')
    def fail_rollback():raise OSError(errno.EIO,'synthetic rollback fault')
    monkeypatch.setattr(s,'_write_candidate',fail_write);monkeypatch.setattr(s,'_rollback',fail_rollback)
    with pytest.raises(a.StoreError) as error:s.commit_page(wire(page()),AT)
    assert error.value.code=='STORE_RESULT_UNKNOWN'
    with pytest.raises(a.StoreError):_=s.records
    s.close()
    with a.AdapterStore(path,source) as fresh:assert fresh.records==() and fresh.committed_cursor is None


@pytest.mark.skipif(not Path('/dev/full').exists(),reason='Linux output failure device required')
def test_output_failure_after_commit_reports_unknown_and_preserves_restart_data(tmp_path):
    a=api();source=adapter();path=tmp_path/'store.sqlite';f=tmp_path/'fixture.json';f.write_text(json.dumps(fixture(page())))
    root=Path(__file__).resolve().parents[1]
    with open('/dev/full','wb') as full:
        result=subprocess.run([sys.executable,'-m','aegis.adapter_store','--fixture',str(f),'--store',str(path)],
            stdout=full,stderr=subprocess.PIPE,env=dict(os.environ,PYTHONPATH=str(root/'src')),timeout=10)
    assert result.returncode==2
    assert json.loads(result.stderr)=={'error':'STORE_RESULT_UNKNOWN'}
    with a.AdapterStore(path,source) as s:assert s.committed_cursor=='delivery-1' and len(s.records)==1


def test_empty_eof_initial_cursor_key_is_unique_and_retransmission_remains_durable(tmp_path):
    a=api();p=page([],next=None,end=True);source=adapter(p);path=tmp_path/'store.sqlite'
    with a.AdapterStore(path,source) as s:
        s.commit_page(wire(p),AT)
        result=s.commit_page(wire(p),AT+timedelta(seconds=1))
        assert result['pageCommitStatus']=='ALREADY_LOCAL_STORE_COMMITTED'
        assert s._connection.execute('SELECT after_key FROM adapter_pages').fetchall()==[('null',)]
        assert s.report()['counts']['committedPages']==1


@pytest.mark.parametrize('mode',['timeout','cancel'])
def test_timeout_or_cancellation_during_pull_never_commits_a_page(tmp_path,mode):
    async def check():
        a=api()
        from aegis.adapter_input import SyntheticReplayAdapter,AdapterError
        source=SyntheticReplayAdapter(fixture(page(),delays=[50]));path=tmp_path/'store.sqlite'
        with a.AdapterStore(path,source) as s:
            session=a.DurableAdapterSession(source,s,timeout_ms=5 if mode=='timeout' else 100,max_attempts=2)
            if mode=='timeout':
                with pytest.raises(AdapterError):await session.poll_once()
            else:
                task=asyncio.create_task(session.poll_once());await asyncio.sleep(.005);task.cancel()
                with pytest.raises(asyncio.CancelledError):await task
            assert s.committed_cursor is None and s.records==()
            assert s.report()['persistenceStatus']=='NO_COMMITTED_PAGE'
    asyncio.run(check())


def test_first_page_cannot_silently_change_the_bound_fixture_payload(tmp_path):
    a=api();source=adapter();path=tmp_path/'store.sqlite'
    with a.AdapterStore(path,source) as s:
        with pytest.raises(a.StoreError):s.commit_page(wire(page([record('changed')])),AT)
        assert s.records==() and s.committed_cursor is None


@pytest.mark.parametrize('changed_evidence',['requested_receipt','prior_receipt','fixture_wire'])
def test_confirmation_pins_original_prefix_receipts_and_fixture_evidence(tmp_path,monkeypatch,changed_evidence):
    a=api();source=adapter(first(),second()) if changed_evidence=='prior_receipt' else adapter()
    path=tmp_path/'store.sqlite';s=a.AdapterStore(path,source)
    if changed_evidence=='prior_receipt':s.commit_page(wire(first()),AT)
    commit=s._commit
    def change_after_commit():
        commit()
        with sqlite3.connect(path) as c:
            if changed_evidence=='fixture_wire':
                modified=json.dumps(json.loads(source.fixture_wire),indent=5).encode()
                c.execute('UPDATE store_meta SET fixture_wire=?,fixture_wire_sha=?',(modified,hashlib.sha256(modified).hexdigest()))
            else:
                later='2026-11-10T00:00:00Z'
                c.execute('UPDATE adapter_pages SET received_at_utc=? WHERE ordinal=0',(later,))
                c.execute("UPDATE adapter_records SET first_received_at_utc=? WHERE record_id='r0'",(later,))
    monkeypatch.setattr(s,'_commit',change_after_commit)
    incoming=second() if changed_evidence=='prior_receipt' else page()
    with pytest.raises(a.StoreError) as error:s.commit_page(wire(incoming),AT+timedelta(seconds=1))
    assert error.value.code=='STORE_RESULT_UNKNOWN'
    with pytest.raises(a.StoreError):_=s.committed_cursor
    s.close()
