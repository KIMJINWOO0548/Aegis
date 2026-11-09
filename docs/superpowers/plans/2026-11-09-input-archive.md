# Implementation plan

1. Pin exact remote base, preserve all old worktrees/remote heads/files and verify publication identity/scope privately.
2. RED/GREEN implement independent explicit write schema, source descriptor, atomic page log/state, idempotence/conflicts, fail/reopen confirmation and read-only validator.
3. In parallel implement finite synthetic fixture collector/CLI and regression tests; retain all old adapter/simulator semantics.
4. RED/GREEN add explicit new format to common read projection and actual synthetic write/requery/restart/failure/provenance tests.
5. Independent review, meaningful fault injection, whole Python suite, emulator unit suite and generated contract/v3.4 checks after source freeze. Record unresolved limits and absent hardware/Windows/CI/UI validation.
6. Scan intended additions for secrets/personal data, preserve noreply authorship, commit to separate branch, normal push and confirm remote SHA plus unchanged previous refs/worktrees. No merge or force push.
