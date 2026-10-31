# Verification record — 2026-10-18

Environment: Windows, Python 3.12.14. Runtime and tests use the standard library. Commands below were run from this project directory using the checkout's `.venv\Scripts\python.exe` interpreter; no Aegis package is needed by the emulator.

1. Before implementation, `python -m unittest discover -s tests -v` failed with nine assertion failures because the independent `aegis_emulator` package did not exist.
2. After implementation, the same command passed nine tests in 2.778 seconds.
3. After adding actual CLI process verification, the complete suite passed **11 tests in 16.560 seconds**, with zero failures/errors. It exercised two independently configured processes, stopping one, continued sample advancement from the other, and restarting the stopped device with a new run UUID. It also checked missing/invalid tokens, duplicate authentication headers, external Hosts, browser Origins, unsupported methods/paths, sequence timing, deterministic signal values, envelope fields and invalid CLI arguments.
4. `python -m pip wheel --no-deps --no-build-isolation --wheel-dir dist .` succeeded. Output: `aegis_equipment_emulator-0.1.0-py3-none-any.whl`, 5,184 bytes, SHA-256 `6b60c7bad4d8d9a787afe88e63f3c04e082226178e910eaef0adcb02960e7975`.
5. Extracted that wheel into a temporary directory, then imported it with Python isolated mode (`-I`) from that directory. Generated a protocol 1.0 snapshot for `EQ-ISOLATED` with exactly eight raw signals. This passed without importing from the Aegis checkout.

These checks establish this local emulator's startup, contract and process isolation. They do not measure the complete Aegis collector/API/UI pipeline, sustained resource use or customer-equipment performance. This section describes the original latest-only implementation; the replay extension below supersedes that transport limit. No durable source history or ML evaluation is implemented.

## Authorized scenario extension — 2026-10-18

Added `--scenario NORMAL|DEG-02|DATA-QUALITY` after the request to send faulty values and verify Aegis behavior. Before implementation, the four deterministic scenario tests could not construct a generator with the new scenario argument. After implementation, the complete suite passed **16 tests in 7.348 seconds**, with zero failures/errors.

The new checks cover unchanged default values, DEG-02 boundaries at 0/59/60/61/100/160/500 seconds, every data-quality phase boundary and repeat at 60/75 seconds, finite values, null preservation, no scenario metadata in the wire payload, invalid scenarios, and actual CLI selection of DEG-02. Scenario timing uses a controlled monotonic clock in unit tests; no long sleeps or time acceleration option were introduced.

Rebuilt scenario-stage wheel: `aegis_equipment_emulator-0.1.0-py3-none-any.whl`, 5,466 bytes, SHA-256 `87f3a54acfc4b8eb7c8b3ba4027daf0de00a28d7c6d4122a29403f1c3d2fbfff`. The later replay wheel record below identifies the current artifact. The Aegis end-to-end response to these scenarios is verified by the parent task, separately from these standalone checks.

## Bounded replay and hundreds of synthetic signals — 2026-10-18

Added authenticated `GET /v1/batches` with strict cursor/limit/optional run UUID validation, a 4096-sequence replay window, maximum 256 snapshots per page and maximum 900000 compact encoded JSON bytes per page. A changed run UUID restarts from the oldest available sequence; a current-run cursor beyond the latest fails with 400. Samples are reconstructed deterministically rather than stored in an unbounded memory queue.

Added `--scenario TRANSIENT` and `--signal-count 8..512`. Replay snapshots use raw protocol 1.1 and include the configured signal count. Legacy `/v1/snapshot` remains protocol 1.0 with exactly eight signals even when 256 or 512 are configured. No source scenario/truth labels are emitted.

TDD evidence: the first replay tests failed because `batch` and `TRANSIENT` did not exist and the new HTTP endpoint returned 404. After that implementation, 22 tests passed. The second test increment failed because the signal-count option was absent and replay snapshots still carried protocol 1.0. The final complete standalone command, `python -m unittest discover -s tests -v`, passed **25 tests in 8.250 seconds**, with zero failures/errors.

New coverage includes strict paging and empty caught-up pages; same-run and absent-run cursor behavior; process-run changes; retention overflow bounds; invalid generator and query arguments; unchanged Host/Origin/token checks on replay; exact legacy compatibility; and partial pages at 256 signals sampled every 10 ms, reconstructing every sequence from 0 through 100 without exceeding the byte cap. With a controlled source clock, latest reads at 0 and 2 seconds appear normal, while replay retains all twenty 10 ms abnormal samples from the two intervening 100 ms transient segments. A real CLI process test starts a 256-signal transient source and verifies its legacy snapshot, replay raw version/count and abnormal values over HTTP. The largest configured 512-signal frame also fits in a nonempty page.

Rebuilt wheel: `aegis_equipment_emulator-0.1.0-py3-none-any.whl`, **6,517 bytes**, SHA-256 `68ff71679c4e804487ad335e6c4d47f53d3babc713692fd18df9a1cc834a7c5a`. An isolated-mode interpreter loaded the extracted wheel outside the checkout, generated an eight-signal legacy snapshot and a 512-signal replay frame successfully.

Limits: this is deterministic synthetic replay, not a physical device buffer. The source replay window is lost on process restart and expires after 4096 sample intervals; permanent raw history and committed consumer cursors belong to the consumer archive. No customer data, arbitrary CMP tag registry, scheduler event model, hard real-time guarantee or sustained end-to-end capacity was verified by these standalone tests.

Final standalone recheck, 2026-10-19: the complete `python -m unittest discover -s tests -v` command passed **25 tests in 9.382 seconds**, zero failures/errors. Parent acquisition/process/desktop results are reported separately in `../../docs/DURABLE_INGESTION_REPORT.md`.
