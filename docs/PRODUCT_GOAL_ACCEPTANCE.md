# JARVIS 제품 Goal 수용 게이트

## 목적

제품 Goal의 8개 축을 전체 자동 회귀와 JARVIS 소유 임시 문서 probe에 연결한다.
자동화가 통과했다는 사실을 실제 화면 읽기 프로그램, 음성 입력, 비숙련자 관찰까지
끝났다고 과장하지 않는 것이 핵심이다.

## 자동 증거 실행

기존의 최근 소유 문서 probe 결과를 사용해 전체 자동 회귀와 8개 Goal 축을
판정한다. 앱 간 업무 축은 Word, 한글, 두 보고서 동시 생성의 세 실제 probe가
모두 있어야 통과한다. Stage 5는 Excel과 한글 대상이 모두 있어야 하며, 한글
결과에는 승인된 생략 글자 크기 선호의 실제 read-back 검사가 포함돼야 한다.
또한 한글 선택 문장 교체 후 같은 범위의 직접 단일 서식 교정이 원문 없이 학습
증거로 기록되는 검사도 필수다.
Stage 6도 Word와 PowerPoint 대상이 모두 있어야 하며, Word 결과에는 접힌
Range→동일 Range 재선택 뒤 직접 서식 교정, PowerPoint 결과에는 단일 Shape
직접 서식 교정의 구조화 증거 기록 검사가 각각 포함돼야 한다.

```powershell
python -m verification.product_goal_acceptance --automated-only
```

결과는 `verification/product_goal_acceptance_report.json`에 생성된다. 문서 경로,
선택 내용, 사용자 원문과 수동 시험 메모는 보고서에 복사하지 않는다. 보고서에는
검사 상태, 실패한 probe ID, 생성 시각, 현재 Git commit과 dirty 여부만 남긴다.

소유 문서 probe가 7일보다 오래됐거나 관련 기능을 크게 바꾼 뒤에는 다음처럼
증거를 새로 만든다.

```powershell
python -m verification.product_goal_acceptance --refresh-probes --automated-only
```

이 명령은 Excel·한글·Word·PowerPoint의 JARVIS 소유 임시 문서만 사용하며 기존
사용자 프로세스가 있거나 한 대상이라도 건너뛰면 수용하지 않는다. Stage 8의
앱별 100회 안정성 검증을 포함하므로 수분이 걸릴 수 있다.

## 수동 수용 항목

다음 세 항목은 자동 테스트가 대신 통과시킬 수 없다.

1. NVDA 등 실제 화면 읽기 프로그램으로 핵심 명령·확인·오류 흐름을 완료한다.
2. 실제 음성 입력으로 대표 자연어 명령과 확인 응답을 완료한다.
3. 비숙련 사용자가 [직접 시험 문장 100개](PROTOTYPE1_MANUAL_TEST_100.md) 중
   대표 업무를 도움 없이 수행하고, 막힌 문구와 포커스 문제를 기록한다.

`verification/product_goal_manual_acceptance.example.json`을
`verification/product_goal_manual_acceptance.json`으로 복사한 뒤 실제로 끝낸
항목만 `passed`와 수행 시각으로 바꾼다. 실제 메모는 별도 사용자 문서에 두고
이 JSON에는 넣지 않는다. 그다음 전체 판정을 실행한다.

```powershell
python -m verification.product_goal_acceptance `
  --manual-evidence verification/product_goal_manual_acceptance.json
```

판정은 다음 셋 중 하나다.

- `automated_failed`: 자동 회귀나 필수 소유 문서 probe가 실패했다.
- `automated_pass_manual_pending`: 자동 범위는 통과했지만 사람만 검증할 수 있는
  항목이 남았다.
- `accepted`: 자동 증거와 세 수동 항목이 모두 명시적으로 통과했다.

개발 단계에서는 Python 소스로만 실행한다. 이 게이트는 EXE를 만들지 않는다.
