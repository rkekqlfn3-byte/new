# JARVIS 브라우저·Office COM 보완 보고서

- 대상 버전: `1.1.0-rc.5`
- 검증일: 2026-07-15 (KST)

## 변경 전

- GUI 시작은 Eel의 `chrome` 모드 한 번만 호출했다. Chrome이 없거나 실행이
  거부되면 Edge·기본 브라우저로 이어지는 명시적 폴백과 사용자 안내가 없었다.
- Excel/HWP 어댑터는 작업 스레드에서 COM을 최초 한 번 초기화한 뒤
  `CoUninitialize()`를 호출하지 않았다.
- 기본 어댑터는 실행 중인 사용자 인스턴스에만 연결했지만, 향후 Jarvis가 직접
  만든 인스턴스와 구분할 소유권 계약이 없었다.

## 변경 후

- GUI는 Chrome → Edge → 시스템 기본 브라우저 → Firefox/Brave 후보 순으로
  실행을 시도한다. 각 실패 이유와 최종 선택 브라우저를 로그에 기록한다.
- 모든 브라우저가 실패하면 시도 결과, 설치·기본 앱·보안 프로그램 확인 방법,
  로그 경로를 Windows 오류 창으로 알리고 실패 상태로 종료한다.
- Eel 서버를 먼저 시작한 뒤 브라우저를 열어 초기 연결 거부 가능성을 줄였다.
- Excel/HWP 작업 단위마다 COM 초기화와 해제를 `try/finally`로 짝지었다.
- `OfficeApplicationLease.owns_application`과 `created_by_jarvis`로 소유권을
  명시한다. 원시 COM 객체는 항상 사용자 소유 연결로 취급한다.
- 사용자 소유 인스턴스에는 문서 `Close()`와 Application `Quit()`을 호출하지
  않는다. Jarvis 소유 세션은 등록된 소유 문서만 닫고 해당 Application만
  종료한 뒤 참조를 비운다.
- 내부 `_oleobj_` 포인터의 직접 `Release()`는 사용하지 않는다.
- AI 생성 Office 코드도 `CoInitialize()`/`CoUninitialize()`와 `try/finally`가
  없거나 사용자 Office에 `Quit()`을 호출하면 거부한다.

## 변경 파일

- `jarvis_app.py`: 비동기 GUI 서버 시작과 브라우저 폴백 연결
- `engine/browser_launcher.py`: 브라우저 검색·실행·로그·오류 안내
- `engine/app_actions/com_lifecycle.py`: COM 구간과 Office 소유권 계약
- `engine/app_actions/excel_adapter.py`: Excel 작업별 COM 구간과 소유권 정리
- `engine/app_actions/hwp_adapter.py`: HWP 작업별 COM 구간과 소유권 정리
- `engine/ai_actions/result_validator.py`, `engine/llm/prompts.py`: 동적 COM 규칙
- `tests/unit/test_browser_launcher.py`: 브라우저 폴백 회귀 테스트
- `tests/windows/test_com_lifecycle.py`: Excel/HWP 소유권·예외·스레드 테스트
- `verification/office_com_lifecycle_probe.py`: 실제 Excel 100회·프로세스 검사
- `tests/test_runner.py`: 새 회귀 테스트를 전체 게이트에 포함
- `engine/version.py`, `README.md`, `CHANGELOG.md`: RC5 문서화

## 테스트 결과

- 전체 자동 테스트: 452개 실행, 449개 통과, 3개 조건부 건너뜀, 실패 0개
- 기존 Excel/HWP 집중 회귀 테스트: 55개 통과
- 새 브라우저·COM 테스트: 14개 통과
- 실제 Excel 임시 통합문서 명령 100회: 검증 성공
- 100회 동안 새 Excel 프로세스 누적: 없음
- 사용자 소유로 취급한 Excel: Jarvis 작업 후 유지됨
- Jarvis 소유 Excel 정상 작업·고의 예외: 두 경우 모두 프로세스 종료
- 실제 검사 전후 `EXCEL.EXE`: 0개 → 0개
- 사용자가 열어 둔 HWP 프로세스와 문서: 검사 전후 유지

## 남은 위험 요소

- 실제 HWP 문서가 열려 있어 사용자 원본을 보호하기 위해 HWP 실물 변경·종료
  검사는 수행하지 않았다. HWP 소유권과 예외 정리는 모의 COM 객체로 검증했다.
- 브라우저 프로세스 생성 성공 뒤 프로필 손상이나 보안 제품이 화면 표시를 나중에
  차단하는 경우까지 완전히 판정할 수는 없다. 이 경우 로그와 기본 브라우저 재시도를
  이용해야 한다.
- Office 추가 기능이나 자체 확인 창이 Application 종료를 지연할 가능성은 남는다.
  Excel 기본 설치 환경의 실제 정상·예외 종료 검사는 통과했다.
- 실행파일은 Authenticode 서명이 없어 Windows SmartScreen 경고가 나타날 수 있다.
