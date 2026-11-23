# Windows 계측 동반 모드

Aegis는 로컬 장비 제어 도구와 나란히 쓸 수 있는 읽기 전용 계측 작업대를 제공합니다. 메인 창에는 그래파이트 제목 표시줄, 수집 상태, 장비·태그 목록과 추세를 배치합니다. 좁은 화면에서는 목록을 접고 선택한 신호를 계속 조사할 수 있습니다. 포터블 후보는 두 합성 수집기, 기존 SQLite 보관 경로, 인증 Gateway, Windows 작업대를 실행합니다.

## 화면 사용

1. Aegis를 시작하면 트레이에서 작업대와 오버레이를 각각 열 수 있습니다.
   작업대 상단의 테마에서 다크, 라이트, 따뜻한 톤을 선택할 수 있습니다. 선택은 작업대와 오버레이에 함께 적용되며 재실행 뒤에도 유지됩니다.
2. `고정 신호 설정`에서 장비·신호를 찾고 고정한 뒤, 크게 볼 신호를 맨 위로 옮깁니다. 고정 순서와 주 화면 모서리 위치는 재시작 후에도 유지됩니다.
3. 작업대를 트레이로 보내면 오버레이를 계속 볼 수 있습니다. 고정한 태그 수에 따라 높이가 달라지고, 화면 높이에 맞춰 최대 다섯 개까지 표시합니다. 남은 태그는 개수로 표시합니다.
4. 작업대의 추세에서는 방향키와 `Home`·`End`로 관측을 선택합니다. 중단과 긴 관측 간격을 다른 선과 문구로 표시하며, 선택 항목의 관측 시각·수신 품질·받은 값을 보여줍니다. 수신 시각이 계약에 있으면 함께 표시합니다. 원시 원본은 기존 보관 이력에서 조회합니다.
5. `Aegis 종료`로 작업대와 동반 창을 닫습니다. 포터블 실행기는 이 세션에 속한 합성 수집기와 Gateway를 정리합니다.

## 구현 방식과 참고 사례

- 기존 Windows 제목 표시줄, 로컬 작업대, 추세, 경보·진단과 보관 조회를 이어서 사용합니다. 오버레이는 독립 WPF/Win32 창으로 표시하고 메인 창의 WebView나 장비 제어 창과 입력을 공유하지 않습니다. 트레이에서 표시와 위치를 결정할 수 있게 설계했습니다. [Microsoft PowerToys 창 고정](https://learn.microsoft.com/en-us/windows/powertoys/always-on-top)
- Ignition의 태그 품질과 UaExpert의 시각 분리를 참고했습니다. 값 0, 값 없음, 미수신, 오래됨, 복원을 별도로 표시하고, 수집기 상태·원래 관측 시각·수신 시각을 각각 보여줍니다. 끊긴 데이터는 현재값으로 갱신되지 않습니다. [Ignition 품질 표시](https://www.docs.inductiveautomation.com/docs/8.3/platform/tags/quality-codes-and-overlays) · [UaExpert Data Access](https://documentation.unified-automation.com/uaexpert/2.0.2/html/data_access_view.html)
- PI Vision의 시계열 관측 선택과 Foxglove의 상태 전환 표시를 참고해 관측 커서와 연결 중단을 구분합니다. 기존 1Hz UI 요약에서 빠진 구간을 보간해 이어 그리지 않습니다. [AVEVA PI Vision](https://docs.aveva.com/bundle/pi-vision/page/1642097.html) · [Foxglove State Transitions](https://docs.foxglove.dev/docs/visualization/panels/state-transitions)

## 실행본 만들기

Windows x64에서 `pnpm --dir ui build` 후 `dotnet build Aegis.slnx -c Release`로 작업대와 Gateway를 만듭니다. `scripts/stage_windows_release.ps1 -Destination <새_폴더>`는 실행본과 무결성 목록을 준비합니다. `Aegis.cmd --check`로 런타임과 패키지 파일을 확인할 수 있습니다. [런타임 및 실행 옵션](../scripts/windows_release/README.ko.md)

## 지원 범위

포터블 실행기는 로컬 합성 장비 전용입니다. 일반 실행은 주 모니터에서 작업대와 함께 사용할 수 있습니다. 실제 장비 드라이버·설치 런타임 자동 등록·코드 서명은 별도 릴리스 범위입니다. Windows 원시 보관 백업·복원과 용량 진단은 기존 플랫폼 제한을 따릅니다. 작업대의 기존 진단과 원본 읽기 경로는 그대로 접근할 수 있습니다.

합성 포터블은 시작할 때 해당 실행에서 만든 최대 네 개의 모듈 보관소를 이력에 연결합니다. 다섯 개 이상인 시험 실행은 제한된 공통 이력에서 보관 원본의 일부만 표시하지 않습니다.
