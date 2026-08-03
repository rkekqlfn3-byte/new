# JARVIS Goal·유지보수 개선 완료 감사보고서

- 감사 시각: 2026-08-04 KST
- 대상: JARVIS `1.1.0-rc.6` Python 소스
- 브랜치: `feature/prototype-1.0-edit-mode`
- EXE 빌드: 수행하지 않음
- 자동 Goal 판정: `automated_pass_manual_pending`
- 유지보수 판정: **B+ / 개발 지속 가능, 구조 개선 효과 확인**

## 1. 결론

요청된 다섯 작업 중 코드와 자동화로 완료 가능한 네 작업은 완료됐다. 101개 dirty
경로는 목적별 커밋으로 분리됐고, parser 전체를 하위 계층에 건네던 결합은
`ParserRuntimeServices`로 축소됐다. 지정된 대형 모듈의 100줄 이상 함수는 기능
서비스로 분리됐으며, Ruff·컴파일·전체 테스트·유지보수 감사·source-bound Goal
게이트가 GitHub Actions에 고정됐다.

현재 소스 해시에 대한 전체 자동 결과는 1,051개 테스트 통과, 1,000문장 1,000건
통과, 네 Office 앱 안정성 400/400 통과다. Goal의 자동 검증 가능한 7개 축은 모두
통과했다.

다만 실제 화면 읽기, 실제 음성 입력, 비숙련자 관찰은 사람이 수행해야 하므로
`passed`를 대신 기록하지 않았다. 세 항목은 정식 프로토콜과 evidence 등록 도구가
준비된 `pending` 상태다. 따라서 제품 전체를 아직 `accepted`라고 선언하지 않는다.

## 2. 변경 추적성

기존 101개 dirty 경로는 다음과 같이 분리됐다.

| 목적 | 커밋 | 내용 |
|---|---|---|
| 리팩터링 | `007b9c9` | parser 구성 서비스 분리 |
| 기능 | `2209253` | 연결 문서 편집 강화 |
| 보안 | `2ea1c41` | 실행 안전·개인정보 계약 강화 |
| 테스트 | `e88b007` | source-bound 유지보수 게이트 추가 |
| 문서 | `573bfcf` | Goal 구조·감사 기록 |

이후 개선도 기능 단위로 분리했다.

| 커밋 | 내용 |
|---|---|
| `fedc918` | full parser runtime 역류를 명시적 서비스로 교체 |
| `2310c91` | 연결 문서 복구·Office Undo 서비스 추출 |
| `2171a70` | Stage 10 의도·실행 서비스 분리 |
| `3b8ba7b` | 업무 조인·분석·실행 서비스 분리 |
| `869fd0a` | Ruff-clean 기준선과 개발 lock |
| `5ed71af` | GitHub 품질·Goal 게이트 |
| `5397f81` | 기존 action executor 줄 예산 보존 |

## 3. Goal 적합성

| Goal 축 | 자동 판정 | 근거 |
|---|---|---|
| 자연어를 행동으로 변환 | 통과 | 1,000문장, 잘못된 실행·crash 0 |
| 현재 문맥 이해 | 통과 | Excel·한글·Word·PPT 선택/커서 read-back |
| 안전한 네이티브 실행 | 통과 | 승인, 재검증, 적용, read-back, Undo/복구 |
| 성공 작업 재사용 | 통과 | 승인된 workflow skill 실제 재생 |
| 여러 앱 업무 연결 | 통과 | Excel 분석→Word/한글 보고서→5장 PPT |
| 사용자 방식 학습 | 통과 | 3회 증거, 승인 전 비활성, 충돌·교체·감사 |
| 실패 책임 분류 | 통과 | 비식별 incident, 합성 재현, 자동 수정 없음 |
| 비숙련자 접근성 | 수동 대기 | 화면 읽기·음성·비숙련자 관찰 필요 |

Goal 철학 중 “너네 실수도 너네가 고쳐”는 검증·복구·실패 진단에서 강하게
구현됐다. “너네가 나를 배워”는 승인 기반 선호와 업무 레시피 수준에서 동작하지만,
장기간 실제 사용자 습관을 폭넓게 학습했다는 증거는 아직 제한적이다.

## 4. 유지보수성

### 4.1 의존성 축소

`ParserRuntimeServices`는 필요한 manager·executor·상태 포트만 제공하며 parser
객체나 bound parser method를 보관하지 않는다. pipeline, confirmation, AI action,
learned replay가 full runtime 대신 이 서비스를 받는다. composition 테스트가 parser
역참조와 금지된 생성자 주입을 감시한다.

### 4.2 대형 함수 분리 결과

| 원본 모듈 | 현재 줄 | 최대 함수 | 100줄 이상 함수 |
|---|---:|---:|---:|
| `business_workflow.py` | 2,402 | 94 | 0 |
| `stage10.py` | 778 | 97 | 0 |
| `controller.py` | 1,628 | 85 | 0 |
| `excel_adapter.py` | 2,349 | 94 | 0 |
| `hwp_adapter.py` | 968 | 71 | 0 |
| `word_adapter.py` | 693 | 80 | 0 |
| `powerpoint_adapter.py` | 1,149 | 80 | 0 |

새 `workflow_join_services`, `excel_analysis_services`,
`workflow_execution_services`, `stage10_intent_services`,
`stage10_action_services`, `connected_document_recovery`,
`office_undo_services`도 모두 함수 99줄 이하 계약을 만족한다.

모듈 자체는 `business_workflow`와 Excel adapter가 여전히 2천 줄을 넘는다. 함수
경계는 좋아졌지만 다음 유지보수 주기에는 기능군별 파일 분할이 필요하다. 이 때문에
유지보수 등급은 A가 아니라 B+로 판정한다.

### 4.3 자동 품질 게이트

개발 lock은 Ruff `0.16.1`을 고정한다. Windows GitHub Actions는 push/PR에서 다음을
순서대로 강제한다.

1. 실제 저장소 Python 소스 compile
2. Ruff lint
3. 전체 unittest
4. strict maintenance audit
5. fresh probe를 포함한 source-bound product Goal gate

가상환경과 생성물을 컴파일 대상에서 제외해 로컬 Python 2 호환 패키지 같은 외부
파일이 저장소 품질 판정을 오염시키지 않는다.

## 5. 최종 자동 검증

| 검사 | 결과 |
|---|---:|
| Python compileall | 통과 |
| Ruff | 통과 |
| 전체 unittest | 1,051 통과, 실패·오류 0, skip 3 |
| strict maintenance audit | 통과, 비차단 경고 1 |
| 1,000문장 배터리 | 1,000/1,000 |
| 네 앱 안정성 | 400/400 |
| Excel VBA·HWP watchdog | 통과 |
| Word/HWP/both workflow | 모두 통과 |
| 사용자 학습·실패 진단 | 통과 |
| 사용자 문서 변경 | 0 |
| EXE 생성 | 0 |

유지보수 감사의 유일한 경고는 Git에서 제외된 `outputs/` 작업 디렉터리가 존재한다는
운영 알림이다. 소스 오류가 아니며 자동 시험 산출물을 보존하기 위해 유지했다.

## 6. 수동 evidence 상태

수동 절차는 [MANUAL_GOAL_EVIDENCE_RUNBOOK.md](MANUAL_GOAL_EVIDENCE_RUNBOOK.md)에
고정했다. 등록기는 실제 수행 확인(`--attest`), 시간대가 있는 수행 시각, 정확한
protocol ID가 없으면 `passed`를 수용하지 않는다. evidence JSON은 Git에서 제외되고
원문 메모나 사용자 문서 내용은 저장하지 않는다.

현재 상태:

```text
screen_reader            = pending
voice_input              = pending
novice_user_observation  = pending
overall_status           = automated_pass_manual_pending
```

## 7. 최종 감사 의견

JARVIS는 현재 컴퓨터에서 Python 소스 기반 강한 프로토타입으로 실행·검증할 수 있다.
Goal의 자연어, 문맥, 네이티브 안전, 재사용, 다중 앱, 승인형 학습, 실패 진단은 최신
소스에 결속된 자동 증거로 만족한다. 의존성 경계와 함수 크기, 자동 CI도 이전 감사의
핵심 유지보수 결함을 해소했다.

남은 차단점은 코드 결함이 아니라 사람만 판정할 수 있는 접근성 evidence 3건이다.
따라서 정확한 결론은 다음과 같다.

> **자동 구현과 유지보수 개선은 완료됐다. 제품 Goal 최종 수용은 화면 읽기·음성
> 입력·비숙련자 관찰을 실제로 수행하고 등록한 뒤에만 `accepted`가 된다.**
