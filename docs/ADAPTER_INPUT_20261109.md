# Independent adapter input: built-in synthetic replay

This independent backend step starts at `2094fbee4f0525daa89bddbcc1c0ca65557dcaee`. It does not restore inaccessible preservation copies or replace unavailable `5075ccd`. Existing SIMULATOR snapshot1.0/raw1.1, source configuration, collector, archive/schema/cursor/gap, IPC/API/health contracts and UI remain unchanged.

## Run and scope

From an installed checkout or with `PYTHONPATH=src`:

```sh
python -m aegis.adapter_input --fixture examples/adapter-input.synthetic.json --timeout-ms 100 --max-attempts 2 --max-pages 64
```

The [small synthetic example](../examples/adapter-input.synthetic.json) mixes explicitly absent and provided original metadata, a negative numeric reading, a large exactly representable integer and a missing/null signal. The CLI reads only the selected bounded regular fixture file and executes the built-in synthetic adapter. There is no URL, command, device control, company library, dynamic plugin import, legacy bridge or new persistent sink. No tool-managed output, deletion or overwrite is performed; stdout is caller-controlled. Do not redirect output into an existing source or backup file.

Every report declares `persistenceStatus: NOT_PERSISTED`, `legacyArchiveIntegration: DISABLED`, `realEquipmentSupported: false` and `hardRealtimeGuaranteed: false`. `END_OF_SYNTHETIC_INPUT` means the finite fixture says it ended, not that anything was durably stored. Empty nonterminal input is `NO_NEW_DATA`; it is distinct from timeout or failure. A diagnosis summary is printed only on a successful run; input/error/read-limit failures return exit2 with fixed-code JSON stderr and no partial stdout or raw exception text. Normal reports omit device/adapter identities, signal keys/values, delivery tokens and filesystem paths.

## Separate input contract

`adapter-input-1` is a new envelope. It is **not** a widened existing RawSnapshot. Adapter version1.0 is exact; unknown versions/fields fail closed. Its axes are separate:

| Field | Meaning and current execution |
| --- | --- |
| declaredOrigin | Schema can represent SYNTHETIC, DEVICE or UNKNOWN declarations; this runner admits only SYNTHETIC |
| originEvidence | DECLARED_ONLY; no hardware identity, authenticity or clock verification |
| acquisitionMethod | Schema can represent SOURCE_BUFFER_REPLAY or SNAPSHOT_READ; this runner admits only replay |
| sourceAccessMode | READ_ONLY; no command/write capability |
| adapterId / adapterVersion | Pinned producer binding, not source equipment authentication |
| streamId | Adapter delivery namespace, not original source runId |
| afterToken / nextToken | Opaque adapter delivery progress, never original source sequence |

All fixture responses, including otherwise unreachable ones, must match their pinned synthetic descriptor. The fixture must form one reachable finite chain from the initial null token, ending in an end flag or a no-data response; hidden trailing pages, missing links and loops are rejected. A snapshot or DEVICE/UNKNOWN declaration being representable by the schema does not enable those adapters, transport modes or real equipment ingestion.

Each record has explicit delivery `recordId`, deviceId,1–512 unique signals and required source metadata fields:

| Value field | Corresponding status |
| --- | --- |
| sourceMetadata.observedAtUtc | timestampStatus = PROVIDED or NOT_PROVIDED |
| sourceMetadata.sequence | sequenceStatus = PROVIDED or NOT_PROVIDED |
| sourceMetadata.runId | runIdStatus = PROVIDED or NOT_PROVIDED |

Every value/status key is required. A null value must be NOT_PROVIDED; a nonnull value must be PROVIDED. Missing keys, contradictions, invalid/naive timestamps, bool/negative/out-of-range sequences and invalid UUIDs fail. PROVIDED states only that the producer supplied a well-formed value; it does not certify clock accuracy, freshness, ordering or unique physical events. Generic replay does not infer source chronology from delivery order.

Host complete-response receipt is sampled immediately after the built-in read returns, before response validation, and retained separately as `firstReceivedAtUtc`. It never fills an original timestamp, sequence or run. A replay stream UUID, local delivery ID, receipt time and opaque token are not substituted for missing source metadata. Source run/sequence can be independently absent. Future or old well-formed source timestamps remain producer declarations; later analysis would need an explicit clock/freshness policy.

Signals use the existing finite binary64 numeric/quality/unit validation. GOOD requires a nonnull value; null/bad/missing quality remains intact. Integers that cannot be represented exactly as binary64 are rejected before conversion. This contract does not offer arbitrary-precision decimal arithmetic or legacy flow-analysis conversion.

## Evidence and retransmission

`validate_page(bytes)` produces a frozen typed model, canonical parsed JSON and exact supplied page wire bytes. Admission retains the original parsed record JSON rather than replacing it with normalized model dumps: explicit null/status, original timezone string and source integer representation remain available. The typed timestamp may normalize to UTC for validation; the separate evidence preserves the producer string.

The synthetic adapter is itself a producer: it parses the caller fixture and generates canonical response JSON with binary64 signals. Those response bytes are not the original fixture's numeric/whitespace lexemes. The original bounded fixture bytes are retained separately as `fixture_wire`, their SHA256 is included in the summary, and `responseEncoding` explicitly states `CANONICAL_PARSED_JSON_FLOAT64_SIGNALS`. For example, a long decimal fixture literal may round to binary64 in the generated response; retaining the fixture does not make that value arbitrary precision. Direct page admission retains exactly the bytes actually supplied to it. These hashes are unsigned evidence references, not proof of external provenance.

Admission fingerprints canonical parsed JSON, so object key order and whitespace do not create a conflict. Repeating an accepted request token with identical canonical content returns RETRANSMISSION, adds no records and never rewinds the current proposal cursor. Changed content under an accepted page token or recordId is REPLAY_CONFLICT. Exact record overlap between later pages preserves the first host receipt; a changed overlapping record rejects the **whole new page**, including otherwise valid new records.

Nonempty pages require a different nonnull next token. Empty pages retain the request token. Empty terminal pages retain their fingerprint and exact response wire so they also retransmit idempotently. Transient no-data pages are not cached as terminal truth; later valid data can use the same request token. Delivery-token loops, stale unknown cursors, wrong stream/descriptor, capacity overflow and all page validation failures leave the prior admission state intact. A single next-state publication happens only after the whole page is validated, with no awaits or write SQL in that publication.

The state is event-loop-local and memory-only. Public evidence access returns copies. It is not thread-safe across independent threads and is not a durable ACK, exactly-once source guarantee, write-ahead cursor or restart checkpoint. CLI failure can discard valid earlier in-memory proposals; it cannot roll back or alter an existing archive because this path is not connected to one.

## Timeouts and execution boundary

Only the exact built-in `SyntheticReplayAdapter` class executes through `AdapterSession`. Arbitrary adapter objects/subclasses are rejected; there is no driver registry or execution from a file. Its immutable fixture responses are selected by an explicit bounded read request. Cooperative delays are synthetic test behavior only.

Read timeout is1–10000ms and attempts are1–3. Timeout retries reuse the same immutable request token. Requests are serialized per session, and source/read/schema failures never advance proposal progress. External cancellation propagates; supported synthetic reads cooperate with cancellation and leave no background read task. Host receipt is sampled only after a read actually returns.

[asyncio timeout uses cooperative cancellation](https://docs.python.org/3.12/library/asyncio-task.html#asyncio.timeout). These limits do not force-stop blocking native/company drivers, synchronous Python code, CPU stalls or OS I/O. No hard wall-clock/realtime, process isolation or untrusted-code sandbox is claimed. Python callers who modify trusted code or inject a host clock are responsible for that code; the type allowlist is an execution API boundary, not protection against code already running in the process. Real/blocking adapters require a separately reviewed process supervision and secret/permission design.

## Bounds and next integration

One regular no-follow/nonblocking fixture is at most1MiB, with1–64 unique linked pages and at most1024 total fixture records. Response wire is at most64KiB, with0–64 records and1–512 signals per record. Delivery tokens are1–128 printable nonspace ASCII characters; source sequences are integers0–2^63−1. In-memory admission retains at most1024 unique records and64 accepted pages, including a terminal empty page. CLI reads at most64 pages unless a smaller explicit limit is selected; output is bounded64KiB. Size/input/type limits produce errors rather than partial successful summaries. OS reads are not hard real-time cancellable.

This first step deliberately preserves the current `source=SIMULATOR` contract. To connect independent real adapters later, design an archive v2 side by side, original metadata/provenance storage, immutable raw evidence and a durable admission/cursor transaction; verify interrupted migration and rollback before switching any writer. Then negotiate IPC/API and strict consumers. Do not relabel DEVICE input as SIMULATOR or populate missing original metadata merely to fit archive v1. UI and equipment integration require separate reviewed work.

Linux synthetic verification, regressions and exact hashes are in [adapter-input-20261109.json](evidence/adapter-input-20261109.json). Windows/Windows services, physical devices, real field/company data, production operation, durable restart behavior, long-duration/high-volume performance and remote CI have not been validated by this step. The CLI file-loading path rejects unsupported platforms. All existing history and restricted preservation files remain untouched.
