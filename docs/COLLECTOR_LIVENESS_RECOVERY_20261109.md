# 내부 수집 생존과 모듈별 유한 복구

후속 기록: 이 보고서 당시 남긴 동기 설정 전송 한계는 별도
[설정 전송 복구 단계](STARTUP_CONFIG_RECOVERY_20261109.md)에서 보완했습니다.
아래는 기존0db6f5e 단계의 범위·시험과 판단을 보존한 기록입니다.

2026-11-09 UTC. 원격 `improve/numeric-tag-integrity-20261109`의
`bbb0ed10bb43b0aec65d9d0c8d7b79c40b9c4144`를 정확히 기준으로 했습니다.
작업 브랜치는 `improve/collector-liveness-recovery-20261109`입니다.
공개 저장소 범위와 기존 숫자 태그 무결성 커밋을 유지합니다.

## 동작

기존 문서가 미구현으로 남긴 watchdog 경계를 Python `--isolate` 모듈 수집 경로에
추가했습니다. 내부 poll task가 정상 반환·예외·뜻밖 취소로 종료되거나, 실행 중 poll이
`max(10초, HTTP timeout×2)` 동안 끝나지 않으면 worker가 정상 요약을 계속 발행하지 않습니다.
완료된 빈 replay·중복 자료·처리된 HTTP 오류와 폴링 사이의 최대60초 sleep은 실패가 아닙니다.

부모는 동기 설정 전송을 완료하고 reader를 등록한 뒤 deadline을 검사합니다.
기준 시각은 시작/마지막 완전하고 유효한 요약이며 기본10초입니다. 무출력·부분 줄은
deadline을 갱신하지 않습니다. 감지 후 구 worker의 stdin을 닫고 기존5초 종료 유예·kill/wait로
회수하며 reader 완료까지 확인한 다음 해당 모듈만 다시 실행합니다. teardown이 시작된
worker의 늦은 frame이 RUNNING을 다시 발행하지 않도록 publication lock을 사용합니다.

자동 복구는 모듈별 한 start/stop 수명에 기본3회, 지연2초입니다. 최초 실행은 별도입니다.
한 frame을 보낸 뒤 종료를 반복해도 예산을 초기화하지 않습니다. 고갈하면 ingestion의
`workerState=FAILED`, connections의 `WORKER_RECOVERY_EXHAUSTED`로 멈춥니다.
회수 실패는 `WORKER_CLEANUP_FAILED`이고 추가 writer를 열지 않으며 stop도 오류를 반환합니다.
로그 close 실패가 monitor를 조용히 종료시키지 않도록 처리했습니다.

기존 archive 경로·SQLite WAL/FULL·원자적 배치/커서·중복 검증을 그대로 사용합니다.
복구가 DB/WAL을 삭제하거나 cursor를 reset/전진시키지 않습니다. 오래된 관측의 NO_NEW_DATA와
worker 무응답/작업 종료를 구분하며, 다른 모듈의 프로세스를 재시작하지 않습니다.

## 검증

- 기준 전체: **307 PASS / 1 skip**, 85.358초. skip은 Windows 전달 스크립트용 PowerShell7 부재입니다.
- Collector RED6: 내부 task 종료3개는 worker가 끝나지 않아 TimeoutError, 생존 확인3개는
  미구현 AttributeError였습니다. 보완 뒤 수집 관련51개가 통과했습니다.
- Parent 초기 RED:23 FAIL/1 PASS. 23개는 새 옵션 미구현 TypeError이므로 실제 무응답 재현으로
  주장하지 않습니다. 기본 silent child의 정상 stop은 통과했습니다.
- 회수 경계 RED2: teardown 중 늦은 frame의 RUNNING 발행, 로그 close 오류로 monitor가
  조용히 끝나는 문제를 assertion으로 확인한 뒤 보완했습니다.
- 최신 대상: **83 PASS**, 실패·오류·skip0, 50.118초. 내부 종료/active stall/정상 idle,
  무출력/부분 줄, flapping·한도·명시 stop/start, 회수 실패·새 writer 차단을 포함합니다.
- 실제 Linux SIGSTOP 두 모듈 시험: 대상 교체·기존 PID 회수·peer PID 불변과 저장 진행,
  원본 전체 prefix/received UTC 보존·커서 전진·DB integrity·연속 순번·gap0·소유 자식 정리 통과.
  합성256태그/모듈, source50ms/poll100ms 조건이며 현장 용량이나 복구 SLA 검증이 아닙니다.
- 독립 에뮬레이터26개, 생성 계약 drift와 YAML/OpenAPI/reference SQL 정적 검사 통과.
- 최종 전체: **344 PASS / 1 skip**, 실패·오류0, runner122.671초. skip은 같은 Windows
  전달 스크립트용 PowerShell7 부재입니다. 시험 중 제품/신규 시험 파일 해시가 유지됐습니다.
- 기존 Starlette TestClient/httpx deprecation 경고1개가 남았습니다. 의존성 교체는 하지 않았습니다.
- 독립 읽기 검토에서 확정된 Critical/Important/Minor 지적0개. 아래 동기 설정 전송은
  판단유보·후속 경계로 기록했습니다. 검토자가 시험을 재실행한 것은 아닙니다.
- 중간 worker GREEN 시도는 오래된 snapshot을 첫 자료로 보낸 잘못된 대조군2개가 실패했습니다.
  정상 빈 replay와 최초 유효 snapshot/긴 idle로 fixture를 고친 뒤 위83개가 통과했습니다.

전체 회귀 결과와 source hash는 `docs/evidence/collector-liveness-recovery-20261109.json`에 기록했습니다.
기존 시험이 고정 경로에 생성하는 evidence6개는 각 실행 후 원래 바이트로 복원했고 새 실행물은
작업용 임시 폴더에 보존했습니다. UI·.NET·config·emulator·archive/contract·기존 evidence 등
보호 대상223개 추적 파일이 기준과 바이트 동일합니다. 원본 checkout도 유지합니다.

## 운영 경계와 남은 검증

Ruling: 정상 frame으로도 복구 예산을 자동 초기화하지 않습니다. flapping 제한을 위한 선택으로,
흩어진 장애3회 뒤에도 원인 확인과 운영자의 명시 stop/start가 필요할 수 있습니다.
Ruling: 10초 기본값은 이 프로토타입의 경계입니다. 정상 디스크/CPU 지연이 이를 넘으면
재시작할 수 있으므로 실제 설비에 맞는 timeout과 장기 부하 검증이 필요합니다.
Ruling: 설정 전송은 기존 동기 경로로 유지합니다. 자식이 설정을 읽기 전에 멈추고 JSON이
pipe 용량을 넘으면 stdin.write/flush가 host와 peer 응답을 막을 가능성을 검토에서 발견했습니다.
이 조건은 재현·수정하지 않았고, 설정 전달 이전에는 watchdog의 응답 deadline을 보장하지 않습니다.
일반 짧은 설정·설정 완료 뒤 SIGSTOP 성공을 이 시작 경계의 검증으로 승격하지 않습니다.

회수 실패 시 소유 프로세스·reader가 끝나기 전 같은 객체의 start를 거부합니다. 막힌 OS I/O
thread를 Python에서 강제로 종료할 수 없고, `shutdown(wait=False)`도 인터프리터 종료를
보장하지 않으므로 이런 terminal 장애에는 운영자/호스트 조치가 필요할 수 있습니다.
[Python executor 종료 계약](https://docs.python.org/3.12/library/concurrent.futures.html#concurrent.futures.Executor.shutdown).
reader 감시는 timeout에 future를 취소하지 않는
[`asyncio.wait`](https://docs.python.org/3.12/library/asyncio-task.html#asyncio.wait)를 사용합니다.

- **NOT_RUN:** 실제 장비·고객 데이터·Windows/Windows 서비스·PowerShell7 전달 시험·.NET/WPF/WebView2 실행·장기 부하/전원 차단.
- 단일 프로세스 Collector 경로 전체에 자동 supervisor를 추가한 것은 아닙니다. 이번 자동 복구는
  Python 모듈 분리 모드입니다. 호스트/API 자체가 멈추는 장애는 외부 서비스 관리 범위입니다.
- source replay 보존 범위를 넘긴 과거 자료는 기존 gap 정책을 따르며 무손실 복구를 보장하지 않습니다.
- 접근 제한된 이전 보존본을 재시도하거나 우회하지 않았습니다. 이번 이력은 접근 가능한 원격에서
  이어진 독립 구현이며 이전 `5075ccd` 보존본의 복원·동등성·전체 통합을 주장하지 않습니다.
- UI·디자인 변경, 실제 서비스 설치·재시작·배포는 수행하지 않았습니다.
