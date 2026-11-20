# Independent adapter input: synthetic first step

Base `2094fbee4f0525daa89bddbcc1c0ca65557dcaee`. This is independent accessible history, not restoration of restricted preservation copies or unavailable `5075ccd`.

## Goal and constraints

Define a separate versioned device-adapter input contract with nullable, explicitly provided/not-provided original metadata; validate a built-in synthetic replay adapter and finite read retries. Preserve existing SIMULATOR snapshot1.0/raw1.1, collector/source configuration, SQLite schema/raw/cursor/gap, IPC/API/strict health consumers, UI and equipment behavior byte-for-byte. Use no company libraries, real device/data, dynamic plugin imports, executables or network URLs.

No legacy bridge, archive v2, durable admission ACK, device control, live hardware or new UI is included. Source observed time/sequence/run are never filled from host receipt, local tokens or generated counters. Existing raw models continue rejecting REAL and missing required metadata.

## Approaches

Changing the existing Source enum/raw models would also change archive keys and strict consumers; reject it for this first step. A new archive v2 plus IPC migration is a larger independent project. The implementer selects a separate typed envelope, in-memory admission boundary and allowlisted built-in synthetic adapter under the authorized small-first-step request. This does not claim that the user separately reviewed these implementation choices.

## Contracts and evidence

`adapter-input-1` pages contain adapter ID/version, adapter delivery stream UUID, declared origin, evidence level, acquisition method and access mode as separate axes. Origin is SYNTHETIC/DEVICE/UNKNOWN at the schema level; evidence is DECLARED_ONLY. This runner accepts only its pinned SYNTHETIC/SOURCE_BUFFER_REPLAY/READ_ONLY descriptor. These declarations do not authenticate physical equipment or promise hard realtime.

Each record has an explicit adapter delivery record ID, device ID, bounded signals and `sourceMetadata`: nullable observedAtUtc, sequence, runId plus corresponding PROVIDED/NOT_PROVIDED status. All keys are required; status/value contradictions fail. Original strings/numeric representation remain in bounded parsed-JSON evidence; model normalization is not substituted for source evidence. Host first receipt is stored separately in memory and excluded from retransmission fingerprints.

Opaque afterToken/nextToken and delivery stream identity are adapter delivery metadata, never original source sequence/run. Nonempty pages advance to a different nonnull token; empty pages retain the request token. Identical accepted page retransmission is idempotent without rewinding; changed content for an accepted request or record ID conflicts. Validate the entire page and capacity before changing any in-memory admission state. Duplicate record IDs within a page are rejected; exact overlap across pages preserves the first receipt. Token loops and stream/descriptor mismatches fail closed.

The admission cursor is proposed in-memory progress only. Every result declares NOT_PERSISTED and legacyArchiveIntegration DISABLED. No restart-durable deduplication, source ACK, checkpoint or transaction with the existing archive is claimed.

## Synthetic execution and bounds

The explicit `synthetic-adapter-fixture-1` file has pinned adapterId/adapterVersion/streamId and up to64 entries `{afterToken,response,delayMs?}`. Store immutable response bytes; built-in reads are stateless for the requested token. Synthetic delays support timeout/retry tests, not external actions. Only the built-in class executes; there is no arbitrary adapter callback/plugin loader. Python callers modifying trusted code are outside this contract's security boundary.

Schema version is exact; adapterVersion is exact `1.0`. Bounds:1MiB JSON fixture,64KiB response page,64 records/page,512 signals/record,1024 admitted records and64 accepted pages/session, opaque printable tokens1–128 chars,1–3 attempts,1–10000ms timeout. A read-only request retries the same token after cooperative timeout; failed validation, source error, timeout or exhausted capacity never advances admission. asyncio cancellation is cooperative and not blocking-driver isolation or a hard wall-clock guarantee. Native/blocking hardware adapters would need a separate process/lifecycle design before support.

CLI prints only a bounded synthetic validation summary after the requested run; raw values, device IDs, opaque tokens and paths are excluded. No-data returns NO_NEW_DATA; finite fixture completion is END_OF_SYNTHETIC_INPUT, never persisted completion. Errors emit a fixed-code JSON stderr/exit2 with no partial stdout. Input files are bounded regular files opened nonblocking/no-follow. No automatic filesystem output, deletion or overwrite is implemented.

## Validation and later migration

Test schema/version/origin mismatches, metadata presence contradictions, exact large integers/null/quality/timezone preservation, changed retry conflicts, whole-page atomic admission, finite timeout/cancellation, empty/no-data, limits and invalid file types. Prove existing RawArchive payload/first receipt/cursor/gap unchanged and legacy contracts remain strict. Run the full suite, independent emulator/contracts checks and fresh whole-change review; inspect privacy/noreply then normal separate-branch push and remote SHA.

Later work must design a side-by-side archive v2 and rollback, explicit durable admission/ack transaction, IPC/API version negotiation and independent real-device adapters with authenticated identity, secret isolation and process supervision. UI requires separate authorization. Windows, devices, operations, long load and CI remain unverified by this first step.
