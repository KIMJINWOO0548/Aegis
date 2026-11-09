import asyncio
from datetime import timedelta
import errno
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from types import SimpleNamespace

import pytest

from test_archive import AT, RUN, snapshot


def api():
    from aegis import storage_diagnostics
    return storage_diagnostics


def seed(path):
    from aegis.archive import RawArchive
    async def build():
        store = RawArchive(path); await store.start()
        await store.append([snapshot(0),snapshot(1, value=None, quality='MISSING')], AT)
        await store.append([snapshot(4)], AT+timedelta(seconds=2), gap=(2,3))
        await store.close()
    asyncio.run(build())
    with sqlite3.connect(path) as connection:
        connection.execute('PRAGMA journal_mode=DELETE')


def policy(**kwargs):
    return {'format':'aegis-storage-policy-1','policyId':'synthetic-ops','version':1,**kwargs}


def capture(at=AT+timedelta(seconds=3), **kwargs):
    row={'deviceId':'CMP-01.PMC-01','transport':'replay','archiveEnabled':True,
         'lastCommittedRunId':RUN,'lastCommittedSequence':4,'archiveCommitMs':12.5,**kwargs}
    return {'format':'aegis-ingestion-capture-1','capturedAtUtc':at.isoformat(),'ingestion':[row]}


def setup(tmp_path):
    source=tmp_path/'raw.db';seed(source)
    directory=tmp_path/'backups';directory.mkdir()
    backup=directory/'one.sqlite';backup.write_bytes(b'synthetic footprint only')
    return source,directory,backup


def test_unconfigured_is_explicit_and_receipt_age_is_not_commit_latency(tmp_path):
    a=api();s,d,b=setup(tmp_path);before=s.read_bytes();original=b.read_bytes()
    r=a.diagnose_storage(s,d,backups=[b],now=AT+timedelta(seconds=5))
    assert r['policy']['status']=='NOT_CONFIGURED'
    assert all(c['status']=='NOT_CONFIGURED' for c in r['checks'].values())
    assert r['archive']['status']=='MEASURED' and r['archive']['rawRows']==3
    assert r['archive']['latestReceiptAgeSeconds']==3
    assert r['commitLatency']['status']=='NOT_MEASURED'
    assert r['backups']['integrity']=='NOT_VERIFIED'
    assert r['backups']['creationTimeStatus']=='NOT_RECORDED'
    assert r['actionsPerformed']==[] and r['sampling']=='NON_ATOMIC'
    assert s.read_bytes()==before and b.read_bytes()==original
    assert str(s) not in json.dumps(r) and 'CMP-01' not in json.dumps(r)


@pytest.mark.parametrize('age,expected',[(2,'EXCEEDED'),(2.001,'WITHIN_LIMIT')])
def test_receipt_and_retention_boundaries_preserve_cursor_and_gap(tmp_path,age,expected):
    a=api();s,d,b=setup(tmp_path)
    with sqlite3.connect(s) as c:before=[c.execute('select * from '+t).fetchall() for t in ('raw_batches','ingest_cursors','ingest_gaps')]
    r=a.diagnose_storage(s,d,policy=policy(maxReceiptAgeSeconds=age),now=AT+timedelta(seconds=4.001))
    assert r['checks']['receiptAge']['status']==expected
    with sqlite3.connect(s) as c:assert before==[c.execute('select * from '+t).fetchall() for t in ('raw_batches','ingest_cursors','ingest_gaps')]


def test_retention_is_first_receipt_advisory_and_backup_age_is_only_mtime(tmp_path):
    a=api();s,d,b=setup(tmp_path)
    os.utime(b,(AT.timestamp(),AT.timestamp()))
    r=a.diagnose_storage(s,d,backups=[b],policy=policy(archiveReceiptRetentionDays=1,backupMtimeReviewDays=1),now=AT+timedelta(days=1,seconds=1))
    assert r['checks']['archiveRetention']['status']=='EXCEEDED'
    assert r['checks']['backupMtimeReview']['status']=='EXCEEDED'
    assert r['backups']['creationTimeStatus']=='NOT_RECORDED'
    assert r['archive']['rowsPastReceiptRetention']==2
    assert r['sourceCompleteness']=='NOT_ASSERTED'


def test_capacity_uses_non_superuser_available_bytes_and_exact_boundary(tmp_path,monkeypatch):
    a=api();s,d,b=setup(tmp_path)
    monkeypatch.setattr(a,'_statvfs',lambda p:SimpleNamespace(f_frsize=4096,f_blocks=100,f_bfree=20,f_bavail=10,f_flag=0))
    r=a.diagnose_storage(s,d,policy=policy(minAvailableBytes=40960,minAvailablePercent=10),now=AT)
    assert r['capacity']['archive']['availableBytes']==40960
    assert r['checks']['archiveCapacity']['status']=='WITHIN_LIMIT'
    assert r['capacity']['sameFilesystem'] is True
    r=a.diagnose_storage(s,d,policy=policy(minAvailableBytes=40961),now=AT)
    assert r['checks']['backupCapacity']['status']=='EXCEEDED'
    assert r['archive']['nextBackupEstimate']['classification']=='ESTIMATE'
    assert r['archive']['nextBackupEstimate']['guaranteedToFit'] is False


@pytest.mark.parametrize('code,status',[(errno.ENOENT,'MISSING'),(errno.EACCES,'PERMISSION_DENIED'),(errno.EPERM,'PERMISSION_DENIED'),(errno.EIO,'IO_ERROR')])
def test_stat_failures_are_not_silently_missing_or_zero(tmp_path,monkeypatch,code,status):
    a=api();s,d,b=setup(tmp_path);stat=a._lstat
    def fail(path):
        if Path(path)==s:raise OSError(code,'synthetic failure')
        return stat(path)
    monkeypatch.setattr(a,'_lstat',fail)
    r=a.diagnose_storage(s,d,backups=[b],policy=policy(maxReceiptAgeSeconds=60),now=AT)
    assert r['archive']['status']==status
    assert r['checks']['receiptAge']['status']=='UNAVAILABLE'
    assert r['backups']['status']=='MEASURED'


@pytest.mark.parametrize('code,status',[(errno.EACCES,'PERMISSION_DENIED'),(errno.EIO,'IO_ERROR')])
def test_capacity_failure_does_not_pass_threshold(tmp_path,monkeypatch,code,status):
    a=api();s,d,b=setup(tmp_path)
    def fail(path):raise OSError(code,'synthetic capacity failure')
    monkeypatch.setattr(a,'_statvfs',fail)
    r=a.diagnose_storage(s,d,policy=policy(minAvailableBytes=0),now=AT)
    assert r['capacity']['backup']['status']==status
    assert r['checks']['backupCapacity']['status']=='UNAVAILABLE'


def test_wal_companions_and_hardlinks_are_measured_without_double_count(tmp_path):
    a=api();s,d,b=setup(tmp_path);alias=d/'alias';alias.hardlink_to(b)
    writer=sqlite3.connect(s,isolation_level=None);writer.execute('pragma journal_mode=wal');writer.execute('pragma wal_autocheckpoint=0')
    writer.execute('update raw_batches set equipment_id=equipment_id')
    try:
        expected=sum(p.stat().st_size for p in [s,Path(str(s)+'-wal'),Path(str(s)+'-shm')])
        r=a.diagnose_storage(s,d,backups=[b,alias],now=AT)
        assert r['archive']['footprint']['apparentBytes']==expected
        assert r['backups']['footprint']['apparentBytes']==b.stat().st_size
        assert r['backups']['footprint']['duplicateFiles']==1
    finally:writer.close()


def test_symlink_and_special_backup_are_not_followed(tmp_path):
    a=api();s,d,b=setup(tmp_path);link=d/'link';link.symlink_to(b)
    r=a.diagnose_storage(s,d,backups=[link],policy=policy(backupMtimeReviewDays=1),now=AT)
    assert r['backups']['status']=='UNSUPPORTED_TYPE'
    assert r['checks']['backupMtimeReview']['status']=='UNAVAILABLE'
    assert link.is_symlink() and b.read_bytes()==b'synthetic footprint only'


@pytest.mark.parametrize('kind',['empty','corrupt','cursor','future'])
def test_unavailable_archive_measurement_is_never_a_pass(tmp_path,kind):
    a=api();s,d,b=setup(tmp_path)
    if kind=='empty':
        with sqlite3.connect(s) as c:c.execute('delete from raw_batches');c.execute('delete from ingest_cursors');c.execute('delete from ingest_gaps')
    elif kind=='corrupt':s.write_bytes(b'not SQLite')
    elif kind=='cursor':
        with sqlite3.connect(s) as c:c.execute('update ingest_cursors set sequence=0')
    r=a.diagnose_storage(s,d,policy=policy(maxReceiptAgeSeconds=10),now=AT-timedelta(seconds=1) if kind=='future' else AT)
    assert r['checks']['receiptAge']['status']=='UNAVAILABLE'


def test_valid_fresh_identity_bound_commit_measurement_has_separate_threshold(tmp_path):
    a=api();s,d,b=setup(tmp_path)
    p=policy(maxArchiveCommitMs=12.5,maxTelemetryAgeSeconds=10)
    r=a.diagnose_storage(s,d,policy=p,telemetry=capture(),now=AT+timedelta(seconds=4))
    assert r['commitLatency']['status']=='MEASURED_LAST_COMMIT'
    assert r['commitLatency']['worstArchiveCommitMs']==12.5
    assert r['checks']['commitLatency']['status']=='WITHIN_LIMIT'
    p['maxArchiveCommitMs']=12.4
    assert a.diagnose_storage(s,d,policy=p,telemetry=capture(),now=AT+timedelta(seconds=4))['checks']['commitLatency']['status']=='EXCEEDED'


@pytest.mark.parametrize('kind',['capture_stale','capture_future','cursor_mismatch','missing','bad_number','receipt_stale','duplicate','recency_unconfigured'])
def test_stale_invalid_missing_or_mismatched_commit_data_cannot_pass(tmp_path,kind):
    a=api();s,d,b=setup(tmp_path);p=policy(maxArchiveCommitMs=100,maxTelemetryAgeSeconds=10);t=capture();now=AT+timedelta(seconds=4)
    if kind=='capture_stale':now=AT+timedelta(seconds=20)
    elif kind=='capture_future':t['capturedAtUtc']=(now+timedelta(seconds=1)).isoformat()
    elif kind=='cursor_mismatch':t['ingestion'][0]['lastCommittedSequence']=1
    elif kind=='missing':t=None
    elif kind=='bad_number':t['ingestion'][0]['archiveCommitMs']=True
    elif kind=='receipt_stale':t=capture(AT+timedelta(seconds=99));now=AT+timedelta(seconds=100)
    elif kind=='duplicate':t['ingestion']*=2
    else:del p['maxTelemetryAgeSeconds']
    r=a.diagnose_storage(s,d,policy=p,telemetry=t,now=now)
    assert r['checks']['commitLatency']['status'] in ('UNAVAILABLE','NOT_CONFIGURED')


@pytest.mark.parametrize('change',[{'version':True},{'unknown':1},{'minAvailableBytes':-1},
    {'minAvailablePercent':float('nan')},{'maxReceiptAgeSeconds':True},{'archiveReceiptRetentionDays':0}])
def test_invalid_policy_rejected_before_filesystem_access(tmp_path,monkeypatch,change):
    a=api();called=[]
    monkeypatch.setattr(a,'_lstat',lambda p:called.append(p))
    with pytest.raises(a.DiagnosticError):a.diagnose_storage(tmp_path/'none',tmp_path,policy=policy(**change),now=AT)
    assert not called


@pytest.mark.parametrize('limit,value',[('MAX_BACKUPS',0),('MAX_ARCHIVE_ROWS',1),('MAX_SECONDS',0),('MAX_SQL_STEPS',0)])
def test_limits_are_explicit_and_never_publish_data(tmp_path,monkeypatch,limit,value):
    a=api();s,d,b=setup(tmp_path);before=s.read_bytes();monkeypatch.setattr(a,limit,value)
    if limit=='MAX_BACKUPS':
        with pytest.raises(a.DiagnosticError):a.diagnose_storage(s,d,backups=[b],now=AT)
    else:
        r=a.diagnose_storage(s,d,now=AT)
        assert r['archive']['status']=='LIMIT_EXCEEDED'
    assert s.read_bytes()==before


def test_capture_cannot_precede_the_matching_stored_receipt(tmp_path):
    a=api();s,d,b=setup(tmp_path)
    r=a.diagnose_storage(s,d,policy=policy(maxArchiveCommitMs=100,maxTelemetryAgeSeconds=10),
        telemetry=capture(AT+timedelta(seconds=1)),now=AT+timedelta(seconds=4))
    assert r['checks']['commitLatency']['status']=='UNAVAILABLE'


def test_closed_wal_without_companions_is_not_initialized_by_diagnostics(tmp_path):
    a=api();s,d,b=setup(tmp_path)
    with sqlite3.connect(s) as c:c.execute('pragma journal_mode=wal')
    before={p.name:p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}
    r=a.diagnose_storage(s,d,policy=policy(maxReceiptAgeSeconds=10),now=AT)
    assert r['archive']['status']=='DB_UNAVAILABLE'
    assert r['checks']['receiptAge']['status']=='UNAVAILABLE'
    assert before=={p.name:p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}


@pytest.mark.parametrize('option',['--policy','--telemetry'])
@pytest.mark.parametrize('kind',['null','fifo','symlink'])
def test_explicit_invalid_json_files_are_rejected_without_hanging(tmp_path,option,kind):
    s,d,b=setup(tmp_path);config=tmp_path/'input.json'
    if kind=='null':config.write_text('null')
    elif kind=='fifo':os.mkfifo(config)
    else:
        actual=tmp_path/'actual.json';actual.write_text(json.dumps(policy()));config.symlink_to(actual)
    root=Path(__file__).resolve().parents[1]
    result=subprocess.run([sys.executable,'-m','aegis.storage_diagnostics','--archive',str(s),
        '--backup-dir',str(d),option,str(config)],env=dict(os.environ,PYTHONPATH=str(root/'src')),
        capture_output=True,text=True,timeout=2)
    assert result.returncode==2 and result.stdout==''
    error=json.loads(result.stderr)
    assert set(error)=={'error'} and str(config) not in result.stderr


def test_partial_backup_inventory_never_reports_an_understated_successful_total(tmp_path):
    a=api();s,d,b=setup(tmp_path)
    r=a.diagnose_storage(s,d,backups=[b,d/'missing'],policy=policy(backupMtimeReviewDays=1),now=AT)
    assert r['backups']['status']=='PARTIAL'
    assert r['backups']['footprint']['apparentBytes'] is None
    assert r['checks']['backupMtimeReview']['status']=='UNAVAILABLE'


def test_sparse_file_apparent_length_is_separate_from_reported_allocation(tmp_path):
    a=api();s,d,b=setup(tmp_path)
    with b.open('wb') as stream:stream.truncate(1024*1024)
    r=a.diagnose_storage(s,d,backups=[b],now=AT)
    f=r['backups']['footprint']
    assert f['apparentBytes']==1024*1024
    assert f['allocatedBytesEstimate']==b.stat().st_blocks*512
    assert f['allocationBasis']=='FILESYSTEM_REPORTED_512_BYTE_BLOCKS'


def test_readonly_volume_is_unavailable_even_with_free_blocks(tmp_path,monkeypatch):
    a=api();s,d,b=setup(tmp_path)
    monkeypatch.setattr(a,'_statvfs',lambda p:SimpleNamespace(f_frsize=4096,f_blocks=100,f_bavail=90,f_flag=os.ST_RDONLY))
    r=a.diagnose_storage(s,d,policy=policy(minAvailableBytes=0),now=AT)
    assert r['capacity']['backup']['status']=='MEASURED' and r['capacity']['backup']['readOnly']
    assert r['checks']['backupCapacity']['status']=='UNAVAILABLE'


def test_nonzero_sql_work_limit_interrupts_and_releases_the_reader(tmp_path,monkeypatch):
    a=api();s=tmp_path/'raw.db';d=tmp_path/'backups';d.mkdir()
    from aegis.archive import RawArchive
    async def build():
        store=RawArchive(s);await store.start()
        await store.append([snapshot(n) for n in range(500)],AT);await store.close()
    asyncio.run(build())
    with sqlite3.connect(s) as c:c.execute('pragma journal_mode=delete')
    before=s.read_bytes();called=[];progress=a._Budget.progress
    def observed(budget):called.append(True);return progress(budget)
    monkeypatch.setattr(a._Budget,'progress',observed);monkeypatch.setattr(a,'MAX_SQL_STEPS',1000)
    r=a.diagnose_storage(s,d,now=AT)
    assert called and r['archive']['status']=='LIMIT_EXCEEDED' and s.read_bytes()==before
    with sqlite3.connect(s) as writer:writer.execute('begin immediate');writer.rollback()


@pytest.mark.parametrize('delay,expected',[(12,'WITHIN_LIMIT'),(12.000001,'UNAVAILABLE')])
def test_commit_recency_boundary_is_inclusive(tmp_path,delay,expected):
    a=api();s,d,b=setup(tmp_path)
    r=a.diagnose_storage(s,d,policy=policy(maxArchiveCommitMs=100,maxTelemetryAgeSeconds=10),
        telemetry=capture(),now=AT+timedelta(seconds=delay))
    assert r['checks']['commitLatency']['status']==expected


@pytest.mark.parametrize('option',['--policy','--telemetry'])
def test_explicit_empty_json_path_is_invalid_rather_than_omitted(tmp_path,option):
    s,d,b=setup(tmp_path);before=s.read_bytes();root=Path(__file__).resolve().parents[1]
    result=subprocess.run([sys.executable,'-m','aegis.storage_diagnostics','--archive',str(s),
        '--backup-dir',str(d),option,''],env=dict(os.environ,PYTHONPATH=str(root/'src')),
        capture_output=True,text=True,timeout=2)
    assert result.returncode==2 and result.stdout==''
    assert set(json.loads(result.stderr))=={'error'}
    assert s.read_bytes()==before
