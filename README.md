# Jarvis Command Center

Windows에서 한국어 명령으로 앱, 웹, Excel, 한글, 미디어 동작을 실행하고 AI 질문·대화를 지원하는 개인용 데스크톱 도우미입니다.

현재 소스 버전은 `1.1.0-preview.2`입니다. 기존 `dist`와 `releases`의 V1.1 EXE는 이 P0 소스보다 먼저 만들어졌으므로 최신 소스와 같은 빌드로 취급하지 않습니다.

## 설치

Python 3.12 x64 기준으로 새 가상환경을 만듭니다. 다른 PC에서 복사한 `.venv`는 사용하지 않습니다.

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

- `requirements.in`: 직접 의존성 범위
- `requirements-lock.txt`: V1 런타임의 정확한 고정 버전
- `requirements-build.txt`: 런타임과 PyInstaller 빌드 도구의 정확한 고정 버전

## 실행

`Jarvis_Start.bat`을 실행합니다. 배치 파일은 창을 띄우기 전에 필수 Python 모듈, `pythonw.exe`, GUI와 초기 데이터 파일을 검사합니다.

직접 실행할 수도 있습니다.

```powershell
.venv\Scripts\python.exe jarvis_app.py
```

일반 로그와 오류 로그는 `%LOCALAPPDATA%\Jarvis\logs`의 `jarvis.log`, `errors.log`에 파일당 최대 5MB, 최근 5개까지 순환 저장됩니다. API 키 형식은 로그에서 가립니다.

## 지원 기능 요약

- Windows 앱·웹사이트 열기, 검색, 창 제어, 미디어와 볼륨 제어
- 사용자 앱 별명, 명령 동의어, 단축키·연속 동작·프로그램 실행 매크로
- OpenAI·Gemini 질문/대화와 Ollama 로컬 대화
- 실행 결과·검증 결과·오류 유형·취소·단계 재시도 진단
- 확인 카드, 오래된 승인 차단, 실행 중 두 번째 명령 차단
- 학습 행동의 로컬 재사용과 네이티브 확장 후보 기록

프로그램 실행 매크로는 매번 확인을 받습니다. `shell=True`를 사용하지 않으며 CMD, PowerShell, 시스템 변경 도구, 스크립트 파일, 셸 연결·리디렉션 문자는 차단합니다.

## Excel

실행 중인 Excel의 활성 통합문서와 시트를 대상으로 다음 작업을 지원합니다.

- 셀 값·수식 입력과 합계
- 글꼴·색·정렬 등 범위 서식
- 조건부 서식 또는 일회성 색칠
- 표 필터와 해제
- 텍스트 찾기·바꾸기
- 표 전체 행 정렬

기존 값 덮어쓰기, 대량 변경, 찾기·바꾸기 등은 확인을 받습니다. 승인 대기 중 문서·시트·범위 상태가 달라지면 이전 승인을 실행하지 않습니다. 작업 후 Excel에서 결과를 다시 읽고, 지원되는 실패 경로는 원복합니다.

예시:

```text
엑셀 B1에 100을 입력해줘
엑셀 매출 열 합계를 B1에 넣어줘
엑셀 A2:C10을 굵게 하고 가운데 정렬해줘
엑셀 상태 열에서 완료만 필터해줘
엑셀 매출 열을 오름차순으로 정렬해줘
```

## 한글(HWP)

화면에 보이는 한글의 활성 문서를 대상으로 다음 작업을 지원합니다.

- 현재 커서 위치에 텍스트 입력
- 선택 영역 글자 모양 변경
- 현재 문단 정렬
- 선택 영역 또는 현재 문서 찾기·바꾸기
- 지원 형식으로 저장

읽기 전용·배포용 문서, 보이지 않는 문서, 여러 Automation 인스턴스처럼 대상이 불명확한 경우는 차단합니다. 승인 후 상태가 달라졌거나 검증에 실패하면 실행을 중단하거나 Undo를 시도합니다.

## AI 설정과 데이터 보호

AI API 키는 `%LOCALAPPDATA%\Jarvis\data\dictionaries.json`의 사용자 설정에 저장됩니다. GUI 조회 API는 실제 키를 반환하지 않고 저장 여부만 반환합니다.

- 빈 API 키 칸으로 설정 저장: 기존 키 유지
- 새 키 입력 후 저장: 기존 키 교체
- `저장된 API 키 삭제`: 별도 확인 후 명시적으로 삭제

배포의 `default_data`에는 API 키, 대화, 사용자 사전, 로그를 넣지 않습니다.

## 사용자 데이터와 백업

소스와 EXE는 모두 `%LOCALAPPDATA%\Jarvis\data`를 사용합니다.

- 사전·기억·세션은 임시 파일 작성 후 원자적으로 교체
- 정상 파일을 갱신하기 전에 직전 `.bak` 보존
- 사전은 시간 간격을 둔 순환 백업 추가 유지
- 손상된 원본은 정상 백업이 있으면 복구하고 UI에 알림
- 새 배포 폴더로 교체해도 사용자 데이터는 유지

전체 백업은 `%LOCALAPPDATA%\Jarvis\data` 폴더를 복사하면 됩니다.

## 테스트 상태

P0 수정 전 기준점에서 2026-07-15에 자동 테스트 418개를 실행해 415개 통과, 외부 AI 선택 테스트 3개 건너뜀, 실패 0개를 확인했습니다.

현재 P0 변경분은 사용자의 진행 순서에 따라 아직 전체 테스트를 실행하지 않았습니다. 따라서 위 수치는 현재 소스의 최종 검증 결과가 아니라 작업 전 기준값입니다. 전체 회귀와 실제 Excel·HWP 검증은 코드 작업 완료 후 P3에서 한 번에 수행합니다.

```powershell
.venv\Scripts\python.exe -m engine.test_suite
```

외부 AI 실시간 테스트는 명시적으로 활성화할 때만 자격 증명과 네트워크를 사용합니다.

## 빌드

빌드 환경을 별도로 설치합니다.

```powershell
.venv\Scripts\python.exe -m pip install -r requirements-build.txt
```

`build_dist.bat`은 다음 순서로 실행됩니다.

1. 소스와 기본 데이터 비밀정보 감사
2. 전체 회귀 테스트
3. 깨끗한 Git 작업 트리 확인과 커밋·버전 메타데이터 생성
4. PyInstaller onedir 빌드
5. ZIP 생성
6. EXE·ZIP 사후 감사

빌드에는 Git이 필요합니다. PATH에 없다면 `JARVIS_GIT`에 `git.exe` 전체 경로를 지정합니다. 작업 트리가 깨끗하지 않으면 빌드를 중단합니다.

EXE에는 다음 정보가 함께 들어갑니다.

- `APP_VERSION`
- 전체 Git commit
- clean/dirty 상태
- UTC 빌드 시각
- Windows 파일·제품 버전 정보

런타임에서는 `get_build_info`와 실행 진단으로 이 정보를 확인할 수 있습니다.

## 문서

- [변경 기록](CHANGELOG.md)
- [현재 제한사항](KNOWN_LIMITATIONS.md)
- [최신 감사 상태](AUDIT_REPORT.md)
- 과거 상세 감사 기록은 `docs/audits/archive`에 보관합니다.
