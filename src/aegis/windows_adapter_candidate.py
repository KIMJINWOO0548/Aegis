"""Bounded experimental adapter snapshots on Windows journal; production disabled."""
import hashlib
import json
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


def _replay(snapshot):
    if (type(snapshot) is not dict or set(snapshot) != {'version', 'fixture', 'pages'}
            or snapshot['version'] != 'windows-adapter-snapshot-1'
            or type(snapshot['pages']) is not list):
        raise JournalError('INVALID_SNAPSHOT')
    source = SyntheticReplayAdapter(_encode(snapshot['fixture']))
    d = source.descriptor
    state = AdapterAdmission(d.adapterId, d.adapterVersion, d.streamId)
    expected = {_encode(p['afterToken']): _encode(p['response'])
                for p in snapshot['fixture']['pages']}
    for entry in snapshot['pages']:
        if type(entry) is not dict or set(entry) != {'page', 'received'}:
            raise JournalError('INVALID_SNAPSHOT')
        payload = _encode(entry['page'])
        parsed = validate_page(payload)
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
        initial = None
        if fixture is not None:
            source = SyntheticReplayAdapter(fixture)
            initial = {'version': 'windows-adapter-snapshot-1',
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
                if (snapshot['fixture'] != previous['fixture']
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

    def commit_page(self, payload, received_at):
        with self._lock:
            snapshot, state = self._read()
            parsed = validate_page(payload)
            expected = {_encode(p['afterToken']): _encode(p['response'])
                        for p in snapshot['fixture']['pages']}
            if expected.get(_encode(parsed.model.afterToken)) != parsed.canonical:
                raise JournalError('FIXTURE_MISMATCH')
            result = state.admit(payload, received_at)
            if result['status'] in ('RETRANSMISSION', 'NO_NEW_DATA'):
                return {'status': result['status'], 'sourceAcknowledgement': 'NOT_SENT'}
            snapshot['pages'].append({'page': parsed.original, 'received': _utc(received_at)})
            _replay(snapshot)
            return self._journal.append(_pack(snapshot))

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
