# Storage Failure Integrity Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans with TDD and final independent review. Existing user instruction authorizes implementation and a separate branch push.

**Goal:** runtime 저장 용량 실패의 원본/커서 보전을 검증하고 저장 사유와 연결 사유를 구분합니다.

**Architecture:** Collector만 typed 원인 분류와 (state, reason) event 전환을 바꿉니다.
RawArchive의 원자적 transaction/rollback·모듈 supervisor·기존 API/화면은 유지합니다.

**Tech Stack:** 기존 Python3.12/SQLite/pytest/httpx. 새 의존성 없음.

**Spec:** `docs/superpowers/specs/2026-11-09-storage-failure-integrity-design.md`.

## Global Constraints / Review Focus

- 자동 삭제·커서 reset/전진·DB/WAL 재작성·UI 변경·접근 제한 자료 우회 금지.
- 에러 메시지만 full인 generic 오류를 용량 실패로 오인하지 않습니다.
- 같은 DISCONNECTED 안에서도 네트워크/저장 사유 변화가 누락되지 않아야 합니다.
- 같은 실패 반복은 로그를 채우지 않으며 로그 실패가 raw 실패를 숨기지 않아야 합니다.
- 실패 page의 일부 row/gap/cursor도 공개·커밋되면 안 됩니다. 기존 peer 저장은 유지합니다.

## Task 1: 저장 사유와 이벤트 경계

**Files:** `src/aegis/collector.py`, 새 `tests/test_storage_diagnostics.py`,
`tests/test_storage_failure.py`, 설계·검증 문서/evidence.

**Interfaces:** `_storage_failure_reason(error)`가 고정 ARCHIVE_FULL/ARCHIVE_UNAVAILABLE를 반환합니다.
poll_once는 기존 state/shape를 유지하고 원인 pair 변경에 저장/연결 event를 기록합니다.

- [x] 기준 전체351PASS/1Windows skip, source/evidence 바이트 보존.
- [x] 실제 SQLite FULL 사유 RED1, diagnostics RED5/기존 대조6PASS 확인.
- [x] typed 분류와 원인 pair 전환만 구현합니다.
- [x] 실제 SQLite 원본/received/cursor/gap 보존·복구 retry/idempotence·peer 진행과 대상53/별도3 GREEN을 확인합니다.
- [x] 전체365PASS/1Windows skip, 독립 emulator26PASS, 계약PASS 및 독립 검토 지적0개를 기록했습니다.
- [x] 공개 diff·noreply·기존 원격7개 ref/4개 checkout 보존을 확인했습니다. commit/push/SHA는 최종 인계로 기록합니다.

## Ledger

Pre-flight: WorkerFrame와 Gateway는 bounded reasonCode를 이미 허용합니다. shape/enum 변경이
필요하지 않으며 RawArchive transaction 계약을 다시 구현하지 않습니다. 위 Ruling 두 건 적용.
전체 실행 중 source/test hash는 유지됐으며 기존 evidence는 실행 후 원래 바이트로 복원됐습니다.
