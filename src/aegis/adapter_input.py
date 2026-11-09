"""Independent input validation and built-in synthetic replay; no legacy storage bridge."""

import argparse
import asyncio
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
import hashlib
import json
import os
import stat
import sys
from types import MappingProxyType
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from aegis.archive_backup import _discard_failed_stdout
from aegis.collector import RawSignal
from aegis.contracts import UtcTimestamp


MAX_FIXTURE_BYTES=1024*1024
MAX_PAGE_BYTES=64*1024
MAX_RECORDS_PER_PAGE=64
MAX_ADMITTED_RECORDS=1024
MAX_ACCEPTED_PAGES=64
MAX_OUTPUT_BYTES=64*1024
Identifier=Annotated[str,Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$')]
Token=Annotated[str,Field(min_length=1,max_length=128,pattern=r'^[\x21-\x7e]+$')]
Provided=Literal['PROVIDED','NOT_PROVIDED']


class AdapterError(Exception):
    def __init__(self,code):
        super().__init__(code)
        self.code=code


class _Model(BaseModel):
    model_config=ConfigDict(extra='forbid',frozen=True,allow_inf_nan=False)


class SourceMetadata(_Model):
    observedAtUtc: UtcTimestamp | None
    timestampStatus: Provided
    sequence: Annotated[int,Field(strict=True,ge=0,le=2**63-1)] | None
    sequenceStatus: Provided
    runId: UUID | None
    runIdStatus: Provided

    @model_validator(mode='after')
    def presence(self):
        for value,status in ((self.observedAtUtc,self.timestampStatus),(self.sequence,self.sequenceStatus),
                             (self.runId,self.runIdStatus)):
            if (value is None)!=(status=='NOT_PROVIDED'):
                raise ValueError('Original metadata presence contradicts its status.')
        return self


class AdapterRecord(_Model):
    recordId: Identifier
    deviceId: Identifier
    sourceMetadata: SourceMetadata
    signals: Annotated[tuple[RawSignal,...],Field(min_length=1,max_length=512)]

    @model_validator(mode='after')
    def unique_signals(self):
        if len({signal.signalKey for signal in self.signals})!=len(self.signals):
            raise ValueError('Duplicate signal identity.')
        return self


class AdapterPage(_Model):
    schemaVersion: Literal['adapter-input-1']
    adapterId: Identifier
    adapterVersion: Literal['1.0']
    streamId: UUID
    declaredOrigin: Literal['SYNTHETIC','DEVICE','UNKNOWN']
    originEvidence: Literal['DECLARED_ONLY']
    acquisitionMethod: Literal['SOURCE_BUFFER_REPLAY','SNAPSHOT_READ']
    sourceAccessMode: Literal['READ_ONLY']
    afterToken: Token | None
    nextToken: Token | None
    endOfStream: Annotated[bool,Field(strict=True)]
    records: Annotated[tuple[AdapterRecord,...],Field(max_length=MAX_RECORDS_PER_PAGE)]

    @model_validator(mode='after')
    def delivery_contract(self):
        if len({record.recordId for record in self.records})!=len(self.records):
            raise ValueError('Duplicate delivery record identity.')
        if self.records:
            if self.nextToken is None or self.nextToken==self.afterToken:
                raise ValueError('A nonempty page needs a new delivery token.')
        elif self.nextToken!=self.afterToken:
            raise ValueError('An empty page cannot advance delivery.')
        return self


class _Descriptor(_Model):
    adapterId: Identifier
    adapterVersion: Literal['1.0']
    streamId: UUID


def _bound_to_descriptor(page,descriptor):
    return ((page.adapterId,page.adapterVersion,page.streamId)==(
        descriptor.adapterId,descriptor.adapterVersion,descriptor.streamId)
        and page.declaredOrigin=='SYNTHETIC' and page.acquisitionMethod=='SOURCE_BUFFER_REPLAY')


def _encode(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode('utf-8')


def _object(pairs):
    result={}
    for key,value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON key.')
        result[key]=value
    return result


def _constant(_value):
    raise ValueError('Nonfinite JSON constant.')


def _decode(payload,limit):
    if not isinstance(payload,bytes) or len(payload)>limit:
        raise AdapterError('INPUT_LIMIT')
    return json.loads(payload,object_pairs_hook=_object,parse_constant=_constant)


@dataclass(frozen=True)
class ValidatedPage:
    model: AdapterPage
    canonical: bytes
    wire: bytes

    @property
    def original(self):
        return json.loads(self.canonical)


def validate_page(payload):
    try:
        original=_decode(payload,MAX_PAGE_BYTES)
        parsed=AdapterPage.model_validate(original)
        # Keep canonical producer JSON, not normalized model_dump values.
        # Exact wire bytes are retained separately for lexical/source evidence.
        canonical=_encode(original)
        return ValidatedPage(parsed,canonical,payload)
    except AdapterError:
        raise
    except (ValueError,TypeError,OverflowError,RecursionError):
        raise AdapterError('INVALID_PAGE') from None


@dataclass(frozen=True)
class _State:
    cursor: str | None=None
    records: dict=field(default_factory=dict)
    pages: dict=field(default_factory=dict)
    wire_pages: tuple=()
    tokens: frozenset=field(default_factory=lambda:frozenset({None}))
    ended: bool=False
    duplicates: int=0
    retransmissions: int=0


class AdapterAdmission:
    """Single-event-loop admission proposal; never a durable archive cursor or ACK."""
    def __init__(self,adapter_id,adapter_version,stream_id):
        try:
            self.descriptor=_Descriptor(adapterId=adapter_id,adapterVersion=adapter_version,streamId=stream_id)
        except (ValueError,TypeError,OverflowError):
            raise AdapterError('INVALID_DESCRIPTOR') from None
        self._state=_State()

    @property
    def cursor(self):return self._state.cursor

    @property
    def ended(self):return self._state.ended

    @property
    def records(self):
        return tuple({'record':json.loads(encoded),'firstReceivedAtUtc':receipt,
                      'declaredOrigin':'SYNTHETIC','acquisitionMethod':'SOURCE_BUFFER_REPLAY'}
                     for _digest,encoded,receipt in self._state.records.values())

    @property
    def wire_pages(self):return self._state.wire_pages

    def admit(self,payload,received_at):
        try:
            if (not isinstance(received_at,datetime) or received_at.tzinfo is None
                    or received_at.utcoffset() is None):
                raise AdapterError('INVALID_RECEIPT')
            receipt=received_at.astimezone(timezone.utc).isoformat().replace('+00:00','Z')
            validated=validate_page(payload)
            page=validated.model
            if not _bound_to_descriptor(page,self.descriptor):
                raise AdapterError('DESCRIPTOR_MISMATCH')
            state=self._state
            digest=hashlib.sha256(validated.canonical).hexdigest()
            prior=state.pages.get(page.afterToken)
            if prior is not None:
                if prior[0]!=digest:
                    raise AdapterError('REPLAY_CONFLICT')
                self._state=replace(state,retransmissions=state.retransmissions+1)
                return {'status':'RETRANSMISSION','acceptedRecords':0,'duplicateRecords':len(page.records),
                        'persistenceStatus':'NOT_PERSISTED'}
            if state.ended or page.afterToken!=state.cursor:
                raise AdapterError('CURSOR_MISMATCH')
            if not page.records:
                if page.endOfStream:
                    if len(state.pages)>=MAX_ACCEPTED_PAGES:
                        raise AdapterError('ADMISSION_LIMIT')
                    self._state=replace(state,ended=True,
                        pages={**state.pages,page.afterToken:(digest,0)},
                        wire_pages=(*state.wire_pages,validated.wire))
                return {'status':'END_OF_SYNTHETIC_INPUT' if page.endOfStream else 'NO_NEW_DATA',
                        'acceptedRecords':0,'duplicateRecords':0,'persistenceStatus':'NOT_PERSISTED'}
            if page.nextToken in state.tokens:
                raise AdapterError('DELIVERY_TOKEN_LOOP')
            pending={}
            duplicates=0
            for original in validated.original['records']:
                encoded=_encode(original)
                record_hash=hashlib.sha256(encoded).hexdigest()
                existing=state.records.get(original['recordId'])
                if existing is not None:
                    if existing[0]!=record_hash:
                        raise AdapterError('REPLAY_CONFLICT')
                    duplicates+=1
                else:
                    pending[original['recordId']]=(record_hash,encoded,receipt)
            if len(state.records)+len(pending)>MAX_ADMITTED_RECORDS or len(state.pages)>=MAX_ACCEPTED_PAGES:
                raise AdapterError('ADMISSION_LIMIT')
            # Construct the whole next state first; one publication, no awaits or source writes.
            following=_State(page.nextToken,{**state.records,**pending},
                {**state.pages,page.afterToken:(digest,len(page.records))},
                (*state.wire_pages,validated.wire),state.tokens|{page.nextToken},page.endOfStream,
                state.duplicates+duplicates,state.retransmissions)
            self._state=following
            return {'status':'ADMITTED','acceptedRecords':len(pending),'duplicateRecords':duplicates,
                    'persistenceStatus':'NOT_PERSISTED'}
        except AdapterError:
            raise
        except (ValueError,TypeError,OverflowError,RecursionError):
            raise AdapterError('INVALID_PAGE') from None

    def report(self):
        source=[json.loads(encoded)['sourceMetadata'] for _,encoded,_ in self._state.records.values()]
        return {'schemaVersion':'adapter-validation-report-1','declaredOrigin':'SYNTHETIC',
            'originEvidence':'DECLARED_ONLY','acquisitionMethod':'SOURCE_BUFFER_REPLAY',
            'sourceAccessMode':'READ_ONLY','persistenceStatus':'NOT_PERSISTED',
            'legacyArchiveIntegration':'DISABLED','realEquipmentSupported':False,'hardRealtimeGuaranteed':False,
            'counts':{'acceptedRecords':len(source),'duplicateRecords':self._state.duplicates,
                'acceptedPages':len(self._state.pages),'retransmittedPages':self._state.retransmissions,
                'missingSourceTimestampRecords':sum(m['observedAtUtc'] is None for m in source),
                'missingSourceSequenceRecords':sum(m['sequence'] is None for m in source),
                'missingSourceRunRecords':sum(m['runId'] is None for m in source)}}


class _FixtureEntry(_Model):
    afterToken: Token | None
    response: AdapterPage
    delayMs: Annotated[int,Field(strict=True,ge=0,le=10000)]=0


class _Fixture(_Model):
    schemaVersion: Literal['synthetic-adapter-fixture-1']
    adapterId: Identifier
    adapterVersion: Literal['1.0']
    streamId: UUID
    pages: Annotated[tuple[_FixtureEntry,...],Field(min_length=1,max_length=64)]


@dataclass(frozen=True)
class AdapterReadRequest:
    after_token: str | None
    limit: int=MAX_RECORDS_PER_PAGE

    def __post_init__(self):
        if (self.after_token is not None and (type(self.after_token) is not str
                or not 1<=len(self.after_token)<=128
                or any(not 33<=ord(c)<=126 for c in self.after_token))
                or type(self.limit) is not int or not 1<=self.limit<=MAX_RECORDS_PER_PAGE):
            raise AdapterError('INVALID_READ_REQUEST')


class SyntheticReplayAdapter:
    """Only immutable, caller-selected synthetic fixture bytes; no URL or plugin execution."""
    def __init__(self,fixture):
        try:
            payload=fixture if isinstance(fixture,bytes) else _encode(fixture)
            original=_decode(payload,MAX_FIXTURE_BYTES)
            parsed=_Fixture.model_validate(original)
            self.descriptor=_Descriptor(adapterId=parsed.adapterId,adapterVersion=parsed.adapterVersion,streamId=parsed.streamId)
            pages={}
            models={}
            total=0
            for index,entry in enumerate(parsed.pages):
                if entry.afterToken in pages or entry.response.afterToken!=entry.afterToken:
                    raise AdapterError('INVALID_FIXTURE')
                if not _bound_to_descriptor(entry.response,self.descriptor):
                    raise AdapterError('DESCRIPTOR_MISMATCH')
                raw=_encode(original['pages'][index]['response'])
                validate_page(raw)
                total+=len(entry.response.records)
                pages[entry.afterToken]=(raw,entry.delayMs,len(entry.response.records))
                models[entry.afterToken]=entry.response
            if total>MAX_ADMITTED_RECORDS:
                raise AdapterError('INPUT_LIMIT')
            cursor=None
            visited=set()
            while True:
                if cursor in visited or cursor not in models:
                    raise AdapterError('INVALID_FIXTURE_CHAIN')
                visited.add(cursor)
                page=models[cursor]
                if page.endOfStream or not page.records:
                    break
                cursor=page.nextToken
            if len(visited)!=len(models):
                raise AdapterError('UNREACHABLE_FIXTURE_PAGE')
            self._pages=MappingProxyType(pages)
            self._fixture_wire=payload
            self.read_calls=0
        except AdapterError:
            raise
        except (ValueError,TypeError,OverflowError,RecursionError):
            raise AdapterError('INVALID_FIXTURE') from None

    @property
    def fixture_wire(self):return self._fixture_wire

    async def read(self,request):
        if type(request) is not AdapterReadRequest:
            raise AdapterError('INVALID_READ_REQUEST')
        self.read_calls+=1
        item=self._pages.get(request.after_token)
        if item is None:
            raise AdapterError('UNKNOWN_DELIVERY_CURSOR')
        payload,delay,count=item
        if type(request.limit) is not int or not 1<=request.limit<=MAX_RECORDS_PER_PAGE or count>request.limit:
            raise AdapterError('READ_LIMIT')
        await asyncio.sleep(delay/1000)
        return payload


class AdapterSession:
    def __init__(self,adapter,*,timeout_ms=100,max_attempts=2,utc_now=None):
        if type(adapter) is not SyntheticReplayAdapter:
            raise AdapterError('UNSUPPORTED_ADAPTER')
        if (type(timeout_ms) is not int or not 1<=timeout_ms<=10000
                or type(max_attempts) is not int or not 1<=max_attempts<=3):
            raise AdapterError('INVALID_READ_POLICY')
        self.adapter=adapter
        d=adapter.descriptor
        self.admission=AdapterAdmission(d.adapterId,d.adapterVersion,d.streamId)
        self.timeout_ms,self.max_attempts=timeout_ms,max_attempts
        self._now=utc_now or (lambda:datetime.now(timezone.utc))
        self._lock=asyncio.Lock()

    @property
    def cursor(self):return self.admission.cursor

    async def poll_once(self):
        async with self._lock:
            if self.admission.ended:
                return {'status':'END_OF_SYNTHETIC_INPUT','acceptedRecords':0,'duplicateRecords':0,
                        'persistenceStatus':'NOT_PERSISTED'}
            request=AdapterReadRequest(self.cursor)
            for attempt in range(self.max_attempts):
                try:
                    async with asyncio.timeout(self.timeout_ms/1000):
                        payload=await self.adapter.read(request)
                    received_at=self._now()
                    page=validate_page(payload)
                    if page.model.afterToken!=request.after_token:
                        raise AdapterError('RESPONSE_CURSOR_MISMATCH')
                    return self.admission.admit(payload,received_at)
                except TimeoutError:
                    if attempt+1==self.max_attempts:
                        raise AdapterError('ADAPTER_TIMEOUT') from None
                except AdapterError:
                    raise
                except Exception:
                    raise AdapterError('SOURCE_UNAVAILABLE') from None

    def report(self):
        result=self.admission.report()
        result['counts']['pageReads']=self.adapter.read_calls
        result['fixtureSha256']=hashlib.sha256(self.adapter.fixture_wire).hexdigest()
        result['responseEncoding']='CANONICAL_PARSED_JSON_FLOAT64_SIGNALS'
        return result


def _load_fixture(path):
    if os.name!='posix' or not hasattr(os,'O_NOFOLLOW'):
        raise AdapterError('PLATFORM_UNVERIFIED')
    fd=os.open(path,os.O_RDONLY|os.O_NONBLOCK|os.O_NOFOLLOW)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise AdapterError('INVALID_FILE_TYPE')
        with os.fdopen(fd,'rb',closefd=False) as source:
            payload=source.read(MAX_FIXTURE_BYTES+1)
        if len(payload)>MAX_FIXTURE_BYTES:
            raise AdapterError('INPUT_LIMIT')
        return payload
    finally:
        os.close(fd)


async def _run(adapter,timeout_ms,max_attempts,max_pages):
    if type(max_pages) is not int or not 1<=max_pages<=MAX_ACCEPTED_PAGES:
        raise AdapterError('INVALID_READ_POLICY')
    session=AdapterSession(adapter,timeout_ms=timeout_ms,max_attempts=max_attempts)
    for _ in range(max_pages):
        outcome=await session.poll_once()
        if session.admission.ended or outcome['status']=='NO_NEW_DATA':
            result=session.report()
            result['status']='END_OF_SYNTHETIC_INPUT' if session.admission.ended else 'NO_NEW_DATA'
            return result
    raise AdapterError('READ_LIMIT')


def main(argv=None):
    class Parser(argparse.ArgumentParser):
        def error(self,_message):raise AdapterError('INVALID_ARGUMENTS')
    parser=Parser(description=__doc__)
    parser.add_argument('--fixture',required=True)
    parser.add_argument('--timeout-ms',type=int,default=100)
    parser.add_argument('--max-attempts',type=int,default=2)
    parser.add_argument('--max-pages',type=int,default=64)
    reporting=False
    try:
        args=parser.parse_args(argv)
        source=SyntheticReplayAdapter(_load_fixture(args.fixture))
        result=asyncio.run(_run(source,args.timeout_ms,args.max_attempts,args.max_pages))
        output=_encode(result)
        if len(output)>MAX_OUTPUT_BYTES:
            raise AdapterError('OUTPUT_LIMIT')
        reporting=True
        print(output.decode('utf-8'))
        sys.stdout.flush()
        return 0
    except (AdapterError,ValueError,TypeError,OverflowError,OSError,RecursionError):
        error=sys.exception()
        if reporting and isinstance(error,OSError):_discard_failed_stdout()
        print(json.dumps({'error':error.code if isinstance(error,AdapterError) else 'INVALID_INPUT'}),file=sys.stderr)
        return 2


if __name__=='__main__':
    raise SystemExit(main())
