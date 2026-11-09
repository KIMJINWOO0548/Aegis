"""Synthetic adapter local receipts and commit confirmation; no raw-v1 delivery."""

import argparse
import asyncio
from datetime import datetime, timezone
import errno
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import sys
import time

from aegis.adapter_input import (AdapterAdmission, AdapterError, AdapterReadRequest,
    SyntheticReplayAdapter, MAX_ACCEPTED_PAGES, MAX_ADMITTED_RECORDS, MAX_FIXTURE_BYTES,
    MAX_PAGE_BYTES, _decode, _encode, _load_fixture, validate_page)
from aegis.archive_backup import _discard_failed_stdout


APPLICATION_ID=0x41445031
STORE_VERSION=1
MAX_STORE_BYTES=32*1024*1024
MAX_SECONDS=5.0
MAX_SQL_STEPS=5_000_000
SCHEMA={
 'store_meta':'''CREATE TABLE store_meta (
 id INTEGER PRIMARY KEY CHECK(id=1), adapter_id TEXT NOT NULL, adapter_version TEXT NOT NULL,
 stream_id TEXT NOT NULL, fixture_sha TEXT NOT NULL, fixture_wire_sha TEXT NOT NULL,
 fixture_wire BLOB NOT NULL)''',
 'adapter_pages':'''CREATE TABLE adapter_pages (
 ordinal INTEGER PRIMARY KEY CHECK(ordinal>=0), after_key TEXT NOT NULL UNIQUE,
 next_key TEXT NOT NULL, canonical_sha TEXT NOT NULL, wire_sha TEXT NOT NULL,
 wire BLOB NOT NULL, received_at_utc TEXT NOT NULL)''',
 'adapter_records':'''CREATE TABLE adapter_records (
 record_id TEXT PRIMARY KEY NOT NULL, record_sha TEXT NOT NULL, record_json TEXT NOT NULL,
 first_received_at_utc TEXT NOT NULL)''',
 'store_state':'''CREATE TABLE store_state (
 id INTEGER PRIMARY KEY CHECK(id=1), cursor_key TEXT NOT NULL,
 ended INTEGER NOT NULL CHECK(ended IN (0,1)), page_count INTEGER NOT NULL CHECK(page_count>=0),
 record_count INTEGER NOT NULL CHECK(record_count>=0))'''}


class StoreError(AdapterError):
    pass


class _Budget:
    def __init__(self):self.deadline=time.monotonic()+MAX_SECONDS;self.steps=0
    def check(self):
        if time.monotonic()>=self.deadline or self.steps>=MAX_SQL_STEPS:
            raise StoreError('STORE_LIMIT')
    def progress(self):self.steps+=1000;self.check();return 0


def _sha(value):return hashlib.sha256(value).hexdigest()
def _key(token):return _encode(token).decode('utf-8')


def _utc(value):
    if not isinstance(value,datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise StoreError('INVALID_RECEIPT')
    return value.astimezone(timezone.utc).isoformat().replace('+00:00','Z')


def _receipt(value):
    if not isinstance(value,str) or len(value)>64:
        raise StoreError('STORE_INTEGRITY')
    result=datetime.fromisoformat(value.replace('Z','+00:00'))
    if _utc(result)!=value:
        raise StoreError('STORE_INTEGRITY')
    return result


def _sql_normal(value):return ' '.join(value.split()).lower()


def _regular_info(path):
    info=os.lstat(path)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1:
        raise StoreError('UNSAFE_STORE_PATH')
    return info


def _check_header(path):
    _regular_info(path)
    fd=os.open(path,os.O_RDONLY|os.O_NONBLOCK|os.O_NOFOLLOW)
    try:
        info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1:
            raise StoreError('UNSAFE_STORE_PATH')
        header=os.read(fd,100)
    finally:os.close(fd)
    if (len(header)!=100 or header[:16]!=b'SQLite format 3\x00'
            or int.from_bytes(header[68:72],'big')!=APPLICATION_ID
            or int.from_bytes(header[60:64],'big')!=STORE_VERSION):
        raise StoreError('STORE_NOT_OURS')


def _sync_parent(path):
    fd=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(fd)
    finally:os.close(fd)


class AdapterStore:
    """One trusted local fixture/stream. Synchronous I/O, no executor or source ACK."""
    def __init__(self,path,adapter):
        self._connection=None
        self._fenced=False
        self._operation=False
        try:
            if os.name!='posix' or not hasattr(os,'O_NOFOLLOW') or not hasattr(os,'O_DIRECTORY'):
                raise StoreError('PLATFORM_UNVERIFIED')
            if type(adapter) is not SyntheticReplayAdapter:
                raise StoreError('UNSUPPORTED_ADAPTER')
            self._descriptor=adapter.descriptor
            self._fixture_wire=adapter.fixture_wire
            original=_decode(self._fixture_wire,MAX_FIXTURE_BYTES)
            self._fixture_sha=_sha(_encode(original))
            self._fixture_wire_sha=_sha(self._fixture_wire)
            self._expected_pages={_key(p['afterToken']):_sha(_encode(p['response'])) for p in original['pages']}
            if not isinstance(path,(str,Path)) or not 1<=len(str(path))<=4096 or '\x00' in str(path):
                raise StoreError('UNSAFE_STORE_PATH')
            self.path=Path(path).absolute()
            if self.path.name.lower().endswith(('-wal','-shm','-journal')):
                raise StoreError('UNSAFE_STORE_PATH')
            parent=os.stat(self.path.parent)
            if not stat.S_ISDIR(parent.st_mode):raise StoreError('UNSAFE_STORE_PATH')
            try:
                _regular_info(self.path)
                fresh=False
            except FileNotFoundError:fresh=True
            for suffix in ('-wal','-shm','-journal'):
                companion=Path(str(self.path)+suffix)
                try:
                    _regular_info(companion)
                    if fresh:raise StoreError('STORE_COMPANION_EXISTS')
                except FileNotFoundError:pass
            if fresh:
                fd=os.open(self.path,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
                os.close(fd)
            else:_check_header(self.path)  # Reject raw-v1 before SQLite can initialize sidecars.
            connection=sqlite3.connect(self.path.as_uri()+'?mode=rw',uri=True,timeout=.2,isolation_level=None)
            self._connection=connection
            connection.execute('PRAGMA trusted_schema=OFF')
            if fresh:
                connection.execute('PRAGMA journal_mode=DELETE')
                connection.execute('PRAGMA synchronous=EXTRA')
                connection.execute('BEGIN IMMEDIATE')
                for statement in SCHEMA.values():connection.execute(statement)
                connection.execute(f'PRAGMA application_id={APPLICATION_ID}')
                connection.execute(f'PRAGMA user_version={STORE_VERSION}')
                connection.execute('INSERT INTO store_meta VALUES(1,?,?,?,?,?,?)',
                    (self._descriptor.adapterId,self._descriptor.adapterVersion,str(self._descriptor.streamId),
                     self._fixture_sha,self._fixture_wire_sha,self._fixture_wire))
                connection.execute("INSERT INTO store_state VALUES(1,'null',0,0,0)")
                connection.commit()
                _sync_parent(self.path)
            self._snapshot()
            if connection.execute('PRAGMA journal_mode=WAL').fetchone()[0]!='wal':
                raise StoreError('STORE_CONFIGURATION')
            connection.execute('PRAGMA synchronous=FULL')
            if connection.execute('PRAGMA synchronous').fetchone()[0]!=2:
                raise StoreError('STORE_CONFIGURATION')
            size=connection.execute('PRAGMA page_size').fetchone()[0]
            connection.execute(f'PRAGMA max_page_count={MAX_STORE_BYTES//size}')
        except BaseException as error:
            self.close()
            if isinstance(error,(StoreError,KeyboardInterrupt,SystemExit,asyncio.CancelledError)):raise
            if isinstance(error,AdapterError):raise StoreError(error.code) from None
            raise StoreError('STORE_UNAVAILABLE') from None

    def __enter__(self):return self
    def __exit__(self,*_args):self.close()

    def close(self):
        connection=self._connection
        self._connection=None
        if connection is not None:connection.close()

    def _live(self,*,public=False):
        if self._fenced:raise StoreError('STORE_RESULT_UNKNOWN')
        if self._connection is None:raise StoreError('STORE_CLOSED')
        if public and self._operation:raise StoreError('STORE_OPERATION_IN_PROGRESS')

    def _fence(self):
        self._fenced=True
        try:self.close()
        except (OSError,sqlite3.Error):pass

    def _rollback(self):
        if self._connection is not None and self._connection.in_transaction:self._connection.rollback()

    def _schema(self,connection):
        if (connection.execute('PRAGMA application_id').fetchone()[0]!=APPLICATION_ID
                or connection.execute('PRAGMA user_version').fetchone()[0]!=STORE_VERSION):
            raise StoreError('STORE_INTEGRITY')
        objects=connection.execute('SELECT type,name,tbl_name,sql FROM sqlite_master').fetchall()
        indexes={'sqlite_autoindex_adapter_pages_1':'adapter_pages',
                 'sqlite_autoindex_adapter_records_1':'adapter_records'}
        if {name for _,name,_,_ in objects}!=set(SCHEMA)|set(indexes):
            raise StoreError('STORE_INTEGRITY')
        for kind,name,table,sql in objects:
            if name in SCHEMA:
                if kind!='table' or table!=name or not isinstance(sql,str) or _sql_normal(sql)!=_sql_normal(SCHEMA[name]):
                    raise StoreError('STORE_INTEGRITY')
            elif kind!='index' or table!=indexes[name] or sql is not None:
                raise StoreError('STORE_INTEGRITY')
        if connection.execute('PRAGMA integrity_check(1)').fetchall()!=[('ok',)]:
            raise StoreError('STORE_INTEGRITY')

    def _read_current(self,budget):
        c=self._connection;budget.check()
        if c.execute('PRAGMA page_count').fetchone()[0]*c.execute('PRAGMA page_size').fetchone()[0]>MAX_STORE_BYTES:
            raise StoreError('STORE_LIMIT')
        self._schema(c)
        if (c.execute('SELECT count(*) FROM store_meta').fetchone()[0]!=1
                or c.execute('SELECT count(*) FROM store_state').fetchone()[0]!=1):
            raise StoreError('STORE_INTEGRITY')
        if (c.execute('SELECT count(*) FROM adapter_pages').fetchone()[0]>MAX_ACCEPTED_PAGES
                or c.execute('SELECT count(*) FROM adapter_records').fetchone()[0]>MAX_ADMITTED_RECORDS):
            raise StoreError('STORE_LIMIT')
        size=c.execute('SELECT length(fixture_wire) FROM store_meta WHERE id=1').fetchone()
        if size is None or type(size[0]) is not int or size[0]>MAX_FIXTURE_BYTES:
            raise StoreError('STORE_LIMIT')
        meta=c.execute('SELECT adapter_id,adapter_version,stream_id,fixture_sha,fixture_wire_sha,fixture_wire FROM store_meta WHERE id=1').fetchone()
        if (meta is None or meta[:4]!=(self._descriptor.adapterId,self._descriptor.adapterVersion,
                str(self._descriptor.streamId),self._fixture_sha)):
            raise StoreError('STORE_BINDING_MISMATCH')
        if not isinstance(meta[5],bytes) or _sha(meta[5])!=meta[4]:raise StoreError('STORE_INTEGRITY')
        stored=SyntheticReplayAdapter(meta[5])
        if _sha(_encode(_decode(stored.fixture_wire,MAX_FIXTURE_BYTES)))!=meta[3]:raise StoreError('STORE_INTEGRITY')
        for table,column,limit in (('adapter_pages','wire',MAX_PAGE_BYTES),('adapter_records','record_json',MAX_PAGE_BYTES)):
            maximum=c.execute(f'SELECT coalesce(max(length(CAST({column} AS BLOB))),0) FROM {table}').fetchone()[0]
            if maximum>limit:raise StoreError('STORE_LIMIT')
        admission=AdapterAdmission(self._descriptor.adapterId,self._descriptor.adapterVersion,self._descriptor.streamId)
        pages=c.execute('SELECT ordinal,after_key,next_key,canonical_sha,wire_sha,wire,received_at_utc FROM adapter_pages ORDER BY ordinal').fetchall()
        for index,(ordinal,after,next_key,canonical_sha,wire_sha,wire,received) in enumerate(pages):
            budget.check()
            if type(ordinal) is not int or ordinal!=index or not isinstance(wire,bytes) or _sha(wire)!=wire_sha:
                raise StoreError('STORE_INTEGRITY')
            parsed=validate_page(wire)
            if (_sha(parsed.canonical)!=canonical_sha or self._expected_pages.get(after)!=canonical_sha
                    or after!=_key(parsed.model.afterToken) or next_key!=_key(parsed.model.nextToken)):
                raise StoreError('STORE_INTEGRITY')
            result=admission.admit(wire,_receipt(received))
            if result['status'] not in ('ADMITTED','END_OF_SYNTHETIC_INPUT'):
                raise StoreError('STORE_INTEGRITY')
        records=c.execute('SELECT record_id,record_sha,record_json,first_received_at_utc FROM adapter_records ORDER BY record_id').fetchall()
        expected=[]
        for entry in admission.records:
            budget.check();encoded=_encode(entry['record'])
            expected.append((entry['record']['recordId'],_sha(encoded),encoded.decode('utf-8'),entry['firstReceivedAtUtc']))
        if records!=sorted(expected):raise StoreError('STORE_INTEGRITY')
        state=c.execute('SELECT cursor_key,ended,page_count,record_count FROM store_state WHERE id=1').fetchone()
        if (state!=(_key(admission.cursor),int(admission.ended),len(pages),len(records))
                or any(type(v) is not int for v in state[1:])):
            raise StoreError('STORE_INTEGRITY')
        budget.check()
        return admission

    def _proof(self):
        meta=self._connection.execute('SELECT id,adapter_id,adapter_version,stream_id,fixture_sha,fixture_wire_sha FROM store_meta').fetchone()
        pages=tuple(self._connection.execute('SELECT ordinal,after_key,next_key,canonical_sha,wire_sha,received_at_utc FROM adapter_pages ORDER BY ordinal'))
        return meta,pages

    def _snapshot(self,expected_confirmation=None):
        self._live();budget=_Budget();c=self._connection
        c.set_progress_handler(budget.progress,1000)
        try:
            c.execute('BEGIN')
            result=self._read_current(budget)
            if expected_confirmation is not None:
                expected_page,expected_meta,expected_prefix=expected_confirmation
                if expected_page is not None:
                    after_key,canonical_sha,wire_sha,receipt=expected_page
                    if c.execute('SELECT canonical_sha,wire_sha,received_at_utc FROM adapter_pages WHERE after_key=?',
                            (after_key,)).fetchone()!=(canonical_sha,wire_sha,receipt):
                        raise StoreError('STORE_INTEGRITY')
                meta,pages=self._proof()
                if meta!=expected_meta or pages[:len(expected_prefix)]!=expected_prefix:
                    raise StoreError('STORE_INTEGRITY')
            c.commit()
            return result
        except BaseException as error:
            try:self._rollback()
            except (OSError,sqlite3.Error):self._fence()
            if isinstance(error,(StoreError,KeyboardInterrupt,SystemExit,asyncio.CancelledError)):raise
            if isinstance(error,AdapterError):raise StoreError('STORE_INTEGRITY') from None
            limited=budget.steps>=MAX_SQL_STEPS or time.monotonic()>=budget.deadline
            raise StoreError('STORE_LIMIT' if limited else 'STORE_INTEGRITY') from None
        finally:
            if self._connection is not None:self._connection.set_progress_handler(None,0)

    def _public_snapshot(self):self._live(public=True);return self._snapshot()
    @property
    def committed_cursor(self):return self._public_snapshot().cursor
    @property
    def ended(self):return self._public_snapshot().ended
    @property
    def records(self):return self._public_snapshot().records

    def _write_candidate(self,candidate,validated,receipt):
        c=self._connection;ordinal=c.execute('SELECT page_count FROM store_state WHERE id=1').fetchone()[0]
        c.execute('INSERT INTO adapter_pages VALUES(?,?,?,?,?,?,?)',
            (ordinal,_key(validated.model.afterToken),_key(validated.model.nextToken),_sha(validated.canonical),
             _sha(validated.wire),validated.wire,receipt))
        existing={row[0] for row in c.execute('SELECT record_id FROM adapter_records')}
        for entry in candidate.records:
            record=entry['record']
            if record['recordId'] in existing:continue
            encoded=_encode(record)
            c.execute('INSERT INTO adapter_records VALUES(?,?,?,?)',
                (record['recordId'],_sha(encoded),encoded.decode('utf-8'),entry['firstReceivedAtUtc']))
        counts=candidate.report()['counts']
        c.execute('UPDATE store_state SET cursor_key=?,ended=?,page_count=?,record_count=? WHERE id=1',
            (_key(candidate.cursor),int(candidate.ended),counts['acceptedPages'],counts['acceptedRecords']))

    def _commit(self):self._connection.commit()
    def _confirm_after_commit(self,expected_confirmation):return self._snapshot(expected_confirmation)

    def commit_page(self,payload,received_at):
        self._live(public=True)
        self._operation=True;attempted=False;budget=_Budget();c=self._connection
        c.set_progress_handler(budget.progress,1000)
        try:
            receipt=_utc(received_at)
            validated=validate_page(payload)
            c.execute('BEGIN IMMEDIATE')
            candidate=self._read_current(budget)
            outcome=candidate.admit(payload,received_at)
            if self._expected_pages.get(_key(validated.model.afterToken))!=_sha(validated.canonical):
                raise StoreError('FIXTURE_PAGE_MISMATCH')
            changed=outcome['status'] in ('ADMITTED','END_OF_SYNTHETIC_INPUT')
            counts=candidate.report()['counts']
            if counts['acceptedPages']>MAX_ACCEPTED_PAGES or counts['acceptedRecords']>MAX_ADMITTED_RECORDS:
                raise StoreError('STORE_LIMIT')
            if changed:self._write_candidate(candidate,validated,receipt)
            expected_page=None
            if outcome['status']!='NO_NEW_DATA':
                saved=c.execute('SELECT canonical_sha,wire_sha,received_at_utc FROM adapter_pages WHERE after_key=?',
                    (_key(validated.model.afterToken),)).fetchone()
                if saved is None:raise StoreError('STORE_INTEGRITY')
                wanted_wire=_sha(validated.wire) if changed else saved[1]
                wanted_receipt=receipt if changed else saved[2]
                if saved!=(_sha(validated.canonical),wanted_wire,wanted_receipt):raise StoreError('STORE_INTEGRITY')
                expected_page=(_key(validated.model.afterToken),_sha(validated.canonical),wanted_wire,wanted_receipt)
            expected_meta,expected_prefix=self._proof()
            confirmation=(expected_page,expected_meta,expected_prefix)
            budget.check();attempted=True
            self._commit()
            confirmed=self._confirm_after_commit(confirmation)
            page_status=('LOCAL_STORE_COMMITTED' if changed else (
                'ALREADY_LOCAL_STORE_COMMITTED' if outcome['status']=='RETRANSMISSION' else 'NO_NEW_PAGE'))
            return {'status':'COMMITTED' if changed else outcome['status'],'pageCommitStatus':page_status,
                'persistenceStatus':'LOCAL_STORE_COMMITTED' if page_status!='NO_NEW_PAGE' else 'NO_NEW_PAGE_COMMITTED',
                'committedCursor':confirmed.cursor,'ended':confirmed.ended,
                'newRecords':outcome['acceptedRecords'],'legacyArchiveDelivery':'NOT_CONNECTED',
                'sourceAcknowledgement':'NOT_SENT'}
        except BaseException as error:
            if attempted:
                self._fence()
                if isinstance(error,(KeyboardInterrupt,SystemExit,asyncio.CancelledError)):raise
                raise StoreError('STORE_RESULT_UNKNOWN') from None
            try:self._rollback()
            except (OSError,sqlite3.Error):
                self._fence()
                raise StoreError('STORE_RESULT_UNKNOWN') from None
            if isinstance(error,(StoreError,KeyboardInterrupt,SystemExit,asyncio.CancelledError)):raise
            if isinstance(error,AdapterError):raise StoreError(error.code) from None
            code=getattr(error,'sqlite_errorcode',0)
            full=(isinstance(code,int) and code&255==sqlite3.SQLITE_FULL) or getattr(error,'errno',None)==errno.ENOSPC
            limited=budget.steps>=MAX_SQL_STEPS or time.monotonic()>=budget.deadline
            raise StoreError('STORE_LIMIT' if limited else ('STORE_FULL' if full else 'STORE_WRITE_FAILED')) from None
        finally:
            self._operation=False
            if self._connection is not None:self._connection.set_progress_handler(None,0)

    def report(self):
        state=self._public_snapshot();report=state.report();counts=report['counts']
        return {'schemaVersion':'adapter-store-report-1',
            'persistenceStatus':'LOCAL_STORE_COMMITTED' if counts['acceptedPages'] else 'NO_COMMITTED_PAGE',
            'legacyArchiveDelivery':'NOT_CONNECTED','sourceAcknowledgement':'NOT_SENT',
            'declaredOrigin':'SYNTHETIC','sourceAccessMode':'READ_ONLY','localStoreAccess':'READ_WRITE',
            'originEvidence':'DECLARED_ONLY','realEquipmentSupported':False,'hardRealtimeGuaranteed':False,
            'fixtureCanonicalSha256':self._fixture_sha,
            'counts':{'committedPages':counts['acceptedPages'],'committedRecords':counts['acceptedRecords'],
                'missingSourceTimestampRecords':counts['missingSourceTimestampRecords'],
                'missingSourceSequenceRecords':counts['missingSourceSequenceRecords'],
                'missingSourceRunRecords':counts['missingSourceRunRecords']}}


class DurableAdapterSession:
    """Standalone fixture process only: synchronous commit blocks its async loop."""
    def __init__(self,adapter,store,*,timeout_ms=100,max_attempts=2,utc_now=None):
        if type(adapter) is not SyntheticReplayAdapter or type(store) is not AdapterStore:
            raise StoreError('UNSUPPORTED_ADAPTER')
        if (type(timeout_ms) is not int or not 1<=timeout_ms<=10000
                or type(max_attempts) is not int or not 1<=max_attempts<=3):raise StoreError('INVALID_READ_POLICY')
        if _sha(_encode(_decode(adapter.fixture_wire,MAX_FIXTURE_BYTES)))!=store._fixture_sha:
            raise StoreError('STORE_BINDING_MISMATCH')
        self.adapter,self.store=adapter,store
        self.timeout_ms,self.max_attempts=timeout_ms,max_attempts
        self._now=utc_now or (lambda:datetime.now(timezone.utc));self._lock=asyncio.Lock()

    async def poll_once(self):
        async with self._lock:
            state=self.store._public_snapshot()
            if state.ended:
                return {'status':'END_OF_SYNTHETIC_INPUT','pageCommitStatus':'ALREADY_LOCAL_STORE_COMMITTED',
                    'legacyArchiveDelivery':'NOT_CONNECTED','sourceAcknowledgement':'NOT_SENT'}
            request=AdapterReadRequest(state.cursor)
            for attempt in range(self.max_attempts):
                try:
                    async with asyncio.timeout(self.timeout_ms/1000):payload=await self.adapter.read(request)
                    received=self._now();break
                except TimeoutError:
                    if attempt+1==self.max_attempts:raise AdapterError('ADAPTER_TIMEOUT') from None
                except AdapterError:raise
                except Exception:raise AdapterError('SOURCE_UNAVAILABLE') from None
            if validate_page(payload).model.afterToken!=request.after_token:raise AdapterError('RESPONSE_CURSOR_MISMATCH')
            # Deliberately no executor and no await across the local transaction/confirmation.
            return self.store.commit_page(payload,received)


async def _run(adapter,store,timeout_ms,max_attempts,max_pages):
    if type(max_pages) is not int or not 1<=max_pages<=MAX_ACCEPTED_PAGES:raise StoreError('INVALID_READ_POLICY')
    session=DurableAdapterSession(adapter,store,timeout_ms=timeout_ms,max_attempts=max_attempts)
    committed_here=0
    for _ in range(max_pages):
        if store.ended:break
        outcome=await session.poll_once()
        if outcome['pageCommitStatus']=='LOCAL_STORE_COMMITTED':committed_here+=1
        if outcome['status']=='NO_NEW_DATA':break
    else:
        if not store.ended:raise StoreError('READ_LIMIT')
    result=store.report()
    result['status']='END_OF_SYNTHETIC_INPUT' if store.ended else 'NO_NEW_DATA'
    result['counts'].update(thisRunCommittedPages=committed_here,pageReads=adapter.read_calls)
    return result


def main(argv=None):
    class Parser(argparse.ArgumentParser):
        def error(self,_message):raise StoreError('INVALID_ARGUMENTS')
    parser=Parser(description=__doc__)
    parser.add_argument('--fixture',required=True);parser.add_argument('--store',required=True)
    parser.add_argument('--timeout-ms',type=int,default=100);parser.add_argument('--max-attempts',type=int,default=2)
    parser.add_argument('--max-pages',type=int,default=64);reporting=False;result=None
    try:
        args=parser.parse_args(argv)
        adapter=SyntheticReplayAdapter(_load_fixture(args.fixture))
        if not 1<=args.timeout_ms<=10000 or not 1<=args.max_attempts<=3 or not 1<=args.max_pages<=64:
            raise StoreError('INVALID_READ_POLICY')
        with AdapterStore(args.store,adapter) as store:result=asyncio.run(_run(adapter,store,args.timeout_ms,args.max_attempts,args.max_pages))
        reporting=True;print(_encode(result).decode('utf-8'));sys.stdout.flush();return 0
    except (AdapterError,OSError,sqlite3.Error,ValueError,TypeError,OverflowError,RecursionError):
        error=sys.exception()
        if reporting and isinstance(error,OSError):_discard_failed_stdout()
        code=error.code if isinstance(error,AdapterError) else (
            'STORE_RESULT_UNKNOWN' if result is not None and result['persistenceStatus']=='LOCAL_STORE_COMMITTED'
            else 'STORE_UNAVAILABLE')
        print(json.dumps({'error':code}),file=sys.stderr)
        return 2


if __name__=='__main__':raise SystemExit(main())
