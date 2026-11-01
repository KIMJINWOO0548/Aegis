# Replay clock fixture diagnosis and platform verification

Baseline: `bd4e44f8a78b08ae5ccb3499a68fe3af67846f78` (`main`). Work is local on
`improve/validation-portability-diagnosis`; no push, remote CI rerun, PR, merge or
deployment was performed. This is a synthetic equipment prototype.

## Actual remote failure

Read-only GitHub job logs from [run 41211685576](https://github.com/KIMJINWOO0548/Aegis/actions/runs/41211685576),
job `124567828006`, show that preparation succeeded. The sole failed check in
its summary was `emulator-unittest`: one of 26 tests failed in
`test_replay_http_pages_are_bounded_and_keep_the_same_auth_boundary` because
`latestSequence` was 299 rather than 300. The log reports Python 300 passed,
UI 8 passed, .NET tests 89 + 32 passed, Release build succeeded, and the separate
41 gateway / 47 selected in-process checks passed. These are observations of
that historical Windows CI run, not results of rerunning this local patch.

This failure differs from the older Windows socket error 10014 recorded in
`HYBRID_DASHBOARD.md`. The current CI log does not justify diagnosing it as
that socket problem, nor does its successful build prove WebView2 interaction.

## Reproduction and correction

The old HTTP test created its generator at an arbitrary real monotonic epoch,
then mocked the clock to `started_monotonic + 300`. Binary floating arithmetic
does not guarantee that subtracting the epoch recovers exactly 300. For example:

```python
start = 763.7746189766141
assert (start + 300) - start == 299.9999999999999
```

With the generator constructed at that epoch, the existing HTTP test fails
deterministically at the same assertion as CI. The runtime floors elapsed time;
the mocked value is actually below its boundary. This evidence supports a test
fixture correction, not a runtime timing tolerance or product scheduler change.

The test now controls both clock endpoints: epoch 0, then the float immediately
before 300, then exactly 300. It asserts 299 immediately before the boundary and
retains the original expected 300 at the boundary, page-size, cursor, run UUID,
byte bound, authentication, Host and Origin assertions. Runtime code and the
existing 26 tests remain. No epsilon, rounding, relaxed expected answer or skip
was added. This does not claim nanosecond or hard real-time scheduling accuracy.

Use an interpreter with the declared Python dependencies and editable package
installed. From `emulators/Aegis.Emulator`, the following command is RED on the
baseline and GREEN on this branch (the arbitrary epoch is applied during setUp):

```bash
python - <<'PY'
import sys, unittest
from unittest.mock import patch
sys.path.insert(0, 'tests')
from test_emulator import HttpTests
with patch('aegis_emulator.core.time.monotonic', return_value=763.7746189766141):
    result = unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite([
        HttpTests('test_replay_http_pages_are_bounded_and_keep_the_same_auth_boundary')]))
sys.exit(not result.wasSuccessful())
PY
```

The corrected test also passed with construction epochs 0, 1000000.1 and
1000000000.1. This check establishes that the HTTP fixture is independent of
the host uptime; it does not change interval or timestamp calculations.

## Current Linux results and commands

Python 3.12.14, .NET SDK 10.0.401 (installed under a workspace directory),
Node 24.19.0, pnpm 10.17.1. The Node version satisfies the stated 22.18+
requirement but differs from the historical CI's exact 22.18.0. Dependencies
were installed from `requirements-dev.txt`, `ui/pnpm-lock.yaml` with a frozen
lockfile, and .NET `packages.lock.json` with locked mode. No lockfiles changed.
Python requirements pin versions but do not contain artifact hashes.

| Check | Current result |
|---|---|
| `python scripts/generate_contracts.py --check` | Passed |
| `python scripts/validate_v34.py` | Passed |
| `python -m pytest -q` from root, baseline and final | 299 passed, 1 skipped |
| `python -m unittest discover -s tests -v` from emulator | Baseline 26 passed; patched 26 passed |
| Deterministic original-failure reproduction | Baseline failed; patched passed |
| `pnpm test`, `pnpm build` from `ui/` | 8 passed; production build passed |
| .NET Contracts / SignalProcessing tests | 89 / 32 passed |
| Gateway internal checks / selected in-process checks | 41 / 47 passed |

The one existing Python skip is `tests/test_delivery.py`: PowerShell 7 is absent.
It checks actual PowerShell parsing of the Windows delivery script. The C#
contracts tests link platform-independent desktop policy sources; they do not
instantiate WPF or WebView2. The 47 selected checks overlap those xUnit tests
and must not be added to the distinct .NET test count of 121.

The first Python attempt before the editable installation reported 6 failures,
293 passes and 1 skip: subprocesses could not import `aegis`. Running the
documented `python -m pip install --no-deps --no-build-isolation -e .` completed
setup; the baseline then passed as above. This is a setup correction, not a code
fix. Initial pnpm attempts encountered an unwritable home data directory and
a noninteractive modules-directory prompt; workspace-scoped data/cache paths,
the pinned pnpm and `CI=true` completed installation without global changes.

From the repository root, the platform-independent .NET commands were:

```bash
dotnet restore tests/Aegis.InProcessChecks/Aegis.InProcessChecks.csproj --locked-mode
dotnet test tests/Aegis.Contracts.Tests/Aegis.Contracts.Tests.csproj -c Release --no-restore
dotnet test tests/Aegis.SignalProcessing.Tests/Aegis.SignalProcessing.Tests.csproj -c Release --no-restore
dotnet restore tests/Aegis.Gateway.Tests/Aegis.Gateway.Tests.csproj --locked-mode
dotnet run --project tests/Aegis.Gateway.Tests/Aegis.Gateway.Tests.csproj -c Release --no-restore
dotnet run --project tests/Aegis.InProcessChecks/Aegis.InProcessChecks.csproj -c Release --no-restore
```

## Unverified scope and preserved evidence

Windows WPF/WebView2, actual desktop rendering, the complete emulator→Python→
gateway→SignalR→WebView2 path, and a remote CI run of this patch remain unverified.
Recheck on Windows with Python 3.12, PowerShell 7, .NET 10.0.401, Node 22.18+
and pnpm 10.17.1 using `./scripts/setup.ps1` then `./scripts/verify.ps1`; launch
the bounded hybrid demo following `HYBRID_DASHBOARD.md` for manual UI checks.
No Windows tests were deleted or newly skipped to obtain Linux results.

A fresh headless Chromium screenshot/DOM attempt at the local static build did
not produce an image or DOM within roughly 54 seconds and was terminated.
DBus/OOM-score warnings were observed; the cause is unconfirmed. Its process
exit code alone was not treated as capture success. No alternate browser route
was attempted. The package includes the original repository fixture screenshot,
explicitly labelled historical, rather than claiming a new desktop capture.

External evidence in the task package includes command arrays, versions, exit
codes, JUnit/TRX, raw read-only remote logs, RED/GREEN logs, source hashes,
patch and a restorable Git bundle. Tests rewrite five historical JSON evidence
files as a side effect; their current-run outputs were copied to the external
evidence directory and the tracked originals restored byte-for-byte. Existing
portfolio/PDF/web and historical evidence were not changed.

The build retains its existing large-chart-chunk warning. Python reports an
existing Starlette/httpx deprecation warning; no new dependency was introduced
to silence it. No equipment, customer data, production capacity, investment or
compliance claim follows from these synthetic checks.
