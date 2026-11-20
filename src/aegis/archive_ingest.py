"""Built-in synthetic fixture collection into the nullable local input archive."""

import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
import sys
from types import MappingProxyType
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from aegis.adapter_input import (AdapterError, AdapterReadRequest, Identifier, Token,
    MAX_ACCEPTED_PAGES, MAX_ADMITTED_RECORDS, MAX_FIXTURE_BYTES, MAX_OUTPUT_BYTES,
    _decode, _encode, _load_fixture)
from aegis.archive_backup import _discard_failed_stdout
from aegis.input_archive import ArchiveWritePage, InputArchive, validate_write_page


HEADERS = ("adapterId", "adapterVersion", "streamId", "declaredOrigin", "originEvidence",
           "acquisitionMethod", "sourceAccessMode")


class IngestError(AdapterError):
    pass


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class _SyntheticDescriptor(_Model):
    adapterId: Identifier
    adapterVersion: Literal["1.0"]
    streamId: UUID
    declaredOrigin: Literal["SYNTHETIC"]
    originEvidence: Literal["DECLARED_ONLY"]
    acquisitionMethod: Literal["SOURCE_BUFFER_REPLAY"]
    sourceAccessMode: Literal["READ_ONLY"]


class _Entry(_Model):
    afterToken: Token | None
    response: ArchiveWritePage
    delayMs: Annotated[int, Field(strict=True, ge=0, le=10000)] = 0


class _Fixture(_Model):
    schemaVersion: Literal["archive-write-fixture-1"]
    descriptor: _SyntheticDescriptor
    pages: Annotated[tuple[_Entry, ...], Field(min_length=1, max_length=MAX_ACCEPTED_PAGES)]


def _sha(value):
    return hashlib.sha256(value).hexdigest()


class SyntheticArchiveSource:
    """Immutable finite synthetic pages; this class does not execute drivers or URLs."""
    def __init__(self, fixture):
        try:
            payload = fixture if isinstance(fixture, bytes) else _encode(fixture)
            original = _decode(payload, MAX_FIXTURE_BYTES)
            parsed = _Fixture.model_validate(original)
            # Binding uses producer spellings, including UUID case, rather than model normalization.
            descriptor = dict(original["descriptor"])
            pages = {}
            seen_records = {}
            total = 0
            for index, entry in enumerate(parsed.pages):
                validated = validate_write_page(_encode(original["pages"][index]["response"]))
                page = validated.model
                page_original = validated.original
                headers = {key: page_original[key] for key in HEADERS}
                if headers != descriptor:
                    raise IngestError("DESCRIPTOR_MISMATCH")
                if entry.afterToken in pages or entry.afterToken != page.afterToken:
                    raise IngestError("INVALID_FIXTURE")
                for record in page_original["records"]:
                    encoded = _encode(record)
                    previous = seen_records.get(record["recordId"])
                    if previous is not None and previous != encoded:
                        raise IngestError("RECORD_CONFLICT")
                    seen_records[record["recordId"]] = encoded
                total += len(page.records)
                if total > MAX_ADMITTED_RECORDS:
                    raise IngestError("INPUT_LIMIT")
                pages[entry.afterToken] = (validated, entry.delayMs)
            cursor = None
            visited = set()
            ordered = []
            while True:
                if cursor in visited or cursor not in pages:
                    raise IngestError("INVALID_FIXTURE_CHAIN")
                visited.add(cursor)
                validated, _ = pages[cursor]
                ordered.append(validated)
                if validated.model.records and validated.model.nextToken in visited:
                    raise IngestError("INVALID_FIXTURE_CHAIN")
                if validated.model.endOfStream or not validated.model.records:
                    break
                cursor = validated.model.nextToken
            if len(visited) != len(pages):
                raise IngestError("UNREACHABLE_FIXTURE_PAGE")
            self._descriptor = MappingProxyType(descriptor)
            self._pages = MappingProxyType(pages)
            self._ordered = tuple(ordered)
            self.read_calls = 0
        except AdapterError:
            raise
        except (ValueError, TypeError, OverflowError, RecursionError):
            raise IngestError("INVALID_FIXTURE") from None

    @property
    def descriptor(self):
        return dict(self._descriptor)

    def check_prefix(self, snapshot):
        """Require every committed page to match before resuming or trusting EOF."""
        if snapshot.descriptor != self.descriptor or len(snapshot.pages) > len(self._ordered):
            raise IngestError("FIXTURE_PREFIX_MISMATCH")
        for ordinal, stored in enumerate(snapshot.pages):
            expected = self._ordered[ordinal]
            if (stored.ordinal != ordinal
                    or stored.after_key != _encode(expected.model.afterToken).decode("utf-8")
                    or stored.next_key != _encode(expected.model.nextToken).decode("utf-8")
                    or stored.canonical_sha != _sha(expected.canonical)):
                raise IngestError("FIXTURE_PREFIX_MISMATCH")

    async def read(self, request):
        if type(request) is not AdapterReadRequest:
            raise IngestError("INVALID_READ_REQUEST")
        self.read_calls += 1
        item = self._pages.get(request.after_token)
        if item is None:
            raise IngestError("UNKNOWN_DELIVERY_CURSOR")
        validated, delay = item
        if len(validated.model.records) > request.limit:
            raise IngestError("READ_LIMIT")
        await asyncio.sleep(delay / 1000)
        return validated.wire


def _read_policy(timeout_ms, max_attempts, max_pages=MAX_ACCEPTED_PAGES):
    if (type(timeout_ms) is not int or not 1 <= timeout_ms <= 10000
            or type(max_attempts) is not int or not 1 <= max_attempts <= 3
            or type(max_pages) is not int or not 1 <= max_pages <= MAX_ACCEPTED_PAGES):
        raise IngestError("INVALID_READ_POLICY")


class ArchiveIngestSession:
    """Cooperative source reads followed by one synchronous local append/confirmation."""
    def __init__(self, source, archive, *, timeout_ms=100, max_attempts=2, utc_now=None):
        if type(source) is not SyntheticArchiveSource or type(archive) is not InputArchive:
            raise IngestError("UNSUPPORTED_SOURCE")
        _read_policy(timeout_ms, max_attempts)
        current = archive.refresh()
        source.check_prefix(current)
        self.source, self.archive = source, archive
        self.timeout_ms, self.max_attempts = timeout_ms, max_attempts
        self._now = utc_now or (lambda: datetime.now(timezone.utc))
        self._lock = asyncio.Lock()
        self.committed_here = 0
        self.commit_possible = bool(current.pages)

    async def poll_once(self):
        async with self._lock:
            current = self.archive.refresh()
            self.source.check_prefix(current)
            self.commit_possible = self.commit_possible or bool(current.pages)
            if current.ended:
                return {"status": "END_OF_SYNTHETIC_INPUT",
                        "pageCommitStatus": "ALREADY_LOCAL_ARCHIVE_COMMITTED",
                        "sourceAcknowledgement": "NOT_SENT"}
            request = AdapterReadRequest(current.cursor)
            for attempt in range(self.max_attempts):
                try:
                    async with asyncio.timeout(self.timeout_ms / 1000):
                        payload = await self.source.read(request)
                    received = self._now()
                    break
                except TimeoutError:
                    if attempt + 1 == self.max_attempts:
                        raise IngestError("SOURCE_TIMEOUT") from None
                except AdapterError:
                    raise
                except Exception:
                    raise IngestError("SOURCE_UNAVAILABLE") from None
            validated = validate_write_page(payload)
            if validated.model.afterToken != request.after_token:
                raise IngestError("RESPONSE_CURSOR_MISMATCH")
            task = asyncio.current_task()
            if task is not None and task.cancelling():
                raise asyncio.CancelledError
            self.commit_possible = self.commit_possible or bool(validated.model.records) or validated.model.endOfStream
            # No executor and no await spans the transaction or its confirmation.
            outcome = self.archive.append_page(payload, received)
            if outcome["pageCommitStatus"] == "LOCAL_ARCHIVE_COMMITTED":
                self.committed_here += 1
            confirmed = self.archive.refresh()
            self.source.check_prefix(confirmed)
            self.commit_possible = self.commit_possible or bool(confirmed.pages)
            status = ("END_OF_SYNTHETIC_INPUT" if confirmed.ended else
                      "NO_NEW_DATA" if outcome["pageCommitStatus"] == "NO_NEW_PAGE" else "PAGE_COMMITTED")
            return {**outcome, "status": status, "ended": confirmed.ended, "committedCursor": confirmed.cursor}

    def report(self):
        current = self.archive.refresh()
        self.source.check_prefix(current)
        self.commit_possible = self.commit_possible or bool(current.pages)
        descriptor = current.descriptor
        metadata = [entry["record"]["sourceMetadata"] for entry in current.records]
        # Keep producer identities and future writer-report additions out of CLI output.
        return {"schemaVersion": "archive-ingest-report-1",
                "persistenceStatus": "LOCAL_ARCHIVE_COMMITTED" if current.pages else "NO_COMMITTED_PAGE",
                **{key: descriptor[key] for key in ("declaredOrigin", "originEvidence", "acquisitionMethod",
                                                   "sourceAccessMode")},
                "localArchiveAccess": "READ_WRITE",
                "writeDelivery": "LOCAL_ARCHIVE", "sourceAcknowledgement": "NOT_SENT",
                "executionSource": "SYNTHETIC_FIXTURE_ONLY", "realDriverExecuted": False,
                "realEquipmentSupported": False, "hardRealtimeGuaranteed": False,
                "counts": {"committedPages": len(current.pages), "committedRecords": len(metadata),
                           "missingSourceTimestampRecords": sum(item["observedAtUtc"] is None for item in metadata),
                           "missingSourceSequenceRecords": sum(item["sequence"] is None for item in metadata),
                           "missingSourceRunRecords": sum(item["runId"] is None for item in metadata),
                           "pageReads": self.source.read_calls,
                           "thisRunCommittedPages": self.committed_here}}


async def _run_session(session, max_pages):
    for _ in range(max_pages):
        outcome = await session.poll_once()
        if outcome["status"] in ("END_OF_SYNTHETIC_INPUT", "NO_NEW_DATA"):
            return {**session.report(), "status": outcome["status"]}
    raise IngestError("READ_LIMIT")


def _collect(source, path, timeout_ms, max_attempts, max_pages):
    archive = InputArchive(path, source.descriptor)
    session = None
    try:
        session = ArchiveIngestSession(source, archive, timeout_ms=timeout_ms, max_attempts=max_attempts)
        result = asyncio.run(_run_session(session, max_pages))
    except BaseException:
        try:
            archive.close()
        except Exception:
            if session is not None and session.commit_possible:
                raise IngestError("STORE_RESULT_UNKNOWN") from None
        raise
    try:
        archive.close()  # A close failure must precede, rather than follow, success output.
    except Exception:
        raise IngestError("STORE_RESULT_UNKNOWN" if session.commit_possible else "STORE_CLOSE_FAILED") from None
    return result, session.commit_possible


def main(argv=None):
    class Parser(argparse.ArgumentParser):
        def error(self, _message):
            raise IngestError("INVALID_ARGUMENTS")
    parser = Parser(description=__doc__)
    parser.add_argument("--fixture", required=True)
    parser.add_argument("--store", required=True)
    parser.add_argument("--timeout-ms", type=int, default=100)
    parser.add_argument("--max-attempts", type=int, default=2)
    parser.add_argument("--max-pages", type=int, default=64)
    reporting = False
    commit_possible = False
    try:
        args = parser.parse_args(argv)
        _read_policy(args.timeout_ms, args.max_attempts, args.max_pages)
        source = SyntheticArchiveSource(_load_fixture(args.fixture))
        result, commit_possible = _collect(source, args.store, args.timeout_ms, args.max_attempts, args.max_pages)
        output = _encode(result)
        if len(output) > MAX_OUTPUT_BYTES:
            raise IngestError("OUTPUT_LIMIT")
        reporting = True
        print(output.decode("utf-8"))
        sys.stdout.flush()
        return 0
    except Exception as error:
        if reporting:
            code = "STORE_RESULT_UNKNOWN" if commit_possible else "OUTPUT_UNAVAILABLE"
            try:
                _discard_failed_stdout()
            except OSError:
                pass
        else:
            code = error.code if isinstance(error, AdapterError) else "INVALID_INPUT"
        try:
            print(json.dumps({"error": code}), file=sys.stderr)
        except OSError:
            pass
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
