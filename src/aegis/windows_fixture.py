"""Bounded, read-only fixture input using one Windows file handle.

This is not a durable-storage implementation or a parent-directory sandbox.
The final path component must be a regular non-reparse disk file. Windows share
checks exclude concurrent write/delete handles for the lifetime of this read.
Existing writable mappings are not excluded. Parent redirection is not blocked;
local-path syntax is not a guarantee about the final filesystem or volume.
"""

import ctypes
from ctypes import wintypes
import ntpath
import os


def _local_path(path):
    path = os.fspath(path)
    if not isinstance(path, str) or not path or '\x00' in path:
        raise ValueError('Invalid fixture path')
    path = path.replace('/', '\\')
    # Do not open device namespaces, UNC servers, ADS, or DOS device aliases.
    if path.startswith('\\\\') or ':' in ntpath.splitdrive(path)[1]:
        raise ValueError('Only local disk fixture paths are supported')
    reserved = {'CON', 'PRN', 'AUX', 'NUL', 'CONIN$', 'CONOUT$'}
    reserved.update(prefix + str(i) for prefix in ('COM', 'LPT') for i in range(10))
    reserved.update(prefix + i for prefix in ('COM', 'LPT') for i in ('¹', '²', '³'))
    for part in ntpath.splitdrive(path)[1].split('\\'):
        if part in ('', '.', '..'):
            continue
        if (part.endswith((' ', '.')) or part.split('.')[0].upper() in reserved
                or any(ord(c) < 32 or c in '<>"|?*' for c in part)):
            raise ValueError('Ambiguous fixture path')
    return ntpath.abspath(path)


def read_fixture(path, limit):
    if os.name != 'nt':
        raise OSError('Windows fixture reader requires Windows')
    if type(limit) is not int or limit < 0:
        raise ValueError('Invalid input limit')
    path = _local_path(path)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetDriveTypeW.argtypes = [wintypes.LPCWSTR]
    kernel.GetDriveTypeW.restype = wintypes.UINT
    if kernel.GetDriveTypeW(ntpath.splitdrive(path)[0] + '\\') not in (2, 3):
        raise ValueError('Fixture path must name a local disk drive')
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                  ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.GetFileType.argtypes = [wintypes.HANDLE]
    kernel.GetFileType.restype = wintypes.DWORD
    kernel.GetFileInformationByHandleEx.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                                   ctypes.c_void_p, wintypes.DWORD]
    kernel.GetFileInformationByHandleEx.restype = wintypes.BOOL
    kernel.ReadFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                               ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
    kernel.ReadFile.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL

    class AttributeTagInfo(ctypes.Structure):
        _fields_ = [('attributes', wintypes.DWORD), ('tag', wintypes.DWORD)]

    # GENERIC_READ, FILE_SHARE_READ, OPEN_EXISTING,
    # FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_BACKUP_SEMANTICS.
    handle = kernel.CreateFileW(path, 0x80000000, 1, None, 3, 0x02200000, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        info = AttributeTagInfo()
        if not kernel.GetFileInformationByHandleEx(handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
            raise ctypes.WinError(ctypes.get_last_error())
        if kernel.GetFileType(handle) != 1 or info.attributes & (0x10 | 0x400):
            raise ValueError('Fixture must be a regular non-reparse disk file')
        result = bytearray()
        while len(result) <= limit:
            size = min(65536, limit + 1 - len(result))
            buffer = ctypes.create_string_buffer(size)
            read = wintypes.DWORD()
            if not kernel.ReadFile(handle, buffer, size, ctypes.byref(read), None):
                raise ctypes.WinError(ctypes.get_last_error())
            if not read.value:
                break
            result.extend(buffer.raw[:read.value])
        return bytes(result)
    finally:
        kernel.CloseHandle(handle)
