"""Experimental same-directory backup publication. Not production durability."""
import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import hashlib
import ntpath
import re
from .windows_fixture import _local_path
from .adapter_input import AdapterError
from .windows_adapter_candidate import WindowsAdapterCandidate, _pack
from .windows_journal_candidate import JournalError


@dataclass(frozen=True)
class PublicationTicket:
    staging: str
    destination: str
    snapshot_sha256: str


def _ticket(ticket):
    if not isinstance(ticket, PublicationTicket):
        raise JournalError('INVALID_PUBLICATION_TICKET')
    staging, destination = _local_path(ticket.staging), _local_path(ticket.destination)
    a, b = ntpath.normcase(staging), ntpath.normcase(destination)
    if a == b or ntpath.dirname(a) != ntpath.dirname(b):
        raise JournalError('DISTINCT_SAME_DIRECTORY_REQUIRED')
    if not isinstance(ticket.snapshot_sha256, str) or not re.fullmatch('[0-9a-f]{64}', ticket.snapshot_sha256):
        raise JournalError('INVALID_PUBLICATION_TICKET')
    return staging, destination


def _verify(path, digest):
    with WindowsAdapterCandidate(path) as candidate:
        # Published artifacts must be exactly one validated snapshot frame.
        if len(candidate._journal.records) != 1:
            raise JournalError('NOT_SEALED_SNAPSHOT')
        snapshot, _ = candidate._read()
        if hashlib.sha256(_pack(snapshot)).hexdigest() != digest:
            raise JournalError('SNAPSHOT_MISMATCH')


def prepare_backup(source, staging, destination):
    # Validate names before any write. The hash is filled after the copy.
    provisional = PublicationTicket(str(staging), str(destination), '0' * 64)
    staging, destination = _ticket(provisional)
    result = source.backup_to_new(staging)
    ticket = PublicationTicket(staging, destination, result['snapshotSha256'])
    _verify(staging, ticket.snapshot_sha256)
    return ticket


def _move_no_replace(staging, destination):
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    move = kernel.MoveFileExW
    move.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD]
    move.restype = wintypes.BOOL
    # WRITE_THROUGH only: no replace, cross-volume copy, reboot or registry work.
    if not move(staging, destination, 0x8):
        raise ctypes.WinError(ctypes.get_last_error())


def publish_backup(ticket):
    staging, destination = _ticket(ticket)
    _verify(staging, ticket.snapshot_sha256)
    try:
        _move_no_replace(staging, destination)
        _verify(destination, ticket.snapshot_sha256)
    except BaseException as error:
        # Rename may have occurred. Never delete either name or retry blindly.
        raise JournalError('PUBLICATION_RESULT_UNKNOWN') from error
    return {'status': 'PUBLISHED_READBACK_CONFIRMED',
            'snapshotSha256': ticket.snapshot_sha256,
            'durablePublication': 'NOT_VERIFIED', 'sourceAcknowledgement': 'NOT_SENT'}


def inspect_publication(ticket):
    """Non-mutating observation, not permission to retry or a durable receipt."""
    staging, destination = _ticket(ticket)
    result = {}
    for key, path in (('staging', staging), ('destination', destination)):
        try:
            _verify(path, ticket.snapshot_sha256)
            result[key] = 'MATCH'
        except FileNotFoundError:
            result[key] = 'ABSENT'
        except (OSError, ValueError, JournalError, AdapterError):
            result[key] = 'UNVERIFIED'
    result['status'] = ('PUBLISHED_OBSERVED' if result == {'staging': 'ABSENT', 'destination': 'MATCH'}
                        else 'REQUIRES_REVIEW')
    result['durablePublication'] = 'NOT_VERIFIED'
    return result

