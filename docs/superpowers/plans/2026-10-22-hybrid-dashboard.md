# Hybrid dashboard implementation plan

> **For agentic workers:** Use superpowers:subagent-driven-development for the bounded UI, engine and gateway tasks; the coordinator owns integration and verification.

**Goal:** Run the selected light React dashboard in a browser and Windows WebView2, backed by actual local acquisition and C# raw-signal evidence.

**Architecture:** Preserve Python module processes and durable SQLite raw archives. A read-only ASP.NET gateway validates upstream summaries and feeds committed raw snapshots into the native C# review engine, then publishes bounded SignalR summaries.

**Tech Stack:** React/TypeScript, Tailwind/shadcn, ECharts, React Flow, ASP.NET Core/SignalR, WebView2, SQLite. ONNX is explicitly not registered; PostgreSQL remains a documented migration boundary.

**Spec:** `docs/superpowers/specs/2026-10-22-hybrid-dashboard-design.md`

## Global constraints

- Approved project-local dependencies only; no global tools, new MCP/plugins, deployment or repository publication.
- Preserve raw data and existing native fallback; do not invent predictions, confidence or equipment condition.
- Same-origin cookie bootstrap, literal loopback, no URL or browser-persistent secrets.
- No repository exists, so worktree/commit steps do not apply.
- Do not run throughput benchmarks alongside builds, tests or installs.

## Review focus

- Stale data must lose current status while retaining labeled historical evidence.
- Archive paging must preserve every committed batch and expose catch-up rather than silently skipping.
- Auth must reject foreign Origin/Host for HTTP and WebSocket, with no query token fallback.
- Device switches must not mix charts/evidence from different sources.
- Process shutdown must stop only owned processes and retain archives.

## Tasks

- [x] UI (`ui/**`):8 focused tests, production build, FIXTURES browser checks, four width boundaries and workflow node bounds.
- [x] C# review: validated raw port, independent review and red/green timestamp watermark regression. Standard vstest is now blocked by host socket failures; selected cases also executed in process.
- [x] Gateway:41 in-process auth/archive/cache checks, including committed600-frame replay, page-boundary pulse and stale history preservation.
- [x] Desktop integration: secure bootstrap, work-area sizing policy, Release build and selected policy checks. Real multi-monitor/DPI rendering remains unverified.
- [ ] Owned demo launcher and reproducible setup/verify commands: small CLI tests, emulator→archive→C#→gateway→browser verification, shutdown evidence.
- [x] Frozen signed-window experiment:160 cases completed after recorded interruption recovery, gates failed, no runtime promotion or tuning.
- [x] Independent engine review and regression checks; whole-suite failures recorded honestly in HYBRID_DASHBOARD.md and evidence summary.

2026-10-31: launcher implemented and CLI checks passed. The remaining unchecked integration task is blocked by repeated Win10014 before API/Kestrel/vstest listen. Overall completion is not claimed. User subsequently authorized a public GitHub repository under their current account; source publication includes these limitations.
