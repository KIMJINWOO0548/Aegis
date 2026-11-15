"""Bounded experimental adapter snapshots on Windows journal; production disabled."""
import hashlib
import base64
import threading
from .adapter_input import (AdapterAdmission, SyntheticReplayAdapter, validate_page,
                            _encode, _decode, MAX_FIXTURE_BYTES)
from .adapter_store import _utc, _receipt
from .windows_journal_candidate import WindowsJournalCandidate, JournalError, MAX_RECORD


def _pack(snapshot):
    payload = _encode(snapshot)
    if len(payload) > MAX_RECORD:
        raise JournalError('SNAPSHOT_LIMIT')
    return payload


def _wire(value, digest):
    if type(value) is not str or type(digest) is not str:
        raise JournalError('INVALID_WIRE_EVIDENCE')
    try:
        payload = base64.b64decode(value, validate=True)
    except ValueError as error:
        raise JournalError('INVALID_WIRE_EVIDENCE') from error
    if hashlib.sha256(payload).hexdigest() != digest:
        raise JournalError('WIRE_HASH_MISMATCH')
    return payload


def _evidence(payload):
    return base64.b64encode(payload).decode('ascii'), hashlib.sha256(payload).hexdigest()


def _replay(snapshot):
    if type(snapshot) is not dict:
        raise JournalError('INVALID_SNAPSHOT')
    version = snapshot.get('version')
    fields = {'version', 'fixture', 'pages'}
    if version == 'windows-adapter-snapshot-2':
        fields |= {'fixtureWire', 'fixtureWireSha256'}
    elif version != 'windows-adapter-snapshot-1':
        raise JournalError('INVALID_SNAPSHOT')
    if set(snapshot) != fields or type(snapshot['pages']) is not list:
        raise JournalError('INVALID_SNAPSHOT')
    fixture_wire = (_wire(snapshot['fixtureWire'], snapshot['fixtureWireSha256'])
                    if version.endswith('-2') else _encode(snapshot['fixture']))
    source = SyntheticReplayAdapter(fixture_wire)
    if _encode(_decode(source.fixture_wire, MAX_FIXTURE_BYTES)) != _encode(snapshot['fixture']):
        raise JournalError('WIRE_CANONICAL_MISMATCH')
    d = source.descriptor
    state = AdapterAdmission(d.adapterId, d.adapterVersion, d.streamId)
    expected = {_encode(p['afterToken']): _encode(p['response'])
                for p in snapshot['fixture']['pages']}
    for entry in snapshot['pages']:
        page_fields = {'page', 'received'} | ({'wire', 'wireSha256'} if version.endswith('-2') else set())
        if type(entry) is not dict or set(entry) != page_fields:
            raise JournalError('INVALID_SNAPSHOT')
        payload = (_wire(entry['wire'], entry['wireSha256'])
                   if version.endswith('-2') else _encode(entry['page']))
        parsed = validate_page(payload)
        if parsed.canonical != _encode(entry['page']):
            raise JournalError('WIRE_CANONICAL_MISMATCH')
        if expected.get(_encode(parsed.model.afterToken)) != parsed.canonical:
            raise JournalError('FIXTURE_MISMATCH')
        result = state.admit(payload, _receipt(entry['received']))
        if result['status'] not in ('ADMITTED', 'END_OF_SYNTHETIC_INPUT'):
            raise JournalError('INVALID_SNAPSHOT_TRANSITION')
    return state


class WindowsAdapterCandidate:
    """Synthetic-only full snapshots. No source ACK, migration or durable publish."""
    def __init__(self, path, *, fixture=None):
        self._lock = threading.RLock()
        self._journal = None
        self._session_commits = 0
        initial = None
        if fixture is not None:
            source = SyntheticReplayAdapter(fixture)
            fixture_wire, fixture_sha = _evidence(source.fixture_wire)
            initial = {'version': 'windows-adapter-snapshot-2',
                       'fixtureWire': fixture_wire, 'fixtureWireSha256': fixture_sha,
                       'fixture': _decode(source.fixture_wire, MAX_FIXTURE_BYTES), 'pages': []}
            _replay(initial)
            _pack(initial)  # Reject oversized input before creating any file.
        self._journal = WindowsJournalCandidate(path, create=fixture is not None)
        try:
            if initial is not None:
                self._journal.append(_pack(initial))
            self._read()
        except BaseException as error:
            self._journal.__exit__(type(error), error, error.__traceback__)
            raise

    def _read(self):
        records = self._journal.records
        if not records:
            raise JournalError('INCOMPLETE_SNAPSHOT')
        previous = None
        for raw in records:
            snapshot = _decode(raw, MAX_RECORD)
            state = _replay(snapshot)
            if previous is not None:
                if ({k: v for k, v in snapshot.items() if k != 'pages'}
                        != {k: v for k, v in previous.items() if k != 'pages'}
                        or len(snapshot['pages']) != len(previous['pages']) + 1
                        or snapshot['pages'][:-1] != previous['pages']):
                    raise JournalError('INVALID_SNAPSHOT_TRANSITION')
            previous = snapshot
        return snapshot, state

    @property
    def committed_cursor(self):
        with self._lock:
            return self._read()[1].cursor

    @property
    def records(self):
        with self._lock:
            return self._read()[1].records

    @property
    def wire_pages(self):
        with self._lock:
            snapshot, _ = self._read()
            if snapshot['version'] != 'windows-adapter-snapshot-2':
                raise JournalError('WIRE_EVIDENCE_UNAVAILABLE')
            return tuple(_wire(p['wire'], p['wireSha256']) for p in snapshot['pages'])

    @property
    def fixture_wire(self):
        with self._lock:
            snapshot, _ = self._read()
            if snapshot['version'] != 'windows-adapter-snapshot-2':
                raise JournalError('WIRE_EVIDENCE_UNAVAILABLE')
            return _wire(snapshot['fixtureWire'], snapshot['fixtureWireSha256'])

    def commit_page(self, payload, received_at):
        with self._lock:
            snapshot, state = self._read()
            if snapshot['version'] != 'windows-adapter-snapshot-2':
                raise JournalError('LEGACY_SNAPSHOT_READ_ONLY')
            parsed = validate_page(payload)
            expected = {_encode(p['afterToken']): _encode(p['response'])
                        for p in snapshot['fixture']['pages']}
            if expected.get(_encode(parsed.model.afterToken)) != parsed.canonical:
                raise JournalError('FIXTURE_MISMATCH')
            result = state.admit(payload, received_at)
            if result['status'] in ('RETRANSMISSION', 'NO_NEW_DATA'):
                return {'status': result['status'], 'sourceAcknowledgement': 'NOT_SENT'}
            encoded, digest = _evidence(parsed.wire)
            snapshot['pages'].append({'page': parsed.original, 'received': _utc(received_at),
                                      'wire': encoded, 'wireSha256': digest})
            _replay(snapshot)
            result = self._journal.append(_pack(snapshot))
            self._session_commits += 1
            return result

    def state_view(self):
        """Presentation contract: receipt/replayed evidence is never live telemetry."""
        with self._lock:
            snapshot, state = self._read()
            pages = snapshot['pages']
            return {'schemaVersion': 'windows-adapter-state-view-1',
                    'stateSource': ('EMPTY' if not pages else
                                    'SESSION_SYNTHETIC' if self._session_commits else
                                    'RESTORED_LAST_KNOWN'),
                    'isLive': False, 'freshness': 'NOT_LIVE_SYNTHETIC',
                    'declaredOrigin': 'SYNTHETIC', 'sourceAcknowledgement': 'NOT_SENT',
                    'committedCursor': state.cursor, 'ended': state.ended,
                    'lastReceiptUtc': pages[-1]['received'] if pages else None,
                    'timestampMeaning': 'HOST_RECEIPT_NOT_SOURCE_OBSERVATION',
                    'records': state.records}

    def backup_to_new(self, destination):
        """Copy one validated snapshot. Destination is never replaced or repaired.

        A failed attempt leaves evidence at destination; do not retry over it.
        Success is local flush/readback only, NOT durable namespace publication.
        The resulting candidate can be reopened directly for recovery/resume.
        """
        with self._lock:
            snapshot, _ = self._read()
            payload = _pack(snapshot)
            with WindowsJournalCandidate(destination, create=True) as target:
                result = target.append(payload)
                if target.records != (payload,):
                    raise JournalError('BACKUP_READBACK_MISMATCH')
            return {**result, 'snapshotSha256': hashlib.sha256(payload).hexdigest(),
                    'durablePublication': 'NOT_VERIFIED'}

    def close(self):
        with self._lock:
            if self._journal is not None:
                self._journal.close()

    def __enter__(self):
        return self

    def __exit__(self, kind, error, traceback):
        return self._journal.__exit__(kind, error, traceback)
