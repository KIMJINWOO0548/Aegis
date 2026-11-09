"""Read-only, bounded archive/storage observations and explicit advisory policies."""

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import errno
import hashlib
from itertools import islice
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import stat
import sys
import time
from uuid import UUID

from aegis.archive_backup import BackupError, _inspect, _reader, _discard_failed_stdout
from aegis.raw_history import RawQueryError, _constant, _object, _timestamp


MAX_JSON_BYTES = 64 * 1024
MAX_OUTPUT_BYTES = 64 * 1024
MAX_BACKUPS = 32
MAX_ARCHIVE_ROWS = 10_000
MAX_SECONDS = 5.0
MAX_SQL_STEPS = 5_000_000
SUFFIXES = ('', '-wal', '-shm', '-journal')
INTEGER_LIMITS = {'archiveReceiptRetentionDays':(1,36500), 'backupMtimeReviewDays':(1,36500),
                  'minAvailableBytes':(0,2**63-1), 'maxTelemetryAgeSeconds':(1,86400)}
NUMBER_LIMITS = {'minAvailablePercent':(0,100), 'maxReceiptAgeSeconds':(0,3153600000),
                 'maxArchiveCommitMs':(0,3600000)}


class DiagnosticError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


class _Budget:
    def __init__(self):
        self.deadline = time.monotonic() + MAX_SECONDS
        self.steps = 0

    def check(self):
        if time.monotonic() >= self.deadline or self.steps >= MAX_SQL_STEPS:
            raise DiagnosticError('LIMIT_EXCEEDED')

    def progress(self):
        self.steps += 1000
        self.check()
        return 0


def _json(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode('utf-8')


def _freeze(value):
    encoded = _json(value)
    if len(encoded) > MAX_JSON_BYTES:
        raise DiagnosticError('JSON_LIMIT')
    return json.loads(encoded)


def _policy(value):
    if value is None:
        return {}, {'status':'NOT_CONFIGURED'}
    value = _freeze(value)
    required = {'format','policyId','version'}
    allowed = required | set(INTEGER_LIMITS) | set(NUMBER_LIMITS)
    if (not isinstance(value,dict) or not required <= set(value) <= allowed
            or value['format'] != 'aegis-storage-policy-1'
            or not isinstance(value['policyId'],str)
            or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}',value['policyId'])
            or type(value['version']) is not int or not 1 <= value['version'] <= 2**31-1):
        raise DiagnosticError('INVALID_POLICY')
    for fields, integral in ((INTEGER_LIMITS,True),(NUMBER_LIMITS,False)):
        for key,(low,high) in fields.items():
            v = value.get(key)
            if key in value and (type(v) not in ((int,) if integral else (int,float))
                    or not math.isfinite(v) or not low <= v <= high):
                raise DiagnosticError('INVALID_POLICY')
    return value, {'status':'CONFIGURED','policyId':value['policyId'],'version':value['version'],
                   'sha256':hashlib.sha256(_json(value)).hexdigest(),'snapshot':value}


def _now(value):
    if value is None:
        return datetime.now(timezone.utc), 'SYSTEM_UTC'
    if isinstance(value,datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise DiagnosticError('INVALID_TIME')
        return value.astimezone(timezone.utc), 'EXPLICIT_UTC'
    return _timestamp(value).astimezone(timezone.utc), 'EXPLICIT_UTC'


def _path(value):
    if not isinstance(value,(str,Path)) or not 1 <= len(str(value)) <= 4096 or '\x00' in str(value):
        raise DiagnosticError('INVALID_PATH')
    return Path(value).absolute()


def _lstat(path):
    return os.lstat(path)


def _statvfs(path):
    return os.statvfs(path)


@contextmanager
def _open_regular(path):
    if os.name!='posix' or not hasattr(os,'O_NOFOLLOW'):
        raise DiagnosticError('PLATFORM_UNVERIFIED')
    fd = os.open(path,os.O_RDONLY|os.O_NONBLOCK|os.O_NOFOLLOW)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise DiagnosticError('INVALID_FILE_TYPE')
        with os.fdopen(fd,'rb',closefd=False) as stream:
            yield stream
    finally:
        os.close(fd)


def _fs_error(error):
    if error.errno == errno.ENOENT:
        return 'MISSING'
    if error.errno in (errno.EACCES,errno.EPERM):
        return 'PERMISSION_DENIED'
    return 'IO_ERROR'


def _regular(path, *, directory=False):
    try:
        result = _lstat(path)
        if not (stat.S_ISDIR(result.st_mode) if directory else stat.S_ISREG(result.st_mode)):
            return 'UNSUPPORTED_TYPE', None
        return 'MEASURED', result
    except OSError as error:
        return _fs_error(error), None


def _inventory(paths):
    seen = set()
    apparent = allocated = duplicate = 0
    allocation_known = True
    errors = []
    mtimes = []
    for path,optional in paths:
        status, info = _regular(path)
        if status != 'MEASURED':
            if not (optional and status == 'MISSING'):
                errors.append(status)
            continue
        key = (info.st_dev,info.st_ino)
        if key in seen:
            duplicate += 1
            continue
        seen.add(key)
        apparent += info.st_size
        blocks = getattr(info,'st_blocks',None)
        if type(blocks) is int and blocks >= 0:
            allocated += blocks * 512
        else:
            allocation_known = False
        mtimes.append(info.st_mtime)
    status = errors[0] if errors and not seen else ('PARTIAL' if errors else 'MEASURED')
    result = {'status':status,'uniqueFiles':len(seen),'duplicateFiles':duplicate,
              'apparentBytes':apparent if not errors else None,
              'allocatedBytesEstimate':allocated if allocation_known and not errors else None,
              'allocationBasis':'FILESYSTEM_REPORTED_512_BYTE_BLOCKS','errorStatuses':sorted(set(errors))}
    return result, mtimes, seen


def _capacity(path):
    status, info = _regular(path,directory=True)
    if status != 'MEASURED':
        return {'status':status}, None
    try:
        result = _statvfs(path)
        unit, blocks, available = result.f_frsize,result.f_blocks,result.f_bavail
        if (any(type(v) is not int for v in (unit,blocks,available))
                or unit <= 0 or blocks <= 0 or not 0 <= available <= blocks):
            return {'status':'INVALID_MEASUREMENT'}, None
        return {'status':'MEASURED','totalBytes':unit*blocks,'availableBytes':unit*available,
                'availablePercent':100*available/blocks,
                'availabilityBasis':'STATVFS_NON_SUPERUSER_AVAILABLE',
                'readOnly':bool(result.f_flag & getattr(os,'ST_RDONLY',1)),
                'quotaGuarantee':False}, info.st_dev
    except OSError as error:
        return {'status':_fs_error(error)}, None


def _archive(path, policy, at, budget):
    status, _info = _regular(path)
    if status != 'MEASURED':
        return {'status':status}, {}
    try:
        budget.check()
        with _open_regular(path) as stream:
            header = stream.read(20)
        if header[:16]==b'SQLite format 3\x00' and 2 in header[18:20]:
            # Ordinary mode=ro WAL connections may create missing WAL/SHM files.
            # Do not initialize a closed WAL archive merely to diagnose it.
            for suffix in ('-wal','-shm'):
                state, _ = _regular(Path(str(path)+suffix))
                if state != 'MEASURED':
                    return {'status':state if state in ('PERMISSION_DENIED','IO_ERROR') else 'DB_UNAVAILABLE',
                            'reason':'WAL_COMPANIONS_REQUIRED'}, {}
        with _reader(path,budget) as connection:
            count = connection.execute('SELECT count(*) FROM raw_batches').fetchone()[0]
            if count > MAX_ARCHIVE_ROWS:
                raise DiagnosticError('LIMIT_EXCEEDED')
            snapshot = _inspect(connection,budget)
            logical = connection.execute('PRAGMA page_count').fetchone()[0] * connection.execute('PRAGMA page_size').fetchone()[0]
            ages = []
            for (receipt,) in connection.execute('SELECT received_at_utc FROM raw_batches'):
                budget.check()
                ages.append((at-_timestamp(receipt)).total_seconds())
            cursors = {}
            for device,run,sequence,receipt in connection.execute(
                    'SELECT c.device_id,c.run_id,c.sequence,b.received_at_utc FROM ingest_cursors c '
                    'JOIN raw_batches b ON c.device_id=b.device_id AND c.run_id=b.run_id AND c.sequence=b.sequence'):
                cursors[device] = (UUID(run),sequence,(at-_timestamp(receipt)).total_seconds())
            budget.check()
        retention = policy.get('archiveReceiptRetentionDays')
        return {'status':'MEASURED','rawRows':count,'cursorRows':snapshot['tables']['ingest_cursors']['rows'],
                'gapRows':snapshot['tables']['ingest_gaps']['rows'],'logicalBytes':logical,
                'latestReceiptAgeSeconds':min(ages) if ages else None,
                'oldestReceiptAgeSeconds':max(ages) if ages else None,
                'receiptStatus':'EMPTY' if not ages else ('CLOCK_SKEW' if min(ages)<0 else 'MEASURED'),
                'rowsPastReceiptRetention':sum(age>retention*86400 for age in ages) if retention is not None else None,
                'nextBackupEstimate':{'classification':'ESTIMATE','bytes':logical,
                    'basis':'SQLITE_LOGICAL_PAGES_ONLY','guaranteedToFit':False}}, cursors
    except DiagnosticError:
        return {'status':'LIMIT_EXCEEDED'}, {}
    except BackupError as error:
        return {'status':'LIMIT_EXCEEDED' if error.code=='RESOURCE_LIMIT' else 'INVALID'}, {}
    except OSError as error:
        return {'status':_fs_error(error)}, {}
    except sqlite3.Error as error:
        limited = time.monotonic() >= budget.deadline or budget.steps >= MAX_SQL_STEPS
        code = getattr(error,'sqlite_errorcode',0) & 255
        return {'status':'LIMIT_EXCEEDED' if limited else (
            'DB_UNAVAILABLE' if code in (sqlite3.SQLITE_CANTOPEN,sqlite3.SQLITE_BUSY,sqlite3.SQLITE_LOCKED,
                                        sqlite3.SQLITE_READONLY,sqlite3.SQLITE_AUTH) else 'INVALID')}, {}
    except (ValueError,TypeError,OverflowError,RawQueryError):
        return {'status':'INVALID'}, {}


def _commit(telemetry, cursors, at, policy):
    if telemetry is None:
        return {'status':'NOT_MEASURED','basis':'LAST_REPORTED_APPEND_AND_CURSOR_DURATION'}
    try:
        value = _freeze(telemetry)
        if (not isinstance(value,dict) or set(value)!={'format','capturedAtUtc','ingestion'}
                or value['format']!='aegis-ingestion-capture-1' or not isinstance(value['ingestion'],list)
                or not 1 <= len(value['ingestion']) <= 32):
            raise ValueError
        age = (at-_timestamp(value['capturedAtUtc'])).total_seconds()
        if age < 0:
            return {'status':'CLOCK_SKEW'}
        rows = {}
        for row in value['ingestion']:
            if not isinstance(row,dict) or not isinstance(row.get('deviceId'),str) or row['deviceId'] in rows:
                raise ValueError
            rows[row['deviceId']] = row
        if not cursors:
            return {'status':'ARCHIVE_UNAVAILABLE'}
        readings = []
        for device,(run,sequence,receipt_age) in cursors.items():
            row = rows.get(device)
            if (row is None or row.get('transport')!='replay' or row.get('archiveEnabled') is not True
                    or type(row.get('lastCommittedSequence')) is not int
                    or row['lastCommittedSequence']!=sequence or UUID(row['lastCommittedRunId'])!=run):
                return {'status':'IDENTITY_MISMATCH'}
            ms = row.get('archiveCommitMs')
            if ms is None:
                return {'status':'NOT_MEASURED'}
            if type(ms) not in (int,float) or not math.isfinite(ms) or ms<0:
                raise ValueError
            readings.append(ms)
        recency = policy.get('maxTelemetryAgeSeconds')
        if recency is None:
            return {'status':'RECENCY_POLICY_NOT_CONFIGURED'}
        if any(age>receipt_age for _,_,receipt_age in cursors.values()):
            return {'status':'CLOCK_SKEW'}
        if age>recency or any(receipt_age>recency for _,_,receipt_age in cursors.values()):
            return {'status':'STALE'}
        if any(receipt_age<0 for _,_,receipt_age in cursors.values()):
            return {'status':'CLOCK_SKEW'}
        return {'status':'MEASURED_LAST_COMMIT','worstArchiveCommitMs':max(readings),
                'measuredSources':len(readings),'captureAgeSeconds':age,
                'basis':'LAST_REPORTED_APPEND_AND_CURSOR_DURATION','captureTrust':'OPERATOR_SUPPLIED_UNSIGNED',
                'commitTimestampStatus':'NOT_RECORDED'}
    except (ValueError,TypeError,OverflowError,KeyError,DiagnosticError,AttributeError,RecursionError):
        return {'status':'INVALID'}


def _check(configured, available, exceeds):
    return {'status':'NOT_CONFIGURED' if not configured else (
        'UNAVAILABLE' if not available else ('EXCEEDED' if exceeds else 'WITHIN_LIMIT'))}


def diagnose_storage(archive_path,backup_dir,*,backups=(),policy=None,telemetry=None,now=None):
    try:
        if os.name!='posix' or not hasattr(os,'statvfs'):
            raise DiagnosticError('PLATFORM_UNVERIFIED')
        p, policy_report = _policy(policy)
        at, clock = _now(now)
        archive_path, backup_dir = _path(archive_path), _path(backup_dir)
        selected = list(islice(iter(backups),MAX_BACKUPS+1))
        if len(selected)>MAX_BACKUPS:
            raise DiagnosticError('BACKUP_LIMIT')
        selected = [_path(path) for path in selected]
        budget = _Budget()
        archive, cursors = _archive(archive_path,p,at,budget)
        footprint,_,archive_ids = _inventory([(Path(str(archive_path)+suffix),bool(suffix)) for suffix in SUFFIXES])
        archive['footprint'] = footprint
        backup_footprint, mtimes, backup_ids = _inventory([(path,False) for path in selected])
        backup_ages = [at.timestamp()-stamp for stamp in mtimes]
        backups_report = {'status':backup_footprint['status'] if selected else 'NOT_CONFIGURED',
            'footprint':backup_footprint,'integrity':'NOT_VERIFIED','creationTimeStatus':'NOT_RECORDED',
            'oldestMtimeAgeSeconds':max(backup_ages) if backup_ages else None,
            'mtimeStatus':'CLOCK_SKEW' if any(age<0 for age in backup_ages) else ('MEASURED' if backup_ages else 'EMPTY'),
            'overlapWithArchive':bool(archive_ids & backup_ids)}
        ca, ida = _capacity(archive_path.parent)
        cb, idb = _capacity(backup_dir)
        commit = _commit(telemetry,cursors,at,p)
        valid_receipts = archive['status']=='MEASURED' and archive['receiptStatus']=='MEASURED'
        latest,oldest = archive.get('latestReceiptAgeSeconds'),archive.get('oldestReceiptAgeSeconds')
        check = {
            'archiveRetention':_check(p.get('archiveReceiptRetentionDays') is not None,valid_receipts,
                oldest is not None and oldest>p.get('archiveReceiptRetentionDays',0)*86400 if p.get('archiveReceiptRetentionDays') is not None else False),
            'receiptAge':_check(p.get('maxReceiptAgeSeconds') is not None,valid_receipts,
                latest is not None and latest>p.get('maxReceiptAgeSeconds',0) if p.get('maxReceiptAgeSeconds') is not None else False),
            'backupMtimeReview':_check(p.get('backupMtimeReviewDays') is not None,
                backups_report['status']=='MEASURED' and backups_report['mtimeStatus']=='MEASURED',
                bool(backup_ages) and max(backup_ages)>p.get('backupMtimeReviewDays',0)*86400 if p.get('backupMtimeReviewDays') is not None else False),
            'commitLatency':_check(p.get('maxArchiveCommitMs') is not None,
                commit['status']=='MEASURED_LAST_COMMIT',commit.get('worstArchiveCommitMs',0)>p.get('maxArchiveCommitMs',0) if p.get('maxArchiveCommitMs') is not None else False)}
        for name,capacity in (('archiveCapacity',ca),('backupCapacity',cb)):
            configured = p.get('minAvailableBytes') is not None or p.get('minAvailablePercent') is not None
            exceeds = ((p.get('minAvailableBytes') is not None and capacity.get('availableBytes',0)<p['minAvailableBytes'])
                or (p.get('minAvailablePercent') is not None and capacity.get('availablePercent',0)<p['minAvailablePercent']))
            check[name] = _check(configured,capacity['status']=='MEASURED' and not capacity.get('readOnly',False),exceeds)
        report = {'format':'aegis-storage-diagnostics-1','evaluatedAtUtc':at.isoformat().replace('+00:00','Z'),
            'clockMode':clock,'policy':policy_report,'archive':archive,'backups':backups_report,
            'capacity':{'archive':ca,'backup':cb,'sameFilesystem':ida==idb if ida is not None and idb is not None else None},
            'checks':check,'commitLatency':commit,'sampling':'NON_ATOMIC','sourceCompleteness':'NOT_ASSERTED',
            'actionsPerformed':[],'mutationScope':'NO_APPLICATION_DATA_WRITES_OR_DELETIONS',
            'sqliteReaderEffects':'EXISTING_WAL_SHARED_MEMORY_MAY_CHANGE'}
        if len(_json(report))>MAX_OUTPUT_BYTES:
            raise DiagnosticError('OUTPUT_LIMIT')
        return report
    except DiagnosticError:
        raise
    except (ValueError,TypeError,OverflowError,OSError,RecursionError):
        raise DiagnosticError('INVALID_INPUT') from None


def _load(path):
    with _open_regular(path) as stream:
        encoded = stream.read(MAX_JSON_BYTES+1)
    if len(encoded)>MAX_JSON_BYTES:
        raise DiagnosticError('JSON_LIMIT')
    value = json.loads(encoded,object_pairs_hook=_object,parse_constant=_constant)
    if not isinstance(value,dict):
        raise DiagnosticError('INVALID_JSON_OBJECT')
    return value


def main(argv=None):
    class Parser(argparse.ArgumentParser):
        def error(self,_message):
            raise DiagnosticError('INVALID_ARGUMENTS')
    parser = Parser(description=__doc__)
    parser.add_argument('--archive',required=True)
    parser.add_argument('--backup-dir',required=True)
    parser.add_argument('--backup',action='append',default=[])
    parser.add_argument('--policy')
    parser.add_argument('--telemetry')
    parser.add_argument('--at')
    reporting = False
    try:
        args = parser.parse_args(argv)
        result = diagnose_storage(args.archive,args.backup_dir,backups=args.backup,
            policy=_load(args.policy) if args.policy is not None else None,
            telemetry=_load(args.telemetry) if args.telemetry is not None else None,now=args.at)
        reporting = True
        print(_json(result).decode('utf-8'))
        sys.stdout.flush()
        return 0
    except (DiagnosticError,ValueError,TypeError,OverflowError,OSError,RecursionError):
        error = sys.exception()
        if reporting and isinstance(error,OSError):
            _discard_failed_stdout()
        print(json.dumps({'error':error.code if isinstance(error,DiagnosticError) else 'INVALID_INPUT'}),file=sys.stderr)
        return 2


if __name__=='__main__':
    raise SystemExit(main())
