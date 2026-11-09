# Adapter Local Store Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Confirm adapter local persistence only after page evidence, records and delivery cursor/EOF commit atomically.

**Architecture:** Add a separate bound SQLite receipt store and standalone synthetic durable session. Reuse existing adapter validation/admission without changing legacy code. Restart rebuilds admission from committed pages and compares derived records/state.

**Tech Stack:** Existing Python3.12/Pydantic2/asyncio/sqlite3/pytest; no dependencies.

**Spec:** `docs/superpowers/specs/2026-11-09-adapter-store-design.md`

## Global Constraints

- Existing SIMULATOR/v1/adapter/IPC/API/UI files unchanged; no fake original metadata or restricted preservation access.
- Local scope only: LOCAL_STORE_COMMITTED, legacyArchiveDelivery NOT_CONNECTED, sourceAcknowledgement NOT_SENT.
- One descriptor/stream/canonical finite fixture perstore; original evidence preserved; no auto delete/overwrite/migration.
- Exclusive fresh file or recognized validated store only; WAL+FULL,32MiB/64pages/1024records/0.2s busy bounds;5s/5millionVM per read/write phase with a fresh confirmation budget.

## Review Focus

- Commit may succeed before an exception or caller cancellation; never infer rollback from the exception or transaction flag.
- NULL initial cursor and empty EOF must not bypass uniqueness or atomicity.
- Corrupt stored cursor/receipt/record/page must fail restart validation, not create a completed report.
- Legacy target rejection must happen before SQLite can change journal companions.
- Caller output/process failure after commit must preserve/recover stored evidence without source ACK or duplicate ingestion.

---

### Task 1: Bound durable store and synthetic session

**Files:** Create `src/aegis/adapter_store.py`, `tests/test_adapter_store.py`, `tests/test_adapter_store_cli.py`.

**Interfaces:** `AdapterStore(path,adapter)`, `.commit_page(payload,received_at)`, `.committed_cursor`, `.ended`, `.records`, `.report()`, `.close()`; `DurableAdapterSession(adapter,store,timeout_ms=100,max_attempts=2).poll_once()`; CLI `python -m aegis.adapter_store --fixture FILE --store DB`.

- [ ] Write core/actual CLI RED cases for commit, nullable fidelity, duplicate/conflict, EOF/no-data, restart and invalid destinations.
- [ ] Run missing-module RED suites and freeze independent CLI tests.
- [ ] Implement exclusive identity-safe creation, exact schema, WAL/FULL and bounded full-store reconstruction.
- [ ] Implement one BEGIN IMMEDIATE page/record/cursor transaction and postcommit verification; fence uncertain results.
- [ ] Implement standalone synchronous-store/async-source session and private summary-only CLI; old defaults unchanged.
- [ ] Add reproduced failure regressions for SQL full/busy, before/after commit, cancellation and true child-process crashes; run focused GREEN.

### Task 2: Verify and publish

**Files:** Create `docs/ADAPTER_STORE_20261109.md`, `docs/evidence/adapter-local-store-20261109.json`; add only a discovery link to `README.md`.

- [ ] Document raw-v1 bridge blocker, local-only acknowledgement, initialization/unknown-result recovery, bounds and unsupported platforms.
- [ ] Verify existing synthetic example through first-run/restart CLI, compare source and legacy DB preservation.
- [ ] Freeze Python sources/tests, run full pytest and restore generated tracked evidence exactly; run26 emulator and contracts/v3.4 checks.
- [ ] Obtain fresh whole-change review on the most capable model; regression-fix material findings and rerun required checks.
- [ ] Match tested hashes, privacy/noreply/public scope and preserved checkouts/remote heads; commit intended paths, normal separate-branch push, verify remote SHA and clean worktree.
