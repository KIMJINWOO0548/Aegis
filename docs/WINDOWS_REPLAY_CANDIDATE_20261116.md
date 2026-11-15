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
