# Changelog

## 개발 중 — Prototype 1.0 편집모드

- `v1.1.0-rc.5`에서 `feature/prototype-1.0-edit-mode` 브랜치를 분리했다.
- Excel·한글·Word·PowerPoint의 COM 등록, 앱 버전, 선택 읽기, 임시 편집,
  Undo, 저장 재개봉, 소유 프로세스 정리를 확인하는 1단계 검증 도구를 추가했다.
- 기존 사용자 앱이 실행 중이면 실제 앱 검증을 거부하고, JARVIS가 생성한 빈
  임시 문서만 사용하는 보호 경계를 추가했다.
- COM 변경이 앱의 네이티브 Undo 이력에 항상 등록되지 않는 점을 반영해,
  네이티브 Undo와 변경 전 스냅샷 복원을 구분해 검증하도록 했다.
- 편집 요청·준비 작업·실행 결과를 COM-free JSON 계약으로 정의하고, 앱
  어댑터 허용 작업만 준비·실행·검증·복구하는 공통 coordinator를 추가했다.
- 편집 세션의 연결·준비·승인·실행·검증·커밋·복구 상태와 revision 충돌
  검사를 추가했다.
- `edit` 요청을 기존 명령 파이프라인과 분리하고, 세션 정보가 없거나 아직
  핸들러가 연결되지 않았으면 실행하지 않도록 했다.
- 알 수 없는 모드의 명령 폴백과 질문·대화모드의 앱 실행을 백엔드에서
  차단하는 모드 권한 정책을 추가했다.
- GUI에 `명령 | 질문 | 편집` 탭과 파일 선택·드롭, 열린 문서 연결, 연결 해제,
  자동 창 배치 컨트롤을 추가했다.
- Excel·한글·Word·PowerPoint 지원 확장자와 앱 설치 여부를 확인하고, 열린
  네이티브 문서의 전체 경로가 일치할 때만 세션을 만드는 `FileIntakeManager`를
  추가했다.
- 세션에는 COM 객체 대신 파일 경로·앱 유형·파일 식별 fingerprint·선택 참조만
  저장하고, 파일 교체나 다른 세션 요청을 `stale_context`로 차단한다.
- 파일 경로 ROT moniker fallback을 추가해 Office Application이 ROT에 늦게
  등록되는 경우에도 선택한 Workbook·Document·Presentation을 정확히 찾는다.
- 문서 모니터 기준 68:32 자동 배치를 세션당 한 번만 시도하고, 배치 실패가
  문서 연결을 취소하지 않도록 격리했다.
- 실제 임시 Excel·Word·PowerPoint 파일의 연결·세션·프로세스 정리를 검증했고,
  실행 중인 사용자 한글 문서는 읽기 전용 세션 탐색만 수행해 보존했다.

## 1.1.0-rc.5 — 브라우저 폴백과 Office COM 생명주기

- Chrome, Edge, 시스템 기본 브라우저, 추가 설치 브라우저 순서의 GUI 실행
  폴백과 사용자 오류 안내, 선택·실패 로그를 추가했다.
- Excel/HWP COM 작업마다 `CoInitialize()`와 `CoUninitialize()`를 짝지어
  호출하고, 기존 인스턴스와 Jarvis 생성 인스턴스의 소유권을 분리했다.
- Jarvis 소유 문서와 Application만 정리하며, 예외가 발생해도 소유
  Application 종료를 시도하도록 공통 생명주기 객체를 추가했다.
- 동적 Office 코드에도 COM 초기화·해제와 사용자 인스턴스 보존 규칙을
  적용했다.

## 1.1.0-rc.4 — 빈 앱 사전과 클라우드 AI 단순화

- 최초 앱 사전을 완전히 비우고 사용자가 앱·최근 앱·북마크 스캔을 실행한 뒤에만
  항목이 등록되도록 기본 앱·웹사이트 자동 주입을 제거했다.
- Ollama 제공자, 로컬 모델 설정, 상단 전환 토글과 클라우드 실패 시 자동 대체 경로를
  제거했다. AI 기능은 OpenAI 또는 Gemini API 키가 있을 때만 동작한다.

## 1.1.0-rc.3 — 단일 실행형 재부팅 보정

- PyInstaller one-file 실행본이 재부팅할 때 이전 `_MEI` 임시 경로를 재사용하지 않도록
  새 압축 해제 환경을 요청한다.
- 친구 전달용 단일 EXE에는 빈 기본 기억·빈 설정만 포함하고 대화 세션을 포함하지 않는다.

## 1.1.0-rc.2 — 보정 릴리스 후보

### 보안과 결과 정확성

- `PyPDF2`를 `pypdf 6.14.2`로 교체하고 PDF 크기·페이지·추출 문자 상한 추가
- 시스템 종료 명령을 `subprocess.run(..., shell=False)`로 전환하고 실제
  반환 코드에 따라 성공·실패 보고
- runtime 소스의 bare `except`, 비의도적 `print()` 경로 정리
- 사전·학습 GUI의 동적 HTML 삽입을 안전한 DOM 조립과 숫자 검증으로 전환

### 구조와 재현성

- 실행 폴더와 개발 저장소를 분리하고 테스트·검증·빌드 도구 복원
- 명령 파이프라인을 대화·네이티브·학습·매크로·AI fallback route로 분리
- AI batch 실행을 dispatcher, action executor, 결과 aggregator로 분리
- confirmation 생성과 학습 행동 재실행을 parser 밖의 서비스로 분리
- Excel·HWP의 COM-free helper 및 Word·PowerPoint 확장 계약 추가
- 동결 EXE 검증 probe를 현재 confirmation 정책과 실행 결과 구조에 맞게 갱신

### 최종 검증

- 자동 테스트 435개: 432개 통과, 외부 AI 3개 skip, 실패 0개
- `pip-audit` 알려진 취약점 0건
- 실제 Excel 20/20, 한글 8/8 통과
- 후보 EXE 기동, frozen worker, timeout·취소·재시도·잠금, TLS, Excel COM 통과
- 기존 사용자 데이터 33개 파일 사전·사후 바이트 동일
- 태그 `v1.1.0-rc.2`, 빌드 commit `ceb6c17d14f495d167a3c12417464c124bde0d90`

## 1.1.0 — 2026-07-15 이전 실행본

- HTML 정화와 로컬 마크다운 렌더러
- 동시 명령 busy 보호와 실행 진단
- 학습 네이티브 실행 경로 보강
- Excel·한글 네이티브 편집, 확인·검증·복구 기반

이 빌드는 R6 교체 전 current였으며, 현재는 `JARVIS_RUNTIME\previous`에
직전 정상 백업으로 보존돼 있습니다.

## 1.0 — 2026-07-14

- Windows 앱·파일·웹·미디어 기본 명령
- AI 질문·대화와 동적 행동 안전 검사
- 사용자 사전·매크로·세션 저장 기반
