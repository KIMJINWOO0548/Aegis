"""Explicit nullable input archive; independent of raw-v1 and fixture adapter stores.

Origin is a producer declaration, never verification of a device. Delivery tokens
and record identities do not supply missing source timestamps, sequences or runs.
"""

import asyncio
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
import errno
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import time
from typing import Literal

from aegis.adapter_input import (AdapterError, AdapterPage, MAX_ACCEPTED_PAGES,
    MAX_ADMITTED_RECORDS, MAX_PAGE_BYTES, _decode, _encode)


APPLICATION_ID=0x49415231
STORE_VERSION=1
MAX_STORE_BYTES=32*1024*1024
MAX_DESCRIPTOR_BYTES=4096
MAX_SECONDS=5.0
MAX_SQL_STEPS=5_000_000
DESCRIPTOR_FIELDS=('adapterId','adapterVersion','streamId','declaredOrigin',
    'originEvidence','acquisitionMethod','sourceAccessMode')
PROVENANCE_FIELDS=('declaredOrigin','originEvidence','acquisitionMethod','sourceAccessMode')
SCHEMA={
 'stream_meta':'''CREATE TABLE stream_meta (
 id INTEGER PRIMARY KEY CHECK(id=1), descriptor_json TEXT NOT NULL, descriptor_sha TEXT NOT NULL)''',
 'archive_pages':'''CREATE TABLE archive_pages (
 ordinal INTEGER PRIMARY KEY CHECK(ordinal>=0), after_key TEXT NOT NULL UNIQUE,
 next_key TEXT NOT NULL, canonical_sha TEXT NOT NULL, wire_sha TEXT NOT NULL,
 wire BLOB NOT NULL, received_at_utc TEXT NOT NULL)''',
 'archive_records':'''CREATE TABLE archive_records (
 record_id TEXT PRIMARY KEY NOT NULL, record_sha TEXT NOT NULL, record_json TEXT NOT NULL,
 first_received_at_utc TEXT NOT NULL)''',
 'archive_state':'''CREATE TABLE archive_state (
 id INTEGER PRIMARY KEY CHECK(id=1), cursor_key TEXT NOT NULL,
 ended INTEGER NOT NULL CHECK(ended IN (0,1)), page_count INTEGER NOT NULL CHECK(page_count>=0),
 record_count INTEGER NOT NULL CHECK(record_count>=0))'''}


class StoreError(AdapterError):
    pass


class ArchiveWritePage(AdapterPage):
    schemaVersion: Literal['archive-write-1']


@dataclass(frozen=True)
class ValidatedWritePage:
    model: ArchiveWritePage
    canonical: bytes
    wire: bytes

    @property
    def original(self):return json.loads(self.canonical)


def validate_write_page(payload):
    """Validate shared input fields while retaining unnormalized producer JSON."""
    try:
        original=_decode(payload,MAX_PAGE_BYTES)
        parsed=ArchiveWritePage.model_validate(original)
        return ValidatedWritePage(parsed,_encode(original),payload)
    except AdapterError as error:
        raise StoreError(error.code) from None
    except (ValueError,TypeError,OverflowError,RecursionError):
        raise StoreError('INVALID_PAGE') from None


def _descriptor_wire(descriptor):
    try:
        if (type(descriptor) is not dict or set(descriptor)!=set(DESCRIPTOR_FIELDS)
                or any(type(descriptor[key]) is not str for key in DESCRIPTOR_FIELDS)):
            raise StoreError('INVALID_DESCRIPTOR')
        ArchiveWritePage.model_validate({'schemaVersion':'archive-write-1',**descriptor,
            'afterToken':None,'nextToken':None,'endOfStream':False,'records':[]})
        encoded=_encode(descriptor)
        if len(encoded)>MAX_DESCRIPTOR_BYTES:raise StoreError('INVALID_DESCRIPTOR')
        return encoded
    except StoreError:raise
    except (ValueError,TypeError,OverflowError,RecursionError):
        raise StoreError('INVALID_DESCRIPTOR') from None


class _Budget:
    def __init__(self):self.deadline=time.monotonic()+MAX_SECONDS;self.steps=0
    def check(self):
        if time.monotonic()>=self.deadline or self.steps>=MAX_SQL_STEPS:
            raise StoreError('STORE_LIMIT')
    def progress(self):self.steps+=1000;self.check();return 0


def _sha(value):return hashlib.sha256(value).hexdigest()
def _key(token):return _encode(token).decode('utf-8')
def _sql_normal(value):return ' '.join(value.split()).lower()


def _utc(value):
    try:
        if not isinstance(value,datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise StoreError('INVALID_RECEIPT')
        return value.astimezone(timezone.utc).isoformat().replace('+00:00','Z')
    except (ValueError,TypeError,OverflowError):
        raise StoreError('INVALID_RECEIPT') from None


def _receipt(value):
    try:
        if not isinstance(value,str) or len(value)>64:raise StoreError('STORE_INTEGRITY')
        result=datetime.fromisoformat(value.replace('Z','+00:00'))
        if _utc(result)!=value:raise StoreError('STORE_INTEGRITY')
        return result
    except (AdapterError,ValueError,TypeError,OverflowError):
        raise StoreError('STORE_INTEGRITY') from None


@dataclass(frozen=True)
class StoredWritePage:
    ordinal: int
    after_key: str
    next_key: str
    canonical_sha: str
    wire_sha: str
    wire: bytes
    received_at_utc: str


@dataclass(frozen=True)
class InputArchiveSnapshot:
    """Fresh verified memory only; properties return independent JSON objects."""
    _descriptor_wire: bytes
    _record_rows: tuple=()
    pages: tuple=()
    cursor: str | None=None
    ended: bool=False
    _tokens: frozenset=field(default_factory=lambda:frozenset({None}))

    @property
    def descriptor(self):return json.loads(self._descriptor_wire)

    @property
    def records(self):
        descriptor=self.descriptor
        return tuple({'record':json.loads(encoded),'firstReceivedAtUtc':receipt,
            'provenance':{key:descriptor[key] for key in PROVENANCE_FIELDS}}
            for _identity,_digest,encoded,receipt in self._record_rows)


def _admit(current,validated,receipt,budget):
    """Construct a full candidate without changing a database or an earlier snapshot."""
    original=validated.original;page=validated.model
    if _encode({key:original[key] for key in DESCRIPTOR_FIELDS})!=current._descriptor_wire:
        raise StoreError('DESCRIPTOR_MISMATCH')
    digest=_sha(validated.canonical);after_key=_key(page.afterToken)
    prior=next((p for p in current.pages if p.after_key==after_key),None)
    if prior is not None:
        if prior.canonical_sha!=digest:raise StoreError('REPLAY_CONFLICT')
        return current,{'status':'RETRANSMISSION','newRecords':0}
    if current.ended or page.afterToken!=current.cursor:raise StoreError('CURSOR_MISMATCH')
    if not page.records and not page.endOfStream:
        return current,{'status':'NO_NEW_DATA','newRecords':0}
    if len(current.pages)>=MAX_ACCEPTED_PAGES:raise StoreError('STORE_LIMIT')
    if page.records and page.nextToken in current._tokens:raise StoreError('DELIVERY_TOKEN_LOOP')
    existing={entry[0]:entry for entry in current._record_rows};pending=[]
    for record in original['records']:
        budget.check();encoded=_encode(record);record_sha=_sha(encoded)
        previous=existing.get(record['recordId'])
        if previous is not None:
            if previous[1]!=record_sha:raise StoreError('REPLAY_CONFLICT')
        else:pending.append((record['recordId'],record_sha,encoded.decode('utf-8'),receipt))
    if len(existing)+len(pending)>MAX_ADMITTED_RECORDS:raise StoreError('STORE_LIMIT')
    evidence=StoredWritePage(len(current.pages),after_key,_key(page.nextToken),digest,
        _sha(validated.wire),validated.wire,receipt)
    following=replace(current,_record_rows=(*current._record_rows,*pending),
        pages=(*current.pages,evidence),cursor=page.nextToken,ended=page.endOfStream,
        _tokens=current._tokens|({page.nextToken} if page.records else set()))
    return following,{'status':'ADMITTED' if page.records else 'END_OF_INPUT','newRecords':len(pending)}


def validate_input_archive_schema(connection):
    """Read only; caller owns the connection and transaction lifecycle."""
    if (connection.execute('PRAGMA application_id').fetchone()[0]!=APPLICATION_ID
            or connection.execute('PRAGMA user_version').fetchone()[0]!=STORE_VERSION):
        raise StoreError('STORE_INTEGRITY')
    indexes={'sqlite_autoindex_archive_pages_1':'archive_pages',
        'sqlite_autoindex_archive_records_1':'archive_records'}
    objects=connection.execute('SELECT type,name,tbl_name,sql FROM sqlite_master').fetchall()
    if {name for _,name,_,_ in objects}!=set(SCHEMA)|set(indexes):raise StoreError('STORE_INTEGRITY')
    for kind,name,table,sql in objects:
        if name in SCHEMA:
            if kind!='table' or table!=name or not isinstance(sql,str) or _sql_normal(sql)!=_sql_normal(SCHEMA[name]):
                raise StoreError('STORE_INTEGRITY')
        elif kind!='index' or table!=indexes[name] or sql is not None:raise StoreError('STORE_INTEGRITY')
    if connection.execute('PRAGMA integrity_check(1)').fetchall()!=[('ok',)]:raise StoreError('STORE_INTEGRITY')


def validate_input_archive_snapshot(connection,budget,*,expected_descriptor=None) -> InputArchiveSnapshot:
    """Read a committed prefix in the caller's stable snapshot, with no SQL writes.

    No migration, source call, transaction, journal configuration or progress
    handler is installed here. The writer and read-only query path share checks.
    """
    c=connection;budget.check()
    if c.execute('PRAGMA page_count').fetchone()[0]*c.execute('PRAGMA page_size').fetchone()[0]>MAX_STORE_BYTES:
        raise StoreError('STORE_LIMIT')
    validate_input_archive_schema(c)
    if (c.execute('SELECT count(*) FROM stream_meta').fetchone()[0]!=1
            or c.execute('SELECT count(*) FROM archive_state').fetchone()[0]!=1):
        raise StoreError('STORE_INTEGRITY')
    if (c.execute('SELECT count(*) FROM archive_pages').fetchone()[0]>MAX_ACCEPTED_PAGES
            or c.execute('SELECT count(*) FROM archive_records').fetchone()[0]>MAX_ADMITTED_RECORDS):
        raise StoreError('STORE_LIMIT')
    for table,column,limit in (('stream_meta','descriptor_json',MAX_DESCRIPTOR_BYTES),
            ('archive_pages','wire',MAX_PAGE_BYTES),('archive_records','record_json',MAX_PAGE_BYTES)):
        maximum=c.execute(f'SELECT coalesce(max(length(CAST({column} AS BLOB))),0) FROM {table}').fetchone()[0]
        if maximum>limit:raise StoreError('STORE_LIMIT')
    meta=c.execute('SELECT descriptor_json,descriptor_sha FROM stream_meta WHERE id=1').fetchone()
    if meta is None or type(meta[0]) is not str:raise StoreError('STORE_INTEGRITY')
    if expected_descriptor is not None and _descriptor_wire(expected_descriptor).decode('utf-8')!=meta[0]:
        raise StoreError('STORE_BINDING_MISMATCH')
    encoded=meta[0].encode('utf-8')
    if _sha(encoded)!=meta[1]:raise StoreError('STORE_INTEGRITY')
    try:
        descriptor_wire=_descriptor_wire(_decode(encoded,MAX_DESCRIPTOR_BYTES))
    except (AdapterError,ValueError,TypeError,OverflowError,RecursionError):
        raise StoreError('STORE_INTEGRITY') from None
    if descriptor_wire!=encoded:raise StoreError('STORE_INTEGRITY')
    current=InputArchiveSnapshot(descriptor_wire)
    pages=c.execute('SELECT ordinal,after_key,next_key,canonical_sha,wire_sha,wire,received_at_utc FROM archive_pages ORDER BY ordinal').fetchall()
    for index,(ordinal,after_key,next_key,canonical_sha,wire_sha,wire,receipt) in enumerate(pages):
        budget.check()
        if type(ordinal) is not int or ordinal!=index or type(wire) is not bytes or _sha(wire)!=wire_sha:
            raise StoreError('STORE_INTEGRITY')
        try:
            validated=validate_write_page(wire);_receipt(receipt)
            if (_sha(validated.canonical)!=canonical_sha or after_key!=_key(validated.model.afterToken)
                    or next_key!=_key(validated.model.nextToken)):
                raise StoreError('STORE_INTEGRITY')
            current,outcome=_admit(current,validated,receipt,budget)
            if outcome['status'] not in ('ADMITTED','END_OF_INPUT'):raise StoreError('STORE_INTEGRITY')
        except AdapterError as error:
            if error.code=='STORE_LIMIT':raise
            raise StoreError('STORE_INTEGRITY') from None
    records=c.execute('SELECT record_id,record_sha,record_json,first_received_at_utc FROM archive_records ORDER BY record_id').fetchall()
    if records!=sorted(current._record_rows):raise StoreError('STORE_INTEGRITY')
    state=c.execute('SELECT cursor_key,ended,page_count,record_count FROM archive_state WHERE id=1').fetchone()
    if (state!=(_key(current.cursor),int(current.ended),len(pages),len(records))
            or any(type(value) is not int for value in state[1:])):
        raise StoreError('STORE_INTEGRITY')
    budget.check();return current


def _regular_info(path):
    info=os.lstat(path)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1:raise StoreError('UNSAFE_STORE_PATH')
    return info


def _check_header(path):
    _regular_info(path)
    fd=os.open(path,os.O_RDONLY|os.O_NONBLOCK|os.O_NOFOLLOW)
    try:
        info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1:raise StoreError('UNSAFE_STORE_PATH')
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


class InputArchive:
    """One explicit stream, synchronous local durability, no source acknowledgement."""
    def __init__(self,path,descriptor):
        self._connection=None;self._fenced=False;self._operation=False
        try:
            if os.name!='posix' or not hasattr(os,'O_NOFOLLOW') or not hasattr(os,'O_DIRECTORY'):
                raise StoreError('PLATFORM_UNVERIFIED')
            self._descriptor_wire=_descriptor_wire(descriptor)
            self._descriptor=json.loads(self._descriptor_wire)
            if not isinstance(path,(str,Path)) or not 1<=len(str(path))<=4096 or '\x00' in str(path):
                raise StoreError('UNSAFE_STORE_PATH')
            self.path=Path(path).absolute()
            if self.path.name.lower().endswith(('-wal','-shm','-journal')):raise StoreError('UNSAFE_STORE_PATH')
            if not stat.S_ISDIR(os.stat(self.path.parent).st_mode):raise StoreError('UNSAFE_STORE_PATH')
            try:_regular_info(self.path);fresh=False
            except FileNotFoundError:fresh=True
            for suffix in ('-wal','-shm','-journal'):
                try:
                    _regular_info(Path(str(self.path)+suffix))
                    if fresh:raise StoreError('STORE_COMPANION_EXISTS')
                except FileNotFoundError:pass
            if fresh:
                fd=os.open(self.path,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
                os.close(fd)
            else:_check_header(self.path)  # Refuse other schemas before SQLite opens sidecars.
            c=sqlite3.connect(self.path.as_uri()+'?mode=rw',uri=True,timeout=.2,isolation_level=None)
            self._connection=c;c.execute('PRAGMA trusted_schema=OFF')
            if fresh:
                c.execute('PRAGMA journal_mode=DELETE');c.execute('PRAGMA synchronous=EXTRA')
                c.execute('BEGIN IMMEDIATE')
                for statement in SCHEMA.values():c.execute(statement)
                c.execute(f'PRAGMA application_id={APPLICATION_ID}');c.execute(f'PRAGMA user_version={STORE_VERSION}')
                c.execute('INSERT INTO stream_meta VALUES(1,?,?)',
                    (self._descriptor_wire.decode('utf-8'),_sha(self._descriptor_wire)))
                c.execute("INSERT INTO archive_state VALUES(1,'null',0,0,0)")
                c.commit();_sync_parent(self.path)
            self._snapshot()
            if c.execute('PRAGMA journal_mode=WAL').fetchone()[0]!='wal':raise StoreError('STORE_CONFIGURATION')
            c.execute('PRAGMA synchronous=FULL')
            if c.execute('PRAGMA synchronous').fetchone()[0]!=2:raise StoreError('STORE_CONFIGURATION')
            size=c.execute('PRAGMA page_size').fetchone()[0]
            c.execute(f'PRAGMA max_page_count={MAX_STORE_BYTES//size}')
        except BaseException as error:
            self.close()
            if isinstance(error,(StoreError,KeyboardInterrupt,SystemExit,asyncio.CancelledError)):raise
            if isinstance(error,AdapterError):raise StoreError(error.code) from None
            raise StoreError('STORE_UNAVAILABLE') from None

    def __enter__(self):return self
    def __exit__(self,*_args):self.close()

    def close(self):
        connection=self._connection;self._connection=None
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

    def _read_current(self,budget):
        return validate_input_archive_snapshot(self._connection,budget,expected_descriptor=self._descriptor)

    def _proof(self):
        meta=self._connection.execute('SELECT id,descriptor_json,descriptor_sha FROM stream_meta').fetchone()
        pages=tuple(self._connection.execute('SELECT ordinal,after_key,next_key,canonical_sha,wire_sha,wire,received_at_utc FROM archive_pages ORDER BY ordinal'))
        return meta,pages

    def _assert_confirmation(self,expected_confirmation):
        expected_page,expected_meta,expected_prefix=expected_confirmation
        if expected_page is not None:
            after_key,canonical_sha,wire_sha,wire,receipt=expected_page
            if self._connection.execute('SELECT canonical_sha,wire_sha,wire,received_at_utc FROM archive_pages WHERE after_key=?',
                    (after_key,)).fetchone()!=(canonical_sha,wire_sha,wire,receipt):raise StoreError('STORE_INTEGRITY')
        meta,pages=self._proof()
        if meta!=expected_meta or pages[:len(expected_prefix)]!=expected_prefix:raise StoreError('STORE_INTEGRITY')

    def _snapshot(self,expected_confirmation=None):
        self._live();budget=_Budget();c=self._connection;c.set_progress_handler(budget.progress,1000)
        try:
            c.execute('BEGIN');result=self._read_current(budget)
            if expected_confirmation is not None:self._assert_confirmation(expected_confirmation)
            budget.check();c.commit();return result
        except BaseException as error:
            try:self._rollback()
            except (OSError,sqlite3.Error):self._fence()
            if isinstance(error,(StoreError,KeyboardInterrupt,SystemExit,asyncio.CancelledError)):raise
            limited=budget.steps>=MAX_SQL_STEPS or time.monotonic()>=budget.deadline
            raise StoreError('STORE_LIMIT' if limited else 'STORE_INTEGRITY') from None
        finally:
            if self._connection is not None:self._connection.set_progress_handler(None,0)

    def refresh(self):self._live(public=True);return self._snapshot()
    @property
    def committed_cursor(self):return self.refresh().cursor
    @property
    def ended(self):return self.refresh().ended
    @property
    def records(self):return self.refresh().records

    def _write_candidate(self,candidate,validated,receipt):
        c=self._connection;ordinal=c.execute('SELECT page_count FROM archive_state WHERE id=1').fetchone()[0]
        c.execute('INSERT INTO archive_pages VALUES(?,?,?,?,?,?,?)',
            (ordinal,_key(validated.model.afterToken),_key(validated.model.nextToken),_sha(validated.canonical),
             _sha(validated.wire),validated.wire,receipt))
        existing={row[0] for row in c.execute('SELECT record_id FROM archive_records')}
        for row in candidate._record_rows:
            if row[0] not in existing:c.execute('INSERT INTO archive_records VALUES(?,?,?,?)',row)
        c.execute('UPDATE archive_state SET cursor_key=?,ended=?,page_count=?,record_count=? WHERE id=1',
            (_key(candidate.cursor),int(candidate.ended),len(candidate.pages),len(candidate._record_rows)))

    def _commit(self):self._connection.commit()
    def _confirm_after_commit(self,expected_confirmation):return self._snapshot(expected_confirmation)

    def append_page(self,payload,received_at):
        self._live(public=True);self._operation=True;attempted=False;budget=_Budget();c=self._connection
        c.set_progress_handler(budget.progress,1000)
        try:
            receipt=_utc(received_at);validated=validate_write_page(payload)
            c.execute('BEGIN IMMEDIATE');current=self._read_current(budget)
            # Pin existing evidence before the write hook, then confirm it again after COMMIT.
            expected_meta,expected_prefix=self._proof()
            candidate,outcome=_admit(current,validated,receipt,budget)
            changed=outcome['status'] in ('ADMITTED','END_OF_INPUT')
            expected_page=None
            if outcome['status']!='NO_NEW_DATA':
                saved=next(p for p in candidate.pages if p.after_key==_key(validated.model.afterToken))
                expected_page=(saved.after_key,saved.canonical_sha,saved.wire_sha,saved.wire,saved.received_at_utc)
            confirmation=(expected_page,expected_meta,expected_prefix)
            if changed:self._write_candidate(candidate,validated,receipt)
            self._assert_confirmation(confirmation)
            # Validate what will be committed, including records and the cursor, not just the page.
            self._read_current(budget);budget.check();attempted=True;self._commit()
            confirmed=self._confirm_after_commit(confirmation)
            page_status=('LOCAL_ARCHIVE_COMMITTED' if changed else (
                'ALREADY_LOCAL_ARCHIVE_COMMITTED' if outcome['status']=='RETRANSMISSION' else 'NO_NEW_PAGE'))
            return {'status':'COMMITTED' if changed else outcome['status'],'pageCommitStatus':page_status,
                'persistenceStatus':'LOCAL_ARCHIVE_COMMITTED' if page_status!='NO_NEW_PAGE' else 'NO_NEW_PAGE_COMMITTED',
                'committedCursor':confirmed.cursor,'ended':confirmed.ended,'newRecords':outcome['newRecords'],
                'sourceAcknowledgement':'NOT_SENT'}
        except BaseException as error:
            if attempted:
                self._fence()
                if isinstance(error,(KeyboardInterrupt,SystemExit,asyncio.CancelledError)):raise
                raise StoreError('STORE_RESULT_UNKNOWN') from None
            try:self._rollback()
            except (OSError,sqlite3.Error):
                self._fence();raise StoreError('STORE_RESULT_UNKNOWN') from None
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
        current=self.refresh();source=[entry['record']['sourceMetadata'] for entry in current.records]
        descriptor=current.descriptor
        return {'schemaVersion':'input-archive-report-1',
            'persistenceStatus':'LOCAL_ARCHIVE_COMMITTED' if current.pages else 'NO_COMMITTED_PAGE',
            'descriptor':descriptor,**{key:descriptor[key] for key in PROVENANCE_FIELDS},
            'localArchiveAccess':'READ_WRITE','sourceAcknowledgement':'NOT_SENT',
            'counts':{'committedPages':len(current.pages),'committedRecords':len(source),
                'missingSourceTimestampRecords':sum(m['observedAtUtc'] is None for m in source),
                'missingSourceSequenceRecords':sum(m['sequence'] is None for m in source),
                'missingSourceRunRecords':sum(m['runId'] is None for m in source)}}
