# Adapter local-store commit boundary

Base `60e81f5b2929c16ebf20550c1b25ecd064b9adb6`. Independent accessible history; no restricted preservation copies or unavailable `5075ccd` are restored.

## Scope and alternatives

The existing raw-v1 key and payload require original device/run/sequence/time plus SIMULATOR. Coercing nullable adapter metadata into it or adding new tables would break fidelity or strict schema consumers. The implementer selects a separate `adapter-store-1` SQLite receipt store, using the existing validated adapter input and SQLite transaction pattern. This is a safe local persistence step, **not completed raw-v1 delivery or a general archive-v2 migration**.

Preserve existing SIMULATOR collector, RawArchive/schema/raw/cursor/gap, adapters, IPC/API/UI and prior CLI defaults. No real equipment/data/company libraries, source ACK, commands, dynamic drivers, auto migration, delete, overwrite or hidden legacy delivery. Report `legacyArchiveDelivery: NOT_CONNECTED` and `sourceAcknowledgement: NOT_SENT` always.

## One store and transaction

One explicit store is bound to one synthetic descriptor/stream and canonical finite fixture. Preserve exact original fixture bytes, their SHA, canonical binding SHA, exact accepted response wire and hashes, canonical original record JSON and first host receipt. Opaque delivery cursor is separate from optional source sequence/run/time; never fabricate original metadata. A null cursor is encoded as JSON scalar `null` in a NOT NULL unique key, not a nullable SQL composite primary key.

For each page: BEGIN IMMEDIATE; validate current store and rebuild committed admission from its bounded page log; validate a candidate page completely; insert page evidence and new records; update delivery cursor/EOF/counts; COMMIT; re-read/verify committed state. Only then return `LOCAL_STORE_COMMITTED`. Identical replay returns `ALREADY_LOCAL_STORE_COMMITTED` without replacing first receipt/evidence. Changed replay/record conflicts reject the whole page. Empty EOF is persisted atomically; transient NO_NEW_DATA commits no new page and acknowledges none.

Stored pages, records, original fixture and metadata/state are revalidated on opening and before public committed state/next reads. Rebuild admission from page order and compare every record/receipt/hash and cursor/EOF/count, not just SQLite integrity. Never repair missing/corrupt data silently. Cursor is authoritative local committed progress, not a proposed in-memory value. No source ACK or downstream legacy confirmation is inferred.

## Files and failures

Accept a fresh explicit path or a valid bound adapter store. Existing regular file headers must identify our application ID/version before SQLite opens them; refuse raw-v1, unknown/empty databases, symlinks, hardlinks and reserved companion filenames. New files use exclusive create0600; parent already exists; preexisting sidecars block new creation. Initialize owned schema/identity with DELETE journal first, then WAL+FULL, so the main header marker is available before later opens. Validate exact schema before writes. Failed initialization files are preserved for manual inspection, never automatically deleted or overwritten.

Errors before COMMIT attempt roll back and preserve committed records/cursor; failed rollback fences the object. Any COMMIT/post-COMMIT confirmation exception is `STORE_RESULT_UNKNOWN`, even when in_transaction is false. Fence/close that object, return no success/next-cursor acknowledgement, and require a new open plus full validation to resolve the actual result. Post-commit output/process failure can leave committed data; never call that proof of rollback.

The first session uses synchronous SQLite work in its standalone fixture process. There is no executor append whose cancellation can leave an unobserved writer behind. Source pulls remain cooperative async and serialized; there is no await between store transaction and confirmed result. Synchronous I/O may block that event loop and must not be installed into production collector/UI loops as-is. Cancellation after a commit can still hide the caller's result; the next session reads authoritative DB progress, never a candidate cursor. Physical/process crash tests distinguish precommit rollback from postcommit/pre-response recovery. Real power-loss durability is unverified.

## Bounds and validation

Keep existing1MiB fixture/64KiB page/64 accepted pages/1024 records limits. Store logical pages <=32MiB, busy timeout0.2s, cooperative SQL/validation budget5s and5million VM steps per read/write phase; postcommit confirmation starts a fresh budget. This is not an end-to-end operation/initialization deadline. No hard OS-I/O cancellation/production performance promise. POSIX local trusted directories only; hostile path replacement/network filesystems unsupported.

Test synthetic nullable metadata fidelity, local commit receipts, duplicate/conflict, first receipt preservation, empty EOF/no-data, SQL busy/full, write/commit/postcommit failures, wrong stored cursor/records, actual child-process crash/restart and caller cancellation. Prove raw-v1 files/companions/records/cursors/gaps unchanged when passed as an invalid target. Full regressions, emulator/contracts, independent review, privacy/noreply and normal separate-branch push with remote SHA follow. Windows, devices, operations, long load and CI remain unverified.
