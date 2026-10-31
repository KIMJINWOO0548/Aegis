# Aegis equipment emulator

An independent Python 3.12+ project that generates synthetic raw equipment signals, including explicit degradation, data-quality and brief transient scenarios. It has no runtime dependencies, Aegis imports, physical equipment connections, model output, hidden fault metadata or control commands. Run one process per device or module using distinct source IDs and ports, for example `CMP-01.CMC-01` and `CMP-01.PMC-01`.

## Run independently

Open a terminal in this directory and select your Python 3.12+ executable. No installation is required. In an existing Aegis checkout, `..\..\.venv\Scripts\python.exe` can replace `python` in these commands; that interpreter is optional and no Aegis package is imported.

Set a private token of at least 32 characters in the terminal, then start the first instance:

```powershell
$env:AEGIS_EMULATOR_TOKEN = python -c "import secrets; print(secrets.token_urlsafe(32))"
python -m aegis_emulator --device-id EQ-SUPPLY-01 --port 8771 --seed 1 --interval-ms 1000
```

Use a second terminal for another independently configured instance:

```powershell
$env:AEGIS_EMULATOR_TOKEN = python -c "import secrets; print(secrets.token_urlsafe(32))"
python -m aegis_emulator --device-id EQ-SUPPLY-02 --port 8772 --seed 2 --interval-ms 1000
```

The consumer must receive the corresponding token through its own environment variable; do not put tokens in configuration files or command arguments. Tokens and sensor samples are absent from emulator logs. Each process prints its device ID and endpoint on successful startup. Stop it with Ctrl+C.

Arguments: `--device-id` is required and accepts 1-64 ASCII letters, digits, dots, underscores and hyphens, beginning with a letter or digit. `--port` defaults to 8771 and accepts 1-65535. `--seed` is any integer and defaults to 1. `--interval-ms` defaults to 1000 and accepts 1-60000. `--signal-count` accepts 8-512 and defaults to 8; it affects replay responses only. Aegis's default freshness limit assumes approximately one sample per second; increase that consumer limit if using a slower emulator interval.

`--scenario` accepts `NORMAL` (default), `DEG-02`, `DATA-QUALITY`, or `TRANSIENT`. Each scenario starts at elapsed simulation time zero when its process starts. Sampling intervals control sample cadence; they do not speed up the scenario clock.

```powershell
python -m aegis_emulator --device-id EQ-DEG-01 --port 8773 --scenario DEG-02
python -m aegis_emulator --device-id EQ-QUALITY-01 --port 8774 --scenario DATA-QUALITY
python -m aegis_emulator --device-id CMP-01.PMC-01 --port 8775 --interval-ms 10 --signal-count 256 --scenario TRANSIENT
```

`NORMAL` preserves the smoothly varying seeded values. `DEG-02` fixes target flow at 10 L/min and actual flow at 9.8 L/min for the first 60 seconds, then decreases actual flow by 0.08 L/min per second to a floor of 2 L/min. Its eight values remain finite and source quality remains `GOOD`.

`DATA-QUALITY` repeats this 60-second cycle for `actualFlowLpm`; all seven other signals retain their normal finite values:

| Elapsed seconds modulo 60 | Quality | Actual flow value |
| --- | --- | --- |
| 0 through 14 | GOOD | Normal seeded signal |
| 15 through 29 | MISSING | null |
| 30 through 44 | BAD | null |
| 45 through 59 | GOOD | -5 L/min |

The last phase deliberately sends a finite, physically implausible value with source quality `GOOD`. It is a valid wire payload, allowing Aegis behavior to be tested separately from malformed-data rejection. The scenario name, phase, fault cause and expected alarm timing are never included in the raw snapshot. This simulator does not create ML warnings.

`TRANSIENT` sends `actualFlowLpm=-5` with source quality `GOOD` during `[0.400, 0.500)` seconds of every second. Other times and the other seven profile signals follow `NORMAL`. Use a sampling interval below 100 ms; at 10 ms, ten samples per second preserve the abnormal segment even if a consumer reads the latest display only once per second. A 1000 ms source interval never samples that segment; replay does not invent intermediate measurements.

Missing/short tokens and invalid arguments exit with code 2. A port binding failure exits with code 1. A normal Ctrl+C exits with code 0. This project never opens a desktop window or installs a service.

## Protocol 1.0

`GET http://127.0.0.1:<port>/v1/snapshot` retains its exact protocol 1.0 envelope and eight-signal profile, including when `--signal-count` exceeds 8. The additional replay endpoint is documented below. Send exactly one `X-Aegis-Emulator-Token` header. The server binds only to `127.0.0.1`; external Host values, browser Origin headers and write/control requests are rejected. There are no redirects, CORS support, discovery endpoint or scenario controls.

```json
{
  "protocolVersion": "1.0",
  "source": "SIMULATOR",
  "deviceId": "EQ-SUPPLY-01",
  "runId": "9a2a13cf-a879-43a2-9d73-34b9c9514775",
  "sequence": 0,
  "observedAtUtc": "2026-10-18T00:00:00.000Z",
  "deviceStateVersion": 1,
  "modeId": "PRODUCTION",
  "signals": [
    {"signalKey": "setpointFlowLpm", "value": 10.0, "unit": "L/min", "quality": "GOOD"},
    {"signalKey": "actualFlowLpm", "value": 9.8, "unit": "L/min", "quality": "GOOD"},
    {"signalKey": "supplyPressureKpa", "value": 250.0, "unit": "kPa", "quality": "GOOD"},
    {"signalKey": "valveCommandPct", "value": 50.0, "unit": "%", "quality": "GOOD"},
    {"signalKey": "valveFeedbackPct", "value": 49.5, "unit": "%", "quality": "GOOD"},
    {"signalKey": "motorCurrentAmp", "value": 2.1, "unit": "A", "quality": "GOOD"},
    {"signalKey": "temperatureC", "value": 27.0, "unit": "degC", "quality": "GOOD"},
    {"signalKey": "cycleCount", "value": 100, "unit": "count", "quality": "GOOD"}
  ]
}
```

The example shows the shape; actual values vary smoothly and deterministically with seed and simulation time. The cycle count advances every ten simulated seconds. Root fields and the eight row fields are fixed. A new process creates a new UUID `runId`, starts a new timestamp origin and resets its sequence. Sequence numbers advance from monotonic elapsed time even when nobody polls. Multiple reads within one sampling interval return the same sample; skipped latest-only reads appear as sequence gaps. The replay endpoint reconstructs a bounded recent sequence window without keeping an unbounded in-memory queue.

`observedAtUtc` identifies the actual simulated sample time, not the HTTP request time. Aegis validates this envelope, constructs its v3.4 Observation rows and stamps `receivedAtUtc` itself. Source samples are `GOOD` except for the explicit missing/bad phases in `DATA-QUALITY`; the consumer is responsible for downgrading stale/unavailable data. Successful transport does not imply a trained model or a normal equipment evaluation.

Errors return a small JSON body with `code` and `message`: 400 invalid Host/query, 401 invalid token, 403 browser Origin, 404 unknown path, or 405 unsupported method. HEAD returns no body.

## Replay stream 1.0 with raw snapshot protocol 1.1

`GET /v1/batches?after=-1&limit=256[&runId=<canonical-UUID>]` uses the same authentication and loopback boundary. Both `after` and `limit` are required. `after` is -1 or a nonnegative integer; `limit` is 1-256. Missing, duplicate, unknown, blank or invalid parameters return 400.

The response contains exactly these fields:

```json
{
  "streamVersion": "1.0",
  "deviceId": "CMP-01.PMC-01",
  "runId": "9a2a13cf-a879-43a2-9d73-34b9c9514775",
  "oldestSequence": 0,
  "latestSequence": 100,
  "snapshots": []
}
```

Each replay snapshot uses the same root fields as the legacy snapshot, with `protocolVersion="1.1"` and 8-512 numeric signals according to `--signal-count`. The first eight signals retain the legacy profile and scenario behavior. Additional signals are named `aux0001`, `aux0002`, and so on, with deterministic finite values, unit `count` and source quality `GOOD`. These auxiliary tags are synthetic load inputs, not a CMP equipment tag registry or scheduler event model. The enclosing `streamVersion` remains `1.0`.

The server exposes the newest 4096 sequences from the current run. An unchanged or absent `runId` returns sequences strictly later than `after`, beginning at `oldestSequence` when older samples have expired. An `after` cursor equal to `latestSequence` returns an empty page; a cursor beyond it returns 400. A valid different `runId` identifies a consumer's old process run and restarts reading at the new run's oldest retained sequence, ignoring that old cursor.

Each page is limited to 256 snapshots and **900000 encoded JSON bytes**. With hundreds of signals, it can contain fewer snapshots than `limit` even when more are available. Continue from the last returned snapshot's sequence and the response's `runId`; compare the response bounds to detect retention overflow. At 100 Hz, 4096 sequences represent approximately 40.96 seconds; at 10 Hz, approximately 409.6 seconds. Neither the replay window nor a process restart provides durable storage. A consumer must archive raw snapshots and its cursor durably if it needs history beyond that window.

## Verify and package

The test suite uses only the standard library and includes real separate-process instances, one-instance outage, restart identity, CLI rejection and local HTTP boundary checks:

```powershell
python -m unittest discover -s tests -v
```

Optional installation creates an `aegis-emulator` command with the same arguments:

```powershell
python -m pip install .
aegis-emulator --device-id EQ-SUPPLY-01 --port 8771
```

Build a wheel using an interpreter with pip and setuptools installed:

```powershell
python -m pip wheel --no-deps --no-build-isolation --wheel-dir dist .
```

Setuptools is needed only for packaging; the running emulator and its tests use the Python standard library. Copying this project folder is also sufficient to run it independently.

`NOISY-FLOW` adds indexed, seeded small measurement noise and a physically valid 100ms flow dip with a pressure change every four seconds. Use 10–50ms acquisition to observe the pulse in replay; 1000ms sampling can miss it. Source quality stays GOOD, and no scenario or cause labels enter the wire. This is a synthetic process-evidence fixture, not a physical equipment model or proof that common sensor noise can be distinguished from real process changes.

This simulator provides latest snapshots and bounded deterministic replay with five CLI scenarios. Durable source history across restarts, interactive scenario commands, ML evaluation, Windows service installation and real-device protocol support are outside this project's scope. Synthetic behavior and local performance do not establish customer-equipment performance.
