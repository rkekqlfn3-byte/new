# JARVIS 최종 Goal·유지보수성 감사보고서

- 보고서 버전: 1.0
- 감사일: 2026-08-03 (KST)
- 대상: JARVIS `1.1.0-rc.6` Python 소스
- 기준 branch: `feature/prototype-1.0-edit-mode`
- 기준 commit: `ddc7239ade4847a6b93c76b352b97b816f1d4465`
- EXE 생성: 수행하지 않음
- 제품 Goal 공식 판정: `automated_pass_manual_pending`
- 유지보수성 판정: **B- / 조건부 양호**

---

## 1. 최종 결론

### Goal 만족 여부

JARVIS는 현재 정의된 제품 Goal 8개 축 중 자동화 가능한 7개 축을 현재 소스와
일치하는 새 증거로 통과했다. 자연어, 현재 문맥, 안전한 네이티브 실행, 성공 작업
재사용, 앱 간 업무, 사용자 선호 학습, 실패 분류는 모두 통과했다.

다만 비숙련자 접근성 축의 핵심 세 항목은 자동화가 대신 판정할 수 없다.

1. 실제 화면 읽기 프로그램
2. 실제 음성 입력
3. 비숙련 사용자 관찰

따라서 **구현은 Goal을 상당히 만족하지만 제품 전체를 `accepted`라고 선언할
단계는 아니다.** 정확한 현재 판정은 `automated_pass_manual_pending`이다.

### 유지보수 용이성

코드는 안전 계약, 테스트, 소스 신원 검증, 실패 주입 면에서 강하다. 반면 핵심
모듈과 함수가 크고, 실행 시점에는 하위 계층이 여전히 `CommandParser` 전체를
`parser` 또는 `runtime`으로 광범위하게 전달받는다. 현재 파서는 961줄·클래스
멤버 73개이며, 1,000줄 제한에 근접했다.

즉 현재 구조는 **회귀를 발견하기는 좋지만 기능을 찾아 안전하게 수정하기 쉬운
구조는 아니다.** 유지보수성은 조건부 양호이며, 릴리스 전 정리와 후속 구조 개선이
필요하다.

### 차단 결함

- 새로 재현된 P0/P1 런타임 기능 결함: 없음
- 릴리스 차단 사항: 101개 경로가 섞인 dirty working tree와 수동 수용 3개
- 유지보수 고위험: 전체 runtime 결합, 대형 모듈·함수, 자동 CI·정적 품질 게이트 부재

---

## 2. 감사 대상 소스 신원

모든 최종 제품 probe는 아래 소스 신원과 일치했다.

```text
identity_version   = 1
commit             = ddc7239ade4847a6b93c76b352b97b816f1d4465
branch             = feature/prototype-1.0-edit-mode
dirty              = true
tree_hash          = 0cf1e93cd57733358411dccac1eb8eb45b9756e14f6b0db12fb1e9acdac2fe86
changed_paths_hash = 8dd7ae6ac46fd2bc6885aba67eb060ab755b5f1a9005c26a0936aefbfed71e1f
```

현재 Git 상태는 다음과 같다.

```text
tracked modified = 81
untracked        = 20 (이 보고서 추가 전)
total dirty paths = 101
tracked diff     = +1,580 / -955
마지막 commit    = 2026-07-23
```

보고서와 `outputs/` 생성물은 제품 소스 hash에 포함되지 않는다. Python·JS·HTML·CSS,
설정과 테스트 변경은 source identity를 바꾼다.

---

## 3. 감사 방법

이전 보고서의 결론을 그대로 재사용하지 않고 다음을 현재 트리에서 다시 수행했다.

- 제품 Goal 문서와 수용 게이트를 코드·테스트에 대조
- parser, confirmation, pipeline, skill, edit, workflow, 진단 경계 검토
- 모듈·함수 크기와 runtime 결합 실측
- 전체 Python 컴파일과 unittest
- 현재 소스에 결박된 11개 owned-fixture 제품 probe 재생성
- Excel·한글·Word·PowerPoint 네이티브 read-back과 안정성 검증
- 동적 코드, 승인, 원자 저장, COM busy, rollback 실패 주입
- 현재 트리와 Git 이력의 비밀정보 패턴 감사
- 성능 p95와 소스 크기 기준선 비교
- 1,000문장 로컬 격리 배터리 실행

사용자 문서, 사용자 앱 프로세스, 사용자 선호를 변경하지 않았다. 새 EXE, 배포본,
릴리스 태그도 만들지 않았다.

---

## 4. 제품 Goal 축별 판정

| Goal 축 | 자동 판정 | 주요 새 증거 | 남은 경계 |
|---|---|---|---|
| 자연어를 행동으로 변환 | 통과 | 1,000/1,000, crash·오동작 0, 정보 누락 200건 정확 질문 | 장문·장기 대화·실제 음성은 별도 |
| 현재 문맥 이해 | 통과 | Excel·한글·Word·PowerPoint 선택·커서·도형 문맥 및 read-back | 더 긴 앱 간 지시 대상과 장기 참조 |
| 안전한 네이티브 실행 | 통과 | 네 앱 실제 편집, 각 앱 100회 안정성, VBA, HWP watchdog, 승인·검증·복구 | 더 많은 실제 환경 조합 |
| 성공 작업 재사용 | 통과 | 내용 없는 workflow skill 저장, 매 실행 승인, 새 산출물 실제 재생 | 임의 새 단계·순서 조합 |
| 여러 앱 업무 연결 | 통과 | Word, 한글, Word+한글 세 업무, Excel 원본 불변, PPT 재열기 | 더 긴 사용자 정의 업무 |
| 사용자 방식 학습 | 통과 | 3회 관찰→후보→승인, 충돌 취소·교체, 보고서·PPT 서식 read-back | 제한된 명시적 선호 범위 |
| 실패 책임 분류·대응 | 통과 | 구조화 incident, 개발 이슈, 환경 차단, 복구 계약, 자동 수정 없음 | 앱 재실행이 필요한 복구 |
| 비숙련자 접근성 | `not_automated` | 키보드·ARIA 계약 테스트는 존재 | 화면 읽기·음성·비숙련자 관찰 3개 필수 |

### 공식 수용 결과

```text
automated_passed  = true
environment_blocked = false
manual_passed     = false
overall_status    = automated_pass_manual_pending
```

이 판정은 Goal을 과장하지 않는다. 자동 코드 증거가 강하다는 것과 실제 비숙련자가
도움 없이 사용할 수 있다는 것은 서로 다른 주장이다.

---

## 5. 유지보수성 평가

### 5.1 강점

#### 명시적 안전 계약

- `ExecutionResult`의 boolean·상태·오류형을 strict하게 검증한다.
- 실행 성공과 사후조건 검증 성공을 구분한다.
- 동적 Python capability 전달과 셸·레지스트리·자격증명·재귀 삭제를 fail-closed로
  다룬다.
- 승인 대기 상태는 `ConfirmationRegistry.pending`이 단일 소유한다.
- 사용자 변경은 승인, 대상 재확인, 실행, read-back, rollback 계약을 거친다.

#### 회귀·실패 증거

- 전체 테스트 1,043개가 단위·통합·Windows 계약을 함께 다룬다.
- engine 49,307줄에 대해 tests 29,080줄, verification 13,998줄의 검증 코드가 있다.
- probe 보고서는 commit뿐 아니라 dirty source tree hash와 변경 경로 hash까지 맞아야
  한다.
- 실패 주입은 승인 만료·취소, 반복 대상 변경, 저장 실패, COM busy, 검증 뒤 rollback
  실패까지 포함한다.

#### 최근 구조 개선

- `_parse_native_*` staticmethod alias가 `CommandParser`에서 제거됐다.
- confirmation 헬퍼가 `ConfirmationRegistry` 아래로 그룹화됐다.
- 조립된 자식 객체가 parser를 장기 보관하는 역참조는 객체 identity 검사까지 포함해
  차단한다.
- 생성자는 이전보다 명시적인 서비스 의존성을 받는다.

### 5.2 유지보수 위험과 결함

#### [P1-릴리스] MNT-01 — 101개 dirty 경로가 하나의 변경 묶음

81개 tracked 수정과 20개 untracked가 한 트리에 섞여 있다. 마지막 commit은
2026-07-23이며, 이후 리팩터링·보안·테스트·문서 변경이 목적별 commit으로 분리되지
않았다.

영향:

- 회귀 시 원인 commit을 좁히기 어렵다.
- 부분 revert와 bisect가 사실상 불가능하다.
- 현재 자동 증거는 강해도 릴리스 provenance는 약하다.

판정: 기능 실행을 막지는 않지만 릴리스·태그를 막는 사항이다.

#### [P2] MNT-02 — 의존성 주입 3단계가 의미상 완결되지 않음

생성자에서 `self` 전체를 넘기는 양방향 참조는 제거됐다. 그러나 실행 시점에는
confirmation, pipeline, AI action, learned replay가 여전히 parser 전체를
`parser` 또는 `runtime`으로 받는다. 관련 정적 참조는 감사 범위에서 약 250곳이다.

예:

- `CommandPipeline.execute(parser, ...)`
- `ConfirmationRegistry.resolve(runtime, ...)`
- `ConfirmationFactory.queue_*(runtime, ...)`
- `AIActionHandler.validate_result(parser, ...)`
- `LearnedReplayService.*(runtime, ...)`

현재 아키텍처 테스트는 “자식이 parser를 저장하지 않는다”는 계약은 잘 잡지만,
“호출 시 필요한 작은 인터페이스만 받는다”는 계약은 강제하지 않는다.

영향:

- 하위 서비스의 실제 의존성이 signature에서 드러나지 않는다.
- parser 변경의 파급 범위가 넓다.
- 독립 단위 테스트와 서비스 재사용이 어려워진다.

#### [P2] MNT-03 — 대형 모듈과 장시간 함수

engine에는 1,000줄 이상 모듈 11개, 900줄 이상 모듈 16개, 100줄 이상 함수 61개가
있다.

| 파일 | 줄 수 |
|---|---:|
| `engine/workflows/business_workflow.py` | 3,715 |
| `engine/app_actions/excel_adapter.py` | 2,350 |
| `engine/edit_mode/stage10.py` | 1,903 |
| `engine/edit_mode/controller.py` | 1,893 |
| `engine/app_actions/powerpoint_adapter.py` | 1,289 |
| `engine/edit_mode/native_bridge.py` | 1,111 |
| `engine/action_executor.py` | 1,101 |

대표 긴 함수:

- `BusinessWorkflow._apply_explicit_join`: 425줄
- `FailureTriage.classify`: 343줄
- `DynamicCodePreflight.visit_Call`: 252줄
- `StructuredEditIntentAnalyzer.analyze`: 246줄
- `BusinessWorkflow.run`: 233줄

`maintenance_audit`의 line budget은 이 파일들의 현재 크기를 그대로 상한으로
고정한다. 추가 비대화는 막지만 현재 복잡도를 줄이지는 않는다.

#### [P2] MNT-04 — CommandParser가 여전히 큰 파사드

`engine/parser.py`는 961줄, 클래스 멤버 73개다. `_parse_native_*`와 많은 confirmation
위임은 제거됐지만 진단, 학습, 동적 코드, AI batch, 실행, 로컬 분석 호환 메서드가
한 클래스에 남아 있다. 1,000줄 계약은 현재 39줄 여유뿐이다.

파사드가 작아진 방향은 맞지만 아직 “얇은 조립·진입점”이라고 보기는 어렵다.

#### [P2] MNT-05 — 자동 정적 품질 게이트가 실행 환경에 없음

- `pyproject.toml`에 Ruff 설정은 있으나 현재 환경에는 Ruff가 설치되지 않아
  `python -m ruff check ...`를 실행할 수 없었다.
- `.github/workflows`가 없어 push/PR 자동 게이트가 없다.
- coverage 기준선과 mypy/pyright 타입 게이트가 없다.
- `pip check`와 requirements lock 자체는 정상이다.

테스트 수는 많지만 개발자가 로컬에서 게이트를 빼먹는 것을 저장소가 자동으로
막지는 못한다.

#### [P2-시험범위] TST-01 — 1,000문장은 넓이보다 결정적 회귀에 집중

1,000개 사례 ID는 모두 독립이며 자동 통과했다. 다만 문장 텍스트는 857개가
고유하고, category+text 조합은 865개다.

- 앱 열기·닫기 직접 명령: 400
- 대상 누락 질문: 200
- Excel 문맥: 150, 15개 표현을 10개 선택 범위에 반복
- 오타·발화 변형: 100
- 두 앱 열기 복합 명령: 100
- 안전 차단·되돌리기: 50

이 배터리는 실제 앱을 1,000번 조작하지 않는다. 격리 로컬 parser·의도 분석·안전
차단을 검증한다. 실제 앱 동작은 별도 owned-fixture probe와 400회 안정성 시험이
담당한다. 장문 대화, 자유 복합 업무, 음성 인식, 화면 읽기는 이 1,000건의 통과로
증명되지 않는다.

#### [P3] OPS-01 — 비차단 운영 경고

- `outputs/` 생성 디렉터리가 있어 유지보수 감사 경고 1개가 발생한다. Git에는
  포함되지 않는다.
- Eel이 사용하는 pyparsing 구형 이름의 deprecation warning이 반복된다.
- Windows 원자 저장 p95는 디스크 flush와 백신 부하에 따라 변동할 수 있다.

---

## 6. 자동 검증 결과

### 6.1 기본 게이트

| 검사 | 결과 |
|---|---:|
| Python compileall | 통과 |
| 전체 unittest | 1,043 통과 |
| 실패 / 오류 | 0 / 0 |
| 조건부 skip | 3 |
| `git diff --check` | 통과 |
| `pip check` | 통과 |
| 유지보수 감사 | 오류 0, 경고 1 |
| 릴리스 보안 감사 | findings 0 |
| 실패 주입 | 9/9 통과 |

### 6.2 1,000문장 배터리

| 범주 | 사례 | 결과 |
|---|---:|---:|
| 앱 직접 명령 | 400 | 400 통과 |
| 정보 누락 질문 | 200 | 200 통과 |
| Excel 문맥 편집 | 150 | 150 통과 |
| 오타·발화 변형 | 100 | 100 통과 |
| 복합 명령 | 100 | 100 통과 |
| 안전 차단·되돌리기 | 50 | 50 통과 |
| 합계 | 1,000 | 1,000 통과 |

추가 결과:

```text
crash = 0
wrong_action = 0
external_ai_calls = 0
user_data_access = 0
user_applications_started = 0
```

### 6.3 실제 Office owned-fixture

| Probe | 결과 | 주요 증거 |
|---|---|---|
| Excel·한글 편집 | 통과 | 선택 교체·입력·삽입·서식 read-back |
| Word·PowerPoint 편집 | 통과 | Range·Shape 편집·이동·서식 read-back |
| 네 앱 안정성 | 통과 | 앱별 100회, 총 400/400 검증 |
| Excel VBA | 통과 | 백업·변경·복원·실행 분리·위험 검사 |
| HWP watchdog | 통과 | 5.75초 생성·read-back·소유 프로세스 정리 |
| Word workflow | 통과 | 관계·집계·조인·Word·PPT·원본 불변 |
| HWP workflow | 통과 | 한글 보고서·PPT·원본 불변 |
| Word+HWP workflow | 통과 | 두 보고서 동시 생성·검증 |
| 사용자 학습 | 통과 | 후보·승인·충돌·재생·최근 산출물 인계 |
| 실패 진단 | 통과 | 비식별 incident·자동 수정/빌드 없음 |

모든 probe는 JARVIS 소유 fixture만 사용했고 사용자 문서를 수정하지 않았다.

### 6.4 성능 기준선

| 지표 | 현재 p95 | 허용값 | 결과 |
|---|---:|---:|---:|
| startup preflight | 1,116.5737ms | 2,100.8733ms | 통과 |
| parser initialization | 35.1486ms | 112.0355ms | 통과 |
| local parse | 0.1844ms | 0.9908ms | 통과 |
| local command | 0.0271ms | 1.0412ms | 통과 |
| result normalize | 0.0053ms | 0.0222ms | 통과 |
| atomic JSON write | 18.0212ms | 24.7278ms | 통과 |

소스 크기 두 기준도 허용 범위 안에서 통과했다.

---

## 7. 사용자용 1,000 예제 파일

동일한 1,000개 사례를 사용자가 직접 재검증할 수 있도록 다음 Excel 추적표를
생성했다.

```text
outputs/final-audit-2026-08-03/JARVIS_1000_예제_테스트_2026-08-03.xlsx
```

- 요약: 전체·자동 통과·사용자 실행·사용자 통과·실패 집계
- 테스트 1000: 사례 ID, 문장, 준비 문맥, 기대 동작, 자동 결과, 사용자 기록 열
- 사용 안내: Python 소스 실행 방법과 안전 수칙
- 자동 검증: 소스 신원, 전체 게이트, 수동 수용 대기 항목

사용자 기록 열은 `미실행/통과/부분통과/실패`, 오류 분류, P0~P3, 재현 여부를
드롭다운으로 제공한다. Excel 문맥 사례는 이 추적표가 아닌 별도 테스트용 문서에서
수행해야 한다.

---

## 8. 릴리스 판단과 권고 순서

### 지금 가능한 것

- Python 소스 개발 실행
- 격리 로컬 자연어 회귀
- 소유 fixture 기반 네 앱 기능·안전 검증
- 사용자 수동 1,000문장 재검증 시작

### 아직 하면 안 되는 선언

- Product Goal `accepted`
- clean release 또는 최종 태그
- 유지보수 리팩터링 완료
- 실제 비숙련자 접근성 완료

### 권고 우선순위

1. 101개 dirty 경로를 기능·리팩터링·보안·테스트·문서 commit으로 분리한다.
2. `ParserRuntimeServices` 또는 작은 Protocol을 도입해 하위 계층의 full runtime
   인자를 필요한 포트로 축소한다.
3. `business_workflow`, `stage10`, `controller`, Office adapter의 100줄 이상 함수를
   기능 단위 서비스로 분리한다.
4. Ruff를 개발 lock에 넣고 GitHub CI에서 compile, lint, unittest, maintenance,
   source-bound goal gate를 강제한다.
5. 실제 화면 읽기, 음성 입력, 비숙련자 관찰을 끝내 수동 evidence를 등록한다.

---

## 9. 최종 감사 의견

JARVIS는 단순 데모 수준을 넘어 실제 Office를 구조적으로 조작하고, 승인·검증·복구,
학습과 실패 분류까지 자동 증거로 보여 주는 강한 프로토타입이다. 특히 안전 계약과
owned-fixture 검증은 Goal 철학인 “너네 실수도 너네가 고쳐”에 실질적으로 접근했다.

그러나 “사용자를 배운다”는 범위는 아직 제한된 승인형 선호·레시피이고, 비숙련자
접근성은 사람 시험이 남았다. 코드도 테스트로 보호되지만 full runtime 결합과 대형
모듈 때문에 수정 비용이 높다.

따라서 최종 판정은 다음과 같다.

> **Goal 자동 구현: 통과. 제품 최종 수용: 수동 3개 대기. 유지보수성: 보호는 강하지만
> 구조는 아직 무거운 B- 수준. Python 개발·사용자 시험은 진행 가능하나 clean release는
> 보류한다.**
