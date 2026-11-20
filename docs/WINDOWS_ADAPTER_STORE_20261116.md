# Windows AdapterStore: local NTFS storage and recovery

## Scope

Gamja Windows 11 Pro build 26200, Python 3.12.10 with SQLite 3.49.1, fixed NTFS.
Work started from `28b08132fc95211681b6071c387b21f7691c4f4a` on
`improve/gamja-audit-20261116` in the separate audit worktree. Desktop main
remained at `bd4e44f8a78b08ae5ccb3499a68fe3af67846f78`. The two branches
matched their respective remote refs before work. No user data or UI changed.

The existing `python -m aegis.adapter_store` entry point now uses the actual
Windows writer for explicitly selected synthetic input. It stores SQLite
application ID `ADP1`, schema v1, exactly received wire bytes, first receipt,
records, and the corresponding cursor. No conversion to the experimental
journal and no schema migration. A clean existing ADP1 or an intact WAL-backed
ADP1 can reopen with the same fixture binding. Raw-v1, incompatible header or
schema and unsupported paths are rejected. This is not device ingestion or an
installed service. Nullable archive, backup/restore and storage diagnostics are
unchanged and remain blocked by their own Windows gates.

## Storage and failure boundary

- A trusted directory whose ACL prevents other users or uncoordinated tools
  from changing its contents is required. The code verifies a fixed local NTFS
  path and keeps no-delete handles on its ancestor directories and main file.
  Native no-follow opens check file type and link count; path admission rejects
  junctions/reparse points, hardlinks, remote/device namespaces, ADS and path
  aliases. Sidecars receive the same checks under the Aegis mutex. SQLite owns
  the sidecar handles and creation after validation; this is not an anchored
  custom VFS or protection against a hostile same-user process. Do not use raw
  SQLite writers, external checkpoints or rename files while Aegis uses them.
- Fresh creation stages the complete validated database beside the final path.
  A successful file flush is required before Windows no-replace rename. A
  competing destination is preserved. Normal failure cleans only the owned
  stage; process termination before rename may leave a stage that is not used
  automatically. An error after publication can leave a valid new database;
  explicitly reopen. Never adopt stale stages by overwriting a target.
- Same-store Aegis instances serialize open, read validation, write, postcommit
  confirmation and checkpointing close through the native mutex. SQLite still
  commits cursor, page and records in one transaction; busy acquisition is
  bounded. Close normally waits for the mutex; if busy it closes without a
  checkpoint, leaving WAL recovery to the next open. The serialized writer
  contract avoids racing Aegis checkpoints/commits on the installed SQLite
  version. It does not patch SQLite or prevent races with other writers.
- Before commit, an exception rolls back. If commit or confirmation is
  uncertain, the instance is fenced with `STORE_RESULT_UNKNOWN`; close and
  reopen before retrying. Identical resend does not extend the ledger or
  replace its original receipt. Source acknowledgment remains `NOT_SENT`.
- The application validates the main header, a nonempty WAL header and its
  checksum, SQLite integrity, exact schema, fixture binding, all visible page
  and record hashes/receipts, and cursor consistency. A rejected open with a
  surviving WAL does not checkpoint the main DB or WAL on close. SQLite may
  rebuild transient SHM. An unexpected rollback journal or invalid WAL header
  requires explicit recovery: preserve the main file and companions together
  and inspect a separate copy. Never delete a WAL to make a store open.
  SQLite identifies its valid committed prefix; Aegis cannot prove a removed
  or torn tail was never committed without an independent receipt or snapshot.
- `durability` in Windows commit/CLI results states `SQLITE_WAL_FULL`,
  `directorySync: UNSUPPORTED`, `powerLoss: NOT_VERIFIED`. File flush and
  same-directory publication do not establish persistence of the directory
  entry or of all companion names across sudden power loss. No volume-wide
  administrative flush or POSIX directory-fsync substitute is attempted.

References: [CreateFileW sharing and reparse flags](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-createfilew),
[FlushFileBuffers scope](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-flushfilebuffers),
[Windows no-replace rename in Python](https://docs.python.org/3.12/library/os.html#os.rename),
[SQLite WAL format](https://sqlite.org/fileformat2.html#walformat),
[SQLite FULL sync and checkpoint behavior](https://sqlite.org/pragma.html#pragma_synchronous),
and [SQLite WAL-reset bug scope](https://sqlite.org/wal.html#the_wal_reset_bug).

## Actual verification on 2026-11-16

Every failing write/termination was on a newly created fixture or pytest
temporary directory; tests never target a user's archive. Text and JUnit logs
are retained under local ignored `runtime/`, not committed.

| Run | Result | Local log |
|---|---|---|
| Baseline new fault tests, existing constructor unchanged | 9 FAIL, all `PLATFORM_UNVERIFIED` | `storage-fault-red.log` |
| Normal errors during staging flush/destination collision | 2 FAIL before cleanup fix; both pass in final run | `storage-fault-followup-red.log` |
| Rejected WAL-backed DB preservation | 1 FAIL before disabling close checkpoint on rejected existing WAL; passes in final run | `storage-wal-red.log` |
| Partial WAL header and per-commit durability qualifier | 2 FAIL before fixes; both pass in final run | `storage-partial-wal-red.log` |
| Final native storage (17) and preflight (4) | **21 PASS**, none skipped | `storage-native-final.log` |
| Adjacent Windows regression on final code | **165 PASS, 3 FAIL, 3 SKIP** | `storage-windows-release-check.log` |
| Actual two CLI invocations, same temporary database | both exit 0; initial 2 pages/2 records, reopen 0 reads and 0 new writes | local `runtime/adapter-cli-*/commands.log` |

The final regression failures are three Windows symlink fixture creation
failures, WinError 1314 before the safety assertions. Existing skips are the
Linux output failure device, POSIX FIFO and one missing symlink privilege.
Windows junction refusal, Windows hardlink refusal and actual locked/read-only
file tests executed. An earlier run also hit intermittent Windows socket
initialization 10014 in two CLI cases; no 10014 was observed in the final scoped
run. No guard was removed or test skipped to hide these failures.

Commands from the audit worktree:

```powershell
$python = '.venv-candidate/Scripts/python.exe'
& $python -m pytest -q tests/test_windows_adapter_production.py tests/test_platform_support.py --tb=short --junitxml=runtime/storage-native-final.xml
& $python -m pytest -q tests/test_windows_adapter_production.py tests/test_platform_support.py tests/test_adapter_store.py tests/test_adapter_store_cli.py tests/test_adapter_store_validation.py tests/test_windows_fixture.py tests/test_windows_journal_candidate.py tests/test_windows_adapter_candidate.py tests/test_windows_backup_candidate.py tests/test_windows_replay_candidate.py --tb=short --junitxml=runtime/storage-windows-release-check.xml
$env:PYTHONPATH = (Resolve-Path src).Path
$storeDir = New-Item -ItemType Directory -Path (Join-Path $env:TEMP ('aegis-adp-' + [guid]::NewGuid()))
& $python -m aegis.adapter_store --fixture examples/adapter-input.synthetic.json --store (Join-Path $storeDir 'store.sqlite')
& $python -m aegis.adapter_store --fixture examples/adapter-input.synthetic.json --store (Join-Path $storeDir 'store.sqlite')
```

Native tests actually terminate the owned writer before first publication,
before transaction commit, and after commit but before a receipt is returned.
Two independent Python processes race initialization and resubmit one page.
Other cases cover SQLite page limit FULL, injected precommit capacity and stage
flush errors, failed confirmation, rejected DB/sidecar preservation, incompatible
formats, and atomic cursor/record/wire recovery. Stage-flush injection is not a
power-loss test. Physical disk exhaustion and all Windows VFS/ACL failures are
not claimed. A full-suite rerun, POSIX run, fresh installer/reboot and soak are
**NOT_RUN** in this change.

## Original acceptance ledger

- **A4, scoped verified:** actual Windows ADP1 write, transaction/reopen,
  no-replace startup, duplicate and concurrent Aegis writes, committed cursor
  and evidence recovery, fail-closed path and damaged-record checks.
- **A1/A3, partial:** improvement ancestry and local/remote SHA can be checked;
  interrupted synthetic store recovery is demonstrated. No same-artifact
  installer or service/reboot mapping; no real sustained collector recovery.
- **A4, remaining:** nullable archive/backup publication and verified restore,
  physical disk-full and power-loss limits, capacity/retention/export/log
  policy, unsupported/shared filesystems, install/update evidence.
- **A2 and A5–A9 remain open at their original scope.** No UI, independent
  detection quality, actual equipment, or 24–72 hour installed acceptance work
  was performed here. No whole acceptance area is marked complete.
