# 저장 용량 실패의 원본·커서 보존과 사유 구분

사용자 요청은 저장 공간 부족 시 원본·커서를 지키고 저장 실패를 연결 실패와 구분하는
작고 안전한 단계입니다. 기준은 원격 `improve/startup-config-recovery-20261109`의
`4f1c8dd2454b3513e21be06250d07289a83e5822`입니다.

## 확인과 선택

RawArchive는 rows/gap/cursor를 같은 transaction으로 쓰고 실패 시 rollback합니다.
기존 실제 SQLite FULL 시험은 이 보존 경계를 이미 확인합니다. 공백은 Collector가
FULL을 일반 ARCHIVE_UNAVAILABLE로 묶는 것과 DISCONNECTED 안에서 사유가 바뀔 때
storage event가 누락되는 것입니다.

선택은 원본 writer·DB schema·복구/커서·UI/API shape를 바꾸지 않고 사유 분류와
이벤트 전환을 보완하는 것입니다. 새 connection state나 임의 삭제·VACUUM·보관 축소를
추가하지 않습니다. 물리 용량 예측을 성공 보장으로 사용하지 않습니다.

## 계약

- sqlite3.Error의 정수 sqlite_errorcode 하위8bits가 SQLITE_FULL이면 ARCHIVE_FULL입니다.
  OSError.errno가 ENOSPC인 경우도 같은 사유입니다. 메시지·번역문·문자열 “full”로 추정하지 않습니다.
- 기타 SQLite/OSError는 기존 ARCHIVE_UNAVAILABLE, HTTP timeout/error는 기존
  POLL_TIMEOUT/SOURCE_UNAVAILABLE를 유지합니다. quota 등 분류하지 않은 코드는 일반 저장 실패입니다.
- 실패 시 DISCONNECTED는 수집 unavailable의 보수적 상태로 유지합니다. 물리 연결이 끊겼다는
  새로운 실증이 아니며 reasonCode와 eventName으로 원인을 구분합니다. UI는 변경하지 않습니다.
- (state, reason)이 바뀌면 해당 event를 기록합니다. 두 ARCHIVE_* 사유는 STORAGE_WRITE_FAILED,
  반복 동일 실패는 중복 event를 만들지 않습니다.
- 공개 observation/counter와 커서는 기존 committed-only 경계를 유지합니다. 복구 시 같은
  page를 재시도하며 원본·최초 received UTC·gap/cursor를 덮거나 자동 삭제하지 않습니다.
- 로그 볼륨도 가득 차면 event의 영구 기록을 보장하지 못합니다. logging_degraded와 API의
  실패 사유를 유지하며 poll이 조용히 종료되거나 정상으로 표시되지 않아야 합니다.

## 판단·증거·범위

Ruling: 기존 API state/shape를 유지하고 사유 코드로 구분합니다. 비용은 기존 화면의
DISCONNECTED 표시가 저장 실패에도 남는다는 점이며, 화면 변경은 별도 승인 범위입니다.
Ruling: typed SQLITE_FULL/ENOSPC만 FULL로 확정합니다. 알 수 없는 코드나 quota는
ARCHIVE_UNAVAILABLE로 남을 수 있지만 연결 실패와는 다른 저장 사유입니다.

실제 SQLite max_page_count로 FULL을 발생시켜 원본 전체행·received UTC·cursor/gap·파일 유지,
제한 해제 후 재시도/idempotence·peer archive 진행을 확인합니다. 네트워크→저장 사유 전환,
반복 실패 중복 억제·오분류 방지·로그 ENOSPC도 검사합니다. 실제 파일시스템을 채우지 않습니다.

범위는 실행 중 replay poll/commit입니다. archive 최초 open의 실패 분류, 물리 디스크/WAL
전면 고갈·전원 차단·Windows/실장비·일반 운영 감시 전체를 완료로 주장하지 않습니다.

근거: [Python sqlite exception metadata](https://docs.python.org/3.12/library/sqlite3.html#sqlite3.Error),
[SQLite primary/extended codes 및 FULL](https://www.sqlite.org/rescode.html#full),
[transaction 오류와 rollback](https://www.sqlite.org/lang_transaction.html).
