# 저장 용량 실패 사유와 원본·커서 보전

2026-11-09 UTC. 원격 `improve/startup-config-recovery-20261109`의
`4f1c8dd2454b3513e21be06250d07289a83e5822`를 기준으로 했습니다.
새 브랜치는 `improve/storage-failure-integrity-20261109`입니다.

## 변경

실행 중 replay 수집의 SQLite FULL 및 OS ENOSPC를 `ARCHIVE_FULL`로 구분합니다.
SQLite primary/extended code의 하위8bits와 OS errno를 사용하며 예외 메시지의
문자열 “full”로 판정하지 않습니다. BUSY·일반 I/O·권한·타입 불명 저장 오류는 기존
`ARCHIVE_UNAVAILABLE`, HTTP 오류는 기존 `POLL_TIMEOUT`/`SOURCE_UNAVAILABLE`입니다.

이벤트는 `(state, reason)` 변화로 기록합니다. 이전에는 네트워크 timeout 뒤 저장 실패가
이어져도 둘 다 DISCONNECTED라 storage event가 누락됐습니다. 이제 두 ARCHIVE_* 사유는
`STORAGE_WRITE_FAILED`로 남고, 반복된 동일 실패는 중복 event를 만들지 않습니다.
원시 예외 메시지·경로·토큰은 API/frame/이벤트에 넣지 않습니다.

기존 API state/shape와 UI는 유지합니다. DISCONNECTED는 수집 unavailable의 보수적 상태이며
물리 연결 단절을 새로 실증한 표시가 아닙니다. 원인은 reasonCode/eventName으로 구분합니다.
RawArchive의 원본·최초 received UTC·gap/cursor 원자적 transaction과 rollback을 유지했습니다.
DB/WAL 삭제·보관 축소·cursor reset/수동 전진·자동 공간 확보는 추가하지 않았습니다.

## 실행한 검증

- 기준 전체 **351 PASS / 1 skip**, runner132.177초. skip은 Windows 전달 스크립트용 PowerShell7 부재입니다.
- 실제 SQLite page 한도 FULL에서 사유가 일반 UNAVAILABLE로 보이는 **RED1**을 확인했습니다.
  원본·received UTC·cursor·gap·inode·integrity 보존은 그 RED 전에도 통과한 대조군입니다.
- 기존 보전/복구·별도 peer archive 대조군 **2 PASS**를 제품 변경 전에 별도로 확인했습니다.
- diagnostics **RED5 / 기존 대조6 PASS**: typed FULL3개, 같은 DISCONNECTED 내 사유 event 누락,
  raw/log ENOSPC 처리. 보완 후 archive/replay 포함 대상 **53 PASS**, 별도 실제 SQLite 시험 **3 PASS**입니다.
- 실제 SQLite `max_page_count` 제한에서 실패 page의 원본 전체행·첫 수신 시각·cursor·gap·공개 카운터가
  변하지 않았습니다. 한도 복원 후 같은 연결·같은 page 재시도, 빈 replay, 실제 중복 append와
  재개방을 거쳐 cursor5·원본4행·gap1회 및 first receipt 보존을 확인했습니다.
- 서로 다른 SQLite archive/전용 disk executor를 쓰는 두 Collector를 함께 poll했습니다.
  대상이 FULL로 계속 실패하는 동안 peer cursor는1→2로 진행하고 원래 prefix는 유지됐습니다.
  이는 같은 프로세스의 합성 HTTP transport 시험이며 별도 OS worker 격리 실증은 아닙니다.
- 저장 실패 reason과 loggingDegraded가 기존 worker frame을 통과하며, 로그 ENOSPC에도 poll은
  반환하고 저장 실패를 유지합니다. 로그 볼륨도 가득 찬 경우 event의 영구 기록은 보장하지 않습니다.

- 최종 전체 **365 PASS / 1 skip**, 실패·오류0, runner130.889초. 시험 중 제품/시험 hash 동일,
  기존 고정 evidence도 원래 바이트로 복원했습니다. 기존 Starlette/httpx deprecation 경고1개가 남았습니다.
- 독립 emulator26개, 생성 계약 drift와 YAML/OpenAPI/reference SQL 정적 검사 통과.
  독립 읽기 검토 Critical/Important/Minor 지적0개이며 검토자가 시험을 재실행한 것은 아닙니다.
- 의도한 README/Collector 변경을 제외한 기준 추적 파일367개가 바이트 동일합니다.
  기존 원본 checkout4곳과 원격 ref7개도 보존했으며 작성자는 검증된 noreply를 사용합니다.

전체 회귀·독립 검토 결과는 `docs/evidence/storage-failure-integrity-20261109.json`에 기록했습니다.
명령은 `python -m pytest -q`, 독립 emulator unittest, `generate_contracts.py --check`,
`validate_v34.py`입니다. 기존 시험의 고정 evidence6개는 매 실행 후 원래 바이트로 복원합니다.

## 판단과 남은 범위

Ruling: 기존 state/shape를 유지해 화면의 DISCONNECTED 표시는 저장 실패에도 남습니다.
API reason/log event로 구분하며 사용자 승인 전 화면 디자인을 변경하지 않았습니다.
Ruling: typed SQLITE_FULL/ENOSPC만 FULL로 확정합니다. quota나 불명 원인은 일반 저장 실패로
남을 수 있습니다. 단순 메시지로 용량 고갈을 추정하지 않습니다.

SQLite FULL은 주 DB뿐 아니라 임시 파일 등 다른 저장 영역에서 발생할 수도 있습니다.
원인 코드만으로 특정 볼륨의 물리 사용률을 확정하지 않습니다.
[SQLite 오류 코드](https://www.sqlite.org/rescode.html#full),
[Python exception metadata](https://docs.python.org/3.12/library/sqlite3.html#sqlite3.Error).

- 시험은 실제 SQLite engine의 논리 page 한도로 FULL을 발생시킵니다. 실제 파일시스템을 채우거나
  공유 볼륨 전체 고갈·WAL/commit 단계별 ENOSPC·전원 차단을 재현한 것은 아닙니다.
- 범위는 실행 중 poll/commit입니다. archive 최초 open 실패의 상위 worker 사유 분류는 별도입니다.
- 기존 poll 간격의 재시도와 source replay 보존/gap 정책은 유지합니다. 무손실·무제한 복구 보장은 없습니다.
- **NOT_RUN:** Windows/Windows 서비스·실장비/고객 자료·PowerShell7 전달·.NET/WPF/WebView2·장기 부하.
- 접근 제한 보존본은 재시도/우회하지 않았습니다. 이전5075ccd의 복원·동등성·전체 통합이 아닙니다.
