# Source Context Contract Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans with TDD and final independent review. Existing user request authorizes the safe first implementation and a separate branch push.

**Goal:** 출처/수집 방식/접근 방식을 분리한 current-configuration 선언을 호환 가능한 opt-in으로 제공합니다.

**Architecture:** 기존 health route에 optional boolean만 추가하고 선택 시 whitelisted sourceContext를 구성합니다.
current input/DB/IPC/native/UI 계약과 기본 health 응답은 유지합니다.

**Tech Stack:** 기존 Python3.12/FastAPI/pytest/httpx. 새 의존성 없음.

**Spec:** `docs/superpowers/specs/2026-11-09-source-context-design.md`.

## Global Constraints / Review Focus

- 기본 health는 strict client를 위해 추가 null조차 없이 동일해야 합니다.
- 혼합 source별 method, fixture의 미노출 sequence, source READ_ONLY/로컬 archive 쓰기 구분.
- current declaration을 원본 row/historical provenance나 실제 하드웨어·hard realtime 실증으로 승격하지 않습니다.
- 필수 source time/sequence를 생성/대체하지 않고 REAL·누락 입력 거부와 원본/receipt/cursor를 보존합니다.
- UI·generated contract·DB·실제 장비 연결·접근 제한 보존본은 변경/우회하지 않습니다.

## Task 1: Opt-in sourceContext와 이행 계획

**Files:** `src/aegis/api.py`, 새 `tests/test_source_context_api.py`, `tests/test_source_integrity.py`,
설계·계약 이행·검증 문서/evidence.

**Interfaces:** `_source_context(collector)`는 source-context-1의 고정 vocabulary/current source 선언을 반환합니다.
health(includeSourceContext=False)는 요청한 경우에만 sourceContext key를 추가합니다.

- [x] 직전 동일 commit의365PASS/1skip 및 모든 tested source byte 일치를 재사용하고 API/contract91PASS로 새 checkout을 확인했습니다.
- [x] 기존 원본 필드/출처 거부·8/512tag·중복/복구·역사 시각 대조군16PASS를 확인했습니다.
- [x] opt-in/혼합method/신선자료·query/OpenAPI RED6실패/4대조 통과 후 최소 metadata를 구현했습니다.
- [x] 기본 wire 정확 보전/보안 경계/신규 opt-in GREEN117개와 단계별 migration/검증 계획을 기록했습니다.
- [x] 전체391PASS/1skip·독립 emulator26PASS·계약 검사 통과, 독립 검토 Critical/Important/Minor0건입니다.
- [x] 공개 diff·noreply·기존5개 checkout/원격8개 ref를 확인했습니다. commit/push/SHA의 실제
  결과는 이 precommit 문서와 분리해 최종 인계 및 외부 실행 기록에 남깁니다.

## Ledger

Pre-flight: current SourceConfig/packet/Observation/RawArchive/WorkerFrame와 C# API 소비자는 그대로입니다.
기존 strict health consumer는 opt-in을 요청하지 않으며 새 parser만 versioned context를 읽습니다.

Verification: 테스트한 Python source/test42개 지문 일치, 기존 보호 대상373개 파일 byte 동일,
기존6개 generated evidence byte 복원. 독립 검토는 비교/기록 검토이며 시험을 별도 재실행하지 않았습니다.
Ruling: opt-in/strict consumer 보전 및 현재 synthetic envelope 유지의 비용은 spec/계약 문서에 기록했습니다.
