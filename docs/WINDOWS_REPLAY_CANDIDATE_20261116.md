# Restart-aware synthetic replay candidate

This is an isolated adapter/controller presentation contract, not production UI,
Windows service or storage enablement. Production platform guards are unchanged.

Previously AdapterSession restarted with an empty in-memory admission cursor;
WindowsAdapterCandidate could reconstruct the persisted cursor but offered no
restart/freshness presentation contract. WindowsReplaySessionCandidate now binds
exact fixture bytes to the candidate store, requests the committed cursor, and
commits a validated response before exposing a changed state. An ended stream
avoids another source read. Timeout/cancellation do not move cursor or receipts.

`state_view()` reports EMPTY, RESTORED_LAST_KNOWN or SESSION_SYNTHETIC. Opening
existing committed pages always starts as RESTORED_LAST_KNOWN. Only a newly
confirmed page commit on that store instance changes the session label; semantic
retransmission never makes restored evidence look new. `isLive` is always false,
including newly replayed synthetic data. The timestamp is explicitly a host
receipt, not a source observation and not the current wall-clock time. Records
retain original nullable source timestamps and first-received receipts. An
ambiguous storage result fences reads rather than exposing a stale success view.

This API gives a future UI an explicit non-live contract. No claim is made that
an existing production screen consumes these fields. SourceAcknowledgement remains
NOT_SENT, and synthetic data is never represented as actual equipment telemetry.
A single session serializes its reads. Sharing one store across unrelated sessions
is not a supported controller topology; store cursor validation still rejects a
stale response. There is no automatic retry of uncertain writes.

Python 3.12.10: all four candidate suites **39/39 PASS**. The four new tests cover
restore label/timestamp and actual resumed poll at the committed cursor, end-state
no-repeat read, retransmission preserving restored labeling, exact fixture binding
rejection, timeout and cancellation preserving the last known state. Existing
process interruption and exact-wire tests remain included. This run does not
establish high-rate performance, hostile-directory safety, physical power-loss
persistence or durable namespace publication. Those production gates remain open.

Additional ambiguous-flush view regression confirms RESULT_UNKNOWN fences the
presentation API and explicit reopen labels recovered evidence RESTORED_LAST_KNOWN,
never live. Final focused total: **40/40 PASS**, including five new replay tests.

Independent review reran the five replay tests on Python 3.12: **5/5 PASS**,
with no blocker inside this isolated scope. `timeout_ms` bounds only adapter.read;
synchronous validation/commit is outside that read timeout and cannot be interrupted
by asyncio cancellation midway. This API does not promise an end-to-end deadline.

## Bounded read retry follow-up

`max_attempts` accepts only integers 1..3 and defaults to 1, preserving existing
single-read behavior. Only adapter read TimeoutError is retried. Each attempt uses
the same committed cursor; validation and persistence happen once after a successful
read. A commit TimeoutError is not misclassified as an adapter timeout, and an
ambiguous flush still fences the store with RESULT_UNKNOWN. External cancellation
is immediately propagated without consuming further attempts. No source ACK is sent.

The per-attempt timeout bounds only cooperative async adapter reads, not synchronous
validation or storage. There is no unbounded loop, background polling, equipment
reconnection or Windows service integration. The production guards are unchanged.

Seven new cases failed before implementing the new policy (missing max_attempts).
The final four Windows candidate suites passed 49/49 on Python 3.12.10, including
nine retry-policy/timeout/cancel/uncertain-write cases. The first sandboxed run had
46 passes and one hardlink fixture PermissionError (WinError 5); rerunning through
the normal approved execution path allowed the hardlink safety test to run and pass.
Full application regression is tracked separately and is not implied by this count.

Independent review reran all 14 replay tests successfully and separately checked
non-timeout AdapterError and response cursor mismatch: one read, zero commits,
original exception preserved. No scoped blocker was found. This remains a
synthetic-only candidate; it does not establish real equipment or service recovery.

Full application pytest was also executed on Windows: **298 failed, 964 passed,
11 skipped, 30 errors**, 2 warnings, 389.05 seconds. This is NOT a green application
regression. The run started before the final two cancellation/flush tests were added;
those are covered by the later 49/49 focused run and independent 14/14 replay run.
Representative failures were rerun individually: stored-records empty-adapter test
fails at AdapterStore with PLATFORM_UNVERIFIED; storage-policy unconfigured test
fails at diagnose_storage with PLATFORM_UNVERIFIED. The journal hardlink failure in
the sandboxed full run is the WinError 5 fixture restriction described above.
The complete 298-failure/30-error set has NOT been individually attributed, and no
claim is made that every failure is environmental or pre-existing. The full output
was in a per-command temporary directory removed when the session ended; its final
summary was captured, but detailed tracebacks were not retained. Test-generated
tracked evidence JSON files were backed up outside the repository and restored to
HEAD; they are not included in this feature commit. Production Windows guards remain
closed rather than being weakened to turn platform-gated tests green.
