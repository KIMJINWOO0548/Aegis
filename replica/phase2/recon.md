# Signal investigation: scoped competitive audit

2026-11-06 UTC. Replica recon/design/diff applied to the user's approved read-only investigation slice. This is an independent implementation of general interaction patterns. No Grafana source, private API, branding or assets are used.

| Source | Evidence and boundary |
|---|---|
| https://play.grafana.org/ | REA 6.8.0 actually captured public home navigation, dashboard links and a time control. The initial community panel was loading. This alone does not verify a working graph, panel inspector, query latency or keyboard flow. Coordinator evidence: `phase2/refs/grafana-play-initial` and `grafana-play-ready`. |
| https://grafana.com/docs/grafana/latest/visualizations/dashboards/use-dashboards/ | Prior REA capture of official time-context documentation. Documentary evidence, separate from actual product behavior. |
| Own `ui/src/SignalChart.tsx`, `data.ts`, `use-dashboard.ts` | Baseline 28ae7db; maximum 120 in-memory UI rows including null markers, not a database/archive query. |

## Inventory and behavior

- S01: public Grafana Play landing/dashboard navigation. Loading state observed; no account or control writes.
- S02: Aegis signal review, current and historical summary states.
- S03: Aegis frozen investigation, exact UTC range, availability/gap/value summary, native disclosure table.
- F01: choose device/signal → retained range → freeze investigation → inspect exact UTC/null rows → resume. Device/signal/source changes remount the investigation so a frozen context cannot leak into another selection.
- Components: existing native range select; real toggle button with `aria-pressed`; status text; data `dl`; native `details`/`summary`; captioned, scoped-header table in a named keyboard-focusable scroll region. Own blue/slate roles preserve the incumbent dashboard identity.

## Critical findings and acceptance

Canvas alone offered no precise keyboard-readable values; range filtering rebuilt the chart on unrelated wall-clock ticks. Investigation could move while a reader compared samples. The new flow copies/freeze-protects at most 120 rows and memoizes range/projection/summary. Collection, session/authentication and current-quality gates remain separate.

Tests exercise real UI `parseSnapshot → appendHistory → interruptHistory`: an already received 80 L/min pulse, a dropped interval, duplicate delayed sample, stale/BAD values and independent immutable capture. This is not a 100 Hz collector/archive/equipment test. A pulse absent from 1 Hz summaries cannot be recovered. Gap/null markers are not measured zeroes; min/max describe only available retained UI values.

Behavior criteria: preserved IDs/nulls/range boundary; exact UTC; immutable freeze despite new input; selection reset; native Enter/Space actions; narrow viewport without page overflow. Performance criterion: stable-series canvas redraws during the same 3.3 s clock-only scenario before/after, not external throughput or latency. The browser harness uses isolated synthetic Playwright API/SignalR routes, never a production auth change.

## Unclosed gaps

Archive retrieval, raw 100 Hz pulse reconstruction, multi-signal overlays/correlation, durable share/export, query inspection, field accuracy, real SignalR server/WebView2 integration and external-product performance remain outside this increment. `features.csv` deliberately includes these gaps. The Replica output is a self-selected slice checklist, not a product score, release verdict or superiority benchmark. Screenshot similarity against a different product/data shape is not measured.

## Final public-reference observation

Public home to dashboard library navigation observed; search/tag/owner/sort controls and table shell; table placeholders still loading. Data panel/time-range/live investigation and competitor performance NOT_RUN. Evidence digest: `3d4207eb8cddb85ef57bc84702da199712dcd056e8ff0b70bd0d25979a9e2ff8`; see `rea-evidence.json`.
