# Independent Adapter Input Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Validate an independent nullable-metadata input contract through a built-in synthetic replay adapter without changing legacy ingestion or storage.

**Architecture:** One focused new Python module owns immutable schema validation, atomic in-memory admission, the allowlisted synthetic reader and a summary-only CLI. Existing source/config/archive/IPC/API/UI files remain untouched. New tests independently exercise library and actual CLI behavior.

**Tech Stack:** Existing Python3.12, Pydantic2, asyncio and pytest; no new dependency.

**Spec:** `docs/superpowers/specs/2026-11-09-adapter-input-design.md`

## Global Constraints

- Exact adapter-input-1 / synthetic-adapter-fixture-1 / adapterVersion1.0; explicit SYNTHETIC replay/read-only binding.
- Nullable original metadata and PROVIDED/NOT_PROVIDED statuses; no fabricated time, sequence or run.
- 1MiB fixture,64KiB page,64 records/page,512 signals/record,1024 admitted records,64 accepted pages;1–3 attempts and1–10000ms cooperative timeout.
- No real data/company libraries/dynamic plugins/URLs/legacy archive bridge/UI/force push or restricted preservation-file access.

## Review Focus

- Changed source payload under a repeated delivery identity must not partially admit other records.
- Caller mutation and model normalization must not alter retained source evidence or first receipt.
- Cancellation and external task cancellation must preserve the admission cursor and leave no synthetic read task running.
- Empty/error/missing original metadata must not become persisted/real/realtime claims.
- FIFO/duplicate JSON keys/NaN/unsupported versions must fail before adapter execution or partial stdout.

---

### Task 1: Contract, admission and built-in reader

**Files:** Create `src/aegis/adapter_input.py`; test `tests/test_adapter_input.py` and `tests/test_adapter_input_cli.py`.

**Interfaces:** `validate_page(payload: bytes)`, `AdapterAdmission(adapter_id,adapter_version,stream_id).admit(payload,received_at)`, `SyntheticReplayAdapter(fixture)`, `AdapterSession(adapter,timeout_ms=100,max_attempts=2).poll_once()` and `python -m aegis.adapter_input --fixture FILE`.

- [ ] Write failing core tests for null/provided metadata, original evidence, descriptor pinning, duplicate/conflicting pages/records, capacity/token loops, first receipt and whole-page atomicity.
- [ ] Write failing actual CLI tests for finite replay, no-data, timeout, version/origin/metadata failure, private errors, regular-file bounds and preservation of a separate legacy archive.
- [ ] Run both RED suites and record expected missing-module failures.
- [ ] Implement frozen Pydantic schemas and bounded duplicate-key/nonfinite-rejecting JSON parse; retain canonical original mappings separately from parsed models.
- [ ] Implement atomic in-memory admission and a stateless, immutable fixture reader; finite cooperative timeout/retry wraps only the built-in class.
- [ ] Implement summary-only CLI with NOT_PERSISTED/DISABLED and bounded nonblocking no-follow file loading.
- [ ] Run focused tests until green; fix reproduced defects with regression cases before proceeding.

### Task 2: Verification, operator contract and preservation

**Files:** Create `examples/adapter-input.synthetic.json`, `docs/ADAPTER_INPUT_20261109.md`, `docs/evidence/adapter-input-20261109.json`; modify only `README.md` for discovery.

- [ ] Add a small synthetic nullable/provided replay example and verify it through the actual CLI.
- [ ] Document descriptor/presence/token/evidence/timeout semantics and archive-v2/IPC migration boundaries.
- [ ] Freeze Python source/tests; run full pytest and restore generated tracked evidence exactly.
- [ ] Run26 independent emulator tests and contract/v3.4 checks.
- [ ] Obtain fresh whole-change review on the most capable model; fix material findings with regressions and rerun required checks.
- [ ] Match tested source hashes, scan added content for secrets/PII, verify noreply owner, preserve all old checkouts/remote heads, commit only intended files and normal-push the separate branch.
- [ ] Verify remote SHA, final clean worktree and public scope; report local tests separately from unverified CI/devices/Windows/operations.
