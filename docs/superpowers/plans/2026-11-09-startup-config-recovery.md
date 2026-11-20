# Startup Configuration Recovery Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans with TDD and one final independent review. Existing user instruction authorizes implementation and a separate branch push.

**Goal:** 설정 pipe 전송 정지가 parent loop를 막지 않고 소유 자식을 안전하게 회수하도록 합니다.

**Architecture:** Popen/소유 등록, 별도 writer executor의 설정 단계, reader의 요약 단계를 분리합니다.
설정 성공 여부로 kill-before-close와 정상 EOF 정리를 구분하고 모든 I/O future의 실제 회수를 확인합니다.

**Tech Stack:** 기존 Python3.12/asyncio/subprocess/SQLite/pytest. 새 패키지 없음.

**Spec:** `docs/superpowers/specs/2026-11-09-startup-config-recovery-design.md`.

## Global Constraints / Review Focus

- 정확한 원격0db6f5e 위 별도 이력이며 UI·archive·커서·기존 이력을 보존합니다.
- 설정 writer와 정상 reader가 executor 슬롯으로 서로를 굶기지 않아야 합니다.
- pending/실패한 buffered writer가 있는데 stdin.close로 먼저 막히면 안 됩니다.
- 설정 전 frame은 RUNNING이나 deadline 연장의 근거가 아닙니다.
- stop/timeout/cleanup failure에서도 소유 자식과 I/O 완료 전에 새 writer를 열지 않습니다.
- Windows·실장비·Popen OS 호출 정지·막힌 OS thread 종료는 검증 범위를 구분합니다.

## Task 1: 설정 단계 감시와 회수

**Files:** `src/aegis/processes.py`, 새 startup tests/harness, 새 검증 문서/evidence.

**Interfaces:** `_launch(worker, stderr)`는 Popen까지만, `_write_config(worker, process)`는
설정 I/O, `_wait_configuration(writer, started)`는 고정 deadline/stop을 감시합니다.
`_reap(process, reader, writer=None)`는 writer 성공 여부에 따라 회수 순서를 선택합니다.

- [x] 기준 전체344PASS/1Windows 전달 skip, source와 기존 evidence 보존 확인.
- [x] 외부 walltime의 실제 설정 정지 RED와 영구 회귀시험을 작성·확인합니다.
- [x] writer 분리/고정 deadline/kill-before-close/추적·회수 경계만 구현합니다.
- [x] startup stop·finite recovery·peer 저장/원본/커서 보전 GREEN을 확인합니다.
- [x] 전체351PASS/1Windows skip·독립 emulator26PASS·계약PASS 및 독립 검토를 기록합니다.
- [x] 공개 diff·noreply·기존6개 원격 ref/세 checkout 보존을 확인했습니다. 이후 commit/push/SHA는 최종 인계로 별도 기록합니다.

## Ledger

Pre-flight: 기존 frame/Collector/RawArchive 계약은 바꾸지 않습니다. 설정 결과가 reader 시작의
유일한 입력이고, 동일 generation의 writer·reader·process 회수 후에만 재시도합니다.
최종 source/harness hash는 전체 시험 동안 유지됐습니다. Critical/Important0개.
Final minor (deferred): 오류 문구가 configuration writer를 명시하지 않는 진단 문구1건.
Ruling 세 건과 비용은 연결된 설계/보고서에 기록했습니다.
