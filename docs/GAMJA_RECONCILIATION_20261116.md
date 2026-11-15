# Gamja / remote reconciliation (2026-11-16 KST)

## Canonical comparison

- Desktop checkout and remote main: `bd4e44f`, clean before this work.
- Latest fetched improvement tip: `cf87691` on
  `improve/nullable-input-archive-20261109`.
- All fetched remote branch tips are ancestors of this tip. It contains 20
  commits absent from main. No cherry-pick or main reset was needed.
- This work uses an isolated worktree/feature branch. Desktop main and user
  runtime files were preserved. This is a reconciliation candidate, not a
  merged or released product.

## Implemented upstream, but not equivalent to Windows acceptance

The 20 commits include collector/startup supervision, archive failure integrity,
source context, bounded tag/history queries, numeric rules/version locking,
backup/restore verification, storage diagnostics, synthetic adapter contracts,
commit-confirmed adapter storage, dual-store reads and nullable input storage.
These changes must not be replaced by the older Desktop main.

The new adapter fixture loader, adapter store, nullable archive writer,
backup/restore and storage diagnostics explicitly reject unsupported platform
primitives. On Windows these paths report `PLATFORM_UNVERIFIED`. Removing these
guards would silently weaken no-follow/atomic-publication/directory-sync
assumptions and was deliberately not attempted.

## New preflight

Run `python -m aegis.platform_support` before selecting the opt-in storage tools.
It emits JSON and returns 2 when any known platform gate is blocked, otherwise
0. `PRIMITIVES_PRESENT` only means symbols exist, NOT that runtime behavior,
permissions, filesystem durability, or an entire product have passed testing.
The report always retains `runtimeValidation: NOT_RUN`; it reads no user data
and writes no files. Original feature-level safety gates remain unchanged.

## Evidence and remaining work

- Python on Gamja: 3.12.14.
- New preflight tests: 4 passed (including subprocess/no-files and negative
  platform combinations).
- Existing targeted adapter store/input archive/backup suites: **118 failed,
  48 passed, 2 skipped** on Windows. No skips were added to hide this failure.
- Full-suite outcome is recorded separately after the run; focused tests do
  not establish whole-suite acceptance.
- Required next: Windows-native safe file/handle and directory durability
  implementation with fault tests, Windows collector/service + WebView2 E2E,
  long-running load/recovery evidence, real equipment adapter validation and
  the remaining operator workflow/retention configuration work.
- No actual hardware, trained model accuracy, 24-hour soak, or production
  deployment was validated in this reconciliation.
