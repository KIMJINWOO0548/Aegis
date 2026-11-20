# 숫자 규칙 버전의 선택적 기준 검증

기준은 `4b20573aa10c16ac6a9147a1fd0c846ba77dcad3`입니다. 사용자는 같은 규칙 ID/버전의 내용
충돌을 거부/표시하고 버전·근거를 재현 가능하게 보존하는 작은 후속 구현을 승인했습니다.
기존 CLI·raw 원본·불량/오래된 자료 UNAVAILABLE·flow/IPC/UI를 보전합니다.

## 선택과 신뢰 경계

기존 `aegis.limit_rules`에 선택 `--rule-lock FILE`을 추가합니다. 없을 때 기존 report와 동작은
정확히 유지하고 검증 표시도 추가하지 않습니다. 지정하면 operator가 신뢰 기준으로 보관한
파일과 현재 설정 전체의 규칙을 archive 접근 전에 비교합니다. 틀리면 정상 결과/부분 stdout은 없습니다.
전역 DB/자동 등록·덮어쓰기 대신 read-only 기준 파일과 stdout 전용 생성 명령을 선택합니다.

키는 `(configId,ruleId,ruleVersion)`입니다. configVersion을 올려도 같은 ruleVersion의 내용 변경을
우회하지 못합니다. 정규화된 유효 rule snapshot이 같으면 허용, 다르면 RULE_VERSION_CONFLICT,
등록되지 않은 버전은 RULE_VERSION_UNREGISTERED입니다. 조회 대상 밖 device 규칙도 검증합니다.

기준 파일은 unsigned operator-owned reference입니다. VERIFIED는 지정 기준과의 일치이며
승인/서명 인증·전역 불변 registry·기준 자체의 위변조 방지는 아닙니다. 기준을 다른 파일로 바꾸거나
이전 기준 없이 새로 만드는 것은 별도 trust reset입니다. 정확한 검증 재현에는 그 기준 artifact도
보관해야 하며 report의 hash만으로 잃어버린 파일을 복원할 수 없습니다.

## 기준 계약과 생성

새 module `aegis.rule_versions`의 frozen `RuleVersionLock`은 다음 필드를 갖습니다.

- schemaVersion: numeric-rule-lock-1
- ruleSchemaVersion: numeric-limit-rules-1
- evaluationSchemaVersion: numeric-limit-evaluation-1
- configId
- rules:1..256개 `RegisteredRule{ruleId,ruleVersion,ruleSha256,ruleSnapshot}`

ruleSnapshot은 기존 LimitRule 모델로 검증한 전체 유효 snapshot이며 inner ID/version은 outer와
같아야 합니다. hash는 기존 canonical JSON SHA256, 소문자64hex입니다. duplicate JSON key,
중복 등록 key(내용이 같아도), unknown field/schema·snapshot/hash/ID 불일치는 거부합니다.
여러 버전의 같은 ruleId는 허용하고 tuple은 key 순서로 정렬·frozen합니다. 공백/key 및 entry 순서는
정규화된 기준 hash에 영향을 주지 않습니다.

파일을 한 번만 UTF8(선택 BOM), regular file256KiB까지 읽어 모델/검증/hash를 같은 snapshot에서
구합니다. canonical UTF8 출력+마지막 newline도256KiB 이하입니다. 최대 entry와 bytes를 각각
확인하며 자동 삭제/eviction은 없습니다. 생성한 stdout은 같은 loader로 다시 읽을 수 있어야 합니다.

`python -m aegis.rule_versions --config FILE [--previous-lock FILE]`은 JSON을 stdout에만 냅니다.
이전 기준을 주면 같은 namespace의 모든 과거 entry를 보존하고 새 ID/version만 추가합니다.
같은 내용 재사용은 안정적이고 같은 key의 다른 내용은 거부합니다. 현재 설정에서 제거된 과거
규칙도 남깁니다. 파일 쓰기/덮어쓰기·시각/무작위 ID 삽입은 하지 않아 같은 입력은 같은 출력입니다.

## 기존 평가와 연결

기존 `review_archive(...,rule_lock=None)` keyword를 추가합니다. 생략 시 기존 signature 호출·
report 필드는 그대로입니다. 기준이 있으면 현재 config 전체를 검증한 뒤 기존 reader와 평가를 호출합니다.
main의 `--rule-lock`은 단일 bounded load 결과만 넘기고 hash를 위해 파일을 다시 읽지 않습니다.

검증 report에만 `ruleVersionVerification`을 추가합니다: schemaVersion=numeric-rule-lock-check-1,
status=VERIFIED, lockSha256, configId, lockRuleCount, checkedRuleCount,
checkedRules[{ruleId,ruleVersion,ruleSha256}]. 기존 configSnapshot·ruleSnapshot·원본 projection,
평가 기준시각·UNAVAILABLE 정책은 유지합니다. 빈 선택에도 이 metadata와 configSnapshot을 남깁니다.

오류는 새 `RuleVersionError(code,message)`입니다. INVALID_RULE_LOCK(미생성/형식/무결성),
RULE_LOCK_LIMIT(bytes/entry), RULE_LOCK_SCOPE_MISMATCH, RULE_VERSION_UNREGISTERED,
RULE_VERSION_CONFLICT를 사용합니다. 두 CLI 모두 고정 stderr JSON{status:UNAVAILABLE,code,message},
exit2, empty stdout입니다. config/기준 경로·원본 오류값을 echo하지 않습니다. 기존 config/query 오류도 유지합니다.

새 함수는 `build_rule_lock(config,previous=None)`, `load_rule_lock(path)`,
`verify_rule_lock(config,lock)`, builder `main(argv=None)`입니다. config는 기존 model 또는 JSON dict를
정규화합니다. limit_rules의 __main__/named class 중복을 피하려고 검증 경계에는 config dict와 독립
RuleVersionError를 사용하고 실제 두 module CLI subprocess를 시험합니다.

## 보전과 검증

같은 key의 bound/unit/target/maxAge 변경, configVersion 우회, missing/duplicate/tampered reference,
새 버전 등록 전 거부/추가 후 허용, 삭제된 과거 entry 보전, 256/257entry·bytes/roundtrip·정규화,
빈 조회/전 device 검증·archive 미접근·원본/receipt/cursor/gap/기준 파일 불변, 같은 기준·원본·
평가시각의 결정성, stale/BAD/MISSING UNAVAILABLE와 noflag 정확한 호환을 시험합니다.
기존 비JSON generator 직접 호출 Minor는 별도 사항으로 남기며 JSON CLI 경로는 영향을 받지 않습니다.

Ruling: 기준 검증은 opt-in입니다. 기존 호출 호환 비용으로 옵션 없는 실행은 버전 검증이 아닙니다.
Ruling: unsigned local reference를 사용합니다. 파일의 신뢰/보관은 operator 책임이며 전역·암호학적
승인/변조 방지·누락 artifact 복원을 제공하지 않습니다. 실제 경보·장비·Windows/UI 실증은 하지 않습니다.
