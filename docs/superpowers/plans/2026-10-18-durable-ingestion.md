# Durable module ingestion execution plan

User scope: preserve brief synthetic signals from equipment with hundreds of numeric tags; separate module responsibilities and aggregate status. Work directly in the existing non-Git workspace; no publishing or remote project.

1. Preserve protocol1.0/eight-signal compatibility. Add byte-bounded protocol1.1 replay pages and high-rate/transient tests in the independent emulator project.
2. Add a SQLite raw archive, atomic batch+cursor+gap transactions, idempotent retry and dedicated disk worker. Test disk-full, lock, commit failure, schema mismatch and reopen.
3. Add replay collection, module hierarchy, generic numeric tags and committed-only publication. Keep1Hz flow advisory separate from raw sampling. Test stale history, timestamp/identity/sequence validation, invalid physical values, restart and overflow.
4. Add per-module worker processes owning transport/archive/evaluation; integration API caches bounded summaries. Test parent lifetime pipe, stale expiry, independent restart, failed worker/peer isolation and parent loss.
5. Update API/demo/config callers and retain raw archives after demo exit. Maintain the existing WPF flow projection; no claim of generic CMP tag UI.
6. Run Python, independent emulator, .NET and contract checks. Measure short single-process and isolated loads separately, including worker memory/CPU, archive latency, data age and gaps. Record exact evidence and limits.

Acceptance is bounded synthetic correctness and measured behavior. This does not establish hardware calibration, hard real-time scheduling, unbounded retention, arbitrary event semantics, service installation or enterprise historian capacity.
