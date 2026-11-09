# Adapter local-store commit confirmation

This independent backend step starts at `60e81f5b2929c16ebf20550c1b25ecd064b9adb6`. Restricted preservation files and unavailable `5075ccd` are not restored. The existing SIMULATOR collector, raw-v1 schema/raw/cursor/gap, adapter-input CLI, IPC/API/health and UI are unchanged.

## What is connected, and the raw-v1 blocker

The new synthetic adapter can now connect to an explicit **separate local SQLite receipt store**. A validated in-memory proposal is no longer presented as local storage completion: `LOCAL_STORE_COMMITTED` is returned only after the page evidence, new records/first receipts and delivery cursor/EOF commit together and are read back successfully.

Raw-v1 delivery remains `legacyArchiveDelivery: NOT_CONNECTED`. Existing RawSnapshot1.1 requires SIMULATOR plus source run/sequence/time and other mandatory legacy fields. A generic nullable record cannot be coerced into it without inventing original metadata or changing source meaning. Adding adapter tables to the same file also breaks strict raw-v1 schema consumers. This implementation is a side-by-side local receipt store, **not a completed general archive-v2 migration, original collector integration or downstream delivery confirmation**.

`sourceAcknowledgement: NOT_SENT` always remains explicit. There is no device ACK, equipment command, network adapter or company library. This is one fixed synthetic fixture/stream per local store. Long-running/open-ended device streams, source ACK transactions, archive-v2/IPC/API migration and old/new writer cutover require separate work.

## Run and resume

From an installed checkout (or with `PYTHONPATH=src`), choose a new local file whose parent already exists:

```sh
python -m aegis.adapter_store --fixture examples/adapter-input.synthetic.json --store adapter-receipts.sqlite
python -m aegis.adapter_store --fixture examples/adapter-input.synthetic.json --store adapter-receipts.sqlite
```

The first run stores the two-page example. The second run validates the stored evidence and sees durable EOF without rereading the fixture source pages or adding duplicates. `counts.committedPages`/`committedRecords` are totals; `thisRunCommittedPages` counts only this session's confirmed new-page commits, excluding an interleaved writer's commits. `pageReads` counts this adapter instance's source reads.

Store binding includes descriptor, delivery stream and canonical parsed fixture SHA256. JSON key order/whitespace can change without changing that binding; content, array order, synthetic delay configuration, origin or descriptor changes are refused under the same store. Exact original fixture bytes and their hash remain the **first stored** evidence, alongside canonical binding. This finite-fixture policy is deliberately stricter than a future open-ended adapter protocol.

The old `python -m aegis.adapter_input` CLI still has `NOT_PERSISTED` behavior. Persistence is opt-in through this separate CLI/library. Normal reports omit raw values, identities, paths and opaque cursor tokens. Library `.committed_cursor` is an explicitly local committed cursor; original source sequence/run/time remain inside original record metadata and are never filled from delivery tokens or SQL row ordinals.

## Atomic data and confirmation

One store contains its pinned fixture/descriptor, an ordered accepted-page log, original record JSON/first host receipts and one delivery state row. Initial null tokens use the NOT NULL JSON scalar key `null`; they are not nullable SQL primary-key components. Empty terminal pages retain evidence and EOF atomically even when the committed cursor remains null. Transient NO_NEW_DATA stores no new page and returns `pageCommitStatus: NO_NEW_PAGE`; a store with only initialization metadata reports `NO_COMMITTED_PAGE`.

For each accepted page:

1. Begin an IMMEDIATE transaction, validate the bounded current store and rebuild a candidate admission from committed pages.
2. Validate the entire new page, pinned fixture response, replay identities and limits. Changed page/record content rejects the whole page. Exact overlap preserves the first receipt; identical retransmission is already committed.
3. Insert exact response wire/hash and canonical original records/first receipts, then update delivery cursor, EOF and counts in the same transaction.
4. COMMIT with WAL and FULL synchronization. Reopen a consistent read transaction, reconstruct the whole stored state, and confirm the **specific requested page's canonical/wire hashes and host receipt**, the unchanged precommit page prefix (including all original receipts), and immutable fixture evidence hashes. A concurrent valid writer may append later pages; it cannot change that confirmed prefix.
5. Only after that confirmation return `LOCAL_STORE_COMMITTED` or `ALREADY_LOCAL_STORE_COMMITTED`.

Public committed-state access is blocked during an operation, so a callback cannot observe a candidate cursor as confirmed progress. Reopening and public reads check SQLite integrity, exact schema objects, bound fixture and every page/hash/order, reconstructed original record/first receipt and cursor/EOF/count. A coherent but empty state is insufficient to acknowledge a page that is absent. Unexpected triggers/views/indexes, missing pages/records, changed receipts or a wrong stored cursor fail validation; there is no automatic repair/reset.

Original nullable metadata, original timezone strings, exact permitted source integers, signal quality/nulls and host first receipt separation are inherited from the adapter-input contract. Synthetic wire is generated from parsed binary64 fixture values; exact caller fixture bytes are saved separately. Hashes and schema markers are unsigned local evidence, not authenticated hardware provenance or protection against coordinated replacement by the database owner.

## Failure and restart boundary

[SQLite transaction errors can have different rollback behavior](https://www.sqlite.org/lang_transaction.html). This implementation never treats `in_transaction == false` as proof that COMMIT failed.

| Outcome | Behavior |
| --- | --- |
| Input/conflict/known precommit write failure | Roll back this page; prior committed evidence/cursor remain. Earlier successfully committed pages in this run are not rolled back. |
| Failed rollback | Fence/close the store object and return STORE_RESULT_UNKNOWN. |
| COMMIT exception, including an exception after actual success | Fence/close; return STORE_RESULT_UNKNOWN with no successful acknowledgement. |
| Postcommit validation cannot find the requested page or readback fails | Fence/close; return STORE_RESULT_UNKNOWN. |
| Output/close failure after confirmed data exists | Error delivery can be uncertain; preserve the file and reopen/validate. CLI reports STORE_RESULT_UNKNOWN when its successful report cannot be delivered. |
| Timeout/cancellation during synthetic read | No current page commit or candidate cursor publication. |
| Caller cancellation/process exit after commit but before observing the response | Data can already be committed. Next open uses the authoritative page log/cursor and idempotent replay. |

After STORE_RESULT_UNKNOWN, that object rejects further state/record/cursor access. Close it and create a new `AdapterStore` with the same fixture/path. Full reconstruction determines whether the page committed; retry against that state. Do not delete, truncate, force-reinitialize or assume failure means no data. Errors such as READ_LIMIT or a later page conflict can leave a valid committed prefix; always resume from the store rather than the old memory proposal.

This first runner performs **synchronous SQLite work inside a standalone fixture process**. It does not schedule an executor append that can continue unnoticed after async cancellation. There is no await between transaction writes, commit and confirmation; source pulls remain async, cooperative and serialized. Synchronous disk I/O can block that event loop. Do not install this implementation into production collector/UI loops as-is; a separately reviewed worker/process and cancellation protocol is needed for that integration. There is no hard real-time or blocking-driver isolation claim.

## Filesystem, schema and limits

New store files use exclusive create0600 in an existing parent. Existing files must be regular, singly linked files with the adapter application marker/version in their main header **before SQLite opens them**. Raw-v1, unknown/empty files, symlinks, hardlinks and reserved `-wal/-shm/-journal` target names are refused. Existing companions block new creation; existing store companions must be regular singly linked files. Trusted local directories without hostile concurrent path replacement are required.

Initialize only the newly created file using DELETE journal/EXTRA synchronization, commit schema/descriptor and sync its parent, then use WAL/FULL. The marker is thereby in the main header before later WAL reads. SQLite manages its own journal/SHM lifecycle; the application performs no retention deletion, backup overwrite, source checkpoint, raw-v1 alteration or migration. Failed initialization artifacts are preserved, not automatically removed. If initialization was interrupted before the marker/schema completed, a later open may refuse the incomplete file; inspect it and choose a new file rather than overwriting it.

Schema validation enumerates all objects and permits only the four known tables and two known SQLite autoindexes. It does not use wildcard prefix filtering that could hide a trigger. Application ID is a format marker, not an authentication mechanism. Existing raw-v1 backup/restore and raw-history validation do **not** thereby support this new format; adapter-store-specific backup/restore verification is separate future work. Copying only a live WAL main file is not a consistent backup.

Limits retain1MiB fixture,64KiB response,64 accepted pages and1024 records, plus32MiB logical SQLite pages and0.2s busy timeout. SQL/validation work has a5-second/5-million-VM-step budget per read/write phase; postcommit confirmation starts a fresh budget. These are not an end-to-end operation deadline, and initialization, filesystem calls and native I/O are not hard-interruptible. Bounds can reject larger/slow valid inputs. No automatic pruning occurs at a limit. A valid partial prefix is preserved for resume, not declared a completed fixture.

## Verified and unverified

Linux synthetic tests include before/after actual COMMIT exceptions, inserted-data rollback, failed rollback, FULL via SQLite page limits, BUSY, targeted postcommit deletion/readback failure, unexpected trigger rejection, caller cancellation and actual child-process exit before/after COMMIT. Restart validates raw page/record/first receipt/cursor/EOF and identical retry adds no duplicate. A raw-v1 target test compares both the original file and its companion set before/after refusal. The existing two-page example is verified through two real CLI runs with unchanged source.

Exact counts/hashes are in [adapter-local-store-20261109.json](evidence/adapter-local-store-20261109.json). Windows/Windows services, physical devices, real/company data, production event-loop integration, quotas, actual host disk exhaustion, power loss, network filesystems, long-duration/high-volume operation, source ACK, raw-v1 delivery and remote CI remain unverified or unimplemented. Page-limit FULL and OS faults are synthetic/injected; the host disk is not filled and user data is not deleted.
