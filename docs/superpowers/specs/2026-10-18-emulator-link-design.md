# Separate emulator project and measured local connection

User priority: performance and actual emulator connection before further visual design. User explicitly requested a separate emulator project for multiple emulators and selected local HTTP as the first protocol. This supersedes the prior D0a-only implementation boundary for this connection slice; it does not authorize physical equipment, ML claims, persistent ACK, or service installation.

## Scope and layout

`emulators/Aegis.Emulator/` is an independently runnable/packageable Python project with no import of Aegis internals. Multiple instances use distinct device IDs and loopback ports. Aegis reads configured sources concurrently, keeps bounded latest state, exposes it through its local API, and lets WPF select a device and display live signal values and connection state. Existing fixed fixtures remain an explicit default mode for regression compatibility; emulator mode never silently substitutes them.

## Emulator wire protocol 1.0

HTTP GET `http://127.0.0.1:<port>/v1/snapshot`; header `X-Aegis-Emulator-Token` from `AEGIS_EMULATOR_TOKEN` (minimum 32 characters). Bind loopback only. Redirects, external hosts, write/control routes and browser Origin requests are not supported.

JSON root has exactly: `protocolVersion` = `1.0`, `source` = `SIMULATOR`, `deviceId`, `runId` (UUID per emulator process), `sequence` (nonnegative integer, advances with simulation time), `observedAtUtc` (UTC ISO timestamp), `deviceStateVersion` (nonnegative integer), `modeId` = `PRODUCTION`, `signals` (exactly eight rows). Each signal row has `signalKey`, finite numeric or null `value`, `unit`, `quality` (v3.4 GOOD/STALE/BAD/MISSING; GOOD requires a value). Units: setpointFlowLpm/actualFlowLpm `L/min`, supplyPressureKpa `kPa`, valveCommandPct/valveFeedbackPct `%`, motorCurrentAmp `A`, temperatureC `degC`, cycleCount `count`. No hidden truth, fault cause, expected alarm time or synthetic analysis results are sent.

Aegis validates the full envelope, expected device identity, run/sequence and size/time bounds before replacing state. It creates v3.4 Observation rows and stamps `receivedAtUtc` at the collector. Duplicate snapshots are not counted as new samples; sequence gaps are counted. New runId permits sequence reset after restart.

## Collector and API

Explicit `--emulators <config.json>` starts emulator mode. Configuration holds at most 32 unique IDs/origins with `deviceId`, `baseUrl`, `tokenEnvironment`; it contains no literal tokens. Defaults: 1 second poll interval, 500 ms timeout, 3 seconds stale threshold. Each source has one sequential async poll loop; slow/unavailable sources cannot stall others. HTTP pool, bounded response (64 KiB) and latest-only data prevent unbounded queues/history. There is no retry storm.

Existing read routes return configured devices and validated latest observations. No sample means an empty observation array. Disconnected/stale sources cannot report GOOD; health remains NO_MODEL and INSUFFICIENT_DATA when current, UNAVAILABLE when unavailable. Runtime trends/timeline/ACK remain unavailable instead of returning unrelated fixtures.

Read-only `/v1/connections` returns one row per configured source: `deviceId`, `state` (CONNECTING/CONNECTED/DISCONNECTED/STALE/REJECTED), nullable `lastReceivedAtUtc`, nullable `lastSequence`, nonnegative `acceptedBatches`, `rejectedBatches`, `droppedBatches`, nullable `pollLatencyMs`, nullable `reasonCode`. API status identifies emulator mode. No token or upstream response is exposed in diagnostics.

## Desktop

WPF reads `AEGIS_API_TOKEN`, optional `AEGIS_API_URL` (default http://127.0.0.1:8765). One cancellable async poll at a time, with a bounded timeout, updates device selection, connection/quality/time and an eight-row signal list. No credentials in UI/logs. Missing token or unreachable API produces an explicit disconnected/unchecked state. NO_MODEL remains visible regardless of successful transport. No extra visual redesign.

## Verification and performance

Tests cover independent emulator instances, strict validation, unavailable/slow source isolation, duplicate/restart/gap handling, stale quality, no fixture fallback, auth and shutdown. Run real separate processes and stop/restart an emulator; verify Aegis recovers while another source keeps updating. Measure elapsed time, accepted/rejected batches, poll p50/p95, API latency, and process CPU/memory at documented source counts; report actual numbers and environment, never customer-device performance. Latest-only retention and missing durable Trend/ML are explicit limits.
