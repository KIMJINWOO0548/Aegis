# Per-module warning aggregation correction

The Python evaluator retains up to 32 warning incidents per module, and worker
frames enforce that same per-module bound. `/v1/warnings` aggregates all modules.
The dashboard already supports up to 32 modules and up to 32 warnings on each
device, but the gateway incorrectly capped the entire upstream warning array
at 32. Two modules with 17 legitimate retained incidents each therefore caused
the whole dashboard to switch to `UNAVAILABLE`.

This local change inherits `51bf109c68c7fa3f31e912b418768a92ea45a0ea` on branch
`improve/per-module-warning-bounds`. It changes only the gateway warning bound:
the aggregate may contain at most `32 × known device count`, and each known
device still has a strict 32-warning limit. Unknown warning device IDs remain
rejected. Warning contract validation and the 262144-byte upstream response
limit are unchanged. No warnings are truncated, and no UI/schema/dependency or
producer-retention change is involved. These are input bounds, not a measured
32-module production capacity guarantee.

## Evidence

Before the runtime change, the original 41 gateway checks passed. The independent
new regression in `tests/Aegis.Gateway.Tests/WarningAggregationChecks.cs` failed
at `valid per-module warning histories above32 aggregate remain connected` for
two 17-warning modules. This is separate from the earlier nondeterministic
replay-clock test fixture correction and the historical Windows socket failures.

After the fix, all **48 gateway checks** passed: the unchanged original 41 plus
seven warning aggregation checks. The new checks cover 2×17 valid, identity
partitioning, 2×32 valid without truncation, the 32×32 synthetic input boundary,
33 warnings on one module rejected even when the aggregate allows 64, an unknown
module rejected, and an aggregate exceeding its device-count bound rejected.

An additional local Python probe fed actual evaluator instances accelerated,
explicitly synthetic observation timestamps to produce 17 resolved incidents
on each of two modules. The real collector/API warning route returned all 34
(6563 encoded bytes), confirming the upstream contract can legitimately produce
the regression input. This is not a wall-clock equipment run or business result.

Current Linux checks actually run with existing declared dependencies:

| Check | Result |
|---|---|
| Gateway application/test Release build and in-process checks | 48 passed |
| Python full suite, including evaluator/worker/replay contracts | 299 passed; 1 existing PowerShell skip |
| C# Contracts / SignalProcessing xUnit | 89 / 32 passed |
| Selected in-process core checks | 47 passed, overlapping xUnit cases |
| Contract generation drift / v3.4 validation | Passed |

The existing Python skip requires PowerShell 7; it was not added or changed.
Python retains its existing Starlette/httpx deprecation warning. The additional
47 selected core checks must not be added to the distinct 121 xUnit cases.
UI code and builds were unchanged, so the previous UI evidence was preserved
without claiming a new UI run. Test-generated historical evidence JSON was
copied to this task's fresh evidence directory and the tracked originals restored.

Run the regression on an environment with the repository's declared tools:

```text
dotnet run --project tests/Aegis.Gateway.Tests/Aegis.Gateway.Tests.csproj --configuration Release
```

`WarningAggregationChecks.Run` is invoked after the original gateway checks by
the existing program, including from `scripts/verify.ps1`. It does not open
network listeners or require WPF/WebView2. No tests were deleted, weakened or
newly skipped to obtain these results.

## Remaining limits

No GitHub call, authentication change, push retry or remote CI was performed.
The known remote 403 remains unresolved. Windows WPF/WebView2 rendering, browser
SignalR and the full manual desktop chain remain unverified. The older local
Windows 257-pass/43-failure host record and the later historical Windows CI
300-pass plus one emulator-fixture failure are separate observations; this
warning aggregation fix does not claim to resolve that old host's root cause.

The fresh evidence package provides RED/GREEN commands and logs, source hashes,
an incremental patch/bundle and a Windows validation handoff. Applying it to any
other checkout requires the parent task to confirm that checkout's state and
authority first; no automatic transfer/application is authorized by this report.
