# Raw Archive History Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans with TDD and a fresh whole-change review. The user explicitly authorized scope selection, implementation, testing and separate branch push.

**Goal:** 보존된 raw numeric tag의 작은 범위에서 관찰 사전과 기간·태그별 원본 projection을 제공합니다.

**Architecture:** 별도 read-only reader와 raw 전용 두 route를 추가합니다. 서버 소유 archive mapping만
사용하고 기존 write/schema/cursor/worker/flow/UI를 보존합니다. 제한 초과는 부분 성공 없이 실패합니다.

**Tech Stack:** 기존 Python3.12/FastAPI/SQLite/pytest/httpx, 새 의존성 없음.

**Spec:** `docs/superpowers/specs/2026-11-09-raw-history-design.md`.

## Global Constraints / Review Focus

- [from,to), offset/소수 초, source/receipt 분리, 원본 정수/null/quality/absent 구분.
- device/run 분리, 동일 순번의 다른 run, 과거 module metadata, run-only gaps의 시간 역추정 금지.
- 후보1024/선택64/tag16/catalog512/payload256KiB/합8MiB/gap1024/SQL budget.
- 손상/스키마/미생성/잠금을 정상 empty나 부분 성공으로 표시하지 않습니다.
- committed WAL 동시 읽기, 원본·첫 receipt·cursor·gap 및 기존 API/복구/모듈 경계 보전.

## Task 1: 제한된 원본 조회와 관찰 태그 사전

**Files:** 새 `src/aegis/raw_history.py`, `tests/test_raw_history.py`, `tests/test_raw_history_api.py`;
수정 `src/aegis/api.py`, README; 새 계약·검증 문서/evidence.

**Interfaces:** `read_raw_archive(path, device_id, run_id, from_utc, to_utc, *, signals=None,
max_batches=64)`는 versioned dict를 반환하고 `RawQueryError(status,code,message)`로 실패합니다.
signals=None은 catalog, 쉼표 문자열은 history입니다. `create_app(..., raw_archive_paths=None)`는
선택 mapping을 복사하고 CLI가 shared/isolated replay 경로를 제공합니다.

- [x] 동일 원격 base의 전체391PASS/1skip와42개 Python 지문 일치, 기존6개 checkout·9개 원격 ref 보전.
- [x] 현재 checkout archive/계약/API baseline121PASS를 확인했습니다.
- [x] core37개/API50개의 미구현 RED, 고유 gap schema1FAIL/잠금1PASS 대조를 확인했습니다.
- [x] reader/route/CLI 연결을 구현해 targeted226PASS와 추가 실제 경계1PASS를 확인했습니다.
- [x] 전체481PASS/1Windows skip, emulator26PASS, 계약 검사 통과. 공개 diff/noreply 및
  기존379개 파일·6개 checkout·9개 ref 보전과 독립 검토를 확인했습니다.
- [x] 별도 branch commit/push/원격 SHA의 실제 결과는 precommit 문서와 분리해 최종 인계에 기록합니다.

## Ledger

Pre-flight: query helper와 route가 공유하는 인터페이스는 위 signature/error/response입니다.
writer/worker/IPC·canonical flow enum에는 새 인터페이스가 없습니다.
Rulings: spec의 두 결정을 그대로 따릅니다. 기존 설계·실행 승인 범위에서 작은 구현을 계속합니다.

Final review: Critical0/Important0/Minor1. 검토자는 기록과 지문을 대조하고 작은 읽기 전용
UTC overflow probe를 실행했으며 전체 suite는 재실행하지 않았습니다.
Final: minor (deferred): 극단 연도/offset의 UTC 정규화 OverflowError가 query422/저장행409 대신
일반500으로 종료될 수 있습니다. fail closed이며 DB 쓰기나 정상 부분 결과는 없고 최종 인계에 명시합니다.
Task1: complete — core/API RED→GREEN, full481PASS/1skip. 실제 Git 결과는 외부 최종 실행 기록입니다.
