"""Native path and writer lifecycle for an Aegis-owned local NTFS directory."""

from contextlib import ExitStack, contextmanager
import ctypes
from ctypes import wintypes as w
from hashlib import sha256
import os
from pathlib import Path
import struct
import tempfile

from .windows_fixture import _local_path


class UnsafePath(ValueError):
    pass


class StoreBusy(OSError):
    pass


class _FileInfo(ctypes.Structure):
    _fields_ = [('attributes', w.DWORD), ('creation', w.FILETIME), ('access', w.FILETIME),
                ('write', w.FILETIME), ('volume', w.DWORD), ('sizeHigh', w.DWORD),
                ('sizeLow', w.DWORD), ('links', w.DWORD), ('indexHigh', w.DWORD),
                ('indexLow', w.DWORD)]


_kernel = ctypes.WinDLL('kernel32', use_last_error=True)


def _api(name, result, *args):
    function = getattr(_kernel, name)
    function.argtypes, function.restype = args, result
    return function


_open = _api('CreateFileW', w.HANDLE, w.LPCWSTR, w.DWORD, w.DWORD, ctypes.c_void_p,
             w.DWORD, w.DWORD, w.HANDLE)
_close = _api('CloseHandle', w.BOOL, w.HANDLE)
_info = _api('GetFileInformationByHandle', w.BOOL, w.HANDLE, ctypes.POINTER(_FileInfo))
_kind = _api('GetFileType', w.DWORD, w.HANDLE)
_drive = _api('GetDriveTypeW', w.UINT, w.LPCWSTR)
_final = _api('GetFinalPathNameByHandleW', w.DWORD, w.HANDLE, w.LPWSTR, w.DWORD, w.DWORD)
_volume = _api('GetVolumeInformationByHandleW', w.BOOL, w.HANDLE, w.LPWSTR, w.DWORD,
               ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, w.LPWSTR, w.DWORD)
_mutex = _api('CreateMutexW', w.HANDLE, ctypes.c_void_p, w.BOOL, w.LPCWSTR)
_wait = _api('WaitForSingleObject', w.DWORD, w.HANDLE, w.DWORD)
_release = _api('ReleaseMutex', w.BOOL, w.HANDLE)


def _checked(result):
    if not result:
        raise ctypes.WinError(ctypes.get_last_error())


def _handle(path, stack, *, directory=False):
    # Data access (not attributes-only) makes omitting FILE_SHARE_DELETE effective.
    handle = _open(str(path), 1 if directory else 0x80000000, 3, None, 3, 0x02200000, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    stack.callback(lambda: _checked(_close(handle)))
    info = _FileInfo()
    _checked(_info(handle, ctypes.byref(info)))
    if (_kind(handle) != 1 or info.attributes & 0x400
            or bool(info.attributes & 0x10) != directory or not directory and info.links != 1):
        raise UnsafePath()
    actual = ctypes.create_unicode_buffer(32768)
    length = _final(handle, actual, len(actual), 0)
    _checked(length)
    if length >= len(actual) or not actual.value.startswith('\\\\?\\'):
        raise UnsafePath()
    if os.path.normcase(actual.value[4:].rstrip('\\')) != os.path.normcase(str(path).rstrip('\\')):
        raise UnsafePath()  # Excludes substituted drives, short names and redirected components.
    return handle, info


def valid_wal_header(path):
    with path.open('rb') as stream:
        header = stream.read(32)
    if not header:
        return True  # SQLite can leave an empty WAL before the first write.
    if len(header) != 32:
        return False
    magic, version, size, _, _, _, checksum1, checksum2 = struct.unpack('>8I', header)
    if (magic not in (0x377f0682, 0x377f0683) or version != 3007000
            or not 512 <= size <= 65536 or size & (size - 1)):
        return False
    # SQLite file format §4.2: header checksums cover the first 24 bytes.
    words = struct.unpack_from('>6I' if magic & 1 else '<6I', header)
    first = second = 0
    for x, y in zip(words[::2], words[1::2]):
        first = (first + x + second) & 0xffffffff
        second = (second + y + first) & 0xffffffff
    return (first, second) == (checksum1, checksum2)


class WindowsStorePath:
    def __init__(self, path):
        try:
            self.path = Path(_local_path(path))
        except ValueError as error:
            raise UnsafePath() from error
        # Reserve room for SQLite companions and a sibling stage under Win32 path limits.
        if len(str(self.path).encode('utf-16-le')) // 2 > 235 or _drive(self.path.anchor) != 3:
            raise UnsafePath()
        self._directories = ExitStack()
        self._file = ExitStack()
        try:
            for parent in reversed(self.path.parents):
                handle, _ = _handle(parent, self._directories, directory=True)
                if parent == Path(self.path.anchor):
                    filesystem = ctypes.create_unicode_buffer(32)
                    _checked(_volume(handle, None, 0, None, None, None, filesystem, len(filesystem)))
                    if filesystem.value != 'NTFS':
                        raise UnsafePath()
            digest = sha256(os.path.normcase(str(self.path)).encode('utf-8')).hexdigest()
            self._writer = _mutex(None, False, 'Global\\Aegis-AdapterStore-' + digest)
            _checked(self._writer)
            self._directories.callback(lambda: _checked(_close(self._writer)))
        except BaseException:
            self.close()
            raise

    @contextmanager
    def serialize(self, timeout_ms=2000):
        result = _wait(self._writer, timeout_ms)
        if result == 258:
            raise StoreBusy()
        if result not in (0, 128):  # WAIT_ABANDONED still acquires ownership; SQLite recovers.
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            yield
        finally:
            _checked(_release(self._writer))

    def companions(self):
        present = set()
        for suffix in ('-wal', '-shm', '-journal'):
            with ExitStack() as temporary:
                try:
                    _handle(Path(str(self.path) + suffix), temporary)
                except FileNotFoundError:
                    continue
                present.add(suffix)
        return present

    def read_header(self):
        """Hold the main file against rename while SQLite holds its own RW handle."""
        _, info = _handle(self.path, self._file)
        if info.attributes & 1:  # FILE_ATTRIBUTE_READONLY
            raise PermissionError('Store is read-only')
        # A separate CRT handle reads the same pinned pathname; SQLite owns its writes.
        with self.path.open('rb') as stream:
            return stream.read(100)

    def stage(self):
        fd, name = tempfile.mkstemp(prefix='.aegis-adp-', suffix='.sqlite', dir=self.path.parent)
        os.close(fd)
        return Path(name)

    def publish(self, stage):
        # Flush the complete staged database, then rename without replacement on Windows.
        with stage.open('r+b') as stream:
            os.fsync(stream.fileno())
        if self.companions():
            raise UnsafePath()
        os.rename(stage, self.path)

    def close(self):
        try:
            self._file.close()
        finally:
            self._directories.close()
