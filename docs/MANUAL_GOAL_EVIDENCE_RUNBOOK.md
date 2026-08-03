# JARVIS 수동 Goal evidence 실행서

이 문서는 자동화로 대신할 수 없는 화면 읽기, 실제 음성 입력, 비숙련자 관찰을
같은 기준으로 수행하기 위한 프로토콜이다. 실제 수행하지 않은 항목은 절대로
`passed`로 기록하지 않는다. 사용자 발화, 문서 내용, 이름과 같은 원문은 Git이나
evidence JSON에 저장하지 않는다.

## 1. 준비

1. Python 소스 버전을 `Jarvis_Start.bat`으로 실행한다. EXE는 만들지 않는다.
2. 개인 문서는 닫고 테스트 전용 Excel·Word·한글·PowerPoint 문서만 연다.
3. evidence 파일을 초기화한다.

```powershell
python -m verification.manual_acceptance_recorder initialize
```

4. 결함 메모는 사용자의 별도 문서에만 남긴다. JARVIS 저장소에는 상태와 수행
   시각만 기록한다.

## 2. 화면 읽기 — `jarvis-screen-reader-v1`

NVDA 또는 사용자가 실제 사용하는 화면 읽기 프로그램을 켜고 마우스 없이 다음
흐름을 수행한다.

1. JARVIS 실행 후 명령 입력란까지 Tab으로 이동한다.
2. `오늘 날짜 알려줘`를 입력하고 답변과 완료 상태를 듣는다.
3. 명령·질문·편집 모드를 차례로 이동하며 선택 상태를 듣는다.
4. 현재 열린 테스트 문서를 연결하고 문서 종류·선택 범위 표시를 듣는다.
5. 쓰기 명령을 내린 뒤 승인 카드의 작업·대상·확인·취소를 듣는다.
6. 한 번은 취소하고, 한 번은 확인하여 완료 상태를 듣는다.
7. 의도적으로 선택이 필요한 명령을 선택 없이 실행해 오류 안내를 듣는다.
8. `방금 거 취소해`로 Undo 결과를 듣고 입력란으로 돌아온다.

통과 조건:

- 모든 입력·모드·승인·취소 컨트롤에 이해 가능한 이름이 있다.
- Tab 순서가 시각적 작업 순서와 일치하고 키보드 함정이 없다.
- 승인 대상, 오류, 완료, Undo 상태가 한 번 이상 명확히 낭독된다.
- 상태 카드가 겹쳐 핵심 안내가 사라지거나 포커스가 문서 밖으로 유실되지 않는다.
- 잘못된 대상이나 승인 없는 쓰기가 0건이다.

완료 뒤 다음 중 실제 결과 하나를 기록한다.

```powershell
python -m verification.manual_acceptance_recorder record screen_reader passed --attest
python -m verification.manual_acceptance_recorder record screen_reader failed --attest
```

## 3. 실제 음성 입력 — `jarvis-voice-input-v1`

Windows 음성 입력(예: `Win+H`)이나 실제 사용 예정 음성 입력기를 통해 다음 10개를
입력한다. 키보드로 문장을 대신 입력하면 이 항목의 evidence가 아니다.

1. 오늘 날짜 알려줘
2. 오늘 날씨 알려줘
3. 엑셀 열어줘
4. 현재 열린 문서 연결해줘
5. A1부터 A10까지 합계 내줘
6. 소리를 조금만 올려줘
7. 소리를 10 내려줘
8. 메모장 열어줘
9. 취소해
10. 방금 거 취소해

통과 조건:

- 10개 모두 첫 발화 또는 한 번의 음성 재시도로 올바른 의도에 도달한다.
- 잘못 인식된 문장은 실행 전에 수정하거나 JARVIS의 재질문으로 회복할 수 있다.
- 승인·취소 응답이 서로 뒤바뀌지 않는다.
- 잘못된 대상, 승인 없는 쓰기, 위험 작업 오실행이 0건이다.

```powershell
python -m verification.manual_acceptance_recorder record voice_input passed --attest
python -m verification.manual_acceptance_recorder record voice_input failed --attest
```

## 4. 비숙련자 관찰 — `jarvis-novice-observation-v1`

개발에 참여하지 않았고 JARVIS 명령 형식을 외우지 않은 사용자 한 명이 수행한다.
관찰자는 시작 방법과 안전용 테스트 파일만 알려 주며, 실행 중 메뉴 위치나 정확한
문장을 코칭하지 않는다.

대표 20개 과제는 날짜·날씨, 앱 열기·앞으로 가져오기, 현재 문서 연결, Excel 선택
합계·값 입력·서식·Undo, Word/한글 문장 교체·서식·Undo, PowerPoint 텍스트 수정,
승인 취소, 오류 후 재시도로 구성한다. 자동 1,000문장 파일에서 같은 범주의 문장을
고르되 개인 파일은 사용하지 않는다.

통과 조건:

- 안전 핵심 과제(대상 확인, 쓰기 승인, 취소, Undo, 오류 복구)를 모두 완료한다.
- 전체 20개 중 18개 이상을 도움 없이 완료한다.
- 잘못된 대상, 승인 없는 쓰기, 복구 불가능한 문서 변경이 0건이다.
- 사용자가 오류 안내를 읽고 적어도 한 번 스스로 재시도에 성공한다.
- 포커스 위치 때문에 수행을 포기한 과제가 없다.

```powershell
python -m verification.manual_acceptance_recorder record novice_user_observation passed --attest
python -m verification.manual_acceptance_recorder record novice_user_observation failed --attest
```

## 5. 최종 판정

세 항목을 실제로 마친 뒤 실행한다.

```powershell
python -m verification.product_goal_acceptance `
  --manual-evidence verification/product_goal_manual_acceptance.json
```

`overall_status=accepted`와 종료 코드 0이 모두 확인되어야 최종 수용이다. 실패 항목은
결함 수정, 자동 회귀, 해당 수동 프로토콜 재수행 순서로 다시 검증한다.
