# Aegis hybrid dashboard — implementation boundary

User selected React+TypeScript, Tailwind+shadcn/ui, Apache ECharts, React Flow, SVG/3D assets, ASP.NET Core+SignalR, WebView2, ONNX Runtime and SQLite→PostgreSQL. Project-local installation was explicitly approved. No global configuration, new MCP/plugin, deployment, remote repository or production database mutation is authorized.

## Selected first slice

- React is the shared Windows/browser UI. Reference: user-provided NEXORA dashboard screenshot, adapted to LIGHT white cards, #F4F6FB background, slate ink and blue accent. Preserve left navigation, metric row, central asset/chart/table and right evidence list. Use actual current signals and explicit unavailable states; no fabricated OEE, MTBF, forecast, confidence, AI claims or profile avatar.
- ASP.NET Core serves built UI and read-only aggregate API at literal127.0.0.1. SignalR publishes only bounded latest summaries at1Hz. No acquisition/raw persistence in browser. Local authenticated bootstrap exchanges a secret for an HttpOnly SameSite=Strict session; secrets never enter URLs/query logs/localStorage. Restrict Host/Origin including WebSocket, disable redirects/proxy upstream and raw exception details.
- Existing Python collectors keep durable raw SQLite WAL/FULL and per-module process boundaries. A new C# signal-review library consumes committed raw snapshot JSON; Gateway tails configured module archives READ ONLY, in bounded pages. It reproduces the validated raw-preserving flow evidence logic (not a newly trained model), keeps last32event summaries, and exposes actual cursor/freshness. If no archive is configured, native review is unavailable rather than falsely described as active. Python EWMA remains separately labeled upstream advisory.
- WebView2 hosts the same local dashboard. Block external/frame navigation, popups, downloads and permissions; do not expose arbitrary host objects. A process-scoped secret can bootstrap through a temporary request header (not query string). Existing native window remains available as fallback during transition.
- ONNX is NOT an invented inference path. Without a real validated model, report NOT_REGISTERED. PostgreSQL is a later storage migration, not a second database copied into this slice. Existing SQLite/raw evidence remains intact. React Flow is a read-only acquisition→archive→review visualization, not equipment control automation.

## Agreed dashboard transport

GET `/api/dashboard` and SignalR hub `/hubs/dashboard` event `snapshot` have the same JSON:

```
{ schemaVersion:"dashboard-1", generatedAtUtc:UTC,
  upstreamState:"CONNECTED"|"UNAVAILABLE"|"CONFIGURATION_REQUIRED",
  sourceMode:"EMULATORS"|"FIXTURES"|"UNKNOWN", message:string|null,
  devices:[{ deviceId, equipmentId:string|null, moduleId:string|null,
    connection:existingConnection|null, ingestion:existingIngestion|null,
    observations:existingObservation[], diagnostics:existingDiagnostics|null,
    warnings:existingWarning[], review:FlowReviewSnapshot|null,
    engineState:"RUNNING"|"UNAVAILABLE"|"ARCHIVE_NOT_CONFIGURED",
    nativeSequence:number|null }],
  inference:{state:"NOT_REGISTERED",message:string} }
```

FlowReviewSnapshot uses the same camelCase fields as current Python review, with algorithmVersion `flow-review-dotnet-dev-1`. All event dispositions UNRESOLVED. Device identity/units/numeric fields/timestamps and bounded lists are validated before publication. Frontend never interprets successful HTTP or source GOOD quality as normal equipment. Stale data has visible history labeling and does not remain green/current.

Default Gateway port8780, upstream API8765, all literal loopback. Environment: AEGIS_GATEWAY_URL, AEGIS_DASHBOARD_TOKEN, AEGIS_API_URL, AEGIS_API_TOKEN, optional AEGIS_ARCHIVE_DIRECTORY. GET /health is non-sensitive readiness. POST /api/session JSON {token} (exact same Origin required) creates cookie. GET /api/session reports authenticated or401; DELETE /api/session clears session. WebView2 may supply X-Aegis-Dashboard-Token only to the exact configured origin for bootstrap; gateway handles it without exposing upstream secret. Browser users can use the local unlock form. No token in SignalR query strings.

## Verification

Port the C# engine against fixed raw traces:100ms valid pulse, sustainedstep→return→secondpulse, invalid/gap/state/setpoint reset, raw immutability, frozen baseline, sparse rates, bounded history and duplicate identity. Gateway tests cover unauthenticated/invalidHost/foreignOrigin and unavailable upstream; browser checks navigation, source selection, signal filtering, empty/stale states, chart bounds, evidence and workflow. Compare reference and browser at equivalent desktop viewport. Label the chart as1Hz UI summary, not reconstructed raw100Hz data. Keep the existing unresolved Windows socket/process failures separate from algorithm validation.

Continue the already frozen signed-window experiment offline; it is not promoted into either runtime engine before the preregistered held-out gate passes.
