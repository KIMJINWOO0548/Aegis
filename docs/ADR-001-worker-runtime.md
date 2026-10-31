# ADR-001: D0a 수집 프로세스 런타임 비교

날짜: 2026-10-18. 상태: 잠정 결정, 서비스 설치 검증 전.

수집 서비스 후보는 .NET Generic Host Worker로 유지한다. Python은 FastAPI와 후속 ML 작업에 사용한다.
Windows 서비스 수명주기 지원은 `Microsoft.Extensions.Hosting.WindowsServices`로 연결했지만 실제 서비스는 설치하지 않았다.
Python 후보는 동일한 이벤트 계약의 유한한 콘솔 heartbeat까지 구현했다. Python 서비스 등록/패키징 방식은 D1에서 결정한다.

## 실제 비교

동일 traceId `ac901964-a7a3-453c-9941-4641655b6d30`, count 3, interval 20ms로 각각 별도 프로세스를 실행했다.

| 항목 | Python | .NET Worker |
|---|---:|---:|
| 종료 코드 | 0 | 0 |
| 이벤트 | SERVICE_STARTED / SERVICE_STOPPED 2개 | 동일 |
| 완료 heartbeat | 3 | 3 |
| 실행 전체 경과 | 1,788.83ms | 994.42ms |
| traceId / 계약 검증 | 통과 | 통과 |

단일 실행의 시작/로드/종료를 포함한 관측값이며 성능 순위의 근거로 사용하지 않는다.
이벤트 내부 durationMs는 시작 이벤트 직렬화의 포함 범위가 달라 직접 비교하지 않는다.
CPU와 메모리는 측정하지 않았다. 결과는 `evidence/heartbeat-comparison.json`과 두 `*-heartbeat.jsonl`에 있다.

```powershell
.\.venv\Scripts\python.exe -m aegis.heartbeat --count 3 --interval-ms 20 --trace-id ac901964-a7a3-453c-9941-4641655b6d30
dotnet src/Aegis.Worker/bin/Release/net10.0/Aegis.Worker.dll --count 3 --interval-ms 20 --trace-id ac901964-a7a3-453c-9941-4641655b6d30
```

## 결정 근거와 다음 검증

.NET 후보는 WPF와 DTO를 공유하고 Generic Host의 종료 및 Windows 서비스 수명주기 연결을 사용할 수 있다.
이는 패키징 방향의 근거이며 운영 안정성이 입증됐다는 뜻은 아니다.
Python 가상환경은 별도 Python 런타임에 의존한다. D1에서는 서비스 래퍼/호스트 후보의 라이선스와 배포 방법도 비교한다.

D1 격리 Windows 환경에서 설치/제거, 서비스 계정, 자동 시작, 재부팅, UI 종료 후 5분 수집, 중단/복구, CPU·메모리를 실제 시험한다.
이번 작업으로 이 항목을 완료 처리하지 않는다.

공식 참고: [Microsoft Windows Worker 서비스](https://learn.microsoft.com/en-us/dotnet/core/extensions/windows-service), [Python venv](https://docs.python.org/3.12/library/venv.html).
