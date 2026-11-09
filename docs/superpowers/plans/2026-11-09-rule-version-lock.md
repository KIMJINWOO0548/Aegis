# Rule Version Lock Implementation Plan

> **For agentic workers:** Use native execution with TDD and a fresh whole-change review. User authorized conflict checking, reproducible provenance, existing CLI/data preservation and separate branch push.

**Goal:** 선택 기준 파일과 비교해 같은 규칙 ID/버전의 내용 충돌을 거부하고 이전 버전을 보존합니다.

**Architecture:** 새 frozen rule_versions module·stdout builder와 기존 limit_rules의 opt-in 검증 연결.
기존 noflag report·raw reader·원본·flow/IPC/UI는 유지합니다.

**Tech Stack:** 기존 Python3.12/Pydantic/pytest/SQLite, 새 의존성 없음.

**Spec:** `docs/superpowers/specs/2026-11-09-rule-version-lock-design.md`.

## Review Focus

- configId namespace와 동일 ruleId/version 내용 충돌·configVersion 우회·미등록은 archive 전 실패.
- 정규화 snapshot/hash/inner identity·중복/손상·기준 미제공을 정상 검증으로 오인하지 않음.
- 이전 entry/기준 파일·raw/firstreceipt/cursor/gap 보전, unsigned reference의 신뢰 경계.
- 단일 bounded snapshot/256entry/256KiB output+newline·stdout→loader roundtrip·결정성.
- 실제 __main__ 두 CLI 오류 class 호환, noflag 정확 report·empty selection·UNAVAILABLE 유지.

## Task1: Rule version reference and optional verification

**Files:** 새 `src/aegis/rule_versions.py`, `tests/test_rule_versions.py`, `tests/test_rule_versions_cli.py`,
합성 기준 예제·설계/실행/검증 문서; 수정 limit_rules.py opt-in 연결, README/기존 규칙 보고서 상태 안내.

**Interfaces:** spec의 RuleVersionLock/RegisteredRule/RuleVersionError와 load/build/verify/main,
기존 review_archive(...,rule_lock=None). 검증 결과는 ruleVersionVerification에만 추가합니다.

- [x] exactbase4b20573..., 이전 full608PASS/1skip와50개 Python 지문·9개 checkout/12개 ref 보전.
- [x] 기존 숫자/flow/raw baseline217PASS.
- [x] core31/CLI31 예상 RED 확인 후 같은 duplicate fixture 한 건을 제거해 최종 core30개.
- [x] bounded immutable lock/builder·opt-in157PASS, 실제 생성955bytes→loader→2규칙 검증.
- [x] 전체669PASS/1Windows skip, emulator26PASS·계약 검사·새 독립 지적0건,
  보호399개 byte/9개 checkout/12개 ref·noreply 확인. commit 전 공개 diff를 재검사합니다.
- [x] 별도 commit/push/remote SHA의 실제 결과는 이 precommit 문서와 분리해 최종 인계에 기록.

## Ledger

Pre-flight: lock verifier는 config dict를 정규화하고 독립 오류 class를 써 __main__ alias를 피합니다.
Builder는 canonical UTF8 bytes+newline을 확인한 후 stdout에 내므로 자기 loader 한도를 넘기지 않습니다.
Rulings: spec의 opt-in 호환 및 unsigned operator reference 두 결정을 따릅니다.

Task1: complete — core/CLI RED→GREEN157PASS, full669PASS/1skip. 실제 Git 결과는 외부 최종 인계 기록입니다.
Final review: 새 Critical0/Important0/Minor0. 기존 비JSON generator 직접 호출 Minor는 baseline에서
상속한 제한으로 유지하며 실제 JSON CLI 경로에는 영향이 없습니다.
