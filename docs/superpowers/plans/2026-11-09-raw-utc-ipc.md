# Raw UTC Boundary and IPC Verification Plan

> **For agentic workers:** Use TDD and native execution with a fresh whole-change review. User authorized the fix, synthetic IPC validation, minimal repair if needed, testing and separate branch push.

**Goal:** raw UTC 범위 오류를 안전하게 분류하고 실제 모듈 IPC→archive→HTTP 조회를 검증합니다.

**Architecture:** raw_history의 기존 두 오류 경계만 확장합니다. 실제 IPC는 기존 stack으로 검증하고
새 제품 수정은 실패 근거가 있을 때만 합니다. 원본·schema·cursor·한도·UI를 보존합니다.

**Tech Stack:** 기존 Python3.12/FastAPI/SQLite/httpx/pytest/Linux subprocess, 새 의존성 없음.

**Spec:** `docs/superpowers/specs/2026-11-09-raw-utc-ipc-design.md`.

## Review Focus

- from/to와 payload/index/receipt의 UTC underflow/overflow는422/409, no500/no success prefix.
- 유효 year1/year9999·offset은 허용하고 source/receipt 원문·원본 숫자·품질·absent를 보전합니다.
- 실제 worker/IPC/live HTTP 경로이며 mapping-only/fake-frame 시험과 혼동하지 않습니다.
- query 한도와 module restart가 원본/커서/peer 저장을 덮거나 중복시키지 않습니다.
- 소유 프로세스 회수, 기존 계약/한도/UI/원격 이력 보전, Windows·장비 미검증 구분.

## Task1: UTC 오류 수정과 실제 IPC 회귀

**Files:** 수정 `src/aegis/raw_history.py`, README·이전 raw 보고서의 상태 안내; 새
`tests/test_raw_history_utc.py`, `tests/test_raw_history_ipc.py`, 설계/실행/검증 문서/evidence.

**Interfaces:** 기존 raw reader/API signature·response·limits는 그대로입니다.
`_query`는 UTC overflow도 INVALID_QUERY, `_batch`는 RAW_ARCHIVE_INVALID로 취급합니다.

- [x] 정확한 원격 base와 이전 full481PASS/1skip의45개 Python 지문 일치·7개 checkout·10개 ref 확인.
- [x] 두 극단 offset에서 query/payload OverflowError를 직접 재현했습니다.
- [x] baseline189PASS, UTC RED22실패/8정상 대조 후 최소2줄 수정, 선택219PASS.
- [x] 실제 synthetic IPC/API/storage1PASS(19.313초), 제품 연결 수정 없이 통합 대조군으로 기록.
- [x] 전체512PASS/1Windows skip, emulator26PASS·계약 검사·보호385개 byte 보전·검토0건·noreply 확인.
- [x] 별도 commit/push/remote SHA의 실제 결과는 이 precommit 문서와 분리해 최종 인계에 기록.

## Ledger

Pre-flight: UTC tests와 IPC tests는 기존 API/reader를 소비하며 worker 계약을 새로 만들지 않습니다.
Ruling: spec의 raw 검증 경계 한정 결정을 따릅니다. IPC가 통과하면 추가 제품 수정을 하지 않습니다.

Task1: complete — UTC RED→GREEN, full512PASS/1skip, 실제 IPC는 기존 연결의 검증 대조군입니다.
Final review: Critical0/Important0/Minor0;14개 독립 작은 probe. 전체 suite는 controller가 실행했습니다.
첫 IPC 시험의 조건식 SyntaxError는 서비스 시작 전 수집 단계에서 고쳤고 실제 통합 PASS와 구분했습니다.
