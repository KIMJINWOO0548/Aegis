"""Experimental single-file Windows receipt journal. Not wired to production.

Requires a trusted local directory. No SQLite sidecars, path publication, repair,
or device acknowledgements. Flush success is not a power-loss durability claim.
"""
import ctypes
from ctypes import wintypes as w
import hashlib
import os
import ntpath
import struct
import threading

from .windows_fixture import _local_path

MAGIC = b'AEGIS-WIN-JOURNAL-1\n'
MAX_RECORD = 1024 * 1024
MAX_BYTES = 16 * 1024 * 1024


class JournalError(Exception):
    pass


class _File:
    def __init__(self, path, create):
        if os.name != 'nt':
            raise JournalError('PLATFORM_UNVERIFIED')
        self.k = k = ctypes.WinDLL('kernel32', use_last_error=True)
        path = _local_path(path)
        k.GetDriveTypeW.argtypes = [w.LPCWSTR]
        k.GetDriveTypeW.restype = w.UINT
        if k.GetDriveTypeW(ntpath.splitdrive(path)[0] + '\\') != 3:
            raise JournalError('LOCAL_FIXED_DISK_REQUIRED')
        k.CreateFileW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, ctypes.c_void_p,
                                  w.DWORD, w.DWORD, w.HANDLE]
        k.CreateFileW.restype = w.HANDLE
        k.CloseHandle.argtypes = [w.HANDLE]
        k.CloseHandle.restype = w.BOOL
        k.GetFileInformationByHandle.argtypes = [w.HANDLE, ctypes.c_void_p]
        k.GetFileInformationByHandle.restype = w.BOOL
        k.GetFileType.argtypes = [w.HANDLE]
        k.GetFileType.restype = w.DWORD
        k.GetFileSizeEx.argtypes = [w.HANDLE, ctypes.POINTER(ctypes.c_longlong)]
        k.GetFileSizeEx.restype = w.BOOL
        k.SetFilePointerEx.argtypes = [w.HANDLE, ctypes.c_longlong,
                                      ctypes.POINTER(ctypes.c_longlong), w.DWORD]
        k.SetFilePointerEx.restype = w.BOOL
        for name in ('ReadFile', 'WriteFile'):
            fn = getattr(k, name)
            fn.argtypes = [w.HANDLE, ctypes.c_void_p, w.DWORD,
                           ctypes.POINTER(w.DWORD), ctypes.c_void_p]
            fn.restype = w.BOOL
        k.FlushFileBuffers.argtypes = [w.HANDLE]
        k.FlushFileBuffers.restype = w.BOOL
        # Exclusive open, no-overwrite create; write-through and final no-follow.
        self.h = k.CreateFileW(path, 0xC0000000, 0, None,
                              1 if create else 3, 0x80200000, None)
        if self.h == ctypes.c_void_p(-1).value:
            self.h = None
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            class Info(ctypes.Structure):
                _fields_ = [('attributes', w.DWORD), ('created', w.FILETIME),
                            ('accessed', w.FILETIME), ('written', w.FILETIME),
                            ('volume', w.DWORD), ('high', w.DWORD), ('low', w.DWORD),
                            ('links', w.DWORD), ('indexHigh', w.DWORD), ('indexLow', w.DWORD)]
            info = Info()
            self.check(k.GetFileInformationByHandle(self.h, ctypes.byref(info)))
            if k.GetFileType(self.h) != 1 or info.attributes & 0x410 or info.links != 1:
                raise JournalError('UNSAFE_FILE')
        except BaseException:
            self.close()
            raise

    def check(self, result):
        if not result:
            raise ctypes.WinError(ctypes.get_last_error())

    def seek(self, offset):
        self.check(self.k.SetFilePointerEx(self.h, offset, None, 0))

    def read(self):
        size = ctypes.c_longlong()
        self.check(self.k.GetFileSizeEx(self.h, ctypes.byref(size)))
        if not 0 <= size.value <= MAX_BYTES:
            raise JournalError('SIZE_LIMIT')
        self.seek(0)
        buffer = ctypes.create_string_buffer(size.value)
        count = w.DWORD()
        self.check(self.k.ReadFile(self.h, buffer, size.value, ctypes.byref(count), None))
        if count.value != size.value:
            raise JournalError('SHORT_READ')
        return buffer.raw

    def write(self, payload):
        buffer = ctypes.create_string_buffer(payload)
        count = w.DWORD()
        self.check(self.k.WriteFile(self.h, buffer, len(payload), ctypes.byref(count), None))
        if count.value != len(payload):
            raise JournalError('SHORT_WRITE')

    def flush(self):
        self.check(self.k.FlushFileBuffers(self.h))

    def close(self):
        if self.h is not None:
            self.check(self.k.CloseHandle(self.h))
            self.h = None


def _decode(data):
    if not data.startswith(MAGIC):
        raise JournalError('INVALID_HEADER')
    offset, records, chain = len(MAGIC), [], bytes(32)
    while offset < len(data):
        if len(data) - offset < 36:
            raise JournalError('RECOVERY_REQUIRED')
        length = struct.unpack_from('<I', data, offset)[0]
        digest = data[offset + 4:offset + 36]
        offset += 36
        if not 1 <= length <= MAX_RECORD or length > len(data) - offset:
            raise JournalError('RECOVERY_REQUIRED')
        payload = data[offset:offset + length]
        if hashlib.sha256(chain + payload).digest() != digest:
            raise JournalError('INTEGRITY_ERROR')
        records.append(payload)
        chain = digest
        offset += length
    return tuple(records), chain


class WindowsJournalCandidate:
    def __init__(self, path, *, create=False):
        self._lock = threading.RLock()
        self._file = _File(path, create)
        self._fenced = False
        try:
            if create:
                self._file.write(MAGIC)
                self._file.flush()
            self._data = self._file.read()
            self._records, self._chain = _decode(self._data)
        except BaseException:
            self.close()
            raise

    @property
    def records(self):
        with self._lock:
            if self._fenced:
                raise JournalError('RESULT_UNKNOWN')
            if self._file is None:
                raise JournalError('CLOSED')
            return self._records

    def append(self, payload):
        with self._lock:
            return self._append(payload)

    def _append(self, payload):
        self.records
        if type(payload) is not bytes or not 1 <= len(payload) <= MAX_RECORD:
            raise JournalError('INVALID_RECORD')
        digest = hashlib.sha256(self._chain + payload).digest()
        frame = struct.pack('<I', len(payload)) + digest + payload
        if len(self._data) + len(frame) > MAX_BYTES:
            raise JournalError('SIZE_LIMIT')
        try:
            self._file.seek(len(self._data))
            self._file.write(frame)
            self._file.flush()
            expected = self._data + frame
            actual = self._file.read()
            if actual != expected:
                raise JournalError('READBACK_MISMATCH')
            records, chain = _decode(actual)
            self._data, self._records, self._chain = actual, records, chain
            return {'status': 'LOCAL_FLUSH_CONFIRMED', 'sequence': len(records),
                    'sourceAcknowledgement': 'NOT_SENT'}
        except BaseException as error:
            self._fenced = True
            try:
                self.close()
            except BaseException as cleanup_error:
                # Keep ownership for an explicit close retry. Never hide the
                # ambiguous append outcome behind a secondary cleanup failure.
                raise JournalError('RESULT_UNKNOWN') from cleanup_error
            raise JournalError('RESULT_UNKNOWN') from error

    def close(self):
        with self._lock:
            if self._file is not None:
                self._file.close()
                self._file = None

    def __enter__(self):
        return self

    def __exit__(self, _type, error, _traceback):
        try:
            self.close()
        except BaseException as cleanup_error:
            if error is None:
                raise
            # Context cleanup must not mask an active ambiguous write result.
            # Retain the handle for explicit close retry by the caller.
            error.add_note('Journal cleanup also failed: ' + type(cleanup_error).__name__)
        return False
