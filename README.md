# Jarvis Command Center

Windows에서 한국어 명령으로 앱, 웹, Excel, 한글, 미디어 동작을 실행하고
AI 질문·대화를 지원하는 개인용 데스크톱 도우미입니다.

현재 개발 소스 버전은 `1.1.0-rc.4`이며, 보정 릴리스 후보 단계입니다.
R6 전체 검증을 통과한 실행본은 개발 저장소 밖의
`C:\Users\PC04\OneDrive\Desktop\JARVIS_RUNTIME\current\Jarvis`에 설치돼
있습니다.

## 개발 환경

Python 3.12 x64 기준으로 새 가상환경을 만듭니다. 다른 PC에서 복사한
`.venv`는 사용하지 않습니다.

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

- `requirements.in`: 직접 의존성 범위
- `requirements-lock.txt`: 런타임 의존성의 정확한 고정 버전
- `requirements-build.txt`: 런타임과 PyInstaller 빌드 도구의 고정 버전

## 실행

소스 실행은 `Jarvis_Start.bat`을 사용합니다. 배치 파일은 창을 띄우기 전에
필수 Python 모듈, `pythonw.exe`, GUI와 초기 데이터 파일을 검사합니다.

```powershell
.venv\Scripts\python.exe jarvis_app.py
```

일반 로그와 오류 로그는 `%LOCALAPPDATA%\Jarvis\logs`에 순환 저장됩니다.
소스와 EXE는 모두 `%LOCALAPPDATA%\Jarvis\data`를 사용자 데이터 경로로
사용하며, 배포의 `default_data`에는 API 키·대화·사용자 사전·로그를 넣지
않습니다.

## 지원 기능

- Windows 앱·웹사이트 열기, 검색, 창 제어, 미디어와 볼륨 제어
- 사용자 앱 별명, 명령 동의어, 단축키·연속 동작·프로그램 실행 매크로
- OpenAI·Gemini 기반 AI 질문·명령 해석
- 확인·취소·busy 보호, 결과 검증, 실패 진단과 단계 재시도
- 학습 행동의 로컬 재사용과 네이티브 확장 후보 기록

프로그램 실행 매크로는 매번 확인을 받습니다. 셸·스크립트·시스템 관리
명령과 셸 연결·리디렉션 문자는 차단합니다.

### Excel

실행 중인 Excel의 활성 통합문서와 시트에서 셀 값·수식 입력, 합계, 범위
서식, 조건부 서식, 필터, 찾기·바꾸기, 행 정렬을 지원합니다. 승인 대기 중
문서 상태가 달라지면 실행하지 않고, 실행 뒤 결과를 다시 읽어 검증합니다.

### 한글(HWP)

화면에 보이는 활성 한글 문서에서 텍스트 입력, 글자 모양, 문단 정렬,
찾기·바꾸기, 지원 형식 저장을 제공합니다. 대상이 불명확하거나 보호 상태인
경우 실행하지 않습니다.

Word와 PowerPoint 네이티브 편집은 아직 구현되지 않았습니다. 확장 시 지킬
경계는 [Office 어댑터 계약](docs/OFFICE_ADAPTER_CONTRACT.md)에 기록돼 있습니다.

## 테스트

복원된 테스트는 다음 그룹으로 실행합니다.

```powershell
.venv\Scripts\python.exe -m tests.test_runner unit
.venv\Scripts\python.exe -m tests.test_runner integration
.venv\Scripts\python.exe -m tests.test_runner windows
.venv\Scripts\python.exe -m tests.test_runner live
.venv\Scripts\python.exe -m tests.test_runner all
```

2026-07-15 R6 최종 검증에서 자동 테스트 435개 중 432개가 통과했고 외부 AI
라이브 테스트 3개는 실제 키 사용 승인이 없어 건너뛰었으며 실패는 0개입니다.
실제 Excel 시나리오 20/20과 한글 핵심 시나리오 8/8도 통과했습니다.

## 빌드와 릴리스

빌드 의존성은 별도 가상환경에 설치합니다.

```powershell
.venv\Scripts\python.exe -m pip install -r requirements-build.txt
build_dist.bat
```

`build_dist.bat`은 입력 감사, 전체 회귀, Git·버전 메타데이터 생성,
PyInstaller onedir 빌드, ZIP 생성, 산출물 감사를 수행합니다. 정식 EXE는
깨끗한 릴리스 커밋에서만 만들며 내장 commit이 릴리스 태그와 같아야 합니다.
빌드 결과는 개발 저장소의 `dist`에 영구 보관하지 않고 검증 후보 경로로
옮깁니다.

개인 로컬 실행본은 미서명을 허용하되 SHA-256을 기록합니다. 타인 또는
인터넷 배포에는 Authenticode 서명과 타임스탬프가 필요합니다.

## 폴더 정책

- 개발 저장소: 소스, Git, 테스트, 검증 도구, 빌드 설정과 문서 보존
- 실행 폴더: 풀린 현재 실행본 1개와 직전 정상 ZIP 1개만 보존
- 사용자 데이터: `%LOCALAPPDATA%\Jarvis\data`, 정리·배포 대상 아님

자세한 삭제·보존 기준은 [RULEBOOK](RULEBOOK.md)을 따릅니다.

## 문서

- [변경 기록](CHANGELOG.md)
- [현재 제한사항](KNOWN_LIMITATIONS.md)
- [감사 상태](AUDIT_REPORT.md)
- [미해결 사항 해소 계획](REMEDIATION_PLAN.md)
