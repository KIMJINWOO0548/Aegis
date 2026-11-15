# Windows adapter snapshot recovery candidate

Experimental only. Production AdapterStore, archive backup and platform guards
are unchanged. This is not a SQLite migration or a replacement public API.

`WindowsAdapterCandidate` reuses SyntheticReplayAdapter, page validation and
AdapterAdmission rather than inventing cursor rules. Each committed frame contains
a versioned complete fixture/page/UTC receipt snapshot. Reopening reconstructs
records and cursor from the same validated pages. Success means journal local
flush/readback, never source ACK. Identical retransmissions do not append. Changed
fixture pages, invalid cursors, schemas and non-prefix snapshot transitions fail.
An ambiguous append fences both reads and writes until a new open resolves it.

`backup_to_new` holds the source object lock, validates its snapshot, then writes
one complete snapshot frame into an exclusively created destination. It never
replaces a path. A header-only or torn frame is not a usable adapter snapshot.
Complete bytes after an ambiguous flush may be accepted on explicit reopen.
Failed destinations are retained as evidence; no deletion, truncation or repair.
The backup can be opened as a separate candidate and resumed without modifying
the source. Return status explicitly says durablePublication NOT_VERIFIED.
This is recovery-copy creation, NOT an atomic directory publication guarantee.

## Validation

Initial existing Python 3.13 verification environment: snapshot suite 8/8 and
native journal suite 14/14, combined 22/22 PASS. Tests cover cursor+records+receipt
reopen, backup/resume with source unchanged, retransmission/conflict, flush failure
fencing and reopen, no overwrite, header-only/torn backup rejection, invalid
schema/non-prefix transition, and ambiguous backup flush recovery.

## Remaining gates

Trusted local directory and underlying journal limitations still apply. Snapshot
payload is limited to 1 MiB, journal to 16 MiB; full replay/write amplification is
unsuitable for high-rate collection. Receipt JSON keeps canonical producer values,
not exact original lexical wire bytes, so evidence parity with AdapterStore is
NOT achieved. No SQL schema migration, existing production cursor import, source
acknowledgement, durable namespace publication, power-loss proof, independent
rollback anchor, untrusted-parent defense or production enablement is provided.
Backups are mutable candidates after reopening, not signed immutable artifacts.
Real power loss, disk exhaustion, every crash boundary and hostile directories
remain NOT_RUN. Do not infer production support from these tests.


Supported Python 3.12.10 rerun: **22/22 focused tests PASS**. The adjacent
adapter_input + windows_fixture + both candidate suites produced **95 PASS,
1 FAIL, 1 SKIP**. Failure occurred constructing asyncio's local socketpair
(WinError 10014), before the cancellation test body; symlink permission test was
skipped. This is not a green full regression. An earlier Python 3.13 run also had
an asyncio socketpair failure. Neither environment failure was hidden or bypassed.
Independent review reran the snapshot suite on Python 3.12: **8/8 PASS**.
Review found no blocker within the experimental scope and confirmed snapshot
prevalidation, append fencing and backup/source separation. It does not approve
production durable storage enablement.
