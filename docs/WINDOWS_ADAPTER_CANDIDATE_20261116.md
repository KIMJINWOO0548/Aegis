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

## Exact-wire schema v2 follow-up

New candidates now use `windows-adapter-snapshot-2`. Fixture and each first-admitted
page include base64-encoded original bytes plus SHA256. Replay checks each wire
hash, validates the original bytes, and checks canonical producer JSON equality
before deriving records/cursor/receipt. Whitespace/key ordering and terminal newlines
survive reopen and backup. Identical semantic retransmission keeps the original
first receipt and original first wire rather than replacing evidence.
`fixture_wire` and `wire_pages` expose verified bytes. Supplying a dictionary fixture
creates canonical encoded input; it cannot invent original wire bytes not supplied.

Schema v1 remains readable and copyable, but is now explicitly read-only for page
commits. Exact-wire properties report WIRE_EVIDENCE_UNAVAILABLE for v1. There is no
automatic migration falsely labeling re-serialized JSON as original wire. Full
snapshot transitions require identical metadata including fixture wire and version.
The earlier canonical-only limitation above describes v1; v2 closes that evidence
gap, not the other production integration or publication gates.

Python 3.12.10 candidate suites: **34/34 PASS**, including 13 adapter tests, 7 backup
tests and 14 journal tests. New tests check exact lexical bytes across backup,
retransmission first-evidence preservation, wire hash/canonical mismatch rejection,
and nonmutating legacy recovery. Real child processes exit with os._exit immediately
before snapshot append and after confirmed append but before caller return. Reopen
observes respectively the old cursor or complete new cursor+wire, and retry admits
once or reports retransmission. The worker exits itself, avoiding the Windows venv
launcher kill ambiguity. This is process interruption, NOT physical power loss.

Remaining: actual production AdapterStore SQL/cursor migration and controller
integration, durable namespace publication, hostile parent protection, device cache
power-loss behavior, and operational throughput. No production guard was relaxed.

Final hardening compares fixture canonical JSON bytes, not Python dict equality
(which equates False and 0). A type-confusion regression was added. Final focused
result is **35/35 PASS** (adapter 14 + backup 7 + journal 14), superseding the prior
34-test count. Tests do not claim physical power-loss durability.

Independent exact-wire review: **14/14 PASS**, no scoped blocker. Its first runs
had 12 PASS / 2 child-import failures: pytest's pythonpath configuration does not
propagate to child interpreters. Repeating with process-local PYTHONPATH=repo/src
passed. The tests now explicitly provide that source path only to owned children;
the adapter interruption test captures stderr and includes it on unexpected exit.
No persistent environment, user setting or security control was changed. With the
parent PYTHONPATH removed, the final complete candidate suites passed **35/35**
on Python 3.12.10 (4.03s). Runtime dependencies must be installed in the selected
interpreter; run from repository root with `python -m pytest` and the three
`test_windows_{adapter,backup,journal}_candidate.py` files. Production support,
physical power-loss safety and namespace durability remain unverified/disabled.
