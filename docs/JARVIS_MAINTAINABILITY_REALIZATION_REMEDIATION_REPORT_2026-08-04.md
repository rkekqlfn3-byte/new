# JARVIS 유지보수·실현 가능성 보완 감사 보고서

감사·수정일: 2026-08-04

검증 대상 runtime source commit: `c191592e49cb375d8414d60889a628f8ccf7be67`

범위: Python 소스, GUI 정적 계약, 저장소·CI·보안·Office/HWP/PDF 자동 수용

제외: EXE 빌드, 외부 배포, 실제 화면 읽기·음성 입력·비숙련자 관찰

## 1. 최종 판정

| 관점 | 보완 후 판정 | 설명 |
|---|---|---|
| 이 PC에서의 실현 가능성 | **자동 범위 합격** | 13개 source-bound probe와 중앙 회귀가 통과했다. |
| 제품 Goal 자동 수용 | **통과** | 최종 상태는 `automated_pass_manual_pending`이다. |
| 제품 Goal 최종 수용 | **수동 증거 대기** | 화면 읽기·실제 음성·비숙련자 관찰은 자동 결과로 대체하지 않았다. |
| 유지보수성 | **조건부 수용 가능** | 게이트 사각지대와 비밀 저장은 닫혔다. 43개 장함수와 넓은 조립 객체는 만료일 있는 부채로 남는다. |
| CI 설계 | **소스 수정 완료, 운영 확인 대기** | hosted 품질 검사와 self-hosted Office/HWP 검사를 분리했다. GitHub 실제 실행·branch protection은 push 뒤 확인해야 한다. |
| 배포 준비 | **아직 아님** | 이번 작업은 소스 전용이며 EXE·서명·태그·배포를 수행하지 않았다. |

결론은 **“현재 PC에서 핵심 Goal의 자동화 가능한 부분은 구현·검증됐고, 소스를
계속 고칠 수 있는 최소 유지보수 안전망도 복구됐다”**이다. 다만 사람 사용성
증거와 원격 CI 증거가 없으므로 제품 Goal 전체 완료나 외부 배포 완료로 표현하면
안 된다.

## 2. 이번에 수정한 내용

### 2.1 진실한 품질 게이트

- Git 추적 경로를 `core.quotepath=false`와 NUL 구분으로 읽어 한국어·공백 경로를
  누락하지 않게 했다.
- Git 경로 열거·UTF-8 해석이 실패하면 빈 목록으로 통과하지 않고 감사 오류로
  종료한다.
- 한국어 파일이 있는 임시 Git 저장소 회귀 테스트를 추가했다.
- 추적 중이던 Excel/PDF 수동 체크표 2개를 생성물 전용 `outputs/`에서
  `docs/manual_tests/`로 옮겼다.
- GitHub hosted `windows-2025`에는 compile, Ruff, JavaScript syntax, `pip check`,
  전체 unittest, strict maintenance, release security를 둔다.
- 실제 Office/HWP Goal은 `[self-hosted, windows, x64, jarvis-office]`와 대화형
  세션을 요구하는 별도 job으로 분리했다.

clean detached worktree의 strict maintenance 결과는 오류 0, 경고 0이다. 현재
개발 폴더에는 Git 비추적 과거 `outputs/`가 있어 일반 감사에서 정리 경고 1개가
남지만, 사용자 산출물을 임의 삭제하지 않았고 clean checkout 품질 판정에는
영향이 없다.

### 2.2 API 키 저장 보호

- Windows 현재 사용자 DPAPI 기반 `CredentialProtector`를 추가했다.
- 저장 schema를 5로 올리고 JSON에는 `api_key_protected`만 저장한다.
- 기존 schema 4의 평문 키를 보호 값으로 이관하고 primary, `.bak`, 버전 백업의
  평문 필드를 함께 제거한다.
- 보호·복호화 실패 시 평문 fallback 저장을 금지하고 GUI에 Windows 보안 저장소
  오류 상태를 표시한다.
- 보호 실패 시 provider·routing·키·파일이 모두 원상태로 남는 회귀를 추가했다.
- 기본 데이터에 평문 키뿐 아니라 비어 있지 않은 보호 blob도 포함할 수 없게 했다.

실제 사용자 데이터 경계의 JSON 7개를 값 출력 없이 검사한 결과는 다음과 같다.

| 항목 | 결과 |
|---|---:|
| 검사한 현재·백업 파일 | 7 |
| 비어 있지 않은 평문 `api_key` | 0 |
| DPAPI 보호 credential | 7 |
| 해석 불가 JSON | 0 |

release security audit도 known secret fingerprint 0건, Git 이력 finding 0건으로
통과했다.

### 2.3 유지보수 예산과 runtime 경계

- 기존 거대 모듈 예산을 과거 크기가 아니라 현재 줄 수로 낮춰 1줄 증가도 감사가
  잡도록 했다. 예: `business_workflow.py` 3,715→2,477,
  `stage10.py` 1,903→778, `controller.py` 1,893→1,628.
- 전체 `engine` AST를 검사해 새 100줄 이상 함수는 즉시 실패하게 했다.
- 현재 43개 예외는 각각 현재 최대 줄 수, 담당 하위 시스템, 구체적 분리 사유,
  `2026-11-30` 만료일을 가진다. 함수가 1줄이라도 늘거나, 만료되거나, 분리 뒤
  stale 예외를 지우지 않으면 CI가 실패한다.
- `ParserRuntimeServices` 26개 필드의 `Any`를 구체적인 collaborator 타입으로
  교체했다.
- 승인 후속 handler는 전체 runtime 객체 대신 continuation 종류별 읽기 전용
  `RuntimePortView`를 받는다. PDF 파일 handler는 PDF task service 하나만 받는
  식이며 미허용 속성 접근과 포트 변경은 `AttributeError`로 끝난다.
- 등록된 확인 handler와 runtime grant 목록이 정확히 일치하는 아키텍처 테스트를
  추가했다.

이는 장함수 자체를 모두 없앤 완료가 아니라 **부채를 숨길 수 없고 다시 늘릴 수
없는 상태**로 만든 것이다. 특히 실패 분류, 동적 코드 preflight, 승인 응답,
skill 실행, command pipeline은 만료일 전에 실제 서비스 분리가 필요하다.

### 2.4 문서와 버전 정합성

- 개발 버전을 `1.1.0-rc.7`, 사용자 데이터 schema를 5로 올렸다.
- README를 PDF-2 상태에서 PDF MVP(PDF-0~4)·PDF-7 자동 수용 완료로 갱신했다.
- PDF 근거 응답·Office 산출물·분할·병합·회전·Undo와 OCR/현재 뷰어 미지원
  경계를 현재 코드에 맞췄다.
- VBA와 HWP는 2026-08-04 이 PC의 source-bound 통과 사실과 다른 PC에서의
  런타임 보안 재확인 의무를 함께 기록했다.
- README 버전, PDF 상태, Known limitations, CHANGELOG의 불일치를 회귀 테스트로
  차단했다.

## 3. 최종 검증 결과

### 3.1 소스·회귀·품질

| 검사 | 결과 |
|---|---|
| 전체 중앙 회귀 | 1,216개 통과, 조건부 3개 skip, 실패·오류 0 |
| clean checkout strict maintenance | 오류 0, 경고 0 |
| Ruff | 통과 |
| Python compileall | 통과 |
| GUI JavaScript `node --check` | 통과 |
| `pip check` | 통과 |
| `git diff --check` | 통과 |
| 격리 실패 주입 | 9/9 통과 |
| release security audit | 통과, known secret 0, finding 0 |

조건부 skip 3개는 실제 OpenAI/Gemini 계정을 쓰는 live AI 검사다. 이번 작업은
사용자의 외부 API 사용 승인을 새로 추론하지 않았으므로 실행하지 않았다.

### 3.2 성능

| 지표 | p95 | 기준 판정 |
|---|---:|---|
| 시작 사전점검 | 1,614.968ms | 통과 |
| parser 초기화 | 35.672ms | 통과 |
| 로컬 parse | 0.149ms | 통과 |
| 로컬 명령 | 0.062ms | 통과 |
| 실행 결과 정규화 | 0.005ms | 통과 |
| atomic JSON write | 23.157ms | 통과 |

전체 테스트 부하 중 overlay 작업자 스레드 시작이 한 번 230ms 걸려 기존 100ms
wall-clock 단정이 실패했다. 실제 2초 locator를 기다리지 않는 비동기 계약은
정상이고 단독 반복은 11ms였다. 단위 테스트를 scheduler benchmark와 분리해
500ms 안에 반환하는 비차단 계약으로 조정했다.

### 3.3 source-bound 제품 Goal

최종 판정 시각: 2026-08-04 14:25 KST

| 항목 | 결과 |
|---|---|
| source identity | clean commit `c191592e...`, dirty=false |
| 1,000문장 수용 | 1,000/1,000, 위험 안전 차단 100% |
| PDF 문장 수용 | 120/120 |
| PDF 실제 변환 | 분할·병합·회전·Undo·원본 불변 통과 |
| Excel/한글 최소 편집 | 통과 |
| Word/PowerPoint 편집 | 통과 |
| 네 앱 안정성 | 각 100/100 통과 |
| Excel VBA | 읽기·백업·수정·복원·별도 실행 통과 |
| HWP watchdog | 생성·재읽기 통과 |
| Excel→Word/한글→PPT | word·hwp·both 세 경로 통과 |
| 사용자 선호·스킬 재사용 | 통과 |
| 실패 분류·자가 진단 | 통과 |
| 최종 자동 판정 | `automated_pass_manual_pending` |

첫 전체 refresh에서 Excel/HWP 최소 편집 probe의 HWP 소유 문서가 40초 안에
응답하지 않아 `environment_blocked`가 한 번 기록됐다. 같은 실행의 HWP 100회
안정성, 저장 watchdog, HWP 보고서와 Word+HWP 동시 보고서는 모두 통과했다.
실패 probe만 독립 재실행하자 선택 교체·직접 서식 관찰·학습 글자 크기 read-back이
통과했고 최종 13개 probe가 모두 통과했다. 이는 코드 누락보다는 HWP Automation의
간헐 응답 위험을 보여준다. watchdog·소유 프로세스 격리·부분 파일 정리는 유지해야
하며 한 번의 성공을 모든 실행의 무응답 부재로 해석하지 않는다.

## 4. 최초 감사 발견 사항의 처리 상태

| 발견 | 상태 | 남은 일 |
|---|---|---|
| hosted CI와 Office Goal 혼합 | 코드 수정 완료 | 원격 push 후 실제 두 runner 결과와 branch protection 확인 |
| 한국어 Git 경로 누락 | 완료 | 없음 |
| API 키 평문 저장·백업 | 완료 | Windows 계정 변경/복원 사용자 안내를 실사용에서 확인 |
| 사람·실계정 Goal 증거 부재 | 대기 | live AI 3개, 화면 읽기, 음성, 비숙련자 관찰 |
| 최신 commit 원격 미보관 | 미완료 | 사용자가 승인한 원격 branch에 push |
| 장함수 43개 사각지대 | 게이트 완료, 분리 진행 필요 | 2026-11-30 전에 우선 안전 함수 분리 |
| 넓은 runtime service locator | 부분 개선 | 확인 handler 외 pipeline·학습 소비자도 작은 port로 추가 축소 |
| 거대 모듈 과거 예산 | 완료 | 감소할 때마다 budget도 함께 낮춤 |
| README·제약·변경 이력 불일치 | 완료 | 문서 정합성 테스트 유지 |
| 로컬 생성물 누적 | 정책 경고 유지 | 사용자 보존 판단 뒤 dry-run 정리 도구로 별도 정리 |

## 5. Goal 기준 최종 해석

자연어→현재 문맥→안전한 네이티브 실행→재읽기 검증·복구→성공 작업 재사용→
여러 앱 업무→사용자 선호 학습→실패 책임 분류의 자동 고리는 이 PC에서 연결돼
있다. 특히 좌표 클릭 데모가 아니라 실제 구조적 Office/HWP/PDF 대상, 승인 중
fingerprint 재확인, 원본 불변, read-back, 실패 주입으로 증명했다.

아직 Goal 전체가 끝났다고 말할 수 없는 이유는 코드가 없어서가 아니라 다음 사람
증거가 없기 때문이다.

1. NVDA 등 실제 화면 읽기 프로그램
2. 실제 음성 입력
3. 고령·비숙련 사용자의 관찰 과제
4. 승인된 소액 키를 사용한 OpenAI/Gemini live smoke

앞의 3개는 제품 수용 필수이며, 4번은 외부 AI 입구의 실제 환경 검증이다. 결과를
사용자가 수행·확인하기 전에는 자동 보고서가 대신 완료 처리하지 않는다.

## 6. 유지보수 기준 최종 해석

지금 상태는 “고칠 부분이 전혀 없는 완벽한 코드”가 아니다. 다만 다음 변경자가
모르고 위험을 키우기 어려운 상태로 개선됐다.

- 모든 engine 장함수와 거대 모듈이 자동 예산 대상이다.
- 예외는 담당·사유·만료일 없이는 존재할 수 없다.
- 승인 handler는 최소 runtime 권한만 받는다.
- 비밀정보·한국어 경로·생성물·문서 상태가 자동 검사 대상이다.
- 실제 앱 검사는 hosted CI 실패와 구분된 보호 runner 책임이다.

가장 중요한 다음 내부 리팩터링은 `failure_triage.classify`,
`dynamic_code_preflight.visit_Call`, confirmation selection, skill executor,
command pipeline 순서다. 새 기능을 이 함수 안에 계속 붙이지 말고 예외 최대 줄 수를
낮추는 방향으로만 변경해야 한다.

## 7. 재현 명령

```powershell
python -m tests.test_runner all -q
python -m verification.maintenance_failure_injection
python -m verification.maintenance_performance
python -m verification.maintenance_audit --strict
python -m verification.release_security_audit
python -m verification.product_goal_acceptance --automated-only --max-probe-age-hours 1
```

실제 Office/HWP probe 전체 갱신은 보호된 대화형 Windows 환경에서만 다음 명령을
사용한다.

```powershell
python -m verification.product_goal_acceptance --refresh-probes --automated-only --max-probe-age-hours 1 --probe-timeout 240
```

## 8. 결론

이번 보완으로 최초 감사의 P1 코드 결함 3개와 주요 문서·예산 사각지대는 닫혔다.
JARVIS는 이 PC에서 제품 Goal의 자동화 가능한 핵심을 실행할 수 있고, 실패 시
사용자 문서를 보호하면서 진단할 수 있다. 다음 릴리스 판단을 막는 것은 새 기능
부족이 아니라 **사람 사용성 증거, live AI 승인 검증, 원격 CI, 만료 전 장함수
분리**다.

이번 작업에서는 EXE를 만들지 않았다.
