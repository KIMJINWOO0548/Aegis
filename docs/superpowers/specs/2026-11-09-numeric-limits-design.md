# 설정형 숫자 태그 상·하한 규칙: 첫 단계

기준은 `6b07c83bb53766ea7219ed38c22a8c49fa5a1781`입니다. 사용자는 규칙 ID/버전/대상/단위와
근거 값을 남기는 범용 숫자 tag 상·하한, 결측/불량/오래된 자료의 평가 불가 구분, 합성 회귀 및
별도 push를 승인했습니다. UI·장비 제어·실제 장비/Windows 성능 주장은 범위 밖입니다.

## 선택

별도 stateless engine + 제한된 JSON config loader + 기존 read_raw_archive를 쓰는 read-only CLI를
구현합니다. 순수 library만 두면 사용 경로가 부족하고, Collector/IPC에 넣으면 기존 flow8개 및
WorkerFrame 계약까지 커집니다. API/flow Evaluator/수집·저장·커서/worker/UI는 바꾸지 않습니다.

## 설정과 규칙

config schemaVersion=numeric-limit-rules-1, configId/configVersion, rules1..16개입니다.
각 rule은 ruleId/ruleVersion, target{deviceId,equipmentId?,moduleId?,signalKey}, unit,
lowerBound?/upperBound?, maxAgeMs(정수1..3,600,000)입니다. 식별자/버전1..64, tag1..128,
unit1..32자이며 제어문자/공백만인 unit은 거부하고 암묵적 trim/변환은 하지 않습니다.
equipment/module은 둘 다 지정하거나 생략합니다. 생략은 DEVICE_TAG scope, 지정은 저장 당시
hierarchy와 정확히 일치하는 EQUIPMENT_MODULE_TAG scope입니다. 현재 SourceConfig를 대신 쓰지 않습니다.

경계는 포함하며 최소 한쪽 필요, lower<=upper, 같은 bound 허용입니다. bool/숫자 문자열/
NaN/Infinity 및 float로 정확히 표현 못 하는 정수 bound를 거부합니다. RawSignal과 같은 수치
정확성 범위입니다. unknown key/중복 JSON key·중복 ruleId/초과 규칙은 거부합니다. config 파일은
UTF8(선택 BOM) regular file64KiB까지 읽습니다. model과 target/rules는 frozen입니다.

config와 rule은 검증 후 canonical JSON의 SHA256와 전체 적용 snapshot을 기록합니다.
공백/key순서만 바뀌면 같은 digest, 같은 ID/버전으로 내용이 바뀌면 다른 digest입니다.
stateless 구현은 버전 증가/과거 registry 불변성을 강제하지 않습니다.

## 평가

`evaluate_rule(rule,batch,*,evaluated_at)`는 raw-history의 tag projection을 소비합니다.
raw1.1/SIMULATOR metadata·UTC 시각/firstreceipt·run/sequence·최대16 projection을 검증합니다.
검증 후 원래 숫자/문자열에서 근거를 복사하고 모델의 float 재직렬화로 원본 int를 바꾸지 않습니다.

outcome은 WITHIN_LIMITS/BELOW_LOWER/ABOVE_UPPER/UNAVAILABLE입니다. WITHIN_LIMITS는 선언한
규칙에 관한 결과이며 기존 flow NORMAL/Warning이나 장비 정상·고장 예측으로 승격하지 않습니다.
target mismatch, projection 미제공(TAG_NOT_PROJECTED), 명시적 present=false(SIGNAL_ABSENT),
quality MISSING/BAD/STALE, null/잘못된 evidence, unit 불일치는 UNAVAILABLE입니다.
source time 또는 receipt가 평가시각보다 미래, source>receipt, source age>maxAge도 UNAVAILABLE입니다.
정확히 maxAge는 허용하며 비교는 timedelta로 합니다. 신선한 receipt로 오래된 source를 대체하지 않습니다.
UTC overflow/누락 시각·순번은 INVALID_EVIDENCE이며 원본 날짜/순번을 만들지 않습니다.

단일 결과 schemaVersion=numeric-limit-evaluation-1, evaluationMode=OFFLINE_ARCHIVE_REVIEW,
evaluatedAtUtc, ruleId/ruleVersion/ruleSha256/ruleSnapshot/targetScope/outcome/reasonCode,
sourceEvidence 및 projectionEvidenceSha256를 제공합니다. sourceEvidence는 저장 device/equipment/module,
source/protocol/run/sequence/state/mode/observed/firstreceipt, target signalKey, present(true/false/null),
reading{value,unit,quality} 및 sourceAgeMs입니다. evidence hash는 선택 tag projection의 hash이며
전체 원본 payload hash가 아닙니다. invalid evidence는 null입니다. 결과 snapshot은 입력과 분리합니다.
projection hash는 파생 sourceAgeMs/평가 기준시각을 제외한 sourceEvidence에 적용하므로 같은 원본의
평가 시각/규칙 버전이 달라도 원본 projection 근거 hash는 같습니다.

## CLI

`python -m aegis.limit_rules --config FILE --archive DB --device ID --run-id UUID
--from-utc ISO --to-utc ISO [--max-batches N] [--evaluated-at-utc ISO]`입니다.
기본 기준시각은 현재 UTC(SYSTEM_UTC), 명시 시 EXPLICIT_REPLAY_UTC로 기록합니다. 둘 다
OFFLINE_ARCHIVE_REVIEW이며 실시간 경보가 아닙니다. 기준시각은 평가 reference이며 원본 시각이 아닙니다.
설정에서 해당 device rule만 적용합니다. 없으면 NO_APPLICABLE_RULES 오류입니다.

`review_archive(config,archive,device_id,run_id,from_utc,to_utc,*,max_batches=64,evaluated_at=None)`는
기존 reader를 선택tag 최대16개로 호출합니다. 기존 후보1024/8MiB·선택64·24시간·SQL budget을
보전하며 최대1024 rule results입니다. 빈 선택은 selectionStatus=NO_SAMPLES, 그 외 HAS_SAMPLES,
per-outcome counts와 results를 내며 전체 NORMAL/PASS 요약은 없습니다.

report schemaVersion=numeric-limit-review-1, evaluationMode, referenceTimeBasis/evaluatedAtUtc,
configId/configVersion/configSha256/configSnapshot/appliedRuleIds, archiveSelection(reader metadata와 run gap),
selectionStatus/counts/results입니다. stdout JSON만 내고 파일을 쓰지 않습니다. CLI 오류는
stderr의 고정 JSON(status=UNAVAILABLE,code,message)와 exit2이며 경로·검증 입력값을 echo하지 않습니다.
빈 선택에도 검증된 전체 configSnapshot을 항상 남겨 적용 내용의 추적성을 보전합니다.
config 오류 INVALID_RULE_CONFIG, 기준시각 INVALID_EVALUATION_TIME, 기존 RawQueryError code를 유지합니다.

Ruling: offline/stateless first step입니다. 실행 중 Collector/IPC의 자동 경보·경고 이력/확인·제어와
실시간 UI 표시는 제공하지 않는 비용으로 기존 수집·유량 계약/원본을 보전합니다.
Ruling: 버전 불변 registry 대신 canonical digest를 남깁니다. 동일 버전 재사용은 드러나지만 금지하지 않습니다.
실제 장비 unit/상한의 보정 근거는 없으므로 합성 규칙만 예제로 넣습니다.
