# Read-only storage observations and advisory policy

This independent backend step starts at `20f53e90a4f8a9df3cfdf90b8e15653e1ada5059`. It does not restore restricted preservation copies or replace unavailable `5075ccd`. Existing collector/writer, API, schema, UI and backup manifests are unchanged.

## Run

From an installed checkout (or with `PYTHONPATH=src`):

```sh
python -m aegis.storage_diagnostics --archive raw.sqlite --backup-dir backups --backup backups/one.sqlite
python -m aegis.storage_diagnostics --archive raw.sqlite --backup-dir backups --backup backups/one.sqlite --policy storage-policy.json --telemetry ingestion-capture.json
```

No files are written by the command as application output. JSON goes to stdout; bad input returns exit 2 and a fixed-code JSON error on stderr. A successfully delivered diagnosis returns exit 0 even when measurements are unavailable or thresholds are exceeded. Inspect individual component/check statuses; there is no overall healthy/PASS flag. Filesystem paths, file names, raw signals and device identities are omitted from reports.

`--at` accepts an aware timestamp for reproducible replay evaluation and marks `clockMode: EXPLICIT_UTC`. Without it, the process clock is `SYSTEM_UTC`. An explicit future evaluation time is not a prediction or a live measurement. Shell redirection is caller-controlled and can truncate its selected output before Python starts; never redirect into source, backup, policy or capture files.

## Explicit policy

For example, using synthetic/operator-reviewed values:

```json
{"format":"aegis-storage-policy-1","policyId":"synthetic-storage-review","version":1,"archiveReceiptRetentionDays":30,"backupMtimeReviewDays":30,"minAvailableBytes":1073741824,"minAvailablePercent":10,"maxReceiptAgeSeconds":60,"maxArchiveCommitMs":100,"maxTelemetryAgeSeconds":30}
```

Only `format`, `policyId` and integer `version` are required. Omit individual thresholds to leave them `NOT_CONFIGURED`; explicit null, bool, NaN, unknown fields and out-of-range values are rejected. Reports retain the normalized policy and canonical SHA256, independent of JSON key order/whitespace. This unsigned policy is an operator-supplied reference, not an authenticated global policy registry.

Retention/review days are integers 1–36,500; minimum available bytes is 0–2^63−1; percent is 0–100; receipt-age seconds is 0–3,153,600,000; commit milliseconds is 0–3,600,000; capture-recency seconds is an integer 1–86,400. Maximum-age/duration equality is within limit; minimum-space equality is within limit. There are no deletion defaults or automatic enforcement.

`NOT_CONFIGURED` means its threshold was omitted. `UNAVAILABLE` means a configured check lacks suitable measurements or prerequisites. `EXCEEDED` and `WITHIN_LIMIT` require the corresponding usable measurement. A configured commit threshold with no recency policy remains `UNAVAILABLE`; it is not silently reclassified as absent.

## Space observations

The archive family includes main, WAL, SHM and rollback-journal names. Up to 32 explicitly selected backup paths are inspected; there is no discovery or recursive scan. Regular files only are accepted, and hardlinks are deduplicated within each inventory by device/inode. No selected backups means `NOT_CONFIGURED`, not zero verified backups. Missing, permission denied and other I/O failures are separate states; partial inventory does not report an understated successful byte total.

`apparentBytes` is summed file length. `allocatedBytesEstimate` uses filesystem-reported [512-byte allocated blocks](https://docs.python.org/3.12/library/os.html#os.stat_result.st_blocks) where supported; it is not exclusive physical occupancy across CoW, compression, deduplication, quotas or snapshots. SQLite `logicalBytes` is page-count × page-size and is separate from file-family footprint. `nextBackupEstimate` uses only those logical pages, explicitly has `classification: ESTIMATE` and `guaranteedToFit: false`; staging, filesystem overhead and concurrent growth are not reserved.

Capacity is sampled at the archive parent and configured backup directory using non-superuser-available blocks. `sameFilesystem` identifies shared volume identity; available space is never summed. The backup-directory capacity is for that configured location; explicit backup files can be elsewhere. A read-only mount is unsuitable for a space-policy check even if it reports available blocks. This does not test actual write permissions or guarantee quota availability or a successful backup.

Filesystems, file sizes and SQL records are non-atomic observations at different instants (`sampling: NON_ATOMIC`). Concurrent growth, checkpointing and file replacement can change the next reading. Trusted local directories are required; adversarial path/sidecar manipulation and remote filesystem guarantees are outside this step.

## SQLite and source preservation

SQL uses `mode=ro`, `query_only`, trusted-schema disabled and one fixed read transaction. Existing raw1.1 schema/payload/cursor/gap validators check the selected snapshot. No ingest call, write SQL, source checkpoint, journal-mode conversion, automatic cleanup, pruning, delete, overwrite, restore activation or equipment command is executed. Only the designated diagnostic inputs are read.

SQLite's [normal WAL readers](https://www.sqlite.org/wal.html#readonly) can initialize missing companions and maintain shared-memory metadata. This diagnostic therefore refuses SQL access to a WAL-header archive lacking regular WAL/SHM companions: it reports `DB_UNAVAILABLE` / `WAL_COMPANIONS_REQUIRED` while still reporting file/volume observations. This conservative refusal is expected for many closed WAL archives. Diagnose the running archive with existing companions, or a separately verified standalone DELETE-journal backup. The tool does not convert a user archive to make it readable.

Existing WAL shared-memory read marks and ordinary filesystem access metadata may change during a read. Companion checks are non-atomic; a concurrent collector close/rename can race them. There is no claim of zero OS metadata effects or zero companion creation in such races. `actionsPerformed: []` means no application data mutation; `mutationScope` and `sqliteReaderEffects` state that boundary explicitly. Footprint is sampled after SQL so ordinary reader-created metadata is included when present. Raw records, first receipt, cursor and gaps are the preserved data boundary.

Unknown/corrupt schema/payload/cursor/gap is `INVALID`; an unavailable/busy/locked SQLite connection is `DB_UNAVAILABLE`. A SQLite open failure is not labeled permission denied without an OS permission error. Empty data, unsupported types, limits and future receipt times never become successful receipt checks.

## Age, retention and commit latency

`latestReceiptAgeSeconds` and `oldestReceiptAgeSeconds` compare stored **first receipt** with the evaluation time. Receipt precedes commit. These values are not disk write latency, source end-to-end latency or collector liveness. `archiveReceiptRetentionDays` is a maximum-first-receipt-age advisory and counts rows older than the policy; it does not prove a minimum continuous retention window. Sparse sequences and declared gaps remain valid; `sourceCompleteness: NOT_ASSERTED` remains explicit.

`backupMtimeReviewDays` evaluates filesystem modification age for operator review. Modification time is not backup creation or last successful verification time. Every selected backup remains `integrity: NOT_VERIFIED` and `creationTimeStatus: NOT_RECORDED`. Size/mtime observations never declare a recoverable backup or select anything for deletion.

Commit duration is not persisted in the archive. Without `--telemetry`, `commitLatency.status` is `NOT_MEASURED`. Optional input is a bounded timestamped capture of existing `/v1/ingestion` rows:

```json
{"format":"aegis-ingestion-capture-1","capturedAtUtc":"2026-11-09T12:00:00Z","ingestion":[{"deviceId":"SYNTHETIC-01","transport":"replay","archiveEnabled":true,"lastCommittedRunId":"11111111-1111-4111-8111-111111111111","lastCommittedSequence":5,"archiveCommitMs":12.5}]}
```

The example is illustrative synthetic input; its identity must match the selected archive to be usable. Additional existing ingestion fields are allowed. Capture time and each matching active cursor's first receipt must be within the explicit recency policy; capture cannot precede that receipt. Missing, duplicate, future, stale, nonfinite/bool/negative duration, wrong transport or mismatched cursor identity cannot pass the configured check. All archived device cursors must be covered.

`MEASURED_LAST_COMMIT` means the worst supplied matched `archiveCommitMs`: the collector's last reported elapsed append-plus-cursor-read duration, not an fsync-only measurement, average, percentile or continuous live monitor. Its actual commit timestamp is not recorded. Capture trust is `OPERATOR_SUPPLIED_UNSIGNED`; no network request, continuous alarm or automatic action is added. This first step does not export captures or instrument a new timing source.

## Verification and limits

Policy/capture inputs must be regular non-symlink files, opened nonblocking with no-follow and descriptor validation; FIFOs are rejected rather than waited on. Input/output JSON is limited to 64 KiB. One archive supports 10,000 raw rows, existing archive-validator page/payload limits, and 32 selected backup paths. A 5-second cooperative SQL/elapsed budget with 5-million SQL virtual steps can reject valid larger archives; OS I/O/metadata calls are not hard real-time cancellable. There is no partial successful receipt result when limits are reached.

Linux synthetic/temporary verification is recorded in [storage-policy-diagnostics-20261109.json](evidence/storage-policy-diagnostics-20261109.json). Windows is rejected as `PLATFORM_UNVERIFIED`; Windows services, actual equipment/field data, physical disk exhaustion, quota enforcement, power loss, remote filesystems and long-duration/high-volume use remain unverified. Capacity/permission failures are injected; the host filesystem is not filled and user data is never deleted.

Explicit empty CLI values such as `--policy ''` or `--telemetry ''` are rejected as invalid file inputs. Omit the option to leave it unconfigured. Explicit files containing JSON null are also rejected.
