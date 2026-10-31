# Signal evidence implementation plan

> **For agentic workers:** Use Superpowers TDD and verification-before-completion. Existing user authorization is to continue development; no Git repository or remote actions.

**Goal:** Retain raw evidence for short valid changes while exposing a separate conditioned trend and explicit uncertainty.

**Spec:** [signal-evidence-design](../specs/2026-10-20-signal-evidence-design.md)

**Architecture:** An evaluator-owned bounded FlowReviewMonitor consumes validated public observations before the 1Hz projection. Its strict Pydantic summary crosses the existing module pipe and a separate authenticated API endpoint. No raw mutation or EWMA behavior changes.

**Tech stack:** Existing Python 3.12, Pydantic, FastAPI, stdlib statistics; no new dependency.

## Tasks and review focus

- [x] Write failing unit cases for 100ms/one-sample pulses, identical-input/different-cause ambiguity, sustained steps, noise trend error, slow drift, resets, bounded snapshots and no raw mutation. Implement `src/aegis/signal_review.py`, integrate `src/aegis/evaluation.py` before raw-to-1Hz projection.
- [x] Write failing collector/API/module summary tests. Connect `signal_review()` in collector/supervisor, typed WorkerFrame field, module frame builder and API. Reject extra fields, invalid numeric types and stale current analysis. Preserve existing diagnostics/C# contracts.
- [x] Add seeded offline probe with clean truth held outside public inputs, explicit candidate recall/false candidates/retained peaks/trend error and non-universal limitations. Save evidence/report, document API usage and priority change in portfolio/ledger.
- [ ] Run relevant tests, then shared verification, and independent code review. Fix actionable findings and record actual results. Do not run throughput benchmarks concurrently. Do not claim hardware separation/calibration or remote CI.

Edge cases requiring coverage: context contamination by current sample; frozen baseline while sustained event remains open; setpoint ramps without reset every tick; invalid and missing inputs interrupt without reporting recovery; event eviction visible; sparse/high-rate capped contexts expose warming; related sensor concurrence never changes unresolved cause; same raw sequence across process boundary remains attributable.

Verification checkpoint: independent design review and two fresh implementation/claim reviews completed; found and fixed sustained-return false excursion via red/green regression. Full verifier recorded280 Python passes/6process failures, including all38new cases passing; emulator26/.NET74/Release/contract checks green. Final checkbox remains open until process integration failures are resolved, not waived by narrower passes. Evidence: `docs/evidence/signal-review-checks/`.
