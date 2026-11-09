# Numeric Limit Rules Implementation Plan

> **For agentic workers:** Use native execution with TDD and a fresh whole-change review. User authorized safe scope selection, implementation, synthetic tests and separate branch push.

**Goal:** 원본 tag projection에 적용하는 설정형 상·하한 규칙의 독립 read-only 분석을 제공합니다.

**Architecture:** 새 limit_rules module의 frozen config·stateless engine·CLI가 기존 raw reader를
소비합니다. 기존 flow/API/collector/archive/worker/IPC/UI는 변경하지 않습니다.

**Tech Stack:** 기존 Python3.12/Pydantic/SQLite/pytest, 새 의존성 없음.

**Spec:** `docs/superpowers/specs/2026-11-09-numeric-limits-design.md`.

## Review Focus

- 포함 경계·단측/동일 bound·음수·숫자 정밀도와 strict 설정 검증.
- missing/not-projected/품질/null/unit/time/target 문제는 WITHIN_LIMITS 대신 UNAVAILABLE.
- source age·receipt·미래/UTC overflow·maxAge equality/1us초과, 원본 날짜 대체 금지.
- rule/config snapshot+digest·version 변경·입력/이전 결과 불변, hash는 projection 범위만.
- 빈 선택/적용 규칙0은 정상 아님, 기존 reader 제한·원본/커서·flow/IPC/UI 보전.

## Task1: 설정형 숫자 상·하한과 archive CLI

**Files:** 새 `src/aegis/limit_rules.py`, `tests/test_limit_rules.py`, `tests/test_limit_rules_cli.py`,
합성 example config, 설계/실행/검증 문서/evidence; README 링크만 수정.

**Interfaces:** `LimitRule`, `LimitRuleConfig`, `LimitRuleError(code,message)`, `load_rule_config(path)`,
`evaluate_rule(rule,batch,*,evaluated_at)`, `review_archive(config,archive,device_id,run_id,from_utc,to_utc,
*,max_batches=64,evaluated_at=None)`, `main(argv=None)`입니다. 상세 계약은 spec에 있습니다.

- [x] exactbase6b07c83..., 이전full512PASS/1skip와47개 Python 지문·기존8개 checkout/11개 ref 확인.
- [x] 현재 checkout의 기존 flow/raw/API baseline240PASS.
- [x] core67/CLI29 미구현 예상 RED, loader4개는 긴 pytest parameter ID를 줄인 후 같은 RED 확인.
- [x] engine/loader/CLI의96PASS와 원본·경계·오류·버전·최대1024결과 확인.
- [x] 전체608PASS/1Windows skip, emulator26PASS·계약 검사, 독립 검토 Critical0/Important0/Minor1,
  보호393개 byte/8개 checkout/11개 ref 및 noreply 확인. 실제 commit 전 공개 변경분을 다시 검사합니다.
- [x] 별도 commit/push/원격 SHA의 실제 결과는 precommit 문서와 분리해 최종 인계로 남깁니다.

## Ledger

Pre-flight: CLI는 기존 read_raw_archive signature/result를 소비하며 raw/flow/IPC의 새 인터페이스는 없습니다.
Rulings: spec의 offline/stateless 및 digest(버전 registry 대신) 두 결정을 따릅니다.

Spec clarification: 빈 결과에도 전체 configSnapshot을 남기고 projection hash는 파생 age/평가시각을 제외합니다.
Task1: complete — core/CLI RED→GREEN96PASS, full608PASS/1skip. Git 실제 결과는 외부 최종 인계 기록입니다.
Final: minor (deferred): 직접 library 호출에서 비JSON iterator/generator signals가 소비되어 TypeError가
날 수 있습니다. 지원 JSON dict/list projection과 archive CLI 정상 경로에는 영향이 없으며 인계에 명시합니다.
