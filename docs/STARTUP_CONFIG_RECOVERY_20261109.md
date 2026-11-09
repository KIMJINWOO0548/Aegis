# 설정 전송 중 정지 감시와 안전한 중단

2026-11-09 UTC. 원격 `improve/collector-liveness-recovery-20261109`의
`0db6f5ebcd0e8375cff5f5278709df1a2997570c`를 정확히 기준으로 했습니다.
새 작업 브랜치는 `improve/startup-config-recovery-20261109`입니다.
기존 숫자 무결성·모듈 생존 감시 이력을 보존하는 후속 변경입니다.

## 변경

Popen이 반환된 뒤 설정 직렬화·stdin write/flush를 전용 writer executor에서 수행합니다.
장기 실행 reader와 슬롯을 공유하지 않으므로 한 module의 설정 정지가 parent event loop와
다른 module의 설정 진행을 막지 않습니다. 모듈 수N에 대해 writer/reader 각각 최대N 슬롯입니다.

기존 worker_timeout 기본10초를 설정 완료와 최초 요약까지 같은 시작 시각으로 검사합니다.
설정 성공 전에는 reader를 시작하지 않으며, 미리 나온 frame이 RUNNING을 발행하거나 설정
deadline을 늘리지 못합니다. timeout과 stop은 future를 취소해 완료한 것처럼 취급하지 않습니다.

설정 writer가 미완료·예외·취소 상태이면 stdin.close보다 소유 자식 kill/wait를 먼저 수행합니다.
이후 writer의 실제 완료와 reader 회수·pipe close를 확인해야 다음 자식을 실행합니다.
실패한 write의 잔여 buffered flush도 이 경계를 따릅니다. 회수 불명은 WORKER_CLEANUP_FAILED로
멈추고 새 writer 및 명시 start를 차단하며 stop도 오류를 반환합니다.

설정 timeout은 WORKER_CONFIG_TIMEOUT입니다. 기존 start수명당 자동 복구3회·2초 지연,
정상 frame으로 예산 reset 없음, 동일 archive 경로와 기존 SQLite 원자적 커서는 유지합니다.
UI·Collector/RawArchive·DB schema·원본·커서를 변경하지 않았습니다.

## 실제 검증

- 기준 전체 **344 PASS / 1 skip**, runner123.148초. skip은 Windows 전달 스크립트의 PowerShell7 부재입니다.
- 영구 회귀 RED: 유효한 설정11,399bytes > 실제 시험 pipe4,096bytes에서 parent가 정지했습니다.
  외부12초 deadline이 작동했고 테스트 소유 자식은 모두 회수됐습니다. 같은-loop timeout을
  무응답 재현의 근거로 사용하지 않았습니다.
- controlled process-I/O 경계도 event-loop block assertion RED를 확인한 뒤 GREEN입니다.
  최종 단위3개는 pending/실패한 writer의 kill-before-close, 미완료 writer의 교체·재시작 차단을 검사합니다.
- 실제 Linux 외부 harness4개 **PASS**, 7.503초: 루프 응답·시작 전송 중 stop·조기 frame의
  deadline/예산 우회 차단·두 실제 module worker의 replay 보존. 모든 최종 외부 harness에서
  외부 구조 deadline 미발동과 소유 자식 회수를 확인했습니다.
- 핵심3개는 실제 긴 Unicode 설정 JSON을4KiB pipe로 전송합니다. peer 통합은 짧은 실제 SQLite
  경로를 쓰고, 정지한 자식 pipe에 테스트용 유효 JSON 공백4KiB를 먼저 채운 뒤 실제 설정
  write/flush를 막았습니다. payload/prefill/전체 wire 크기를 별도 기록합니다.
- peer 통합에서 peer PID·저장 진행, 대상 재복구, 교체 전 실제 writer 완료·구 자식 회수,
  원본 전체 prefix/received UTC·커서 비후퇴/진행·SQLite integrity·연속 순번·gap0을 확인했습니다.
  합성 HTTP producer/8신호 조건이며 실장비·현장 성능·Windows 서비스 증거가 아닙니다.

- 최종 전체 **351 PASS / 1 skip**, 실패·오류0, runner130.490초. baseline과 같은 Windows 전달
  시험만 건너뛰었고 기존 Starlette/httpx deprecation 경고1개가 남았습니다.
- 전체 실행 중 제품·시험/harness 해시 동일, 보호 대상230개 추적 파일과 기존 evidence 바이트 동일.
  독립 emulator26개와 생성 계약/YAML/OpenAPI/reference SQL 정적 검사도 통과했습니다.
- 독립 읽기 검토 Critical/Important0개. Minor1개(아래 진단 문구)를 남겼으며 검토자가 시험을 재실행한 것은 아닙니다.

전체 회귀와 독립 검토 결과는 `docs/evidence/startup-config-recovery-20261109.json`에 기록했습니다.
명령은 `python -m pytest -q`, 독립 emulator의 unittest, `generate_contracts.py --check`,
`validate_v34.py`입니다. 시험 로그·DB·raw process registry는 임시 폴더에만 남깁니다.

## 실패한 준비·시험과 보존

첫 임시 probe의 자연 pipe는64KiB여서 설정 크기가 용량을 넘지 않았습니다. 이 시도는 제품
RED가 아닙니다. 해당 임시 wrapper의 소유 등록 누락도 발견했으며, 자식은 이미 종료된 Z 상태로
호스트 reaper 대기였습니다. 실행 중인 자식이 아니며 직접 부모가 아닌 종료 기록은 회수하지 못했습니다.
후속 영구 harness는 Popen 직후 등록, 시작 시각/PID 검증과 별도 subreaper로 회수를 확인합니다.

중간 peer 시험은 약3KiB의 경로가 OS/WorkerConfig 제약을 통과해도 SQLite에서 열리지 않아
초기 커밋 대기가 실패했습니다. 제품 수정으로 숨기지 않고 위 짧은 DB 경로/prefill fixture로
바꾸었습니다. 원본 prefix·수신 시각·커서 검증은 그대로 유지했습니다.
중간84개 regression은 통과했으나 새 시험 파일이 병렬 작성 중이어서 전체 tree hash가 달랐습니다.
이 결과를 동결된 최종 전체 suite 증거로 사용하지 않습니다.

기존 시험의 고정 evidence6개는 실행마다 원래 바이트로 복원하고 새 실행물은 별도로 보관합니다.
접근 제한 보존본을 재시도하거나 우회하지 않았으며 이전5075ccd의 복원·동등성을 주장하지 않습니다.

## 판단과 남은 범위

Ruling: writer 전용 최대N thread를 추가합니다. reader 슬롯 고갈을 피하지만 최대32개 추가
thread 비용이 있습니다. 장기·32module 용량 검증은 하지 않았습니다.
Ruling: 설정 성공 뒤 시작 budget을 reset하지 않습니다. 설정이 느리면 최초 frame까지 남는
시간이 짧아져 복구할 수 있으며, 현장 timeout 조정은 별도 검증입니다.
Ruling: Popen 자체와 로그 파일 열기 등 기존 동기 OS 호출 정지는 이번 범위 밖입니다.
이 작은 보완은 **Popen이 반환된 뒤 설정 I/O가 막히는 장애**를 다룹니다.

Minor deferred: 시작/종료 오류 문구의 “worker or reader”에는 configuration writer가 명시되지
않습니다. writer 차단/회수 검사는 실제로 수행되며 남은 항목은 운영 진단 문구입니다.

막힌 OS thread는 강제로 취소하지 않습니다. 자식을 회수해도 writer가 끝나지 않으면 교체를
막고 운영자/호스트 조치가 필요할 수 있습니다. `shutdown(wait=False)`도 프로세스 종료를
보장하지 않습니다. [Python executor 종료 계약](https://docs.python.org/3.12/library/concurrent.futures.html#concurrent.futures.Executor.shutdown).

**NOT_RUN:** 실제 장비·고객 데이터·Windows/Windows 서비스·PowerShell7 전달·.NET/WPF/WebView2
실행·장기 부하·전원 차단. 서비스 설치·배포·UI/디자인 변경은 수행하지 않았습니다.
