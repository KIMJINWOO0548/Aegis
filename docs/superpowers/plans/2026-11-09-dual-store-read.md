# Implementation plan

1. Preserve exact base, old worktrees and remote heads; verify public/push permission and noreply identity privately.
2. RED/GREEN extract connection-only adapter validation; preserve writer behavior with existing regressions.
3. RED/GREEN a bounded opt-in dual-store read projection and CLI, explicit receipt window, native identity namespaces, nullable source metadata and stored evidence.
4. Inject invalid sources, tampering, rollback/restart/retry and concurrent WAL append. Verify no logical writes, no partial success and unchanged original files/history.
5. Obtain independent code review; resolve material findings with regression tests. Freeze sources and run whole Python suite, emulator unit suite, generated contract check and v3.4 validation.
6. Record verified and unverified scope, publish a separate normal branch, verify remote SHA and preservation of all prior refs/worktrees. No merge, force push, UI change or automatic migration.
