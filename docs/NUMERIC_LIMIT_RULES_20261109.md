# 설정형 숫자 태그 상·하한의 첫 단계

기준 원격은 `improve/raw-query-utc-ipc-20261109`, commit은
`6b07c83bb53766ea7219ed38c22a8c49fa5a1781`입니다. 접근 가능한 개선 이력에 독립 분석을
추가했습니다. 이전 접근 제한 보존본이나 `5075ccd`의 복구·대체가 아닙니다.

## 제공 범위

새 `aegis.limit_rules`는 frozen 설정 모델·엄격한 JSON loader·stateless 규칙 평가·read-only
archive CLI입니다. 기존 여덟 유량 신호의 Evaluator/health/Warning, SourceConfig, Collector,
API·IPC·worker·simulator·raw writer/schema/cursor·canonical YAML·생성/native·UI를 보존했습니다.
새 결과를 기존 flow의 NORMAL이나 장비 정상/고장 예측으로 해석하지 않습니다.

Collector/IPC에 넣으면 기존 엄격한 계약과 자동 경고 경로까지 커집니다. 첫 단계는 실제로
사용 가능한 별도 archive 명령으로 구성하고, 운영 중 자동 경보·ack/이력·UI 연결은 추가하지 않았습니다.
장비 제어 코드는 없습니다. 예제는 [합성 설정](../examples/numeric-limit-rules.synthetic.json)이며
실장비 보정/engineering 근거가 있는 값으로 제시하지 않습니다.

## 설정

최상위 `schemaVersion=numeric-limit-rules-1`, `configId`, `configVersion`, `rules`가 필요합니다.
규칙은1..16개, 파일은 UTF8(선택 BOM) regular file64KiB까지입니다. unknown/중복 JSON key,
중복 ruleId, 빈/초과 규칙을 거부합니다. rules는 tuple이며 각 rule/target도 frozen입니다.

| 규칙 필드 | 의미 |
| --- | --- |
| ruleId / ruleVersion | 설정 안에서 고유한 규칙 ID와 버전 |
| target.deviceId / signalKey | 정확한 device 및 raw numeric tag |
| target.equipmentId / moduleId | 둘 다 지정하거나 생략. 지정하면 저장 당시 metadata와 정확히 일치해야 함 |
| unit | 정확히 일치할 단위. 자동 trim/변환 없음 |
| lowerBound / upperBound | 최소 한쪽 필요. 양쪽이면 lower<=upper, 같은 bound 허용 |
| maxAgeMs | source observed time의 최대 나이, 정수1..3,600,000ms |

두 경계는 **포함**입니다. 예를 들어 [-10,0]에서 -10/-5/0은 WITHIN_LIMITS입니다. 기존 flow의
0하한을 범용 tag에 가져오지 않습니다. Bool/숫자 문자열/NaN/Infinity와 float로 정확히 변환되지
않는 정수 bound를 거부합니다. 수치는 현재 RawSignal과 같은 finite binary64 정확성 범위입니다.
미세한 차이를 임의 반올림하지 않습니다.

식별자/버전은 ASCII 식별자1..64, tag는 현재 raw contract의1..128, unit은1..32자입니다.
빈/공백만인 unit과 제어문자를 거부합니다. equipment/module 생략은 **DEVICE_TAG** scope이며
과거 저장 hierarchy를 지정하지 않는다는 뜻입니다. 두 값을 지정하면 **EQUIPMENT_MODULE_TAG**
scope입니다. 현재 SourceConfig를 과거 원본에 덧씌우지 않습니다.

## 평가와 근거

단일 `evaluate_rule(rule,batch,*,evaluated_at)`는 기존 raw-history의 선택 tag projection을
소비합니다. source=SIMULATOR/raw1.1 metadata, 원본 시각/firstreceipt/run/순번과 projection을
검증합니다. source/receipt 날짜가 없거나 잘못된 경우 대신 생성하지 않습니다.

| outcome | 의미 |
| --- | --- |
| WITHIN_LIMITS | 유효 근거가 선언한 포함 경계 안에 있음 |
| BELOW_LOWER | 유효 근거가 선언한 하한보다 작음 |
| ABOVE_UPPER | 유효 근거가 선언한 상한보다 큼 |
| UNAVAILABLE | 대상/자료/시각/품질/단위가 평가 조건을 충족하지 못함 |

UNAVAILABLE의 reasonCode는 TARGET_MISMATCH, TAG_NOT_PROJECTED, SIGNAL_ABSENT,
QUALITY_MISSING/BAD/STALE, UNIT_MISMATCH, SOURCE_TIME_IN_FUTURE,
RECEIPT_TIME_IN_FUTURE, SOURCE_AFTER_RECEIPT, STALE_SOURCE, INVALID_EVIDENCE 등입니다.
여러 문제가 있으면 먼저 확인된 사유를 기록하며 전체 사유 집합을 분석한다고 주장하지 않습니다.
신선한 receipt가 오래된 source time을 대체하지 않습니다. maxAge와 정확히 같으면 허용하고
1마이크로초라도 넘으면 평가 불가입니다. 비교는 timedelta이고 sourceAgeMs는 보고용 파생 수치입니다.
미래 나이를0으로 clamp하지 않습니다. 기준시각 자체가 잘못되면 INVALID_EVALUATION_TIME 오류입니다.

projection에 대상 tag가 아예 없으면 present=null/TAG_NOT_PROJECTED입니다. 원본 tag가 없다고
reader가 명시한 present=false/SIGNAL_ABSENT와 다릅니다. 실제 MISSING/null 또는 BAD/null은
present=true와 원래 reading을 남기며 WITHIN_LIMITS로 세지 않습니다. 구조 자체가 잘못된
근거는 INVALID_EVIDENCE, sourceEvidence/projectionEvidenceSha256=null입니다.

각 결과는 `numeric-limit-evaluation-1`, OFFLINE_ARCHIVE_REVIEW, 평가 UTC, ruleId/version,
정규화한 전체 ruleSnapshot/ruleSha256, targetScope, outcome/reasonCode를 담습니다.
sourceEvidence는 원래 device/equipment/module/protocol/source/run/sequence/state/mode,
observedAtUtc/최초 receivedAtUtc, 선택 signalKey/present/reading과 파생 sourceAgeMs입니다.
검증 후 원래 JSON 값/문자열을 복사하여 원본 int를 모델의 float로 바꿔서 반환하지 않습니다.

projectionEvidenceSha256는 파생 age/평가시각을 제외한 **선택 tag projection 근거**의 canonical
JSON SHA256입니다. 전체 원본 payload hash나 하드웨어 인증이 아닙니다. 같은 원본의 rule version/
평가 기준시각만 바뀌면 원본 projection hash는 같습니다. rule/config hash는 검증된 전체 snapshot의
canonical JSON에 적용합니다. 공백/key순서만 다른 설정은 같은 hash입니다.

value9에 v1 upper10을 적용하면 WITHIN_LIMITS, v2 upper8이면 ABOVE_UPPER입니다. 원본 근거는
같고 버전/적용 snapshot/digest가 다릅니다. 같은 ID·버전으로 내용만 바꾸어도 digest로 구별하지만,
stateless 구현은 버전 증가나 과거 registry의 불변성을 강제하지 않습니다. 결과는 입력과 분리해
복사하므로 뒤의 설정/입력 변경이 앞의 결과를 바꾸지 않습니다.

## Archive CLI

기존 설치 환경에서 다음 형태로 실행합니다. 설정의 device/tag/unit은 조회할 보존본과 일치하도록
operator가 준비합니다. 경로는 로컬 operator가 명시하며 HTTP endpoint를 추가하지 않았습니다.

~~~text
python -m aegis.limit_rules --config rules.json --archive raw.sqlite3 \
  --device LIMIT-SYNTHETIC-01 --run-id 11111111-1111-4111-8111-111111111111 \
  --from-utc 2026-11-09T12:00:00Z --to-utc 2026-11-09T12:00:01Z \
  --evaluated-at-utc 2026-11-09T12:00:01Z
~~~

기본 reference는 현재 UTC(SYSTEM_UTC), 명시하면 EXPLICIT_REPLAY_UTC입니다. 두 경우 모두
OFFLINE_ARCHIVE_REVIEW이며 원본 시각이나 실시간 경보가 아닙니다. 평가 reference보다 source나
firstreceipt가 미래면 평가 불가입니다. 과거 reference로 재생한 결과를 현재 정상 상태로 쓰지 않습니다.

해당 device의 규칙만 적용하고 없으면 NO_APPLICABLE_RULES 오류입니다. 기존 reader의
후보1,024/8MiB·선택64 batch·tag16·24시간·SQL budget 등 모든 한도를 보전합니다. 최대
64×16=1,024개 결과입니다. 큰 run은 좁은 시간 선택도413인 기존 제한을 그대로 갖습니다.

report는 `numeric-limit-review-1`, mode/referenceTimeBasis/evaluatedAtUtc,
configId/version/hash/전체 configSnapshot/appliedRuleIds, archiveSelection,
selectionStatus/per-outcome counts/results입니다. archiveSelection은 reader의 window/한도/
scanned/matched/sourceCoverage=NOT_ASSERTED 및 선택 run 전체의 sequence gap을 보존합니다.
gap을 가짜 시간 gap으로 바꾸지 않습니다. 빈 선택은 NO_SAMPLES이며 configSnapshot도 남습니다.
전체 NORMAL/PASS 판정은 없습니다. exit0은 명령이 결과를 생성했다는 뜻이며 모든 규칙 통과가 아닙니다.

정상 실행은 stdout JSON만 내고 결과 파일을 쓰지 않습니다. 오류는 stderr에 고정 JSON
`{status:UNAVAILABLE,code,message}`와 exit2를 내며 stdout은 비어 있습니다. INVALID_RULE_CONFIG,
INVALID_EVALUATION_TIME, NO_APPLICABLE_RULES 또는 기존 RawQueryError code를 유지합니다.
경로·원본 설정 내용·검증 오류 값을 echo하지 않습니다. raw archive가 손상됐으면 정상 빈 자료로
대체하지 않고 reader 오류로 종료합니다. 읽기에 RawArchive.start나 쓰기/삭제를 호출하지 않습니다.

## 검증과 범위

최종 전체 Python은 **608통과·Windows 전용1건너뜀**(609개,runner158.516초)입니다.
독립 emulator26개와 생성 계약/v3.4 검사도 통과했습니다. 기존 보호 파일393개는 byte 동일하며
이전8개 checkout과11개 원격 head를 보존했습니다. 기존 Windows 전달 skip과 TestClient/httpx 경고1개는 남습니다.

새 core67개와 CLI29개는 미구현 상태의 예상 RED를 먼저 확인한 뒤 **96개 통과**했습니다.
경계 직전/일치/직후·음수/단측/동일 경계, 부정 설정/정수 손실, quality/absence/unit,
시간/target 오류, version/snapshot/digest/입력 불변, 실제 CLI subprocess·오류 JSON,
빈 자료/무규칙 및64batch×16rule 결과를 시험했습니다. 원본 rows·firstreceipt·cursor·gap과
DB hash가 평가 전후 동일함을 확인했습니다. 합성 예제2개 규칙의 public loader 검증도 통과했습니다.

정확한 전체 실행·독립 검토·보호 파일·작성자 확인은
[evidence](evidence/numeric-limit-rules-20261109.json)에 기록합니다. 실제 commit/push와 원격 SHA는
precommit 문서와 분리해 최종 인계합니다. 실제 장비·고객 데이터·Windows/.NET/UI·보정된
물리 한계·연속 운전/대량 동시 조회·자동 경보 실증은 포함하지 않습니다.

독립 검토는 Critical0/Important0/Minor1입니다. 검토자는 작은 읽기 전용 probe와 실제 전체/선택
시험 기록·source 지문·generated evidence 복원을 확인했으며 suite를 별도 재실행하지 않았습니다.
**남은 Minor:** Python library 직접 호출의 batch.signals에 generator/iterator를 주면 Pydantic이
소비한 후 원래 iterator를 다시 읽는 과정에서 TypeError가 날 수 있습니다. 지원 입력은 reader가
만든 JSON dict/list projection이고 실제 archive CLI는 목록을 제공하므로 정상 경로는 영향이 없습니다.
비JSON container를 직접 넘기는 경우의 일관된 UNAVAILABLE 처리는 후속 보완입니다.

Ruling: offline/stateless first step으로 한정했습니다. 비용은 실행 중 자동 경보·경고 이력/확인·제어·
실시간 UI가 추가되지 않는 것입니다. 기존 flow와 원본 경로를 보전합니다.
Ruling: 버전 registry 대신 digest를 남겼습니다. 같은 버전의 내용 변경은 식별되지만 금지되지는 않습니다.
