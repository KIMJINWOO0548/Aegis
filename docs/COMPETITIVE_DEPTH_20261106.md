# Retained-summary investigation — 2026-11-06 UTC

This increment makes a moving signal chart inspectable: freeze an independent copy, read exact UTC/null rows with a keyboard, see retained coverage and resume collection-following. It preserves device/signal/source context and resets on a different selection. It remains a synthetic local engineering prototype, not verified equipment monitoring or learned fault prediction.

## Evidence and independent implementation

The public [Grafana Play](https://play.grafana.org/) navigation was actually captured by REA 6.8.0: home links → uniquely observed sidebar Dashboards link → library search, tag/owner/sort controls and dashboard table shell. The library table still showed loading placeholders. A data panel, time-range change and live investigation were not exercised, and competitor performance is NOT_RUN. Official [dashboard time controls](https://grafana.com/docs/grafana/latest/visualizations/dashboards/use-dashboards/) and [panel inspection](https://grafana.com/docs/grafana/latest/visualizations/panels-visualizations/panel-inspector/) describe time context, data tables, query/stats inspection and exports. Documentary patterns are separate from observed product behavior. Metadata/digests are in `replica/phase2/rea-evidence.json`; third-party screenshots/source are not shipped. No source, endpoint, brand asset or UI copy was taken.

The critical own-software weaknesses were canvas-only precision and repeated range/projection work on clock-only updates. `SignalChart` now memoizes derived views and skips unrelated parent renders. Its native table and summary expose gaps/availability without silently substituting zero. Freeze copies and freezes individual rows/array; source collection is unaffected. Authentication, quality gates, archive storage and diagnostic algorithms are untouched.

## Validation

| Check | Actual result |
|---|---|
| Baseline UI suite | 19 passed |
| New independent cases | 6; absent module, arbitrary-ID false cause and isolated-point chart policy reproduced RED, then GREEN |
| Final UI suite | 25 passed |
| Product TypeScript source check | Exit 0 with existing read-only desktop dependencies |
| Impeccable detector on changed TSX/CSS | Exit 0, no findings; limited mechanical source detector |
| Replica selected-role contrast | 7/7 AA; normal text minimum 4.88:1; UI boundary minimum 3.57:1 |
| Production build | Exit 0; existing PURE annotations and >500 kB chunk warning remain |
| Actual Chrome browser | Baseline and final exit 0; keyboard disclosure, pulse/gap/delayed-data inspection, continuing collection during freeze, resume, device/signal reset, 390 px page overflow and no page exceptions |
| Actual isolated-point regression | Old compiled UI failed: last valid value existed in table but not canvas. Bounded 4 px markers fixed it; actual final canvas contained 10 matching marker pixels |
| Stable-series repaint work | Identical 3.3 s clock-only scenario: baseline 3 canvas clears → final 0; no latency/throughput or competitor-performance inference |

The pipeline cases call `parseSnapshot`, `appendHistory` and `interruptHistory`: received 80 L/min pulse retained; dropped interval null marker; delayed duplicate cannot replace that pulse; BAD/stale values remain unavailable; zero remains zero; immutable capture; explicit chart gap projection. A separate RED regression caught arbitrary source IDs being mistaken for transport/gap markers; these causes now use internal typed marker provenance, never observation-ID prefixes. They are UI pipeline regressions, not a raw collector, equipment, DB durability or .NET/SignalR/WebView2 integration run.

Visual inspection of the actual desktop and mobile captures found the isolated-point issue; its policy regression uses the same `signalSeries` options as production, and the browser reads actual canvas paint. Markers remain bounded by the 120-row retention limit and nulls stay disconnected. Final screenshots and hashed before/after/RED/GREEN results: [desktop](evidence/competitive-depth/desktop-frozen.png), [mobile](evidence/competitive-depth/mobile-table.png), [browser-checks.json](evidence/competitive-depth/browser-checks.json). The final built index hash is `ac50a2164c79e5b6c2f113dece1daf1041186345c1ed6d29e309e1ce5e65a8d8`.

`scripts/check_signal_investigation.py` is a reproducible browser harness. It hosts only the built static UI on an ephemeral loopback port and intercepts synthetic session/dashboard/negotiate/WebSocket responses inside Playwright. It never uses a real token, changes authentication, contacts equipment or issues controls. Its same-input baseline/current scenario measures chart canvas clears during 3.3 s of stable-series wall-clock ticks. This metric is repaint work in one synthetic scenario, not general throughput/latency or competitor performance.

Host reproduction with already installed dependencies, no install step:

```powershell
# Project's normal installed environment
Set-Location ui
pnpm test
pnpm build
Set-Location ..
python scripts/check_signal_investigation.py --dist ui/dist --output artifacts/competitive-depth/browser-fresh
```

The coordinator's isolated validation configs reuse existing dependency paths read-only under ignored `artifacts/`; no recursive pnpm dependency copy, new package or global mutation is required. The exact phase-1 baseline is 28ae7dbc0da35796ad47ad535b173e2dc79f12a7, extracted into ignored artifacts without replacing working source.

## Boundaries and remaining gaps

At most 120 memory rows are retained, including null/transport/gap markers. The table is not an archive query; min/max describe available 1 Hz UI values only. Missing sub-second pulses cannot be reconstructed. A retained null does not preserve the original BAD/MISSING/STALE cause, so the UI uses the qualified “값 사용 불가” rather than inventing it. Freeze means historical investigation, not paused ingestion or current equipment state.

Archive retrieval, multi-signal overlay, share/export, query inspection, real server/WebView2 integration, raw 100 Hz pulse preservation and field validation remain gaps. Full Python/.NET suites were not rerun for this UI increment; historical failures remain documented in the README. The scoped Replica checklist is 8 rows (raw weighted 50.0; 2/4 must complete), not a product competitiveness score or superiority claim. Full screen-reader, 200% zoom and external-product performance checks are not established by token/source checks.
