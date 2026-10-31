# D0a execution ledger

Plan: docs/superpowers/plans/2026-10-16-d0a.md

- Initial state: design documents plus one validation script, no Git repository or application.
- Baseline: bundled Python 3.12.14 cannot run validate_v34.py because PyYAML is absent. System `python` resolves to Windows Store alias. .NET SDK 10.0.401 is installed.
- Ruling: Work directly in the user-designated folder; Git worktree steps are inapplicable because this is not a repository. No remote publishing or commits.
- Ruling: User authorized review, corrections, and starting development. Use the existing D0a specification as the bounded first deliverable; preserve later Gate boundaries.
- Ruling: Preserve original DOCX/PDF; document stale references and contract corrections in a separate ADR.

## 2026-10-18 local verification

- Contracts generated; contract/OpenAPI/example/reference SQL checks pass.
- Python full suite: 102 passed, 0 failed/skipped, 1 upstream deprecation warning (26.03s).
- Release build including WPF: 0 warnings/errors (12.36s). xUnit: 32 passed, 0 failed/skipped. Locked restore passes.
- Independent cross-review found root C# collection serialization bypass; regression 3 failed/1 passed, fixed, full 32 green.
- .NET Worker empty implementation: 11 red tests, then real Generic Host/options/JSONL implementation and green suite.
- Python/.NET heartbeat separate processes: common traceId, 3 completions, 2 validated lifecycle events, exit 0.
- Remote CI, service installation and 150% DPI checks remain unverified; subsequent WPF visual checks are recorded below.
- User requested external UI design tooling. Product Design and Figma installed/connection confirmed by user; begin Product Design visual ideation before editing WPF styling.
- Product Design ideate complete: three independent built-in Image Gen concepts displayed once each and saved to docs/design-concepts/aegis-ui-{1,2,3}.png in displayed order. No WPF styling implementation before the user's visual selection.

## 2026-10-18 selected first concept

- User selected the first displayed concept. Implemented the light native WPF equipment screen, generated transparent pump artwork, official Feather icons and bundled Pretendard fonts. Font/icon licenses are copied to the output.
- Owner-modal Mock guide, named caption controls, visible keyboard focus, and vertical scrolling at 640×480. No API/device evaluation added.
- Independent review found low primary-text contrast; corrected base blue to #2679BC (4.62:1), with darker hover and pressed colors.
- Font-resolution probe reproduced the invalid absolute font reference (all weights unresolved); relative URI against the compiled dictionary resolves all three embedded weights. Software rendering experiment did not improve the symptom and was reverted.
- Final Release solution build: 0 warnings/errors, exit 0. xUnit: 32 passed, 0 failed/skipped. Native screenshot comparisons and residual P3 differences are recorded in project-root design-qa.md. Python was not rerun for this presentation-only change.

## 2026-10-18~19 emulator and durable ingestion scope

- User moved priority from design to performance and independent emulator connection, then explicitly requested faulty values, hundreds of tags, brief raw data preservation and responsibility processes with integration.
- Preserved protocol1.0/eight-signal fixtures. Added a dependency-free emulator project, authenticated loopback transport, protocol1.1 numeric replay, hierarchical configuration and per-module raw archives.
- Raw SQLite WAL/FULL commit and cursor are atomic. Raw evidence precedes advisory/UI projection. Fixed total-body deadline, stale history interpretation, retrospective live warning/recovery, timestamp/version transition mixing and retired-run cursor publication.
- Added module collector/archive/advisory processes; integration API caches bounded summaries. Independent restart, EOF shutdown, freshness and per-module writer isolation have direct tests. No installed service, remote repository, real device access or control command.
- Current implementation and evidence: docs/DURABLE_INGESTION_REPORT.md. Historical D0a/static UI counts above remain scoped to those earlier checks.
- User installed and explicitly requested `codex-auto-model-router`. Applied its default balanced `decide` path: observed `gpt-6-luna/high` leaf implemented WorkerFrame metadata validation, coordinator reviewed producer compatibility and completed eight-signal issue/oversized-number regressions. A separate `gpt-6.1-sol/medium` leaf performs the final read-only process review. The coordinator model is unchanged; recommendations are not recorded as execution without dispatch and a result. Execution records: docs/evidence/model-routing.jsonl.
- Reproduced stop during delayed worker initialization: first-summary readiness had not been reached and the existing five-second owned-child fallback killed the child with exit1. Strengthened the normal shutdown test to wait for a first summary and added a startup-stall cleanup regression; no arbitrary shutdown timeout extension or exit-code assertion relaxation.
- Final independent review found raw-only numeric replay with default flow analysis could be falsely STALE because the evaluator had no flow timestamp. Added raw `lastObservedAtUtc` to ingestion metadata, validated it at IPC and used actual observation timestamps for freshness/data age. Regression checks current raw-only acquisition, eight missing-flow diagnostics and delayed old summaries. Extremely large integer latency metrics now fail validation without an OverflowError that could stop supervision.
- Final checks: Python246 passed92.62s (one upstream deprecation); independent emulator25 passed9.382s; Release build0 warnings/errors and .NET74 passed; generated contract/v3.4 checks pass. Final process inventory found no Aegis API/module/emulator Python processes left. A prior full run failed during intermittent Windows loop/socket initialization; its XML and bounded unsuccessful diagnosis are preserved, and a clean rerun is not treated as an OS fault fix.
- Isolated100ms synthetic trial,4 modules ×256 numeric signals:10238.674 recorded values/s, API p953.110ms, observed-data age p95353.526ms, final pending0 for all modules, archive integrity/contiguous prefixes pass. Earlier100Hz trial still misses realtime and is retained in the report. No capacity or improvement-factor guarantee.

## 2026-10-19 portfolio presentation and reproducibility

- User authorized measured bottleneck improvements, reproducible execution, design explanation and demonstration material for job applications. No Git initialization, remote publishing, real hardware or invented personal history.
- cProfile identified repeated Pydantic-to-dict/Python JSON encoding. Added direct validated-model JSON archive path, preserved legacy retry compatibility/first receipt/atomic rollback and all transport validation/WAL FULL. Raw signal enum strings avoid repeated Enum serialization. New regression covers old/new serializer retry, changed duplicate rollback and BAD/null evidence.
- Six alternating serialization-only CPU trials on the same validated200×256 frames: median320.3125ms versus78.125ms. Full A/B/A/B runs varied; previous code also kept up in one run, and an optimized run ended with247 pending batches. No causal whole-pipeline speedup or sustained100Hz claim. Frozen baseline code and source hashes are retained.
- Added PowerShell7 setup/verify shared by local checks and CI definition. Fresh Unicode/space folder installation succeeded. First verifier attempt failed with pytest exit4 from split JUnit arguments; retained summary, fixed the argument boundary and added a real PowerShell parsing regression. Second complete verifier run:248 Python passed120.08s,25 emulator passed9.862s,74.NET passed, Release0 warnings/errors, contract/locked restore passed. Remote Actions remains unexecuted.
- Produced a12-second phase-ordered GIF from actual WPF screenshots plus raw-archive inspection of three256-signal modules. It is not continuous recording. Preserved transient POLL_TIMEOUT/STALE observation; all four raw flow phases survived in the first module's contiguous archive.
- Added portfolio case study, performance report, reproducibility report and demo guide. Router leaf execution and independent review are recorded; coordinator integrated and verified their results and corrected a script error that static review missed.

## 2026-10-20 signal evidence before cause classification

- User clarified that noisy data and preservation of noise-like true process changes matter more than headline throughput. Added spec/plan and `docs/SIGNAL_REVIEW.md`; retained previous portfolio/performance evidence as historical rather than reusing it as current capacity proof.
- Added evaluator-owned flow review before the1Hz early return. Separate5-point causal median trend and prior1s/256-point median/MAD candidate context; fixed development thresholds, frozen per-event baseline, raw extrema/observationIds/timing/relatedsensor evidence, all causes UNRESOLVED. Original values/quality/archive and existing EWMA/C#diagnostics are unchanged. No new dependencies.
- Added strict bounded review model to worker IPC and separate authenticated `/v1/devices/{id}/signal-review`; stale current values cleared while historical evidence remains. Sequence gaps reset context even below1.5s. Last32memoryevents/omittedcounts; no persistentreview/ACK, no WPFview.
- TDD: missingevaluatorinterface/API first failed; added unit and archive/IPC/API cases. Integration fixtures initially incorrectly omitted required archive and compared equivalent UTC textual forms; corrected fixture configuration/canonical timestamp, preserving production durability boundary.37unit/in-process cases passed before newrealprocess case.
- IndependentSol6.1/medium review reproduced sustainedstep→return creating a secondpersistentfalse event from a contaminated baseline. Wrote failingregression, excluded activeevent samples from futurebaseline, keptactive reviewvisible after localhistory expires. Regression and37cases green. No claim that firstimplementation was correct.
- Added independently runnable seeded `NOISY-FLOW` simulator scenario, no cause/scenario fields onwire. Added real256-signal module/archive/API smoke test at50ms; first10ms and one50ms run timed out30s with WinError10053 while rawpulse evidence remained. Offline reevaluation of archived50ms data produced8closedcandidates; later50ms process run passed25.33s with rawprefix/integrity/cleanup. Firstpass retained separately, pending8/dataage1.08s; instability not diagnosed/fixed by successful retry.
- Offline39cases (13conditions×3seeds), cleantruth heldoutside public inputs: smallnoise meanRMSE0.1206→0.0604; true100ms pulses6/6newcandidateonsets but smoothedRMSEworsened0.1206→0.4265; heavy-noise smallpulses4/6 (2missed); burstnoise106candidates/24s (265/min); truepulse+burst6/6but96candidates/24s. Identical publicobservations with oppositecause labels give identicalunresolvedevents. These are neither calibrated causeaccuracy nor fieldrecall.
- Router used boundedSol6.1/medium read-only design and implementation reviews; observed execution recorded in model-routing.jsonl. No remote CI, hardware access, Git initialization, publishing or fabricated independentfield data.
- Sharedfull verifier:280 Python pass/6processfail in375.66s; all38newreviewcases passed.26emulator pass,74.NETpass,Release0warnings/errors,contracts/lockedrestoregreen. One scoped recheck of6failures also6failed (timeouts,Win10014socketinitialization andWin32tempfolderinuse), no timeout/assertion waivers. RecheckXML/UTF8log saved before a cp949console-print UnicodeEncodeError; XML remains authoritative. Fullfailures and recheck retained under docs/evidence/signal-review-checks. Operationalverification remains incomplete.
