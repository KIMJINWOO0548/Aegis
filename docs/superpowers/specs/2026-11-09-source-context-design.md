# 출처 선언과 수집 방식의 분리: 호환되는 첫 단계

사용자는 실제/합성 출처와 실시간/재생 방식을 혼동하지 않고, 제공되지 않은 원본 시각·순번을
만들지 않도록 요청했습니다. 기준은 원격 `improve/storage-failure-integrity-20261109`의
`856f7f7f1d55a034185f71d73d6df5f48803b98a`입니다.

## 현재 제약과 선택

현재 입력 snapshot1.0/raw1.1은 `source=SIMULATOR`, 원본 observedAtUtc·runId·sequence가
필수입니다. Source enum의 READ_ONLY는 접근 방식으로, 실제 장비 출처라는 뜻이 아닙니다.
RawArchive cursor와 row key가 run/sequence에 의존하므로 실장비와 nullable 원본 필드를
즉시 허용하면 protocol·DB·IPC·C#/UI까지 이행해야 합니다.

첫 단계는 **기본 health 응답을 그대로 유지하고 opt-in metadata만 추가**하는 것입니다.
기존 Desktop은 알 수 없는 필드를 거부하므로 기본 응답에는 sourceContext:null도 넣지 않습니다.
신규 route/config·Observation/RawSnapshot/WorkerFrame·DB·generated YAML/C#·UI는 바꾸지 않습니다.

## opt-in 계약

`GET /v1/health?includeSourceContext=true`에만 sourceContext를 추가합니다. false/생략은 기존
키/값을 보존합니다. optional boolean query를 runtime OpenAPI와 별도 시험으로 확인합니다.

- schemaVersion: source-context-1, scope: CURRENT_CONFIGURATION.
- archivedRowProvenance: NOT_INFERRED. 선언은 과거 archive/개별 관측과 결합된 provenance가 아닙니다.
- realEquipmentSupported: false, hardRealtimeGuaranteed: false(JSON boolean).
- sources는 현재 설정 순서의 device별 선언이며 최대32개입니다. 혼합 snapshot/replay를
  하나의 global method로 압축하지 않습니다.
- 공통 declaredOrigin=SYNTHETIC. fixture는 producerKind=FIXTURE, acquisitionMethod=FIXED_FIXTURE,
  sourceAccessMode=NOT_APPLICABLE, originEvidence=BUILT_IN_FIXTURE,
  sourceTimestampPolicy=FIXTURE_DEFINED, sourceSequencePolicy=NOT_EXPOSED입니다.
- emulator는 producerKind=SIMULATOR, originEvidence=CONTRACT_DECLARED,
  sourceAccessMode=READ_ONLY, sourceTimestampPolicy/sourceSequencePolicy=PRODUCER_REQUIRED입니다.
  snapshot은 SNAPSHOT_POLL, replay는 SOURCE_BUFFER_REPLAY입니다.
- READ_ONLY는 외부 소스 조회 방식이며 로컬 archive 쓰기를 금지한다는 뜻이 아닙니다.
- origin은 현재 지원 protocol의 선언입니다. 물리 하드웨어 인증이나 source clock 정확도,
  수집 성공·신선도·hard realtime 보장이 아닙니다. 선언은 연결 실패 중에도 동일합니다.
- 토큰/환경변수명/URL/파일경로/원시 값/원본 시각·순번을 metadata에 포함하지 않습니다.

## 보전과 이행

현재 계약의 누락 시각/순번과 REAL/READ_ONLY 입력은 기존대로 거부합니다. receivedAtUtc는
collector receipt이며 원본 시각의 대용이 아닙니다. observationId도 source sequence의 대용이 아닙니다.
원본 observedAtUtc/sequence·최초 receipt·raw/cursor·중복/복구 경로를 그대로 회귀 검증합니다.

후속 versioned envelope/nullable 원본 metadata·별도 local receipt/id·archive key/cursor·IPC/C#
이행 순서와 검증 계획은 별도 계약 검토 문서에 기록합니다. 기존 row의 미기록 출처·수집 방식은
UNKNOWN으로 남기며 현재 설정을 과거 row에 역으로 적용하지 않습니다.

Ruling: metadata는 opt-in으로만 제공합니다. strict 구버전은 보호되지만 기존 화면에는 새
구분이 자동 표시되지 않습니다. 지원 parser를 가진 caller가 명시적으로 요청해야 합니다.
Ruling: 현재 producer 선언은 SYNTHETIC/CONTRACT_DECLARED로 한정합니다. 이 단계로 실제
장비 입력이나 하드웨어 출처 인증을 제공하지 않습니다. nullable 원본 입력은 후속 versioning 대상입니다.
