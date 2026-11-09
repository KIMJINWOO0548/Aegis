# 시작 설정 pipe 전송의 생존 경계

사용자가 이전 단계의 남은 동기 설정 전송 한계를 우선 보완하도록 요청했습니다.
기준은 원격 `improve/collector-liveness-recovery-20261109`의
`0db6f5ebcd0e8375cff5f5278709df1a2997570c`입니다.

## 선택과 계약

설정 write를 reader executor와 공유하면 정상 reader가 모든 슬롯을 점유할 수 있습니다.
전체 Popen을 thread로 옮기면 timeout 뒤 늦게 생성되는 자식의 소유권 문제까지 생깁니다.
선택은 Popen/소유 등록을 유지하고 **설정 전용 N-slot executor**에서 직렬화·write/flush를
실행하는 것입니다. reader용 N 슬롯과 분리하며 추가 의존성·UI·저장 형식은 없습니다.

- 프로세스 생성이 반환된 시각부터 고정 시작 deadline을 검사합니다. 기존 worker_timeout
  기본10초를 설정 완료와 첫 유효 frame까지 공유합니다. 설정 전 frame은 수신/발행하지 않아
  RUNNING을 만들거나 deadline을 연장할 수 없습니다.
- timeout/stop은 writer future를 취소하지 않습니다. 실제 writer 완료를 추적합니다.
- 설정 미완료·예외·취소이면 stdin.close보다 **소유 자식 kill/wait를 먼저** 수행합니다.
  buffered write lock/잔여 flush가 close를 막아 kill에 도달하지 못하는 교착을 피합니다.
- 이후 writer 실제 완료, reader 실제 완료, pipe close와 자식 종료를 확인해야만 교체합니다.
  하나라도 회수되지 않으면 WORKER_CLEANUP_FAILED, 추가 writer 금지, stop 오류입니다.
- 기존 모듈별 start수명3회·2초 지연·정상 frame으로 예산 reset 없음은 유지합니다.
  설정 단계 timeout은 WORKER_CONFIG_TIMEOUT으로 구분합니다.
- 원본 archive 경로·DB/WAL·커서·정상 peer worker는 보존합니다. 정상 무데이터와는 별개입니다.

## 판단과 한계

Ruling: writer용 최대N개 thread pool을 분리합니다. 독립 module의 설정 진행을 reader에
종속시키지 않는 선택이며 최대32개 추가 writer thread 비용이 생깁니다.
Ruling: 시작 budget은 설정 완료 뒤 reset하지 않습니다. 느린 설정이 budget을 대부분 쓰면
첫 summary까지 남는 시간이 짧아져 복구할 수 있습니다. 실제 현장 timeout 보정은 미검증입니다.
Ruling: Popen 자체·로그 파일 열기 등 기존 동기 OS 호출 정지는 이번 범위에 넣지 않습니다.
생성이 반환되기 전에는 아직 감시/중단 deadline을 보장하지 못합니다. 막힌 OS thread도 강제 취소하지 않습니다.

## 검증

외부 wall-clock harness로 이벤트 루프가 멈춰도 시험 자체가 끝나도록 합니다. 실제 전송
크기와 pipe 용량을 측정하고, 충분히 작은 시험 소유 pipe에 설정을 읽지 않는 자식을 둡니다.
처음64KiB pipe에서 크기 조건을 충족하지 못한 시도는 제품 RED가 아닙니다.
복구/stop/유한 반복·설정 전 frame·writer 잔존 교체 금지를 시험하고, 두 replay module에서
peer PID와 저장 진행·원본 prefix/수신 시각·커서·SQLite integrity를 확인합니다.
실제 Windows/설비 시험으로 승격하지 않으며 접근 제한 보존본을 재시도하지 않습니다.
