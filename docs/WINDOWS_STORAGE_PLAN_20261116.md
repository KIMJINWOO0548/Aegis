# Windows storage implementation boundary

## Current result

Production `AdapterStore`, nullable archive and backup publication remain
`PLATFORM_UNVERIFIED` on Windows. The fixture reader is read-only and does not
change this. Removing `os.name` checks is not a backend implementation.

`python scripts/probe_windows_sqlite_recovery.py` creates its own temporary,
synthetic SQLite WAL database, commits row 1, leaves row 2 in an open transaction,
kills only its child process, reopens the database, checks integrity and the exact
committed prefix, and commits a new row after recovery. This is a standalone
SQLite process-termination test, not an Aegis ingest test or power-loss test.

On the selected Windows PC, one initial run and ten consecutive runs completed
successfully on 2026-11-16 KST. Each confirmed prefix preservation, uncommitted
rollback and resume; power loss was NOT_RUN and production storage BLOCKED.

## Why the existing path cannot simply be enabled

- `adapter_store._check_header` and nullable archive use POSIX no-follow opens
  before SQLite opens the pathname again. A Windows backend must validate file
  type, link count and identity, plus sidecar identity and trusted directory scope.
- `_sync_parent` requires a directory fsync. Success on a SQLite COMMIT does not
  establish durability of a newly published name or backup directory entry.
- Backup uses exclusive hard-link publication, removes the staging alias, then
  syncs the destination directory. A replacement must preserve no-overwrite and
  distinguish publication success from uncertain flush/cleanup outcomes.
- A read-only handle denying writes cannot stay open while SQLite needs writes.
  Reusing the fixture reader as a write-store lock would either break SQLite or
  drop the claimed protection. WAL/SHM/journal names are part of this contract.

## Implementation sequence and acceptance gates

1. Introduce a platform filesystem boundary without changing transaction logic.
   Keep the current POSIX implementation and all checks. Add Windows native
   handles for regular-file/type/link/identity checks and explicit ownership.
   Restrict the initial candidate to a trusted local NTFS workspace; reject UNC,
   device/ADS names and reparse paths, rather than claiming broad filesystem support.
2. Build a Windows candidate with explicit create/open/close and sidecar lifetime.
   Prove that another writer and path rename cannot redirect the active database.
   Preserve exact schema, fixture binding, cursor and commit-readback contracts.
3. Prove reopen behavior in the actual AdapterStore after precommit termination,
   after commit before acknowledgement, and during validation/close. Record
   ambiguous outcomes as `STORE_RESULT_UNKNOWN`, not failure/no-op or success.
4. Implement backup publication independently. Verify full snapshot first;
   publish without replacement; preserve destination on any later error. Establish
   and document the Windows metadata durability contract with official APIs and
   target filesystem tests before enabling production backup.
5. Validate local disk-full, sharing violations, existing companion files,
   destination races, hardlinks/reparse points, interruption and readback errors.
   Include fresh install and existing-data upgrade paths with the same artifact.
6. Only then update the platform preflight and remove the corresponding guard.
   Archive/backup guards must not be relaxed merely because fixture reads work.

## Still not verified

Actual Aegis Windows durable ingestion; Windows backup publication and directory
durability; power cut; hostile existing writable mappings; real equipment;
installer/Windows service; hardware cache behavior. The process probe does not
close any of these gates. User security settings and existing files are unchanged.
