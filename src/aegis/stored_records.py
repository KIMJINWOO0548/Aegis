"""Opt-in bounded read projection of raw-v1 and nullable local adapter receipts."""

import argparse
from contextlib import contextmanager
from datetime import timedelta
import hashlib
from itertools import islice
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import sys
import time

from aegis.adapter_input import AdapterError, _encode
from aegis.adapter_store import validate_adapter_snapshot
from aegis.archive import COLUMNS
from aegis.archive_backup import BackupError, _inspect, _discard_failed_stdout
from aegis.raw_history import RawQueryError, _timestamp, _object, _constant


MAX_SOURCES = 4
MAX_RAW_ROWS = 10_000
MAX_RECORDS = 64
MAX_SIGNALS = 16
MAX_OUTPUT_BYTES = 4 * 1024 * 1024
MAX_CONFIG_BYTES = 64 * 1024
MAX_SECONDS = 5.0
MAX_SQL_STEPS = 5_000_000
ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z')
STORE_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z')
HEX = re.compile(r'[0-9a-f]{64}\Z')
KINDS = {'RAW_V1', 'ADAPTER_STORE_V1'}


class QueryError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class _Budget:
    def __init__(self):
        self.deadline = time.monotonic() + MAX_SECONDS
        self.steps = 0
        self.exhausted = False

    def check(self):
        if time.monotonic() >= self.deadline or self.steps >= MAX_SQL_STEPS:
            self.exhausted = True
            raise QueryError('QUERY_LIMIT')

    def progress(self):
        self.steps += 1000
        try:
            self.check()
            return 0
        except QueryError:
            return 1


def _utc(value):
    return value.isoformat().replace('+00:00', 'Z')


def _parameters(start, end, device, signals, maximum):
    try:
        start, end = _timestamp(start), _timestamp(end)
        if not timedelta(0) < end-start <= timedelta(days=1):
            raise ValueError
        if type(maximum) is not int or not 1 <= maximum <= MAX_RECORDS:
            raise ValueError
        if device is not None and (not isinstance(device, str) or not ID.fullmatch(device)):
            raise ValueError
        if signals is not None:
            if isinstance(signals, str):
                if len(signals) > MAX_SIGNALS*129:
                    raise ValueError
                signals = signals.split(',')
            elif isinstance(signals, (list, tuple)):
                signals = list(signals)
            else:
                raise ValueError
            if (not 1 <= len(signals) <= MAX_SIGNALS
                    or any(not isinstance(s, str) or not ID.fullmatch(s) for s in signals)
                    or len(set(signals)) != len(signals)):
                raise ValueError
        return start, end, signals
    except (ValueError, TypeError, OverflowError, RecursionError):
        raise QueryError('INVALID_QUERY') from None


def _sources(values):
    try:
        if isinstance(values, (str, bytes, dict)):
            raise ValueError
        values = list(islice(iter(values), MAX_SOURCES+1))
        if not 1 <= len(values) <= MAX_SOURCES:
            raise ValueError
        result = []
        labels = set()
        for value in values:
            if not isinstance(value, dict) or not {'storeId','kind','path'} <= set(value) <= {
                    'storeId','kind','path','expectedFixtureSha256'}:
                raise ValueError
            label, kind, path = value['storeId'], value['kind'], value['path']
            if (not isinstance(label, str) or not STORE_ID.fullmatch(label) or label in labels
                    or not isinstance(kind, str) or kind not in KINDS
                    or not isinstance(path, (str, Path)) or not 1 <= len(str(path)) <= 4096
                    or '\x00' in str(path)):
                raise ValueError
            pin = value.get('expectedFixtureSha256')
            if 'expectedFixtureSha256' in value and (kind != 'ADAPTER_STORE_V1'
                    or not isinstance(pin, str) or not HEX.fullmatch(pin)):
                raise ValueError
            path = Path(path).absolute()
            if path.name.lower().endswith(('-wal','-shm','-journal')):
                raise ValueError
            labels.add(label)
            result.append(dict(storeId=label, kind=kind, path=path, pin=pin))
        return result
    except (ValueError, TypeError, OverflowError):
        raise QueryError('INVALID_QUERY') from None


def _regular(path):
    info = os.lstat(path)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise QueryError('SOURCE_UNAVAILABLE')
    return info.st_dev, info.st_ino


@contextmanager
def _reader(source, budget):
    path = source['path']
    for suffix in ('-wal','-shm','-journal'):
        companion = Path(str(path)+suffix)
        if os.path.lexists(companion):
            _regular(companion)
    c = sqlite3.connect(path.as_uri()+'?mode=ro', uri=True, timeout=.2, isolation_level=None)
    try:
        if _regular(path) != source['physical']:
            raise QueryError('SOURCE_UNAVAILABLE')
        c.set_progress_handler(budget.progress, 1000)
        c.execute('PRAGMA query_only=ON')
        c.execute('PRAGMA trusted_schema=OFF')
        c.execute('BEGIN')
        c.execute('SELECT count(*) FROM sqlite_master').fetchone()
        yield c
    finally:
        c.close()


def _raw_snapshot(c, budget):
    # Enumerate all objects: LIKE 'sqlite_%' would also hide sqliteX... names.
    objects = c.execute('SELECT type,name,tbl_name FROM sqlite_master').fetchall()
    if any((kind == 'table' and name not in COLUMNS)
           or (kind == 'index' and table not in COLUMNS)
           or kind not in ('table','index') for kind,name,table in objects):
        raise QueryError('SOURCE_INVALID')
    # Validate columns before count/SELECT so a wrong store kind is an integrity error.
    from aegis.raw_history import _schema
    _schema(c)
    if c.execute('SELECT count(*) FROM raw_batches').fetchone()[0] > MAX_RAW_ROWS:
        raise QueryError('QUERY_LIMIT')
    snap = _inspect(c, budget)
    rows = c.execute('SELECT * FROM raw_batches ORDER BY device_id,run_id,sequence')
    result = []
    for device,run,seq,observed,receipt,equipment,module,encoded in rows:
        budget.check()
        original = json.loads(encoded)
        result.append(dict(
            nativeKeyKind='RAW_DEVICE_RUN_SEQUENCE', nativeKey=[device,run,seq], deviceId=device,
            sourceMetadata=dict(observedAtUtc=original['observedAtUtc'],timestampStatus='PROVIDED',
                sequence=original['sequence'],sequenceStatus='PROVIDED',runId=original['runId'],runIdStatus='PROVIDED'),
            firstReceivedAtUtc=receipt,
            provenance=dict(declaredOrigin='SYNTHETIC',originEvidence='DECLARED_ONLY',
                declarationSource='LEGACY_PAYLOAD',producerDeclaration=original['source'],
                acquisitionMethod='NOT_RECORDED',sourceAccessMode='NOT_RECORDED',hardwareIdentityVerified=False),
            hierarchy=dict(equipmentId=equipment,moduleId=module,evidence='STORED_COLUMNS'),
            legacyContext={key:original[key] for key in ('protocolVersion','deviceStateVersion','modeId')},
            signals=original['signals'],
            evidence=dict(recordSha256=hashlib.sha256(encoded.encode('utf-8')).hexdigest(),
                representation='LEGACY_STORED_PAYLOAD_JSON',pageWireSha256=None)))
    return result, dict(gapEvidence=dict(kind='LEGACY_RECORDED_SEQUENCE_GAPS',
        count=snap['tables']['ingest_gaps']['rows'],scope='ENTIRE_STORE',timeCoverage='NOT_INFERRED'),
        commitState='LEGACY_ROWS_SNAPSHOT',bindingEvidence='STORED_SELF_CONSISTENCY')


def _adapter_snapshot(c, budget, pin):
    admission = validate_adapter_snapshot(c, budget, expected_fixture_sha=pin)
    first_pages = {}
    for wire,wire_sha in c.execute('SELECT wire,wire_sha FROM adapter_pages ORDER BY ordinal'):
        budget.check()
        page = json.loads(wire)
        for record in page['records']:
            first_pages.setdefault(record['recordId'], (page,wire_sha))
    hashes = dict(c.execute('SELECT record_id,record_sha FROM adapter_records'))
    d = admission.descriptor
    result = []
    for entry in admission.records:
        budget.check()
        record = entry['record'];page, wire_sha = first_pages[record['recordId']]
        result.append(dict(nativeKeyKind='ADAPTER_DELIVERY_STREAM_RECORD',
            nativeKey=[d.adapterId,d.adapterVersion,str(d.streamId),record['recordId']],
            deviceId=record['deviceId'],sourceMetadata=record['sourceMetadata'],
            firstReceivedAtUtc=entry['firstReceivedAtUtc'],
            provenance=dict(declaredOrigin=page['declaredOrigin'],originEvidence=page['originEvidence'],
                declarationSource='ADAPTER_PAGE',producerDeclaration=page['declaredOrigin'],
                acquisitionMethod=page['acquisitionMethod'],sourceAccessMode=page['sourceAccessMode'],
                hardwareIdentityVerified=False),
            hierarchy=dict(equipmentId=None,moduleId=None,evidence='NOT_RECORDED'),legacyContext=None,
            signals=record['signals'],evidence=dict(recordSha256=hashes[record['recordId']],
                representation='ADAPTER_CANONICAL_RECORD_JSON',pageWireSha256=wire_sha)))
    return result, dict(gapEvidence=dict(kind='NOT_RECORDED',count=None,
        scope='NOT_RECORDED',timeCoverage='NOT_ASSERTED'),commitState='LOCAL_COMMITTED_PREFIX',
        bindingEvidence='EXPECTED_FIXTURE_SHA_MATCHED' if pin else 'STORED_SELF_CONSISTENCY')


def query_stored_records(sources, received_from, received_to, *, device_id=None,
                         signals=None, max_records=MAX_RECORDS):
    """Read validated per-store snapshots; fail whole query on any unavailable source."""
    start,end,selected = _parameters(received_from,received_to,device_id,signals,max_records)
    sources = _sources(sources)
    budget = _Budget()
    try:
        physical = set()
        for source in sources:
            budget.check()
            identity = _regular(source['path'])
            if identity in physical:
                raise QueryError('DUPLICATE_SOURCE')
            physical.add(identity);source['physical'] = identity
        records, reports = [], []
        for source in sources:
            budget.check()
            with _reader(source,budget) as c:
                rows, detail = (_raw_snapshot(c,budget) if source['kind']=='RAW_V1'
                                else _adapter_snapshot(c,budget,source['pin']))
                matched = 0
                for row in rows:
                    budget.check()
                    if not start <= _timestamp(row['firstReceivedAtUtc']) < end:
                        continue
                    if device_id is not None and row['deviceId'] != device_id:
                        continue
                    matched += 1
                    if len(records) >= max_records:
                        raise QueryError('QUERY_LIMIT')
                    readings = {s['signalKey']:{k:s[k] for k in ('value','unit','quality')}
                                for s in row['signals']}
                    keys = selected if selected is not None else list(readings)
                    row['signals'] = [dict(signalKey=k,present=k in readings,reading=readings.get(k)) for k in keys]
                    row['identity'] = dict(storeId=source['storeId'],storeKind=source['kind'],
                        nativeKeyKind=row.pop('nativeKeyKind'),nativeKey=row.pop('nativeKey'))
                    records.append(row)
                reports.append(dict(storeId=source['storeId'],kind=source['kind'],scannedRecords=len(rows),
                    matchedRecords=matched,integrity='VERIFIED_STORED_SNAPSHOT',**detail))
        records.sort(key=lambda r:(_timestamp(r['firstReceivedAtUtc']),r['identity']['storeId'],
                                   _encode(r['identity']['nativeKey'])))
        result = dict(schemaVersion='stored-record-query-1',timeBasis='FIRST_RECEIPT',
            window=dict(receivedFromUtc=_utc(start),receivedToUtc=_utc(end),bounds='[from,to)'),
            readCompatibility='DUAL_STORE_PROJECTION',adapterToRawV1WriteDelivery='NOT_CONNECTED',
            sourceAcknowledgement='NOT_SENT',schemaMigrationPerformed=False,
            crossStoreDeduplication='NOT_PERFORMED_DIFFERENT_NAMESPACES',
            snapshotConsistency='PER_STORE_SNAPSHOT_NO_DISTRIBUTED_ATOMICITY',
            sourceCoverage='NOT_ASSERTED',selectionComplete=True,sources=reports,records=records,
            limits=dict(maxSources=MAX_SOURCES,maxRawRowsPerStore=MAX_RAW_ROWS,
                maxRecords=max_records,maxSelectedSignals=MAX_SIGNALS,maxOutputBytes=MAX_OUTPUT_BYTES,
                cooperativeSeconds=MAX_SECONDS,maxSqlVmSteps=MAX_SQL_STEPS))
        if len(_encode(result)) > MAX_OUTPUT_BYTES:
            raise QueryError('QUERY_LIMIT')
        budget.check()
        return result
    except QueryError:
        raise
    except AdapterError as e:
        code = ('SOURCE_BINDING_MISMATCH' if e.code=='STORE_BINDING_MISMATCH'
                else 'QUERY_LIMIT' if e.code=='STORE_LIMIT' else 'SOURCE_INVALID')
        raise QueryError(code) from None
    except BackupError as e:
        raise QueryError('QUERY_LIMIT' if e.code=='RESOURCE_LIMIT' else 'SOURCE_INVALID') from None
    except RawQueryError as e:
        raise QueryError('QUERY_LIMIT' if e.status==413 else 'SOURCE_INVALID') from None
    except sqlite3.Error as e:
        code = getattr(e,'sqlite_errorcode',0) & 255
        raise QueryError('QUERY_LIMIT' if budget.exhausted else 'SOURCE_INVALID' if code in (
            sqlite3.SQLITE_CORRUPT,sqlite3.SQLITE_NOTADB,sqlite3.SQLITE_SCHEMA) else 'SOURCE_UNAVAILABLE') from None
    except OSError:
        raise QueryError('SOURCE_UNAVAILABLE') from None
    except (ValueError,TypeError,OverflowError,KeyError,RecursionError):
        raise QueryError('SOURCE_INVALID') from None


def _config(path):
    fd = None
    try:
        fd = os.open(path,os.O_RDONLY|os.O_NONBLOCK|os.O_NOFOLLOW)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_CONFIG_BYTES:
            raise ValueError
        data = bytearray()
        while len(data) <= MAX_CONFIG_BYTES:
            chunk = os.read(fd,min(8192,MAX_CONFIG_BYTES+1-len(data)))
            if not chunk:
                break
            data.extend(chunk)
        if len(data)>MAX_CONFIG_BYTES:
            raise ValueError
        result = json.loads(data,object_pairs_hook=_object,parse_constant=_constant)
        if (not isinstance(result,dict) or set(result)!={'schemaVersion','sources'}
                or result['schemaVersion']!='stored-record-sources-1' or not isinstance(result['sources'],list)):
            raise ValueError
        return result['sources']
    except (OSError,ValueError,TypeError,OverflowError,RecursionError):
        raise QueryError('INVALID_QUERY') from None
    finally:
        if fd is not None:
            os.close(fd)


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise QueryError('INVALID_QUERY')


def main(argv=None):
    try:
        p = _Parser(description=__doc__)
        p.add_argument('--sources',required=True)
        p.add_argument('--received-from',required=True)
        p.add_argument('--received-to',required=True)
        p.add_argument('--device')
        p.add_argument('--signals')
        p.add_argument('--max-records',type=int,default=MAX_RECORDS)
        args = p.parse_args(argv)
        result = query_stored_records(_config(args.sources),args.received_from,args.received_to,
            device_id=args.device,signals=args.signals,max_records=args.max_records)
        print(_encode(result).decode('utf-8'),flush=True)
        return 0
    except QueryError as e:
        print(json.dumps({'error':e.code}),file=sys.stderr)
        return 2
    except (OSError,ValueError):
        _discard_failed_stdout()
        print(json.dumps({'error':'OUTPUT_UNAVAILABLE'}),file=sys.stderr)
        return 2


if __name__=='__main__':
    sys.exit(main())
