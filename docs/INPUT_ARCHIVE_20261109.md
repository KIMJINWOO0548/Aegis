# Nullable 원본의 명시적 새 쓰기 계약

기준 commit은 `02b3722f81a98585e7f46a5197106a5c5915aebc`입니다. 접근 가능한 원격 이력에서 독립 구현했으며 접근 제한 보존본을 복원하거나 대체하지 않았습니다.

## 연결 범위

새 `archive-write-1` 입력을 별도 **input-archive-1** SQLite에 영속 저장하고, 기존 공통 조회의 명시적 `INPUT_ARCHIVE_V1` kind로 다시 읽습니다. `archive_ingest`는 이 흐름을 실행하는 별도 합성 fixture collector입니다. 기존 raw-v1, finite SYNTHETIC adapter-store-1, SIMULATOR 수집, 유량, HTTP/IPC, UI와 기존 자료를 자동 전환하지 않습니다. 자동 이전·삭제·실행 중 저장소 교체도 없습니다.

raw-v1의 필수 source run/sequence/time과 SIMULATOR 문맥을 일반 입력에 억지로 채우지 않았습니다. 따라서 기존 `adapterToRawV1WriteDelivery: NOT_CONNECTED`는 유지됩니다. 이번에 실제 연결한 경로는 **새 입력→새 local archive→공통 읽기**이며, 기존 물리 raw-v1 테이블로의 쓰기나 장비 source ACK 완료가 아닙니다.

## 쓰기 계약

각 입력 페이지의 schemaVersion은 `archive-write-1`입니다. 나머지 generic adapter page 구조와 원본 record 검증을 재사용하되 기존 SYNTHETIC 고정 admission은 재사용하지 않습니다. 한 파일의 명시적 descriptor는 아래7개 필드를 정확히 갖습니다.

```json
{"adapterId":"synthetic-01","adapterVersion":"1.0",
 "streamId":"11111111-1111-4111-8111-111111111111",
 "declaredOrigin":"SYNTHETIC","originEvidence":"DECLARED_ONLY",
 "acquisitionMethod":"SOURCE_BUFFER_REPLAY","sourceAccessMode":"READ_ONLY"}
```

generic writer의 declaredOrigin은 `SYNTHETIC|DEVICE|UNKNOWN`, acquisitionMethod는 `SOURCE_BUFFER_REPLAY|SNAPSHOT_READ`이며 sourceAccessMode는 READ_ONLY입니다. descriptor는 파일 내에서 고정됩니다. 이후 페이지나 reopen 시 출처·수집 방식·stream 등이 다르면 거부합니다. DEVICE는 제공자 선언을 보존한다는 뜻이며 실제 장비 인증·드라이버 실행을 입증하지 않습니다. 이번 DEVICE/UNKNOWN 보존 시험도 합성 객체만 사용했습니다. CLI fixture collector는 **SYNTHETIC replay만** 허용하여 합성 fixture를 실제 장비 입력으로 표시하지 않습니다.

원본 sourceMetadata의 observedAtUtc, sequence, runId는 제공/미제공 status와 함께 저장합니다. null/NOT_PROVIDED는 그대로 남깁니다. 제공한 원본 시각의 offset 문자열·큰 정수·품질·단위를 원본 JSON에 보존합니다. delivery stream/recordId/afterToken/nextToken/SQL ordinal은 수집 전달 식별자이며 원본 run·순번·시각을 대신하지 않습니다. 최초 host 수신 시각 `firstReceivedAtUtc`는 별도로 보관합니다. CLI는 읽기 완료 직후 이를 캡처하며 library append 호출자는 aware datetime을 명시합니다. source 관측 시각과 같다고 추정하지 않습니다.

라이브러리는 `InputArchive(path, descriptor)`, `append_page(page_bytes, received_at)`를 사용합니다. 페이지 wire와 SHA, canonical producer JSON과 SHA, 신규 원본 record/최초 receipt, delivery cursor/EOF를 한 transaction에 기록합니다. 저장 후 요청 페이지와 변경되지 않은 이전 descriptor/page prefix를 새 snapshot으로 확인해야 `LOCAL_ARCHIVE_COMMITTED`를 반환합니다. sourceAcknowledgement는 항상 NOT_SENT입니다.

같은 after-token의 같은 canonical 페이지를 재전송하면 최초 wire와 receipt를 유지하고 `ALREADY_LOCAL_ARCHIVE_COMMITTED`로 응답합니다. 다른 canonical 내용은 충돌입니다. 새 페이지의 동일 recordId가 이전과 같은 원본이면 중복을 추가하지 않고 첫 receipt를 유지합니다. 원본이 다르면 해당 페이지 전체를 거부합니다. 새 페이지는 현재 committed predecessor만 연결할 수 있으며 token loop를 허용하지 않습니다. 빈 terminal 페이지도 EOF 증거와 함께 저장합니다. 빈 nonterminal 무데이터는 cursor를 움직이거나 새 page를 commit하지 않습니다.

## 합성 수집과 재조회 실행

설치된 checkout 또는 `PYTHONPATH=src`에서, 기존 원본과 다른 명시적 새 파일을 지정합니다. 부모 디렉터리는 존재해야 합니다.

```sh
python -m aegis.archive_ingest --fixture examples/archive-write.synthetic.json --store input-archive.sqlite
python -m aegis.archive_ingest --fixture examples/archive-write.synthetic.json --store input-archive.sqlite
```

예제는 원본 시각·순번·run이 모두 없는 기록과, 원본 offset 시각·큰 정수가 제공된 기록을 갖습니다. 두 번째 실행은 저장된 cursor/EOF를 사용하고 중복 원본을 만들지 않습니다. fixture는 실행 전에 전체 descriptor/페이지/유한 chain과 한도를 검사합니다. fixture 변경은 새 archive에서 전체 fixture SHA binding으로 강제하지 않습니다. 대신 저장된 페이지/descriptor와의 충돌 및 재개 prefix 일치 경계는 collector 검증을 따릅니다. generic writer는 유한 외부 fixture 자체가 아니라 실제 committed page log에 묶입니다.

collector는 최대64page, timeout1..10000ms, 최대1..3회 cooperative read 시도를 지원합니다. read timeout/취소는 현재 페이지를 commit하지 않습니다. 저장 transaction 안에는 await/executor가 없으며 동기 SQLite가 별도 fixture 프로세스의 event loop를 막을 수 있습니다. production native driver/async 수집 루프에 그대로 넣을 수 있는 격리·중단 보장을 제공하지 않습니다. 후속 read 실패나 READ_LIMIT 이전에 저장된 유효 prefix는 보존합니다.

요약은 `archive-ingest-report-1`, writeDelivery LOCAL_ARCHIVE, executionSource SYNTHETIC_FIXTURE_ONLY, realDriverExecuted false 및 실제 persistence/count를 보고합니다. 원본 값·식별자·파일 경로·내부 delivery token을 요약에 포함하지 않습니다. 종료·출력·flush 오류로 commit 결과를 호출자가 확신할 수 없으면 성공 대신 STORE_RESULT_UNKNOWN을 표시하고 자료를 보존합니다.

조회 설정은 다음과 같습니다. 경로는 현재 작업 디렉터리 기준입니다.

```json
{"schemaVersion":"stored-record-sources-1","sources":[
  {"storeId":"new-input","kind":"INPUT_ARCHIVE_V1","path":"./input-archive.sqlite"}
]}
```

```sh
python -m aegis.stored_records --sources sources.json \
  --received-from 2026-11-09T00:00:00Z --received-to 2026-11-10T00:00:00Z
```

조회는 실행 시의 **최초 수신 시각**에 맞는 aware 최대1일 구간을 지정합니다. 누락된 원본 timestamp가 있어도 FIRST_RECEIPT로 찾을 수 있습니다. 기존 raw-v1/adapter kind와 함께 설정할 수 있으며 새 형식은 별도 ARCHIVE_DELIVERY_STREAM_RECORD identity와 ARCHIVE_CANONICAL_RECORD_JSON hash 표현을 사용합니다. 출처는 검증된 저장 descriptor/페이지를 반영하며 hardwareIdentityVerified는 false입니다. tag 부재와 MISSING null, 원본과 receipt, 별도 저장 identity를 유지합니다.

공통 조회의 한도·fail-whole-result·per-store snapshot·sourceCoverage NOT_ASSERTED 계약은 [기존 읽기 안내](DUAL_STORE_READ_20261109.md)를 따릅니다. 새 kind는 선택 fixture pin을 받지 않습니다. 새 descriptor/hash 검증은 자기 일관성이며 암호학적 진본 확인이 아닙니다. 자동 전체 기간 수집 보증이나 source gap이 없다는 뜻도 아닙니다.

## 장애·파일 경계

commit 이전 저장 오류는 transaction을 rollback하고 기존 원본·최초 receipt·cursor를 유지합니다. rollback 실패, COMMIT 예외 또는 postcommit 확인 실패는 결과가 불명확하므로 writer를 fence/close하고 STORE_RESULT_UNKNOWN으로 처리합니다. reopen하여 전체 저장 evidence를 검증한 뒤 같은 입력을 재시도해야 합니다. 호출자가 성공 응답을 받기 전 종료돼도 저장이 완료됐을 수 있습니다. 파일을 삭제·truncate·강제 초기화하지 않습니다.

새 파일은 POSIX exclusive0600로 만들고, 초기 descriptor/schema를 DELETE/EXTRA로 commit 및 parent fsync한 후 WAL/FULL을 사용합니다. 기존 파일은 SQLite writer를 열기 전에 새 appID/version을 확인하므로 raw-v1/기존 adapter/알 수 없는 파일을 변경 없이 거부합니다. 초기화 중단 산출물도 자동 제거하지 않으며 불완전 파일은 이후 거부될 수 있습니다. 일반 단일 hardlink 파일과 신뢰하는 로컬 디렉터리만 대상으로 하며 symlink·특수파일·위험한 companion 경로를 거부합니다.

고정 schema4개 table와 SQLite autoindex를 모두 열거하여 검증하고, exact wire/canonical/page chain/첫 record 등장/첫 receipt/최종 cursor·count를 대조합니다. 별도 reader는 기존 connection의 read transaction에서 같은 검증기를 사용하며 writer를 생성하지 않습니다. 일반 WAL 읽기의 SHM/read metadata 효과는 있을 수 있습니다. immutable/nolock/live-main-only 복사로 우회하지 않습니다.

최대64page·1,024record·32MiB, page64KiB, cooperative phase별5초/SQL VM5백만 step 및0.2초 busy timeout입니다. postcommit 확인에는 새 phase budget이 적용됩니다. 한도 도달은 자동 삭제 대신 실패로 처리합니다. 파일/native I/O의 강제 종료 deadline이나 무제한 장비 stream은 보장하지 않습니다.

## 검증 범위

합성 입력의 실제 수집→영속 저장→공통 재조회, null/status·offset/정수·출처/수집 방식·최초 receipt 보전, 중복/충돌·저장 실패·재시작과 기존 형식 거부를 시험합니다. 수치·실행 범위는 [검증 기록](evidence/input-archive-20261109.json)을 확인하세요.

실제 장비·회사 라이브러리·개인/운영 데이터는 사용하지 않았습니다. Windows/서비스·원격 CI·UI 검증, 물리 용량 고갈·전원 차단, 장기/대량·원격 파일시스템·운영 통합은 수행하지 않았습니다. 장비 드라이버와 source ACK, 장기 저장 운영, 새 형식의 backup/restore, 승인된 archive 이행은 후속 작업입니다.
