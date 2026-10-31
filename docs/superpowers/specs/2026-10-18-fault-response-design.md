# Faulty data and response verification

User added: send problematic values as well as normal values to verify Aegis reacts. Preserve the independent emulator projects and read-only transport. This authorizes the following simulator evaluation slice; no physical device commands or automatic repair.

## Emulator

Optional --scenario NORMAL (default), DEG-02, DATA-QUALITY. The raw protocol remains unchanged and carries no scenario name, hidden truth, expected warning time or fault cause.

- DEG-02: through 60 seconds, setpoint10 and actual9.8 L/min; then actual decreases0.08 L/min/second, floor2. Other raw signals remain valid.
- DATA-QUALITY: repeat a 60-second cycle: 0..14 normal;15..29 actual flow MISSING/null;30..44 BAD/null;45..59 source-labeled GOOD but actual flow -5. These distinguish sensor quality, missing data and semantically invalid values.

## Aegis response

Explicit API --evaluate enables the frozen development EWMA evaluator. Default fixture/connection-only operation remains NO_MODEL. The demo launcher enables evaluation and can apply a selected fault scenario to its first emulator while other devices remain normal.

Development input ranges: flow0..100L/min, pressure0..2000kPa, valve0..100%, current0..100A, temperature-40..200C, nonnegative integer cycle count. These are synthetic test hypotheses requiring equipment-specific calibration before any real deployment.

Quality defects immediately generate structured diagnostic issues and operator-facing sensor/input checks. A source-labeled GOOD/-5 is an invalid value, not a process anomaly accepted into the model. Missing/BAD/STALE/out-of-range, gaps over1.5seconds, and mode changes invalidate/reset warmup. No new ML warning or normal/recovery judgment is made from bad or disconnected input.

Frozen before evaluation: 60 consecutive1Hz samples covering at least59seconds; evaluation every30seconds; absolute flow tracking error averaged over the window; EWMA alpha0.2; warning above1.5L/min for3 consecutive evaluations; recovery at/below1.0 for3 evaluations. Model ID ewma-flow-dev-1, lifecycle ADVISORY, null percentile. This is a transparent development rule, not trained AI or a probability of failure.

Warnings have stable incident IDs and bounded in-memory timelines using existing v3.4 EWMA_WARNING_RAISED and ADVISORY_RECOVERY_CONFIRMED events. No durable ACK or persistence is claimed.

Read-only GET /v1/devices/{id}/diagnostics extends the local emulator API: deviceId, enabled, modelVersion, evaluation, sampleCount, flowErrorEwmaLpm, issues[{signalKey,reasonCode,message}]. Existing warnings/timeline/health routes expose evaluator results only in enabled emulator mode.

WPF displays model/warmup/EWMA, separate data-quality recommendations and active warning IDs for the selected device. It never upgrades disconnected data to NORMAL or GOOD.

## Verification

Deterministic tests cover normal warmup, progressive degradation, exact consecutive-window trigger, recovery hysteresis, missing/BAD/GOOD-out-of-range, unavailable state and device isolation. Real separate processes verify DATA-QUALITY produces actionable diagnostics while another device remains healthy; use bounded public clock fixtures for long EWMA sequences rather than claim independent private ML evaluation. Existing connection/performance evidence remains scoped to evaluation-disabled acquisition until measured otherwise.
