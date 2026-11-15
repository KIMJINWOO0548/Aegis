# Windows read-only synthetic fixture input (2026-11-16 KST)

## Change and boundary

The synthetic adapter fixture CLI used to reject every Windows file before
reading it with `PLATFORM_UNVERIFIED`. It now uses `CreateFileW` with
`OPEN_EXISTING`, `GENERIC_READ`, `FILE_SHARE_READ`, and
`FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_BACKUP_SEMANTICS`.
Metadata, file type and bytes are read from that same handle. Directories,
final-component reparse points, non-disk handles and malformed/device/ADS/UNC
path syntax are rejected. Reads stop after the configured limit plus one byte;
the adapter still reports `INPUT_LIMIT` rather than partially admitting input.
The handle is closed on successful reads and all tested failure paths.

This does **not** certify all Windows storage. Adapter storage, nullable archive
writing, backup/restore and diagnostics keep their existing platform gates.
Preflight still returns overall `BLOCKED` on Windows; its fixture entry alone
reports `NATIVE_READER_AVAILABLE`, never runtime PASS.

The reader is **not a path containment sandbox**. Parent junctions/symlinks
may redirect a locally spelled path to another filesystem, including a network
volume. The drive-name check is not final-volume verification. Windows sharing
excludes conflicting file handles, but does not exclude a pre-existing writable
file mapping. This is not a transactional snapshot guarantee. The input is a
user-selected synthetic fixture, not an untrusted remote filesystem boundary.

## Actual verification

Windows, existing Python 3.12 environment. Ran against the worktree source,
not the old Desktop main checkout:

```
python -m pytest tests/test_windows_fixture.py tests/test_adapter_input.py tests/test_platform_support.py -q --tb=short
```

- 78 passed, 1 skipped.
- Native tests cover exact binary bytes, non-ASCII/spaced filenames, bounds,
  missing/directory input, existing writer sharing violations, post-read handle
  release, metadata/read fault injection, injected reparse-attribute rejection,
  new writer/rename blocking while the reader handle is open, unchanged source,
  a successful actual synthetic CLI subprocess, and retained non-persistence.
- Actual file-symlink test is skipped because Windows symlink privilege is
  unavailable. Fault-injected reparse rejection is not equivalent to this test.

```
python -m pytest tests/test_adapter_input_cli.py -q --tb=short
```

- 32 passed, 1 failed, 1 skipped.
- Failure is the unchanged test's `Path.symlink_to` setup with WinError 1314;
  it did not reach product code. FIFO test is POSIX-only and skipped.
- This suite is **not PASS**. No privilege or security setting was changed.

No full repository regression, real equipment, durable Windows storage,
WebView2, soak test, writable-mapping race or parent-reparse race was validated
by this change. Existing broader failures remain open.

## References

- [CreateFileW: sharing and reparse-point flags](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-createfilew)
- [GetFileInformationByHandleEx](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-getfileinformationbyhandleex)

Main and existing runtime data are not changed. This is an incremental feature
branch improvement, not an enterprise-ready release.
