# Consistent archive backup and verified fresh restore

This independent backend step starts at rule-lock commit `52844968bbd25ea6ea14bea40ca64b3b37a86fbe`. It does not restore inaccessible preservation copies or replace unavailable `5075ccd`. Collector/writer/schema, prior analysis CLI, UI and equipment behavior are unchanged.

## Use

Run from an installed Aegis checkout (or set `PYTHONPATH=src`). Select a trusted local directory and new destination names. Keep stdout as a separately retained reference:

```sh
python -m aegis.archive_backup backup --source raw.sqlite --destination backup.sqlite > backup.manifest.json
python -m aegis.archive_backup verify --source backup.sqlite --manifest backup.manifest.json
python -m aegis.archive_backup restore --source backup.sqlite --destination restored.sqlite --manifest backup.manifest.json > restored.manifest.json
```

The backup/restore command emits a bounded manifest only after publication and directory synchronization. `verify` returns `status: VERIFIED` plus the checked manifest. Retain the successful manifest separately; shell redirection creates/truncates its selected output file before Python starts, so use a **new output filename outside the source/destination companion names**. The tool does not write or atomically publish the reference file itself. A failed output write can leave a completed backup without a recoverable reference; preserve the file and investigate before reuse.

Only POSIX execution is currently enabled. Windows is explicitly rejected as `PLATFORM_UNVERIFIED` before a destination is created; implementing and testing Windows publication/directory durability is a later step.

## Consistency and validation

The source connection is SQLite `mode=ro`, `query_only`, with a fixed read transaction. SQLite's [online backup API](https://www.sqlite.org/backup.html) copies that pinned snapshot, including committed WAL state, rather than copying the main file alone. A subsequent WAL writer commit is outside this backup snapshot. No source checkpoint, journal mode change, ingest call, cursor movement or source record deletion is requested. SQLite may maintain its ordinary WAL/shared-memory companions while reading; this is not an immutable-file copy. Holding the read snapshot can delay WAL reclamation until the bounded operation closes.

The validator checks SQLite integrity, archive schema v1, supported raw1.1 payloads, stored/source identity and timestamps, active-run cursor maximum and recorded gap consistency. It hashes each table in deterministic key order, preserving exact stored JSON text, first receipt, hierarchy and sequence values. Raw records, cursors and gaps must match before and after copying. Sparse runs and older retired runs are valid; `coverage: NOT_ASSERTED` explicitly means that complete source sequence coverage and undeclared gaps are not proven.

The private staged database is converted to standalone DELETE journal form, closed, reopened read-only and verified. The manifest binds its SHA256, schema fingerprint, per-table row counts and logical SHA256 values. Verify/restore require this retained unsigned manifest; they reject non-standalone artifacts and mismatches. This detects deviation from that supplied reference, not malicious coordinated replacement of both reference and database, source authenticity or global provenance. Raw values and filesystem paths are absent from generated reports and normal errors.

Restore validates the input file hash and logical state, copies to a **fresh** file, then revalidates logical identity and input stability. SQLite headers can differ after copying, so the restored file receives its own physical SHA256. This command does not activate a restored database, reconnect collectors or switch running workers. The original and all completed backups remain available. Manual activation requires a separately reviewed operational procedure.

## Publication and failures

Existing destination files, directories, symlinks/hardlinks and `-wal`, `-shm`, `-journal` companions are refused. Source and source companion names are refused even when absent. Parents must already exist. Publication uses a same-filesystem atomic no-replace hard link after validation and file fsync. A competing destination file is preserved. The short-lived staging alias is removed before directory fsync so two SQLite names do not retain the same inode.

Only files with the exact staging names inside the private directory created by this invocation are cleaned up. There is no automatic retention deletion, source pruning, backup deletion, overwrite, force option or in-place restore. Trusted local directories without concurrent path manipulation are required; this is not a defense against a hostile directory owner, sidecar-name races, filesystem replacement or network filesystem semantics.

Before publication, failures report `publication: NOT_PUBLISHED`. After publication, sync/output failure reports `PUBLISHED_DURABILITY_UNKNOWN` and preserves the completed destination. `PUBLISHED_CLEANUP_REQUIRED` also means a tool-owned alias may remain; inspect the `.aegis-backup-*` directory and do not open that alias as another database. Do not blindly retry into the existing destination or automatically remove it. Error exit is 2, with structured stderr and no raw exception/path. If the error stream itself is unusable, delivery of that error cannot be guaranteed.

## Bounds and evidence

One operation supports at most 256 MiB logical database pages, 100,000 raw rows, 4,096 cursors, 100,000 recorded gaps, 256 KiB per payload and 128 MiB total payload, with a 64 KiB reference. A 30-second cooperative budget and 20-million SQL virtual-step limit cover source validation, incremental copying, artifact validation and hashing. SQLite busy timeout is 0.2 seconds and copy progress is checked every 64 pages. These limits can reject a valid larger archive; there is no partial-success backup. OS I/O/fsync calls are not hard real-time cancellable. Incremental export, scheduling, retention policy and large-archive tuning are future work.

The Linux synthetic test evidence is recorded in [archive-backup-20261109.json](evidence/archive-backup-20261109.json). It distinguishes the fixed snapshot from later committed and uncommitted WAL writes; verifies original/receipt/cursor/gap roundtrips; checks an actual CLI restore can resume idempotent appends; injects copy/capacity/validation/fsync/publication/output failures; and exercises corruption, manifest mismatches, existing targets and limits. Capacity errors are injected, not actual host disk exhaustion.

Actual Windows/Windows service, device, field data, remote filesystem, power-loss durability and long-duration/high-volume backup loads remain unverified. Existing unrelated generator/iterator behavior in the direct numeric-rule Python API remains outside this change.
