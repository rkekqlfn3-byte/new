# JARVIS 감사 후 수정 계획서

- 문서 버전: 1.0
- 기준일: 2026-07-29 (KST)
- 적용 대상: `C:\Users\lovelycom\Desktop\ai`의 JARVIS Prototype 1.x Python 소스
- 기준 커밋: `ddc7239ade4847a6b93c76b352b97b816f1d4465`
- 현재 상태: working tree dirty, 수정 66개·신규 15개 파일
- 상위 기준: `docs/JARVIS_PRODUCT_GOAL.md`, `docs/JARVIS_MAINTENANCE_MASTER_PLAN_2026-07-29.md`
- 실행 원칙: 개발 단계에서는 Python 소스만 실행·검증한다. EXE는 사용자가 별도로 지시할 때만 생성한다.

---

## 1. 목적

이 계획서는 2026-07-29 독립 감사에서 확인된 안전·검증·개인정보·유지보수 문제를 수정하고, 현재 미커밋 변경을 검증 가능한 단위로 분리하기 위한 실행 기준서다.

이번 수정의 최우선 목적은 기능 추가가 아니라 다음 안전 계약을 다시 성립시키는 것이다.

```text
대상 확인
-> 위험 작업 승인
-> 실행
-> 실제 결과 재조회
-> 검증
-> 실패 분류와 복구
-> 검증된 성공만 학습
```

다음 세 문장은 모든 수정의 판정 기준이다.

1. 정적 분석기가 이해하지 못한 위험 실행은 허용하지 않는다.
2. 실행 성공과 검증 성공을 타입·상태·증거 수준에서 구분한다.
3. 현재 소스로 생성되지 않은 검증 보고서는 현재 소스의 통과 증거가 아니다.

---

## 2. 감사 기준선

### 2.1 자동 검증 기준선

| 검증 | 결과 |
|---|---:|
| 전체 `unittest` | 1,027 통과 / 실패 0 / 오류 0 / skip 3 |
| 안전·구조 집중 테스트 | 92 통과 |
| 유지보수 감사 | 오류 0 / 경고 1 |
| `git diff --check` | 통과 |
| Product Goal 자동 판정 | 자동 통과 / 수동 3개 대기 |

통과한 기존 테스트는 회귀 기준선이지 안전성 완료 증거가 아니다. 기존 테스트가 다루지 않은 공격형 입력에서 승인 우회가 재현됐기 때문이다.

### 2.2 수정 차단 결함

| ID | 등급 | 결함 | 현재 영향 |
|---|---|---|---|
| SEC-01 | P0 | 동적 Python 간접 호출 승인 우회 | 승인 없이 `macro_runner` 실행 경로 도달 |
| RES-01 | P1 | 문자열 boolean을 성공·검증으로 변환 | 실패 결과와 미검증 결과가 성공으로 승격 가능 |
| EVD-01 | P1 | probe와 현재 소스의 귀속 관계 없음 | 변경 전 Office 증거가 현재 코드 통과 근거로 재사용됨 |
| PRV-01 | P1 | 문서 절대경로의 일반 로그 기록 가능성 | 사용자 경로가 회전 로그에 남을 수 있음 |
| ARC-01 | P2 | parser 역참조 검사가 이름 기반 | 속성 이름만 바꾸면 구조 규칙 우회 가능 |
| MNT-01 | P2 | 거대 모듈에 유지보수 예산 없음 | 변경 영향 범위와 리뷰 비용 지속 증가 |
| DOC-01 | P3 | 문서의 테스트 수·진척 수치 드리프트 | 현재 상태 오판 가능 |

---

## 3. 작업 순서와 의존성

수정 순서는 아래와 같이 고정한다. 앞 단계의 완료 조건을 만족하지 못하면 다음 단계의 커밋·태그·릴리스 판단으로 넘어가지 않는다.

```text
0. 변경면 고정 및 기준선 보존
   |
   v
1. SEC-01 동적 코드 승인 우회 차단
   |
   v
2. RES-01 실행 결과 계약 강화
   |
   +--> 3. PRV-01 개인정보 로그 경계 강화
   |
   +--> 4. EVD-01 검증 증거의 소스 귀속 강화
                  |
                  v
5. ARC-01 구조 계약 강화
   |
   v
6. MNT-01/DOC-01 유지보수·문서 정리
   |
   v
7. 전체 자동·Windows·수동 수용 검증
```

---

## 4. 단계별 수정 계획

## 4.0 변경면 고정과 커밋 경계 준비

### 목적

현재 81개 파일의 변경을 잃거나 서로 섞지 않고, 안전 수정과 기존 리팩터링을 독립적으로 검증할 수 있게 만든다.

### 수행 항목

- 현재 `git status`, `git diff --stat`, 기준 커밋을 감사 기록에 남긴다.
- 사용자 변경을 reset, checkout, stash로 제거하지 않는다.
- 변경 파일을 다음 논리 그룹으로 분류한다.
  - ConfirmationRegistry·parser 의존성 리팩터링
  - 동적 코드 보안
  - ExecutionResult·사후조건
  - 편집 문맥·문서 포커스
  - JSON 복구
  - 유지보수 도구·문서
- 한 파일에 여러 목적이 섞였으면 `git add -p` 또는 후속 정리로 커밋 경계를 분리한다.

### 완료 조건

- 모든 변경 파일이 정확히 하나 이상의 작업 패키지에 배정된다.
- 미분류 변경 0개.
- 기존 변경이 유실되지 않았음을 `git diff`로 확인한다.

---

## 4.1 SEC-01: 동적 Python 승인 우회 차단

### 대상

- `engine/security/dynamic_code_preflight.py`
- `engine/ai_actions/batch_preflight.py`
- `engine/ai_actions/generated_action_executor.py`
- `engine/ai_actions/learned_action_executor.py`
- `engine/skills/run_policy.py`
- `tests/integration/test_dynamic_code_preflight.py`
- 필요 시 신규 보안 단위 테스트

### 구현 원칙

1. `SAFE`는 분석기가 안전을 입증한 경우에만 반환한다.
2. 위험 callable의 별칭과 전달 관계를 끝까지 해석하지 못하면 `CONFIRMATION_REQUIRED` 또는 `BLOCKED`로 종료한다.
3. AI 신규 코드, 적응된 매크로, 학습된 동적 스킬이 동일한 preflight와 승인 정책을 사용한다.
4. 승인 대기 중에는 batch의 선행·후행 동작도 실행하지 않는다.
5. 승인은 세션과 코드 fingerprint에 묶고 한 번만 소비한다.

### 최소 탐지 범위

아래 패턴은 모두 직접 호출과 동일한 위험도로 판정해야 한다.

```python
f = os.system
f("...")

f, g = (os.system, print)
f("...")

functions = [os.system]
functions[0]("...")

functions = {"run": os.system}
functions["run"]("...")

(lambda callback: callback("..."))(os.system)
```

추가 공격형 테스트 범위:

- `getattr`, `globals`, `locals`, `__builtins__`를 이용한 호출
- 중첩 튜플·리스트·딕셔너리
- 조건식과 walrus 대입
- 함수 반환값으로 전달되는 callable
- `subprocess`, `os`, `ctypes`, `winreg`, PowerShell·CMD 실행
- 파일 덮어쓰기·재귀 삭제·보호 경로 변경

### 권장 구현

- AST 값 추적을 단순 문자열 별칭이 아니라 제한된 callable provenance로 표현한다.
- 구조 분해 대입은 좌우 요소를 대응해 provenance를 전파한다.
- 상수 인덱스 컨테이너는 추적하고, 해석 불가능한 동적 인덱스는 보수적으로 판정한다.
- 위험 모듈 또는 위험 callable이 존재하는 코드에서 해석 불가능한 간접 호출이 발견되면 최소 승인 요구로 처리한다.
- 장기적으로는 동적 코드를 별도 프로세스와 제한된 capability API에서 실행한다.

### 필수 테스트

- 정적 preflight 결과 테스트
- AI batch 전체 경로에서 `macro_runner.run` 미호출 확인
- 승인 후 정확히 1회 실행 확인
- 취소·만료·세션 불일치 시 미실행 확인
- 학습 후보와 학습 스킬 경로에서도 동일 정책 확인
- 기존 정상 데이터 처리 Python 코드가 불필요하게 모두 차단되지 않는지 확인

### 완료 조건

- 알려진 간접 호출 우회 사례 0개.
- 위험 코드가 `SAFE`로 판정되는 회귀 테스트 0개.
- 승인 전 `macro_runner` 호출 0회.
- 보안 집중 테스트와 전체 테스트 통과.

---

## 4.2 RES-01: ExecutionResult 계약 강화

### 대상

- `engine/execution_result.py`
- `engine/skills/postconditions.py`
- `engine/skills/skill_executor.py`
- `engine/user_feedback/event_adapter.py`
- `engine/pipeline/edit_route.py`
- `tests/integration/test_execution_result.py`
- `tests/integration/test_postconditions.py`
- `tests/integration/test_skill_executor.py`

### 구현 원칙

- `success`, `verified`, `retryable`은 `type(value) is bool`인 값만 유효하다.
- 문자열, 숫자, 객체를 `bool()`로 조용히 변환하지 않는다.
- `success`, `status`, `error_type`, `verified` 사이의 모순을 정규화 전에 검사한다.
- 계약 위반 결과는 성공으로 복구하지 않고 구조화된 `contract_error` 실패로 바꾼다.
- 레거시 호환은 허용 목록에 명시된 형태만 변환한다.

### 상태 불변식

| 조건 | 요구 결과 |
|---|---|
| `success is False` | `verified=False`, 실패 또는 비완료 status |
| `verified is True` | `success=True`, 검증 증거 또는 명시적 검증 상태 존재 |
| `status=failed` | `success=False`, `error_type` 존재 |
| confirmation·clarification 대기 | `success=False`, `verified=False` |
| 계약 타입 오류 | `status=failed`, `error_type=contract_error` |

### 필수 테스트

- `"false"`, `"true"`, `"yes"`, `0`, `1`, `None`의 boolean 필드 거부
- `success=True + status=failed` 거부
- `success=False + verified=True` 거부
- malformed adapter·plugin·legacy 결과가 학습 성공으로 기록되지 않음
- `event_from_execution_result`가 잘못된 결과를 `verification_passed`로 만들지 않음
- 정상 canonical result의 기존 동작 보존

### 완료 조건

- `normalize_execution_result()`가 malformed 결과를 성공으로 승격하는 사례 0개.
- 계약 검사 함수가 프로덕션 경계에서 실제 사용됨.
- 검증되지 않은 성공이 학습 streak와 성공 피드백으로 전달되지 않음.

---

## 4.3 PRV-01: 로그 개인정보 경계 강화

### 대상

- `engine/logging_config.py`
- `engine/api/command_api.py`
- `engine/ai_actions/basic_action_executor.py`
- `engine/builtins.py`
- `engine/pipeline/conversation_route.py`
- 개인정보·로그 관련 테스트

### 구현 원칙

- 사용자에게 보이는 UI 메시지와 디스크 진단 로그를 분리한다.
- 디스크 로그에는 문서 절대경로, 문서 원문, 선택 내용, API 키를 남기지 않는다.
- 파일 식별이 필요하면 확장자·앱 유형·해시·안전한 basename 등 최소 정보만 기록한다.
- 예외 traceback의 인자와 메시지에도 경로가 포함될 수 있으므로 formatter 경계에서 재차 정리한다.

### 필수 테스트

- Windows 절대경로, UNC 경로, 사용자 프로필 경로 마스킹
- API 키·Bearer token 기존 마스킹 유지
- GUI에는 필요한 문구가 보이되 파일 로그에는 민감 경로가 없음
- 회전 로그와 오류 로그 모두 동일한 정책 적용
- 진단 보고서의 `paths_or_contents_reported=False`를 실제 로그 검사로 입증

### 완료 조건

- 테스트 fixture 경로가 `jarvis.log`와 `errors.log` 원문에 나타나지 않음.
- 사용자 메시지와 로그 메시지의 책임이 코드상 분리됨.

---

## 4.4 EVD-01: 검증 증거를 현재 소스에 귀속

### 대상

- `verification/product_goal_acceptance.py`
- 각 Windows·Office probe 보고서 생성기
- `verification/windows_commit_probe.py` 계열
- Product Goal acceptance 테스트

### 보고서 필수 source 필드

```json
{
  "source": {
    "commit": "...",
    "dirty": true,
    "tree_hash": "...",
    "changed_paths_hash": "..."
  }
}
```

### 구현 원칙

- 보고서의 생성 시각뿐 아니라 현재 source identity와 일치하는지 검사한다.
- clean tree는 commit 일치를 요구한다.
- dirty tree는 재현 가능한 tree hash 또는 변경 파일 hash 일치를 요구한다.
- `source=None`인 과거 보고서는 현재 수용 판정에 사용할 수 없다.
- 각 probe가 실제 어떤 Office 앱·fixture·기능을 검증했는지 기록한다.
- 관련 모듈 변경 시 영향받은 probe만 무효화할 수 있는 dependency map을 둔다.

### 접근성 판정 보완

- 자동 probe가 0개인 축을 단순 `passed`로 표기하지 않는다.
- `manual_pending`, `not_automated`, `passed`를 구분한다.
- 전체 상태가 자동 통과여도 수동 3개가 끝나기 전 최종 Goal 달성으로 표시하지 않는다.

### 필수 테스트

- 같은 commit·clean tree 보고서 수용
- 다른 commit 보고서 거부
- 같은 commit이지만 다른 dirty tree 보고서 거부
- `source=None` 보고서 거부
- 만료 보고서 거부
- 미래 시각 보고서 거부
- 사용자 문서 변경 증거가 있는 보고서 거부

### 완료 조건

- 현재 트리와 일치하지 않는 probe가 자동 통과에 기여하지 않음.
- Product Goal 보고서에서 자동·환경·수동 상태가 분명히 구분됨.

---

## 4.5 ARC-01: 파서 역참조·의존성 구조 검사 강화

### 대상

- `tests/unit/test_parser_composition.py`
- `verification/maintenance_audit.py`
- `engine/confirmation/confirmation_registry.py`
- 주요 pipeline·manager·service 생성자

### 구현 원칙

- 속성 이름이 아니라 실제 객체 identity로 `CommandParser` 보관 여부를 검사한다.
- 자식 객체의 `__dict__`, 컨테이너, `functools.partial`, bound method를 제한 깊이로 순회한다.
- runtime 인자로 잠깐 전달되는 parser와 장기 보관되는 parser를 구분한다.
- 검사는 무한 순환과 프레임·모듈·클래스 같은 비대상 객체를 안전하게 건너뛴다.

### 필수 테스트

- `self._host = parser`를 잡음
- `self.callback = parser.some_method`를 잡음
- `self.services = {"runtime": parser}`를 잡음
- 필요한 독립 서비스 객체 주입은 허용
- ConfirmationRegistry의 pending manager 단일 소유권 유지

### 완료 조건

- 이름 변경만으로 역참조 금지 규칙을 우회할 수 없음.
- 현재 주요 자식 매니저에서 parser 객체·bound method 보관 0개.

---

## 4.6 MNT-01/DOC-01: 구조 부채와 문서 드리프트 정리

### 1차 범위

이번 안전 수정과 섞지 않고 별도 커밋으로 수행한다.

- `business_workflow.py` 3,715줄
- `excel_adapter.py` 2,350줄
- `stage10.py` 1,903줄
- `edit_mode/controller.py` 1,893줄
- 기타 1,000줄 이상 모듈

### 수행 항목

- 파일별 책임과 변경 빈도를 측정해 분리 우선순위를 정한다.
- 신규 증가는 막되 즉시 대규모 이름 변경은 하지 않는다.
- 최초 예산은 경고 방식으로 도입하고 기준선보다 커질 때만 실패시킨다.
- `stage5`, `stage10`, `stage11`은 기능 이름으로의 대응표를 먼저 문서화한다.
- 실제 rename은 import·테스트·문서 마이그레이션을 포함한 별도 작업으로 진행한다.
- 유지보수 계획서의 테스트 기준을 1,027개로 갱신한다.
- 57%, 학습 35%는 2026-07-25 당시 계획 추정치임을 명시한다.
- HWP 보안 모듈 현재 등록 상태와 환경별 확인 방법을 갱신한다.

### 완료 조건

- 거대 파일별 소유 책임과 분리 목표가 문서화됨.
- 신규 코드가 무제한으로 거대 파일에 추가되지 않음.
- 문서의 테스트 수·Goal 상태·환경 차단 설명이 현재 코드와 일치함.

---

## 4.7 전체 검증과 최종 수용

### 자동 검증 순서

```powershell
python -m unittest -q tests.integration.test_dynamic_code_preflight
python -m unittest -q tests.integration.test_execution_result
python -m unittest -q tests.integration.test_postconditions
python -m unittest -q tests.unit.test_parser_composition
python -m unittest -q tests.integration.test_confirmation_flow
python -m unittest -q
python -m verification.maintenance_audit
python -m verification.maintenance_failure_injection
python -m verification.maintenance_performance
python -m verification.utterance_acceptance_battery
python -m verification.stage12_capability_benchmark
git diff --check
```

Windows·Office probe는 현재 source identity를 기록하도록 수정한 뒤 owned fixture로 다시 실행한다.

### 수동 검증

- NVDA 등 실제 화면 읽기 프로그램
- 실제 음성 입력
- 비숙련 사용자 관찰
- 승인 모달의 초점 이동·복귀
- 문서 전환 후 선택 영역과 대상 재확인
- 저장하지 않은 Office 문서 연결·편집
- 위험 명령 승인·취소·만료 안내 이해도

### 최종 완료 조건

- P0/P1 결함 0개.
- 전체 자동 테스트 실패·오류 0개.
- skip은 환경 조건이 문서화된 기존 항목만 허용.
- 현재 source와 일치하는 Office probe 전부 통과.
- 개인정보 로그 검사 통과.
- Product Goal 수동 항목 3개에 실제 수행 시각과 결과 기록.
- working tree의 변경이 목적별 clean commit으로 분리됨.
- 사용자가 별도로 요청하지 않은 EXE가 생성되지 않음.

---

## 5. 커밋 계획

권장 커밋 순서는 다음과 같다.

1. `fix(security): block indirect dynamic-code capability bypasses`
2. `fix(results): enforce strict execution-result contracts`
3. `fix(privacy): separate UI messages from redacted disk logs`
4. `fix(verification): bind probe evidence to source identity`
5. `refactor(parser): finalize confirmation registry and dependency injection`
6. `fix(edit): preserve focused document context and unsaved document identity`
7. `fix(storage): preserve recovered JSON when primary repair is deferred`
8. `test(maintenance): strengthen architecture and failure-injection gates`
9. `docs(maintenance): synchronize audit, Goal, and verification baselines`

한 커밋에는 한 가지 위험 또는 한 가지 구조 목적만 넣는다. 테스트는 해당 구현 커밋과 함께 포함한다.

---

## 6. 중단·롤백 기준

다음 상황에서는 해당 단계를 중단하고 원인을 분리한다.

- 정상적인 데이터 처리 동적 코드까지 대부분 차단되는 경우
- 기존 native Excel·한글·Word·PowerPoint 경로가 AI fallback으로 퇴행하는 경우
- `verified=False`가 무조건 실패로 취급되어 정상적인 수동 확인 흐름이 깨지는 경우
- 로그 마스킹 때문에 사용자 화면에 필요한 파일 구분 정보까지 사라지는 경우
- source identity 계산이 사용자 파일 또는 저장소 밖 파일을 읽는 경우
- 리팩터링 중 기존 사용자 변경과 충돌하는 경우

롤백은 해당 논리 커밋 단위로만 수행한다. 사용자 작업을 포함한 전체 트리를 reset하지 않는다.

---

## 7. 완료 보고서 형식

수정 완료 보고서에는 최소한 다음을 포함한다.

1. 결함 ID별 수정 파일과 핵심 변경
2. 공격형 재현의 수정 전·후 결과
3. 전체 테스트 수, 실패, 오류, skip
4. Windows·Office probe의 source identity
5. 수동 검증 결과와 미완료 항목
6. 개인정보 로그 검사 결과
7. 성능 기준선 변화
8. 생성한 커밋 목록과 각 커밋 목적
9. 알려진 한계와 다음 유지보수 후보

최종 판정은 다음 셋 중 하나만 사용한다.

- `release_ready`: 모든 필수 자동·수동 검증 완료
- `automated_pass_manual_pending`: 자동 검증 완료, 수동 수용 대기
- `blocked`: P0/P1 또는 환경 차단이 남아 있음

현재 판정은 `blocked`다. SEC-01과 RES-01을 가장 먼저 해소한 뒤 다시 판정한다.
