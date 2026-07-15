# JARVIS 감사 미해결 사항 전면 해소 계획서

- 작성일: 2026-07-15 (KST)
- 근거 문서: `AUDIT_REPORT.md`
- 대상 버전: `1.1.0-preview.2` 이후 첫 보정 릴리스
- 종합 목표: 감사 P1·P2 지적을 모두 닫고, 통합 계획서의 최종 완료 기준을 충족한 뒤 Word·PowerPoint 개발로 넘어간다.
- 단계 표기: 이 문서의 `R0~R6`은 **실행 순서**다. 감사 심각도 P0·P1·P2와 혼동하지 않는다.

## 1. 결론부터 본 실행 순서

```text
R0 개발 저장소·실행 폴더 분리 및 도구 복원
→ R1 PDF 취약 의존성 제거
→ R2 런타임·GUI 안전성 보완
→ R3 핵심 대형 함수·모듈 구조 정리
→ R4 문서·RULEBOOK·릴리스 추적성 정리
→ R5 최종 소스 동결 및 재현 빌드
→ R6 마지막 전체 테스트·실앱 검증·릴리스 교체
```

### 현재 진행 상태

| 단계 | 상태 | 기준 커밋 또는 비고 |
| --- | --- | --- |
| R0 | 완료 | 개발·실행 경계와 검증 도구 복원 |
| R1 | 완료 | `pypdf 6.14.2`, PDF 입력 한계 |
| R2 | 완료 | 시스템 결과·로그·GUI 삽입 경계 |
| R3 | 완료 | `b00916b`까지 핵심 구조 분리 |
| R4 | 완료 | 문서·RULEBOOK·추적성 최신화 |
| R5 | 대기 | 릴리스 버전·태그·후보 빌드 |
| R6 | 대기 | 마지막 전체 검증과 실행본 교체 |

테스트는 사용자의 결정대로 **R6에서 마지막에 한 번에 실행한다.** R1~R4에서는 필요한 회귀 테스트 코드를 추가하되 실행하지 않는다. R6에서 실패가 나오면 해당 원인을 수정하고 새로 빌드한 다음 R6 전체를 처음부터 다시 실행한다.

## 2. 해결 대상

### 반드시 닫을 감사 지적

1. `PyPDF2 3.0.1`의 알려진 PDF 무한 루프·CPU 점유 취약점
2. 실행 폴더와 개발 저장소의 역할 충돌
3. 삭제된 테스트·검증·빌드 도구로 인한 현재 체크아웃의 재현성 상실
4. README·CHANGELOG·KNOWN_LIMITATIONS·RULEBOOK의 실제 상태 불일치
5. `command_pipeline.execute` 335줄과 `batch_executor.execute` 399줄을 포함한 핵심 복잡도
6. `engine/builtins.py`의 bare `except`와 `os.system()` 무조건 성공 반환
7. 사전·학습 UI의 남은 동적 `innerHTML` 삽입 경계
8. EXE 빌드 커밋과 현재 개발 HEAD 관계의 불명확성
9. 외부 배포 시 EXE 디지털 서명이 없다는 점

### 이번 계획에 포함하지 않는 기능

- Word·PowerPoint 네이티브 편집 기능 구현
- Eel 프레임워크 교체
- `--onefile` 전환
- Excel·HWP COM 어댑터의 전면 재작성
- 새로운 AI 제공자 또는 사용자 기능 추가

Word·PowerPoint는 R6가 완전 통과한 뒤 별도 기능 계획으로 시작한다.

## 3. 절대 보존 기준선

작업 중 다음 두 산출물은 R6의 새 릴리스가 완전 통과하기 전까지 삭제하거나 덮어쓰지 않는다.

### 현재 실행본

- 경로: `C:\Users\PC04\OneDrive\Desktop\JARVIS_RUNTIME\current\Jarvis\Jarvis.exe`
- SHA-256: `B237B5678C147988431CE93EAE66904A5A12474985DA6154B1C2D57C42FE8455`
- 빌드 커밋: `a096e9303e6f339e1c9c57472183f5c6d901af39`

### 직전 백업

- 경로: `C:\Users\PC04\OneDrive\Desktop\JARVIS_RUNTIME\previous\Jarvis.zip`
- SHA-256: `D4F485386AC47AB0A9C2B4697462B5BB5FDA7D6121AE04E4C50AE1D6B4599116`

작업 시작 시 기준 태그를 추가하고, 각 단계는 한 가지 목적만 가진 커밋으로 종료한다. 현재 사용자 데이터 `%LOCALAPPDATA%\Jarvis\data`는 복사·초기화·수정 대상이 아니다.

## 4. 목표 폴더 구조

현재 혼합 폴더를 다음 두 역할로 분리한다.

```text
JARVIS_DEV/
├─ .git/
├─ engine/
├─ gui/
├─ default_data/
├─ tests/
├─ verification/
├─ docs 또는 현재 감사·계획 문서
├─ Jarvis.spec
├─ build_dist.bat
├─ requirements.in
├─ requirements-lock.txt
├─ requirements-build.txt
├─ pyproject.toml
└─ 소스·개발 문서

JARVIS_RUNTIME/
├─ current/Jarvis/       # 현재 실행 가능한 풀린 배포본 1개
└─ previous/Jarvis.zip   # 바로 이전 정상 백업 1개
```

### 적용 원칙

- `JARVIS_DEV`에는 `.venv`, `build`, `dist`, `releases`, 캐시, 테스트 결과물을 남기지 않는다.
- `JARVIS_RUNTIME`에는 소스, Git, 테스트, 빌드 도구, 보고서를 넣지 않는다.
- 테스트와 빌드 도구는 프로그램 실행 파일이 아니라 **개발 저장소의 필수 구성**으로 정의한다.
- RULEBOOK의 실행 파일 정리 규칙은 `JARVIS_RUNTIME`에 적용한다.
- 개발 저장소의 테스트·빌드 도구는 배포에 포함하지 않는 조건으로 보존한다.
- 폴더 분리 중 생기는 임시 중복은 해시 검증이 끝날 때까지만 허용하고 R6에서 제거한다.

## 5. 단계별 실행 계획

## R0 — 개발 경계 복원 및 안전 기준선 확정

### 목적

현재 EXE를 보호하면서 다음 수정과 재빌드가 가능한 개발 환경을 되살린다.

### 작업

1. 현재 EXE·직전 ZIP·Git HEAD·사용자 데이터 파일 목록을 다시 기록한다.
2. 현재 실행본과 직전 백업을 `JARVIS_RUNTIME` 역할 폴더로 복사한다.
3. 복사 전후 SHA-256이 동일한지 확인하고, 복사된 current EXE의 시작·종료 및 사용자 데이터 무변경만 안전 확인한다.
4. 안전 확인 후 개발 저장소에 남은 `dist`·`releases` 중복본을 제거한다. 이때 `JARVIS_RUNTIME`의 검증된 current·previous가 보존본이 된다.
5. 현재 Git 저장소를 `JARVIS_DEV` 역할로 확정한다.
6. Git 커밋 `a096e93`에서 다음 항목을 개발 저장소에 복원한다.
   - `tests/` 58개 파일
   - `verification/` 31개 파일
   - `Jarvis.spec`
   - `build_dist.bat`
   - `requirements-build.txt`
   - `pyproject.toml`
7. `.gitignore`가 `.venv`, `build`, `dist`, `releases`, 캐시와 생성 보고서를 계속 제외하는지 정리한다.
8. 작업 시작 태그를 만들고 R0 복원 커밋을 남긴다.

### 이 단계에서 하지 않는 일

- 테스트 실행
- 새 EXE 빌드
- 검증된 runtime current·previous 삭제
- 사용자 데이터 접근 방식 변경

R0의 해시·시작·데이터 무변경 확인은 파일 이동 전 필수 안전 확인일 뿐이며, 기능 회귀 테스트는 아니다. 기능 테스트는 R6에서만 실행한다.

### 완료 기준

- 개발 저장소만으로 테스트와 빌드 명령을 다시 구성할 수 있다.
- 실행 폴더에는 현재 실행본과 직전 백업만 존재한다.
- 복원된 파일은 배포 패키지에 포함되지 않도록 설정돼 있다.

### 예상 시간

30분~1시간

### 권장 커밋

`R0 개발·실행 경계 및 검증 도구 복원`

---

## R1 — PDF 취약점 제거

### 목적

실제로 사용 중인 `PyPDF2 3.0.1`을 제거하고 알려진 PDF 무한 루프 취약점을 닫는다.

### 작업

1. `requirements.in`의 `PyPDF2`를 유지보수 중인 `pypdf`로 교체한다.
2. Python 3.12·Windows를 지원하고 공개 보안 권고에서 해당 취약점의 영향 범위를 벗어난 버전을 정확히 고정한다. 실제 `pip-audit` 실행은 R6에서만 한다.
   - 이 취약점의 최소 수정선은 `pypdf 3.9.0`이다.
   - 실제 고정 버전은 작업 시점의 최신 보안 권고와 호환성을 확인해 결정한다.
   - 근거: [OSV PYSEC-2026-1835](https://osv.dev/vulnerability/PYSEC-2026-1835), [PyPDF2의 pypdf 전환 안내](https://pypi.org/project/PyPDF2/)
3. 다음 import와 오류 메시지를 `pypdf` 기준으로 변경한다.
   - `engine/document_reader.py`
   - `engine/app_actions/hwp_adapter.py`
   - `engine/startup_check.py`
   - 관련 요구사항·문서
4. PDF 파일 크기, 최대 페이지 수, 최대 추출 문자 수의 상한을 명시해 비정상 입력의 자원 사용을 제한한다.
5. 다음 회귀 테스트를 추가하되 R6 전에는 실행하지 않는다.
   - 일반 PDF 텍스트 추출
   - 빈 PDF·손상 PDF 실패 처리
   - 페이지·텍스트 상한 처리
   - HWP PDF 페이지 수 확인 경로
   - 취약 패키지 이름이 잠금 파일과 배포본에 남지 않는지 확인

### 완료 기준

- 소스·잠금 파일·시작 검사·배포 설정에 `PyPDF2` 참조가 0건이다.
- `pypdf`가 정확한 버전으로 잠긴다.
- R6의 `pip-audit` 결과가 알려진 취약점 0건이다.
- PDF 오류가 성공으로 기록되지 않고 시간·크기 상한을 초과하면 안전하게 실패한다.

### 예상 시간

1~2시간

### 권장 커밋

`R1 PyPDF2 제거 및 PDF 입력 한계 보강`

---

## R2 — 런타임 결과 정확성 및 GUI 삽입 경계 보완

### R2-1. 시스템 종료 명령

1. `os.system("shutdown ...")`을 `subprocess.run([...], shell=False)`로 교체한다.
2. 실행 파일·인자를 리스트로 분리한다.
3. 반환 코드와 표준 오류를 확인한다.
4. 실제 명령이 성공했을 때만 성공 결과를 반환한다.
5. 실패·권한 부족·명령 미존재를 구분해 `failure_result()`로 반환한다.

### R2-2. 예외 및 로그

1. `engine/builtins.py`의 bare `except`를 구체적 예외와 debug 로그로 교체한다.
2. 레지스트리·프로그램·바로가기·브라우저 스캐너의 `print()`를 중앙 logger로 이동한다.
3. `print()`는 시작 검사와 동결 워커의 의도된 콘솔·프로토콜 출력만 허용 목록으로 남긴다.
4. API 키·사용자 명령·문서 내용이 로그에 그대로 남지 않도록 기존 redaction 경계를 유지한다.

### R2-3. GUI 동적 HTML

1. `action_dictionary.js`의 `${e.message}` 오류 출력을 `textContent` 기반 DOM 생성으로 교체한다.
2. `record.step_count`, 실행 횟수, 진행률 등 숫자 필드를 `Number()`로 변환하고 범위를 검사한다.
3. 사용자·백엔드 값이 들어가는 모든 `innerHTML`, `outerHTML`, `insertAdjacentHTML` 위치를 출처부터 삽입점까지 목록화한다.
4. 고정 UI 템플릿만 HTML 문자열을 허용한다.
5. 사용자·AI·저장 데이터는 `textContent`, 안전한 DOM 속성, 또는 기존 허용 목록 정화기를 거치게 한다.
6. 사전, 사용자 매크로, 학습 행동, 네이티브 후보, 채팅 Markdown 각각에 악성 문자열 회귀 사례를 추가하되 R6 전에는 실행하지 않는다.

### 완료 기준

- 비테스트 Python 코드의 bare `except` 0건
- 동적 시스템 명령의 `shell=True` 및 `os.system` 0건
- 종료 명령 실패를 성공으로 반환하는 경로 0건
- 사용자·백엔드 데이터가 검증 없이 HTML 문자열로 삽입되는 경로 0건
- 의도된 CLI 출력 외 중앙 로그 우회 0건

### 예상 시간

2~3시간

### 권장 커밋

- `R2 시스템 명령 결과 검증 및 예외 로그 정리`
- `R2 GUI 동적 HTML 삽입 경계 보강`

---

## R3 — 핵심 구조 정리

### 공통 원칙

- 기능 추가와 구조 변경을 섞지 않는다.
- `CommandParser`, `AIActionHandler`, `ExcelAdapter`, `HwpAdapter`의 기존 외부 인터페이스를 유지한다.
- 파일 이동 커밋과 로직 변경 커밋을 분리한다.
- Excel·HWP COM 코드를 한 번에 재작성하지 않는다.
- 새 구조의 회귀 테스트는 작성만 하고 R6에서 마지막으로 실행한다.

### R3-1. `command_pipeline.execute` 분리

현재 335줄 단일 함수의 책임을 다음 단위로 분리한다.

```text
engine/pipeline/
├─ command_pipeline.py       # 단계 조율만 수행
├─ conversation_route.py     # 대화·질문 모드
├─ local_route.py            # 로컬 분석·기본 매크로
├─ learned_route.py          # 학습 매크로 정책·재실행
└─ ai_fallback_route.py      # AI fallback과 오류 정규화
```

목표:

- `command_pipeline.execute()` 150줄 이하
- 각 route는 구조화된 `ExecutionResult`만 반환
- confirmation 상태, run policy, AI fallback이 중복 실행되지 않음
- 기존 라우팅 순서와 공개 응답 스키마 유지

### R3-2. `batch_executor.execute` 분리

현재 399줄 함수의 액션별 실행과 결과 집계를 분리한다.

```text
engine/ai_actions/
├─ batch_executor.py             # 반복과 중단 규칙만 담당
├─ batch_action_dispatcher.py    # action 종류 선택
├─ learned_action_executor.py    # use/adapted macro
├─ generated_action_executor.py  # action_plan/dynamic_code
└─ batch_result_aggregator.py    # 실패·검증·확인 결과 합성
```

목표:

- `batch_executor.execute()` 150줄 이하
- confirmation_required가 발생하면 이후 액션을 실행하지 않음
- 실패를 성공 응답에 섞지 않음
- 학습 후보 생성과 실제 실행 결과를 분리
- 다단계 명령의 원문이 개별 학습 문장에 중복 저장되지 않음

### R3-3. `parser.py`의 남은 서비스 책임 추출

다음 묶음을 `CommandParser` 밖으로 이동하고 parser에는 호환 façade만 남긴다.

- 앱 실행 방법·앱 대상·HWP 범위 confirmation 레코드 생성
- 동적 코드와 학습 실행 정책 confirmation 생성
- 학습 스킬 인자·슬롯 조립 및 재시도 조율
- UIA 대상 선택 confirmation 생성

권장 구조:

```text
engine/confirmation/confirmation_factory.py
engine/skills/learned_replay_service.py
engine/skills/skill_policy_service.py
```

목표:

- `parser.py` 1,000줄 전후, 최대 1,100줄
- parser의 새 orchestration 함수 150줄 이하
- confirmation handler와 record 생성 책임이 순환 참조 없이 분리
- 외부 Eel API와 저장 스키마 변경 없음

### R3-4. Office 확장 전 최소 경계 정리

Excel·HWP 어댑터에서 COM 호출이 없는 순수 normalization·범위 해석·서식 비교 helper만 별도 모듈로 이동한다. 실제 COM prepare·execute·rollback 흐름은 현재 façade에 유지한다.

공통 계약을 문서와 타입으로만 확정한다.

```text
prepare(request)
execute(prepared_action)
verify(prepared_action, result)
rollback(prepared_action)
fingerprint(context)
```

이 계약은 Word·PowerPoint 단계의 기준이지만 이번 단계에서 Word·PowerPoint 코드는 작성하지 않는다.

### 완료 기준

- `command_pipeline.execute` 150줄 이하
- `batch_executor.execute` 150줄 이하
- `parser.py` 1,100줄 이하
- 새로 추출한 함수는 150줄 이하
- 공개 API, 저장 스키마, 사용자 명령 결과의 의도된 변경 없음
- 대형 함수 감소가 단순 helper 포장이나 줄 수 숨기기가 아니라 책임 분리로 설명 가능

### 예상 시간

5~8시간

### 권장 커밋

- `R3 대화·로컬·학습·AI 명령 라우트 분리`
- `R3 AI batch 액션 실행과 결과 집계 분리`
- `R3 confirmation 생성과 학습 재실행 서비스 분리`
- `R3 Office 순수 helper와 공통 계약 정리`

---

## R4 — 문서·RULEBOOK·추적성 정리

### 작업

1. `README.md`
   - 현재 버전과 실행 방식만 표시
   - 실제 존재하는 설치·테스트·빌드 명령만 기재
   - 과거 테스트 개수 누적 제거
2. `CHANGELOG.md`
   - P0 진행 중 문구 제거
   - 이미 끝난 Excel·HWP 및 구조 정리 결과 기록
   - 이번 R0~R4 변경을 버전별로 기록
3. `KNOWN_LIMITATIONS.md`
   - Word·PowerPoint 미구현
   - 복합 네이티브 학습 재실행 보류
   - 외부 AI 실계정 검증 여부
   - 실제 Office/HWP 버전 범위
4. `RULEBOOK.md`
   - 실행 폴더와 개발 저장소 정의 분리
   - 테스트·빌드 도구는 개발 저장소에 보존하되 배포 금지
   - 현재 실행본 1개와 직전 백업 1개 규칙은 실행 폴더에 엄격히 적용
5. `AUDIT_REPORT.md`
   - 해결된 항목과 남은 항목을 현재 상태로 갱신
   - 최종 테스트 숫자는 R6 완료 후 외부 릴리스 기록과 함께 확정
6. 삭제된 경로나 존재하지 않는 명령을 가리키는 링크를 모두 제거한다.

### 빌드 추적 규칙

- 정식 EXE는 깨끗한 릴리스 커밋에서만 만든다.
- EXE 내부 빌드 커밋은 릴리스 태그가 가리키는 커밋과 정확히 같아야 한다.
- 테스트 결과 보고를 위한 후속 문서 커밋이 생겨도 EXE의 기준은 현재 HEAD가 아니라 명시된 릴리스 태그다.
- EXE SHA-256과 테스트 결과는 개발 저장소 감사 문서에 보관하고 실행 폴더에는 불필요한 보고서를 복사하지 않는다.

### 디지털 서명

- 개인용 로컬 릴리스: 미서명을 허용하되 SHA-256을 기록한다.
- 타인 또는 인터넷 배포: Authenticode 코드 서명과 타임스탬프를 필수 릴리스 게이트로 추가한다.
- 인증서가 없는 상태에서는 “외부 배포 준비 완료” 판정을 하지 않는다.

### 완료 기준

- 삭제된 파일·경로를 참조하는 문서 링크 0건
- README·CHANGELOG·KNOWN_LIMITATIONS 간 버전·기능·테스트 상태 불일치 0건
- RULEBOOK이 개발 저장소와 실행 폴더를 명확히 구분
- EXE 빌드 커밋을 릴리스 태그 하나로 재현 가능

### 예상 시간

1~2시간

### 권장 커밋

`R4 문서·정리 규칙·릴리스 추적 기준 최신화`

---

## R5 — 최종 소스 동결 및 재현 빌드

### 작업

1. R0~R4 커밋을 검토하고 작업 트리를 clean 상태로 만든다.
2. APP_VERSION을 보정 릴리스 버전으로 확정한다.
3. 릴리스 후보 커밋과 태그를 만든다.
4. 프로젝트 폴더 밖에 깨끗한 Python 3.12 가상환경을 생성한다.
5. `requirements-build.txt`와 `requirements-lock.txt`만 사용해 설치한다.
6. 태그 커밋에서 EXE를 빌드한다.
7. 빌드 ID의 버전·커밋·dirty 상태를 확인한다.
8. 현재 실행본은 덮어쓰지 않고 별도 후보 경로에 보관한다.

### 이 단계에서 하지 않는 일

- 현재 정상 EXE 교체
- 직전 백업 삭제
- 전체 테스트 실행

### 완료 기준

- 깨끗한 환경에서 빌드 명령 한 번으로 후보 EXE 생성
- 후보 EXE의 내장 commit이 릴리스 태그 commit과 정확히 일치
- `git_dirty=false`
- 빌드 중간 산출물은 개발 저장소 추적 대상이 아님

### 예상 시간

1~2시간

### 권장 태그

`v1.1.0-rc.1` 또는 실제 버전 정책에 맞는 다음 RC 태그

---

## R6 — 마지막 전체 테스트·실앱 검증·릴리스 교체

R6 전에는 테스트를 실행하지 않는다. 다음 순서를 끊지 않고 수행한다.

### R6-1. 정적·보안 검사

1. Python compile/구문 검사
2. JavaScript 구문 검사
3. JSON 스키마·기본 데이터 검사
4. bare `except`, `shell=True`, `os.system`, 위험 HTML 삽입점 검사
5. `pip check`
6. `pip-audit` — 알려진 취약점 0건
7. 소스·배포본·ZIP 비밀정보 검사
8. 사용자 데이터·세션·로그의 배포 혼입 검사

### R6-2. 자동 회귀

1. 기존 420개 테스트 전체
2. R1 PDF 보안 회귀
3. R2 종료 명령·예외·GUI 악성 문자열 회귀
4. R3 명령 라우팅·batch 중단·학습 저장 회귀
5. unit → integration → windows 순으로 실행
6. 실패 0건이 아니면 릴리스 중단

### R6-3. 실제 앱 및 EXE 검증

1. 실제 Excel 핵심·보조 시나리오
2. 실제 HWP 핵심 시나리오
3. 후보 EXE 시작·종료
4. EXE 동결 워커, timeout, cancel, lock, retry
5. EXE Excel COM 저장·재열기 왕복
6. 기존 사용자 데이터 환경과 신규 격리 데이터 환경 기동
7. 실행 전후 실제 사용자 데이터 SHA-256 비교
8. 프로세스·임시 파일·Office 문서 잔여물 확인

### R6-4. 외부 AI

- 사용자가 실제 키 사용을 승인하면 Gemini·OpenAI 라이브 테스트 3개를 실행한다.
- 승인하지 않으면 3개 skip을 정확히 기록하고 “외부 AI 완전 검증”으로 표시하지 않는다.
- API 키 값은 로그·보고서·배포물에 기록하지 않는다.

### R6-5. 최종 릴리스 교체와 정리

모든 검증이 통과한 경우에만 다음을 수행한다.

1. 기존 current 실행본을 previous ZIP으로 승격한다.
2. 기존 previous보다 오래된 백업을 삭제한다.
3. 후보 EXE 배포 폴더를 새 current로 교체한다.
4. 새 current와 previous의 SHA-256을 감사 문서에 기록한다.
5. `JARVIS_RUNTIME`에는 current 1개와 previous 1개만 남긴다.
6. 개발 저장소의 `.venv`, `build`, `dist`, 캐시, 생성 테스트 결과를 삭제한다.
7. 소스·테스트·검증·빌드 설정은 `JARVIS_DEV`에 보존한다.
8. 최종 감사 보고서를 “완전 통과” 또는 남은 제한이 있는 “조건부 통과”로 갱신한다.

### 완료 기준

- 자동 테스트 실패 0건
- 실제 Excel·HWP 실패 0건
- 알려진 의존성 취약점 0건
- 비밀정보·사용자 데이터 배포 혼입 0건
- EXE 실행 전후 사용자 데이터 바이트 변경 0건
- 새 EXE build commit과 release tag commit 일치
- 실행 폴더에는 current와 previous만 존재
- Git 개발 저장소 clean

### 예상 시간

3~5시간

---

## 6. 전체 예상 시간

| 단계 | 예상 시간 |
| --- | ---: |
| R0 경계 복원 | 0.5~1시간 |
| R1 PDF 보안 | 1~2시간 |
| R2 런타임·GUI 안전성 | 2~3시간 |
| R3 구조 정리 | 5~8시간 |
| R4 문서·추적성 | 1~2시간 |
| R5 재현 빌드 | 1~2시간 |
| R6 마지막 전체 검증 | 3~5시간 |
| 합계 | **13.5~23시간** |

현재 작업 속도를 유지하면 집중 작업 기준 약 2일, 보수적으로는 3일 분량이다. 가장 변동이 큰 부분은 R3 구조 정리와 R6 실제 Office 검증이다.

## 7. 중단 및 롤백 기준

다음 중 하나라도 발생하면 새 릴리스로 교체하지 않는다.

- 현재 EXE 또는 직전 백업의 해시 불일치
- 사용자 데이터 변경 또는 누락
- 기존 명령 응답 스키마의 의도하지 않은 변경
- confirmation·취소·busy·fallback의 중복 실행
- Excel·HWP 실제 문서 손상 또는 미검증 성공
- `pip-audit` 알려진 취약점 잔존
- 자동 테스트 실패 1건 이상
- 후보 EXE의 빌드 커밋 불일치 또는 dirty 빌드

롤백은 현재 보존 EXE와 직전 ZIP을 기준으로 하며, 검증이 끝나기 전에는 두 파일을 삭제하지 않는다.

## 8. 최종 종료 조건

다음 조건을 모두 만족해야 이 해결 계획을 완료로 표시한다.

```text
P1 감사 지적 0건
P2 감사 지적 0건 또는 명시적으로 승인된 외부 배포 전용 항목만 잔존
PyPDF2 참조 0건
알려진 의존성 취약점 0건
bare except 0건
검증 없는 동적 HTML 삽입 0건
command_pipeline.execute 150줄 이하
batch_executor.execute 150줄 이하
parser.py 1,100줄 이하
전체 자동 테스트 실패 0건
실제 Excel·HWP 실패 0건
사용자 데이터 변경 0건
빌드 commit과 release tag 일치
README·CHANGELOG·KNOWN_LIMITATIONS·RULEBOOK 최신 상태
실행 폴더 current 1개 + previous 1개
개발 저장소 clean
```

이 조건을 통과한 뒤에만 Word·PowerPoint 구현 계획을 시작한다.
