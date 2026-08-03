# JARVIS 감사 수정·재검증 보고서

- 보고서 버전: 1.0
- 작성일: 2026-07-29 (KST)
- 대상: JARVIS Prototype 1.x Python 소스
- 기준 commit: `ddc7239ade4847a6b93c76b352b97b816f1d4465`
- branch: `feature/prototype-1.0-edit-mode`
- EXE 생성: 수행하지 않음
- 최종 판정: `automated_pass_manual_pending`

---

## 1. 결론

2026-07-29 1차 감사에서 확인된 P0/P1 결함을 수정하고 동일 항목을 다시 공격형·통합·실제 Office probe로 검증했다.

최종 결과는 다음과 같다.

- P0 동적 Python 승인 우회: 수정 및 재현 차단 완료
- P1 ExecutionResult 성공·검증 오판: 수정 및 생성자까지 strict 계약 적용 완료
- P1 문서 절대경로 로그 노출: UI·디스크 로그 경계 분리 및 마스킹 완료
- P1 오래된 probe의 현재 소스 재사용: source identity 강제 완료
- P2 parser 역참조 이름 우회: 객체 identity 기반 검사 추가 완료
- P2 거대 모듈 무제한 증가: 현재 실측치 기반 성장 예산 추가 완료
- 전체 자동 테스트: 1,043개 통과, 실패 0, 오류 0, skip 3
- Product Goal 자동 probe: 요구되는 11개 모두 통과
- 사용자 문서 변경: 0
- 자동 EXE 생성: 0

자동화할 수 있는 코드 수정과 검증은 완료됐다. 최종 제품 수용에는 실제 화면 읽기 프로그램, 실제 음성 입력, 비숙련 사용자 관찰 3개가 남아 있어 `accepted`로 과장하지 않는다.

---

## 2. 최종 source identity

모든 최종 제품 probe는 아래의 동일한 소스에서 생성됐다.

```text
identity_version   = 1
commit             = ddc7239ade4847a6b93c76b352b97b816f1d4465
dirty              = true
tree_hash          = 0cf1e93cd57733358411dccac1eb8eb45b9756e14f6b0db12fb1e9acdac2fe86
changed_paths_hash = 8dd7ae6ac46fd2bc6885aba67eb060ab755b5f1a9005c26a0936aefbfed71e1f
```

11개 보고서를 현재 source identity와 다시 대조한 결과는 전부 `MATCH`였다. 보고서와 문서 자체는 실행 소스 hash에서 제외하되 Python·JS·HTML·CSS·설정·테스트 변경은 hash를 바꾸도록 구성했다.

---

## 3. 결함별 수정 결과

### SEC-01 — 동적 Python 간접 호출 승인 우회

#### 수정 전

```text
os.system(...) 직접 호출       -> blocked
f = os.system; f(...)          -> blocked
튜플 구조 분해                 -> safe
lambda 인수 전달               -> safe
list/dict callable 조회        -> safe
```

AI batch 전체 경로에서 구조 분해 우회가 `success`, `macro_runner_called=True`, 승인 대기 없음으로 재현됐다.

#### 수정

- 위험 callable이 직접 호출 이외의 값으로 전달되면 fail-closed 처리
- 튜플·리스트·딕셔너리·람다·위험 모듈 객체 전달 차단
- `open`, `subprocess`, `os`, `pathlib.Path`, UI 제어, 네트워크, 레지스트리, ctypes 등 capability 전달 차단
- 위험 정책 버전 2에서 3으로 증가
- AI 전체 실행 경로에서 승인 전 실행기 미호출 테스트 추가

#### 수정 후

```text
tuple_alias     -> blocked
lambda_argument -> blocked
list_lookup     -> blocked
dict_lookup     -> blocked
module_container -> blocked
path_factory_container -> blocked
```

정상적인 직접 네트워크·앱 자동화 호출은 기존 정책대로 `confirmation_required`를 유지한다.

### RES-01 — 문자열 boolean과 모순 결과 승격

#### 수정 전

`"false"`가 Python의 `bool("false") == True` 규칙 때문에 `success=True`, `verified=True`로 승격됐다.

#### 수정

- `success`, `verified`, `retryable`은 정확한 `bool`만 허용
- 정규화 경계에서 타입·상태·오류형 계약 검사 강제
- malformed 결과는 `contract_error` 실패로 전환
- `success=True + status=failed` 등 모순 상태 거부
- `ExecutionResult` 생성자와 `success_result`·`failure_result`·confirmation helper에도 동일 계약 적용
- 잘못된 결과가 사후조건, 학습 streak, 사용자 피드백 성공으로 전달되지 않게 차단

#### 수정 후

```text
"false" / "yes" / 0 / 1 boolean -> contract_error
success=True + status=failed      -> contract_error
success=False + verified=True     -> contract_error
unknown error_type                -> contract_error
```

### PRV-01 — 사용자 문서 경로 로그 노출

#### 수정

- GUI 터미널 payload는 UI에만 전달
- 디스크 로그에는 `Terminal message forwarded` 이벤트만 기록
- 회전 일반 로그와 오류 로그 formatter에서 다음을 재차 마스킹
  - 인용된 Windows 절대경로
  - `path=`, `file_path=`, `target_path=` 등 구조화 경로
  - UNC 경로
  - 공백·쉼표·세미콜론을 포함한 비인용 경로
  - 기존 API 키·Bearer token

테스트에서는 UI가 원래 메시지를 받으면서 persistent logger가 payload를 받지 않는 것을 확인했다.

### EVD-01 — 현재 소스와 무관한 probe 증거

#### 수정

- `verification/source_identity.py` 추가
- commit, dirty, source tree hash, 변경 파일 집합 hash 생성
- 1,000문장 및 Stage 5·6·8·9·10·11·12 probe 전부 source identity 기록
- Product Goal 게이트가 현재 소스와 다른 보고서를 거부
- `source=None`, 다른 commit, 다른 tree, 다른 changed paths, dirty 불일치 거부
- 자동 probe가 없는 접근성 축은 `passed`가 아니라 `not_automated`로 표시

#### 최종 확인

최종 11개 probe의 source identity가 모두 현재 소스와 일치했다.

### ARC-01 — parser 역참조 검사 우회

#### 수정

속성 이름뿐 아니라 실제 객체 identity를 제한 깊이로 탐색하도록 했다.

- 다른 이름의 직접 참조: `self._host = parser`
- dict/list/tuple/set 내부 참조
- parser bound method
- `functools.partial`
- closure cell

현재 주요 15개 하위 서비스에서 parser 객체와 parser bound method의 장기 보관은 발견되지 않았다.

### MNT-01/DOC-01 — 구조 예산과 문서 드리프트

- parser 1,000줄 제한 유지
- 1,000줄 이상 핵심 모듈 11개에 현재 실측 성장 상한 추가
- 상한 초과 시 유지보수 감사 오류
- 과거 57%, 학습 35%를 2026-07-25 계획 추정치로 명시
- HWP 보안 모듈 현재 등록 상태 갱신
- 자동 테스트 기준을 1,043개로 갱신

대형 모듈을 이번 안전 수정과 섞어 즉시 분해하지는 않았다. 현재 예산으로 추가 성장을 막고 기능 단위 분리는 별도 리팩터링으로 수행한다.

---

## 4. 검증 결과

### 4.1 자동 회귀

| 항목 | 결과 |
|---|---:|
| Python compileall | 통과 |
| 전체 unittest | 1,043 통과 |
| 실패 | 0 |
| 오류 | 0 |
| 환경 조건부 skip | 3 |
| `git diff --check` | 통과 |

### 4.2 공격형·실패 주입

| 항목 | 결과 |
|---|---:|
| 동적 코드 간접 capability 우회 | 전부 차단 |
| malformed ExecutionResult | `contract_error` 처리 |
| 승인 만료·취소 | 미실행 |
| 반복 대상 변경 | 매번 새 승인 요구 |
| atomic fsync 실패 | 원본 보존 |
| 백업 복구 중 잠금 | 백업값 보존 |
| 영구 COM busy | 제한 시간 종료 |
| 일시 COM busy | 정확한 문서로만 복구 |
| 검증 후 rollback 실패 | 성공으로 승격하지 않음 |
| 실패 주입 전체 | 9/9 통과 |

### 4.3 자연어·진단

| 항목 | 결과 |
|---|---:|
| 1,000문장 | 1,000/1,000 통과 |
| direct command | 400 |
| missing information | 200 |
| context reference | 150 |
| typo/spoken | 100 |
| compound request | 100 |
| risk approval/undo | 50 |
| 외부 AI 호출 | 0 |
| 사용자 앱 시작 | 0 |
| 사용자 데이터 접근 | 0 |
| Stage 12 진단 계약 | 통과 |

### 4.4 실제 Office owned-fixture probe

| Probe | 결과 | 주요 증거 |
|---|---|---|
| Excel·HWP 편집 | 통과 | 쓰기·삽입·선택 교체·서식 read-back |
| Word·PowerPoint 편집 | 통과 | 커서·재선택·도형 텍스트·이동·스타일 read-back |
| 네 앱 안정성 | 통과 | 앱별 100회, 총 400회 검증 |
| Excel VBA | 통과 | 백업·변경·복원·별도 실행·위험 검사 |
| HWP watchdog | 통과 | 실제 생성·read-back·부분 출력 안전 |
| Word workflow | 통과 | 관계·집계·조인·보고서·PPT·원본 불변 |
| HWP workflow | 통과 | HWP 보고서·PPT·원본 불변 |
| Word+HWP workflow | 통과 | 두 보고서 동시 생성 |
| 사용자 선호 학습 | 통과 | 3회 관찰·승인·충돌·재사용·산출물 인계 |
| 실패 진단 | 통과 | 비식별 incident·자동 수정 없음 |

모든 Office probe는 소유 fixture만 사용했고 사용자 문서를 수정하지 않았다. 생성한 소유 프로세스만 정리했다.

### 4.5 성능

최종 정식 성능 결과:

| 지표 | p95 | 허용값 | 결과 |
|---|---:|---:|---|
| startup preflight | 1,699.00ms | 2,100.87ms | 통과 |
| parser initialization | 36.02ms | 112.04ms | 통과 |
| local parse | 0.2882ms | 0.9908ms | 통과 |
| local command | 0.0734ms | 1.0412ms | 통과 |
| result normalize | 0.0064ms | 0.0222ms | 통과 |
| atomic JSON write | 10.7651ms | 24.7278ms | 통과 |

Office 종료 직후 두 번의 실행에서 atomic JSON write p95가 약 31ms로 일시 초과했다. 기준을 완화하지 않고 독립 7개 배치로 재측정했으며 p95 범위는 7.31~13.65ms였다. 최종 정식 실행은 10.77ms로 통과했다. 이 변동은 Windows 디스크 flush·백신 간섭 가능성이 있으므로 차기 유지보수에서도 관찰한다.

---

## 5. Product Goal 판정

| Goal 축 | 자동 판정 |
|---|---|
| 자연어 | 통과 |
| 현재 문맥 | 통과 |
| 안전한 네이티브 실행 | 통과 |
| 성공 작업 재사용 | 통과 |
| 앱 간 업무 | 통과 |
| 사용자 선호 학습 | 통과 |
| 실패 분류 | 통과 |
| 비숙련자 접근성 | `not_automated` |

최종 자동 판정:

```text
automated_passed = true
environment_blocked = false
manual_passed = false
overall_status = automated_pass_manual_pending
```

남은 수동 수용 항목:

1. 실제 화면 읽기 프로그램
2. 실제 음성 입력
3. 비숙련 사용자 관찰

이 세 항목은 코드 테스트나 COM 자동화 결과로 대체하지 않는다.

---

## 6. 잔여 사항과 릴리스 판단

### 코드 차단 사항

현재 재현 가능한 P0/P1 코드 차단 사항은 없다.

### 비차단 관찰 사항

- `outputs/` 생성 디렉터리가 존재해 유지보수 감사 경고 1개가 남는다. Git 추적 대상은 아니다.
- Eel이 사용하는 pyparsing 구형 API의 deprecation warning이 있다.
- Windows atomic fsync 성능은 시스템 부하에 따라 변동하므로 지속 관찰한다.
- 대형 모듈은 성장 예산만 적용했으며 기능 단위 분해는 후속 유지보수 작업이다.

### 버전 관리 상태

현재 working tree는 기존 누적 개발 변경을 포함해 dirty 상태다. 최종 감사 시점 기준:

```text
tracked modified = 81
untracked        = 19 (이 보고서 추가 전)
tracked diff     = +1,580 / -955
```

기능과 자동 증거는 통과했지만, 기존 리팩터링·동작 변경·이번 보안 수정이 아직 clean commit으로 분리되지 않았다. 사용자 변경을 임의로 커밋하거나 reset하지 않았으며, 릴리스 태그 전에는 목적별 커밋 분리가 필요하다.

### 최종 판단

- Python 개발·자동 검증: 완료
- 실제 Office owned-fixture 검증: 완료
- EXE 빌드: 미수행, 사용자 지시 준수
- Product Goal 최종 수동 수용: 3개 대기
- 릴리스 태그: 수동 수용과 clean commit 전까지 보류
