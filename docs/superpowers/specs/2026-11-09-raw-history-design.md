# 원본 숫자 태그의 제한된 과거 조회

기준은 출처 선언 개선 `7d303ee27d9ec31cc33d2c1ec69cbc8b036ac867`입니다. 사용자는 기존
유량 계약·원본·simulator를 보전하며 태그 사전과 기간별 원본 조회의 작은 backend 기능을
구현·시험·별도 push하도록 승인했습니다. UI·실장비·Windows 변경/실증은 범위 밖입니다.

## 선택과 경계

기존 /trends를 확장하면 여덟 유량 신호 enum/TrendResponse/native consumer와 충돌합니다.
RawArchive writer에 조회를 붙이면 수집 disk executor와 복구 경계가 섞입니다. 별도 read-only
reader와 raw 전용 runtime route 두 개를 선택합니다. 시간 인덱스가 없으므로 이 단계는
전체 대용량 기간 검색이 아닙니다. 명시한 device+정확한 runId의 후보 전체를 제한해 읽고,
UTC aware 비교로 [from,to) 기간을 고릅니다. 한도 초과는 오류이며 성공 prefix는 반환하지 않습니다.

## 계약

- GET /v1/devices/{id}/raw-tags: runId, fromUtc, toUtc 필수. 조회 범위에서 관찰한 태그/단위/품질
  빈도의 사전이며 권위 장비 사전·전체 과거 목록·최신값이 아닙니다.
- GET /v1/devices/{id}/raw-history: 위 필드 + signals(쉼표 구분1..16개) 필수.
- 두 route 모두 maxBatches 기본64/허용1..64. runId는 저장 키의 UUID 표기(대소문자 보존)를
  정확히 지정합니다. 시간은 timezone 필수 ISO8601, 최대24시간, 끝 제외입니다.
- 응답 schemaVersion=raw-query-1, kind=RAW_ARCHIVE_HISTORY/RAW_ARCHIVE_OBSERVED_TAGS,
  selectionComplete=true는 저장된 선택 범위 조회 완료만 뜻합니다. sourceCoverage=NOT_ASSERTED로
  미수집/유실/전체 원천 시계열 완전성을 주장하지 않습니다.
- batch에는 저장된 protocol/source/device/run/sequence/observedAtUtc/stateVersion/mode,
  최초 receivedAtUtc와 저장 당시 equipment/module을 반환합니다. 현재 설정을 과거 행에 찍지 않습니다.
- 요청 tag가 없으면 present=false/reading=null입니다. 실제 저장된 MISSING/BAD/null은
  present=true/원래 reading으로 구분합니다. 보간·carry-forward·품질 재작성·숫자 요약은 없습니다.
- tag metadata에는 관측 unit variants·present/absent batch 수·원래 quality 빈도를 유지합니다.
- 기록된 replay gaps는 SELECTED_RUN_SEQUENCE_ONLY 범위로 원래 sequence/reason을 제공합니다.
  시간 gap으로 바꾸거나 미기록 gap이 없다고 보장하지 않습니다.

## 자원과 무결성

고정 상한: run 후보1024개, 선택 batch64개, 선택 tag16개, 관측 catalog512개,
단일 payload256KiB, 후보 payload 합계8MiB, run gap1024개, SQL progress1,000,000VM step/1초,
SQLite busy timeout0.2초입니다. 입력/조회 한도 초과는422/413으로 구분합니다. SQL progress는
hard realtime deadline이 아니며 물리 I/O/플랫폼에 대한 지연 보장이 아닙니다.

mode=ro URI/query_only/단일 read transaction을 씁니다. immutable/WAL 무시는 하지 않습니다.
RawArchive.start·DB 생성·쓰기·schema/cursor 변경·원본 복제/삭제는 하지 않습니다. WAL의
committed snapshot만 읽습니다. SQLite 자체의 WAL sidecar/lock 관리는 그대로입니다.
후보 수/length를 먼저 확인해 큰 JSON 전체를 반환받지 않고 byte 제한을 적용합니다.
raw1.1 검증, JSON 중복 key/비정상 수치 거부, DB identity/시각과 payload semantic 일치를 확인합니다.
한 후보라도 손상되면 정상 원본 조회로 숨기지 않고409로 실패합니다. 미생성/잠금/읽기 실패는503입니다.
검증 후에는 원본 JSON 값/문자열에서 반환하여 정수를 float로 바꾸지 않습니다.

서버 소유 raw_archive_paths를 create_app에 선택 주입하고 CLI가 replay source만 연결합니다.
client 파일 경로 입력은 없고 fixture/snapshot/미연결 embedding은501로 명시합니다.
sync route는 기존 FastAPI worker thread에서 읽으며 acquisition/IPC/worker executor는 건드리지 않습니다.
기존 health·flow·YAML/generated/native·simulator·저장/복구 동작은 보존합니다.

Ruling: 전체 run 후보를 제한해 검증한 후 결과를 만듭니다. 시간 인덱스/비파괴 이행을 피하는
대신1024행/8MiB를 넘는 run은 좁은 기간도413입니다. 대량 시간 seek/pagination은 후속 과제입니다.
Ruling: 태그 사전은 관측된 metadata만 제공합니다. 장비 tag 설명·engineering 의미·장비 인증은
제공된 근거가 없으므로 만들지 않습니다. 이전 접근 제한 보존본은 사용하지 않습니다.
