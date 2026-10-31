# Durable replay ingestion — first scalable slice

The user requires large equipment with independently running CMC/PMC/scheduler modules, near-real-time acquisition and preservation of brief changes for analysis. Counts and rates are private. Use synthetic loads; do not infer customer capacity or request production details.

## Decision and limits

Keep independent source processes and the existing authenticated snapshot protocol unchanged. Add an opt-in replay transport and a local raw archive, using the installed Python standard library. This is the first reliable ingestion slice, not a complete CMP signal/event model or a hard real-time system.

Logical source IDs may identify modules such as CMP-01.CMC-01 and CMP-01.PMC-01. Optional equipmentId/moduleId configuration records the hierarchy separately. Each source has its own cursor, poll loop and failure state. Opt-in isolated execution gives each source a Python process owning its collector, archive and development advisory. The integration API keeps only bounded latest summaries and warnings; raw batches do not cross its IPC boundary. Existing single-process execution remains for comparison and compatibility.

## Replay wire boundary

GET /v1/batches?after=-1&limit=256[&runId=UUID] returns exactly streamVersion=1.0, deviceId, runId, oldestSequence, latestSequence, snapshots. Replay uses raw protocol1.1 with1..512 distinct numeric signals, bounded identifiers/units, finite values or null, and quality. Existing /v1/snapshot remains exact protocol1.0/eight signals. Maximum256 snapshots/page,900000 encoded bytes/page and4096 replayable sequences/source; collector body capped1MiB. Same-run after cursor means strictly later sequences. A different run ID starts from the oldest retained sequence. Invalid query parameters fail. Explicit oldest/latest sequence bounds expose retention overflow; no invented intermediate physical readings. The synthetic generator can deterministically reconstruct its retained sequences from its monotonic clock and seed. CLI signal-count8..512 supplies hundreds of deterministic numeric tags without changing the legacy endpoint.

An additional synthetic transient scenario produces a short abnormal flow segment every second, permitting tests where source sampling is faster than UI refresh. It carries no hidden scenario/truth label on the wire.

## Durable archive and ordering

Replay requires an archive path. SQLite WAL with synchronous FULL, one dedicated writer thread/archive, bounded work (one pending transaction/source, max32 sources). Isolated workers each own a source-ID named database under an explicit archive directory, avoiding SQLite's single-writer contention between modules. Persist full raw snapshot JSON, original timestamp/quality, collector receipt UTC, hierarchy, and the committed per-source run/sequence cursor in one transaction. Unique source/run/sequence IDs make retries idempotent; changed content for the same identity is rejected. Record exact missing sequence intervals when replay retention is exceeded. No raw samples in operational INFO JSONL.

## Worker responsibilities and lifecycle

Each module worker owns source transport, raw commit/cursor and its optional flow analysis. The integration process serves the existing API and supervises workers; WPF remains its own process. Do not create a process per individual tag. CPU-heavy decoding can run on different cores, but aggregate disk bandwidth, process memory and IPC still need measurement.

Use stdlib Popen plus a bounded newline JSON summary over stdout (max1MiB). Worker stdin receives one private startup configuration, then remains open as a lifetime pipe: EOF requests worker shutdown even if the integration API was killed. Dedicated reader threads keep Windows SelectorEventLoop compatible; no asyncio subprocess calls. Never pass tokens in command arguments or stdout. Raw archive files contain synthetic samples and do not get deleted when the demo exits.

Restart only a failed worker with a bounded backoff. Reject malformed/oversized summary frames; expire cached GOOD to STALE/UNAVAILABLE after3seconds or process exit. API reads do not wait on module disk or analysis work. Module DB failures preserve that module's cursor and do not hold another module's writer. Supervisor graceful stop closes owned pipes, waits then terminates only owned workers if required. Worker death/restart, supervisor abrupt death and stale summary behavior require tests. This remains a foreground process tree, not an installed autonomous Windows service.

Only advance the cursor and publish/analyze after the archive commit succeeds. On storage error retain the cursor and old published values, mark the source unavailable, and retry from the same cursor. Blocking disk work must not run on the API event loop. API restart resumes from the committed cursor. No silent deletion/rotation: retention/export is unimplemented and disk pressure is surfaced as failure. Do not claim power-loss durability independent of filesystem/hardware behavior.

## Projection and freshness

Raw sampling and UI/advisory rates are separate. Archive every available source sequence; WPF reads the latest flow projection. Evaluate the existing flow profile at1Hz while inspecting quality/range failures on all replay samples. Raw-only modules disable this profile. The fixed8-signal profile remains a legacy development profile; protocol1.1 accepts hundreds of numeric CMP tags without expanding its enums. Text/state/scheduler events and a semantic tag registry remain a separate versioned schema rather than pretending they are flow signals.

Replay frames may be older than the latest-only6second bound, but never future-dated beyond the existing5second tolerance. Validate sequence ordering, device/run identity and bounded envelope before any commit. Only a recent observed timestamp can produce CONNECTED/GOOD; receipt of an old replay page is not fresh data. Missing/invalid input resets advisory warmup; recovery must use valid fresh input. Replay archive preserves invalid physical values with source quality; diagnostics may classify them BAD without rewriting the raw evidence.

## Verification

Test exact legacy compatibility; bounded replay paging and invalid queries; short transient captured despite slower polling; archive retry idempotency and conflicting duplicate rejection; atomic cursor rollback on disk failure; reconnect/API restart catch-up; replay overflow with explicit gap; a stalled source and a blocked archive do not block API reads. Use synthetic10/100Hz sources and report capture count, gaps, commit latency and latency distributions. Existing1Hz/evaluation-disabled performance evidence is not capacity evidence for this new path.

SQLite design basis: https://www.sqlite.org/wal.html and https://www.sqlite.org/pragma.html#pragma_synchronous (consulted2026-10-18). WAL permits concurrent readers but one writer; long readers/checkpoints and FULL sync latency require measurement.
