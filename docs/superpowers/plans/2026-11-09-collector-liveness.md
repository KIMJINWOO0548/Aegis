# Collector Liveness Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans for this bounded task with TDD and final independent review. Existing user instruction authorizes design, implementation, tests and a separate branch push.

**Goal:** 내부 수집 종료와 무응답 worker를 모듈 단위로 감지·유한 복구합니다.

**Architecture:** Collector의 task/active-poll 생존 확인을 module_worker 요약 발행 앞에 연결하고,
ProcessCollector가 유효 frame deadline과 유한 복구·회수 경계를 소유합니다.

**Tech Stack:** 기존 Python3.12, asyncio, subprocess, SQLite, pytest. 새 의존성 없음.

**Spec:** `docs/superpowers/specs/2026-11-09-collector-liveness-design.md`.

## Global Constraints

- 위 원격 HEAD를 정확히 기준으로 기존 숫자 무결성 커밋을 보존합니다.
- UI·그래픽·원본·DB/WAL·커서 및 다른 모듈을 덮거나 삭제하지 않습니다.
- 무데이터를 재시작 근거로 사용하지 않으며 정상 frame으로 예산을 reset하지 않습니다.
- 실제 장비/Windows 서비스 성공을 주장하지 않습니다. 접근 거부 보존본은 재시도하지 않습니다.
- 공개 저장소의 새 diff에 비밀·개인정보·로그/DB/바이너리를 넣지 않고 검증된 noreply를 사용합니다.

## Review Focus

- 60초 idle과 빈/중복/오류 응답은 task stall이 아닙니다.
- 부분 출력이 timeout을 무한 연장하지 않아야 합니다.
- 구 reader/프로세스 회수 전 새 writer를 열지 않아야 합니다.
- 정상 frame 후 반복 종료도 복구 예산을 우회하지 않아야 합니다.
- 정상 stop은 실패 재시작을 유발하지 않아야 합니다.

## Task 1: 생존 감시와 모듈 복구

**Files:** `src/aegis/collector.py`, `src/aegis/module_worker.py`, `src/aegis/processes.py`,
새 `tests/test_collector_liveness.py`, `tests/test_worker_recovery.py` 및 새 검증 문서/evidence.

**Interfaces:** `Collector.ensure_live()`는 종료/정지 코드를 가진 RuntimeError를 발생시키고,
module_worker가 이를 발행 전에 호출합니다. ProcessCollector의 선택 timing/budget 인자는
기존 호출과 호환되며 FAILED는 기존 ingestion workerState와 connection reasonCode로 노출합니다.

- [x] 기준 전체 Python 시험:307 PASS/1 skip, 기존 generated evidence 바이트 복원.
- [x] task 종료/active stall·정상 idle, worker 무응답/부분 줄·유한 복구 시험을 먼저 작성합니다.
- [x] 대상 시험의 제품 결함/미구현으로 인한 RED를 확인합니다.
- [x] 생존 확인과 parent watchdog·유한 예산·회수 경계만 구현합니다.
- [x] 대상 GREEN83과 실제 두 모듈 정지/복구·원본/커서/peer 보존을 확인합니다.
- [x] 전체 Python344PASS/1skip, 독립 에뮬레이터26PASS, 계약 검사PASS와 미실행을 기록합니다.
- [x] 독립 검토: 확정된 중요 지적0개. 동기 설정 전송 판단유보를 설계/보고서의 범위·비용으로 기록했습니다.
- [x] 공개 diff·noreply 작성자·기존5개 ref/원본 checkout 보존과 새 target 부재를 확인했습니다.

Commit/push와 서버 SHA 검증은 이 증거를 담은 source commit 이후 수행하며, 실제 SHA와
보존 결과는 최종 인계에 기록합니다. 이 계획 파일은 커밋 이전 검증 시점의 기록입니다.

## Execution ledger

Pre-flight: Collector.ensure_live를 module_worker가 소비하고, 부모는 기존 frame만 소비합니다.
frame schema·archive transaction·UI는 변경하지 않습니다. 위 설계의 Ruling 두 건을 적용합니다.
공유 source와 기존 evidence의 변경을 보호하는 별도 worktree에서 실행합니다.
최종 검증:344PASS/1Windows 전달 skip, emulator26PASS, 계약 정적PASS, protected223바이트동일.
Final Ruling: 동기 설정 전달은 이번 post-configuration 감시 범위 밖입니다. 시작 pipe 용량 장애의
host/peer 응답 한계는 후속 검증 대상이며 설계에 비용을 명시했습니다.
