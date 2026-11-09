# 숫자 규칙 버전 기준 검증과 재현

기준 원격은 `improve/numeric-limit-rules-20261109`, commit은
`4b20573aa10c16ac6a9147a1fd0c846ba77dcad3`입니다. 이전 단계의 digest 기록에 선택적
기준 비교를 더했습니다. 기존 접근 제한 보존본이나 `5075ccd`를 복구·대체한 이력이 아닙니다.

## 보완한 동작

기존 결과는 같은 ruleId/version의 내용 변경을 hash로 식별했지만 거부하지 않았습니다.
이제 operator가 보관하는 rule-lock을 명시하면 **같은 configId·ruleId·ruleVersion**에 다른 유효
내용이 있는지 비교합니다. 상·하한/unit/target/maxAge 변경은 configVersion만 올려도 충돌입니다.
미등록 새 ruleVersion도 자동 승인하지 않습니다. 현재 설정 전체를 검사하므로 조회 대상 밖
device의 규칙이나 빈 archive 선택에서도 검증을 건너뛰지 않습니다.

`--rule-lock` 없는 기존 명령·report/판정/오류는 그대로입니다. 이 경우 기준 검증을 수행했다고
표시하지 않습니다. 옵션을 주면 일치할 때만 `ruleVersionVerification`을 추가합니다. 실패하면
archive 읽기 전에 고정 오류로 끝나며 정상/부분 stdout은 없습니다.

## 생성과 버전 추가

새 명령은 완성된 UTF8/LF JSON을 stdout에만 내고 파일을 쓰거나 덮어쓰지 않습니다.
operator가 성공한 결과를 **새 파일**로 보관합니다. 기존 설정·archive·기준 파일 위로 shell redirect하지 마세요.

~~~text
python -m aegis.rule_versions --config rules.v1.json > rules.v1.lock.json

python -m aegis.limit_rules --config rules.v1.json --rule-lock rules.v1.lock.json \
  --archive raw.sqlite3 --device LIMIT-SYNTHETIC-01 \
  --run-id 11111111-1111-4111-8111-111111111111 \
  --from-utc 2026-11-09T12:00:00Z --to-utc 2026-11-09T12:00:01Z \
  --evaluated-at-utc 2026-11-09T12:00:01Z

python -m aegis.rule_versions --config rules.v2.json \
  --previous-lock rules.v1.lock.json > rules.v2.lock.json
~~~

v2는 ruleVersion도 명시적으로 바꿉니다. 같은 key에 다른 내용을 넣으면 생성기와 검증 명령 모두
RULE_VERSION_CONFLICT입니다. 새 버전은 이전 기준으로 평가할 때 RULE_VERSION_UNREGISTERED이며,
명시적으로 추가한 새 기준에서 허용됩니다. 이전 기준의 모든 entry와 snapshot은 남습니다.
현재 설정에서 삭제된 과거 규칙도 보존하고, 같은 내용 재등록은 안정적입니다. 시각·무작위 ID를
삽입하지 않아 같은 입력은 같은 stdout을 냅니다. 기존 파일은 바뀌지 않습니다.

[합성 기준 예제](../examples/numeric-rule-lock.synthetic.json)는 기존
[합성 숫자 규칙](../examples/numeric-limit-rules.synthetic.json)에서 실제 module CLI로 만들고
loader→검증을 다시 통과시킨 두 규칙입니다. 실제 장비 보정/승인의 예제가 아닙니다.

## 파일 계약·한도

`numeric-rule-lock-1`은 ruleSchemaVersion=numeric-limit-rules-1,
evaluationSchemaVersion=numeric-limit-evaluation-1, configId와1..256개의 rules를 가집니다.
각 entry는 ruleId/ruleVersion/ruleSha256 및 기존 LimitRule로 검증한 전체 ruleSnapshot입니다.
inner ID/version과 outer 값이 같고 canonical snapshot SHA256가 일치해야 합니다.

중복 JSON key·등록 key(같은 내용도), unknown field/schema, hash/ID/버전 부정합은 거부합니다.
여러 버전의 같은 ID는 허용합니다. frozen tuple을 key 순서로 정렬하므로 공백·JSON key/entry 순서와
정규화 후 snapshot이 같은 표기(예:10/10.0)는 canonical hash에 영향을 주지 않습니다.
정규화 snapshot 자체가 다르면 별도 내용으로 판정합니다.
configId는 namespace이며 다른 configId의 기준을 재사용하면 RULE_LOCK_SCOPE_MISMATCH입니다.

입력 regular file은 UTF8(선택 BOM) **256KiB**, entry는 **256개**까지입니다. canonical 출력 bytes와
마지막 LF도256KiB 이하인지 먼저 확인해, 자신의 loader로 읽지 못하는 결과를 출력하지 않습니다.
내용이 긴256개는 byte 제한에 먼저 걸릴 수 있습니다. raw 입력과 정규화 크기를 각각 확인합니다.
자동 삭제/eviction은 없습니다. 기준 파일을 한 번 제한해 읽고 frozen snapshot에서 검증/hash를
구하며 hash를 만들려고 경로를 다시 읽지 않습니다.

## 결과와 신뢰 경계

일치한 report의 ruleVersionVerification은 numeric-rule-lock-check-1, VERIFIED,
lockSha256/configId/lockRuleCount/checkedRuleCount와 검사한 ruleId/version/hash를 담습니다.
hash는 정규화 기준 전체에 적용하고, report는 전체 과거 기준을 복제하지 않습니다.
기존 configSnapshot·각 ruleSnapshot·원본 projection·source/firstreceipt·기준시각은 그대로입니다.
빈 선택에도 configSnapshot과 기준 검증 metadata가 남고, 전체 NORMAL/PASS를 만들지 않습니다.

같은 원본9에 v1 upper10이면 WITHIN_LIMITS, v2 upper8이면 ABOVE_UPPER입니다. 이전 버전을
확장된 기준에서 다시 선택하면 예전 결과·원본 projection hash·firstreceipt가 같습니다. 같은
기준·설정·원본·평가시각을 주면 report도 같습니다. BAD/MISSING/STALE·태그 부재·오래된 source와
단위/시각 불량은 기존대로 UNAVAILABLE이고, 새 검증이 이를 정상으로 승격하지 않습니다.

**VERIFIED는 operator가 지정한 unsigned 기준과의 일치**입니다. 서명/승인 인증·기준 자체의
위변조 방지·전역 불변 registry가 아닙니다. 기준의 snapshot과 hash를 함께 바꾸거나 이전 기준 없이
새 기준을 만드는 행위는 새로운 trust baseline입니다. operator가 신뢰 파일과 그 hash를 보관해야 합니다.
정확한 검증 재현에는 해당 artifact 및 평가 계약/코드 버전도 필요하며, report의 SHA만으로 잃어버린
기준 파일을 복원할 수 없습니다. 기존 snapshot/근거로 평가를 재현하는 것과 기준 검증 재현을 구분합니다.

## 오류와 호환

두 CLI 오류는 고정 stderr JSON `{status:UNAVAILABLE,code,message}`, exit2와 빈 stdout입니다.
원본 설정/기준 내용·경로·오류 값을 echo하지 않습니다. INVALID_RULE_LOCK(형식/무결성/미생성),
RULE_LOCK_LIMIT(bytes/entry), RULE_LOCK_SCOPE_MISMATCH, RULE_VERSION_UNREGISTERED,
RULE_VERSION_CONFLICT이며 기존 config/query 오류도 유지합니다. 실제 두 `python -m` 경로를
시험해 __main__/named model·오류 class의 중복도 확인했습니다.

기존 read-only reader의 후보1,024/8MiB·선택64·tag16·기간/SQL 제한, sourceCoverage=NOT_ASSERTED,
run sequence gap은 그대로입니다. 새 코드는 기준 JSON을 읽고 stdout을 생성할 뿐 raw archive를
쓰기/삭제하지 않습니다. 기존 flow/API/collector/raw writer/schema/cursor/worker/IPC/simulator,
canonical YAML·생성/native·UI는 바꾸지 않습니다. 실제 경보 자동 연동·장비 제어는 추가하지 않았습니다.

## 검증

최종 전체 Python은 **669통과·Windows 전용1건너뜀**(670개,runner171.904초)입니다.
독립 emulator26개와 생성 계약/v3.4 검사가 통과했습니다. 독립 검토에서 새 Critical/Important/Minor0건이며
검토자는 전체 기록/지문·실제 예제 생성/재검증·작은 Unicode 부정 입력 probe를 확인했고 suite는
별도 재실행하지 않았습니다. 기존 보호399개 파일은 byte 동일, 이전9개 checkout·12개 원격 head를
보존했습니다. 기존 Windows 전달 skip과 TestClient/httpx 경고1개는 남습니다.

미구현 core31개·CLI31개에서 예상 RED를 확인했습니다. core의 동일한 duplicate fixture 한 건을
제거한 최종 core30개와 CLI31개 및 기존 숫자 규칙96개, 합계**157개가 통과**했습니다.
내용 충돌·configVersion 우회·등록 전 거부/등록 후 허용·과거 보존·무결성/namespace/정규화,
256/257entry·정확 byte 경계/개행·roundtrip·단일 snapshot, noflag 정확한 report 호환,
빈 선택·모든 device 검증·오류 JSON·원본 rows/firstreceipt/cursor/gap/기준 파일 불변을 시험했습니다.

전체 실행·지문·독립 검토·보전 결과는 [evidence](evidence/rule-version-lock-20261109.json)에 남깁니다.
실제 commit/push 및 원격 SHA는 precommit 문서와 분리해 최종 인계합니다.
실제 장비·고객 데이터·Windows/.NET/UI·대량 장기 성능·자동 경보는 실증하지 않습니다.
이전 비JSON generator 직접 호출 Minor도 이 범위에서 고치지 않았으며 JSON CLI 경로는 영향이 없습니다.

Ruling: 검증은 opt-in입니다. 기존 호출 호환 비용으로 옵션 없는 실행은 버전 검증이 아닙니다.
Ruling: unsigned local reference입니다. operator의 신뢰/보관이 필요하며 전역·암호학적 승인/변조 방지·
누락 artifact 복원까지 제공하지 않습니다.
