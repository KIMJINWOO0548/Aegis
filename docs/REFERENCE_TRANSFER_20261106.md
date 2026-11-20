# 신호 추이 표시 범위

신호가 빠르게 바뀌면 기존 최대 120개 요약의 넓은 그래프에서 최근 변화를 읽기 어려웠다. 차트에 보관 중인 전체/마지막 보관 시각 기준 30초/60초를 선택하는 native select를 추가했다. 표시된 시작·종료 UTC와 표시/보관 개수를 함께 보여 준다. 필터는 UI 메모리 이력을 자르거나 원시 저장소를 조회하지 않는다. 수신 중단 null 표식도 마지막 보관 시각에 포함되며 이력의 현재성은 기존 판정에 따른다.

공개 Grafana의 상대 범위·현재 범위 맥락을 참고했고 [Replica 조사 기록](../replica/recon.md)에서 관찰과 독자 선택을 구분했다. REA는 실제 공개 문서 화면을 관찰했으며 서비스 계정·제품 소스·사유 API는 사용하지 않았다. screenshots/DOM은 로컬 참고 전용으로 이 저장소에 배포하지 않는다.

## 실제 검증

기존 desktop Aegis UI 설치의 Node v24.22.1/Vitest 3.2.4/TypeScript 5.9.2를 읽기 전용으로 재사용했다. 새 패키지·플러그인은 설치하지 않았다. 원본 저장소의 의존성과 코드도 변경하지 않았다.

| 검사 | 결과 |
|---|---|
| 원래 Vite 설정으로 설치된 Vitest 호출 | clone에 Vite 의존성이 없어 설정 로딩 실패, exit 1 |
| ignored `artifacts/reference-20261106/vitest-reference.config.mjs`로 기존 2개 suite | 16 통과, exit 0 |
| 독립 `history-window.test.ts` 구현 전 | 3 실패, exit 1 |
| 같은 config로 UI 전체 | 19 통과, exit 0 |
| ignored `tsconfig.reference.json`으로 제품 src 타입 검사 | 통과, exit 0; 변경하지 않은 Vite 설정의 타입 검사는 제외 |
| 임시 별도 설정으로 실제 Vite production build | esbuild가 기존 desktop 의존성 Junction을 읽을 때 Access denied; 번들 생성 전 실패, exit 1 |

실제 Vitest 명령은 UI 디렉터리에서 `node <기존 desktop node_modules>/vitest/vitest.mjs run --root . --config ../artifacts/reference-20261106/vitest-reference.config.mjs`다. 임시 config는 Vitest import만 기존 설치 경로로 해석한다. 3개 독립 테스트는 시간 경계 포함·빈 이력·null 단절 유지·전체 복귀·원본 불변을 확인한다.

처음 시도한 dependency 복사는 pnpm Junction의 중복 순회가 길어 Ctrl+C로 중단했고, clone의 Git-ignored node_modules 일부만 남았다. 후속 top-level Junction 생성은 sandbox Access denied로 실행되지 않았고 재시도하지 않았다. 사용자 기록을 확인한 뒤 신규 권한이나 설치 없이 위 순수 테스트 설정으로 진행했다. Vite build의 Access denied도 같은 기록 확인 후 이 leaf에서 재시도하지 않았다. 상위 작업의 허용된 host build 결과가 있으면 별도 기록한다.

실제 브라우저 DOM·키보드·반응형·차트 레이아웃·gateway/SignalR·WebView2는 이번 증분에서 실행하지 않았다. Python/C# 전부 검사도 UI 변경과 분리하여 미실행이다. 이전 문서의 알려진 실패를 해결한 것으로 표시하지 않는다. Replica parity는 선정된 구현 패턴만 계산하며 시각·출시 준비의 판정이 아니다. 커밋·push·원격 CI는 상위 작업의 최종 기록으로 확인한다.

## 상위 작업의 빌드 확인

동일 기존 설치를 읽는 별도 host 설정으로 Vite 7.1.7 production build가 통과했다(exit0, 2461모듈, 25.38초). 검증 설정의 Tailwind 별칭을 패키지 디렉터리 대신 기존 `index.css`로 지정했다. 제품 Vite 설정·전역 설정·의존성 버전은 변경하지 않았다. 최초 host 시도의 EISDIR 실패와 위 sandbox 실패는 이 통과 결과로 대체하지 않고 구분한다. SignalR의 기존 PURE 주석과 500kB 초과 chunk 경고는 남는다. 실제 gateway/브라우저 통합 통과를 의미하지 않는다.
