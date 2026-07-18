# Jarvis Command Center

제품의 장기 방향과 기능별 수용 기준은
[JARVIS 제품 Goal](docs/JARVIS_PRODUCT_GOAL.md)에 고정해 두었습니다.
자동 증거와 수동 수용 항목의 구분은
[제품 Goal 수용 게이트](docs/PRODUCT_GOAL_ACCEPTANCE.md)를 따릅니다.

Windows에서 한국어 명령으로 앱, 웹, Excel, 한글, Word, PowerPoint를 제어하고
AI 질문·대화를 지원하는 개인용 데스크톱 도우미입니다. 현재 개발 소스 버전은
`1.1.0-rc.6`입니다.

Prototype 1.0 1~8단계는 파일 연결, 현재 선택 문맥, 네 앱의 미리보기·승인·편집·
재읽기·복원과 안정성 검증을 다룹니다. Prototype 1.1 9~12단계는 Excel VBA,
Excel→Word/한글→PowerPoint 워크플로, 명시적 사용자 선호 학습, 개인정보가 제거된
자가 진단을 추가합니다.

일반 명령의 `방금 거`, `아까처럼`, `지난번 그대로`는 같은 대화 세션의 최근
3회 구조화 결과 안에서만 대상을 찾습니다. 참조 표현이 없는 새 명령은 이전 앱을
상속하지 않습니다. 재사용되는 구조화 스킬도 실행 전에 등록 앱과 실제 계획의 앱
대상을 대조하며, 특정 앱 스킬이 다른 앱을 가리키면 변경 전에 중단합니다.
UI Automation 컨트롤을 못 찾은 경우에도 아직 클릭·입력이 시작되지 않았고 같은
앱·selector·창임을 증명할 수 있을 때만 캐시를 비우고 한 번 새로 탐색합니다.

편집 문맥은 상시 polling하지 않습니다. 문서 연결, 편집 탭 진입, 창 focus,
수동 새로고침, 명령 전과 처리 후에만 읽으며, 명령 전 갱신 실패나 선택 변경이
있으면 미리보기 생성 전 차단합니다. 단, 명시적 편집 요청의 준비·승인 직전에
연결 문서가 다른 창 뒤로 밀린 경우에는 그 문서 창만 한 번 앞으로 가져온 뒤 동일
문서 fingerprint와 현재 선택을 다시 확인합니다. 평소 상태 조회는 창 focus를
변경하지 않습니다. 최초 편집 요청에서 저장된 연결 문서가 완전히 닫힌 경우에는
같은 파일 신원을 재검증한 뒤 정확한 경로를 한 번만 다시 열 수 있습니다.

## Prototype 1.1 11단계: 명시적 사용자 선호 학습 v1

요약 길이, 보고서 문체, 제목·숫자·표 표현, PPT 기본 장수, 저장 위치와 VBA 수정
방식에 대한 허용된 명시적 문장을 증거로 기록합니다. 한 번의 지시로 확정하지
않으며 같은 값이 세 번 확인된 뒤 사용자 승인을 받아야 활성 기본값으로 저장합니다.
범위 우선순위는 `파일 > 업무 > 앱 > 전역`이고 현재 명령의 명시적 값이 우선합니다.
편집 미리보기를 다시 쓰거나 취소하면서 말한 이유와 검증된 후속 교정도 허용된
선호 값으로 구조화해 증거에 포함합니다. Word·한글·PowerPoint에서는 JARVIS가
검증한 텍스트 편집 직후 같은 구조적 시작점을 사용자가 직접 고친 행동을 한
사건당 하나의 관찰로 기록합니다. 명확한 축약은 `간결한 문체`, 비슷한 길이의
재작성에서 문장 어미가 명확히 바뀐 경우는 `격식체`·`친근한 문체`, 텍스트가
완전히 같고 굵기·글자 크기·문단 정렬 중 하나만 바뀐 경우(Word·한글·PowerPoint)는
해당 서식 선호의 파일 범위 증거가 됩니다. Word의 범위 끝점, 한글의
선택 끝 좌표, PowerPoint의 텍스트 끝점이 축약으로 달라져도 문서 fingerprint와
앱별 시작 식별자가 같으면 동일 대상으로 확인합니다. 선택 시작점 변경, 사소한
축약, 거의 전부 삭제, 여러 서식 동시 변경, 혼합·불명 어미, Excel 셀 변경은
제외하며 자유 문장과 문서 원문은 저장하지 않습니다. 직접 수정 증거 역시 같은 값
3회와 사용자 승인이 있어야 기본값으로 활성화되며, 증거가 모인 뒤의
`앞으로도 간결하게 해줘` 같은 확인 문장은 현재 파일에 대기 중인 후보의 활성화
승인으로 연결됩니다. PowerPoint는 JARVIS가 단일 Shape 전체 텍스트를 편집한
경우 같은 SlideID·ShapeID의 단일 Shape 재선택도 안전한 동일 대상으로 봅니다.
빈 텍스트 커서가 같은 Shape에 있으면 본문을 더 읽지 않고 최대 5분만 기다리며,
여러 Shape 또는 다른 Shape로 이동하면 관찰하지 않습니다. 한글은 교체 뒤
커서가 접혀도 검증된 선택 시작 좌표와 새 텍스트 지문·서식값을 최대 5분
유지하며, 사용자가 같은 범위를 다시 선택한 뒤 한 서식 축을 바꿨을 때만
관찰합니다. Word도 검증된 Range 교체의 시작 좌표와 새 텍스트 지문·서식값을
최대 5분 유지합니다. `Selection.Start == Selection.End`인 접힌 Range가 같은
시작 위치일 때 선택 밖 본문을 읽지 않고 기다리며, 같은 Range를 다시 선택한
뒤 한 서식 축을 바꿨을 때만 관찰합니다.

이미 승인한 기본값과 다른 패턴이 반복되면 기존 기본값을 즉시 덮어쓰지 않습니다.
새 값이 전체 증거의 75% 이상이 되고 같은 값이 최소 3회 확인된 뒤에만 교체
후보를 만들며, 미리보기에서 현재 승인값·새 후보값·증거 수·신뢰도를 설명합니다.
승인하면 기존값을 새 값으로 교체하고, 취소하면 기존값을 유지한 채 후보만
기각합니다. 취소한 값은 새 증거가 3회 더 쌓여야 다시 확인합니다.

예를 들어 `PPT는 항상 7장으로 만들어줘`를 세 번 확인하고 활성화하면 다음
보고서 워크플로의 기본 PPT가 7장이 됩니다. `5장짜리 PPT`라고 직접 요청하면
그 작업만 5장으로 생성합니다. 승인된 굵기·글자 크기·문단 정렬 기본값도
보고서 생성 미리보기에 표시되고, 승인 후 새 Word 또는 한글 보고서 전체에
적용합니다. Word는 저장 전 COM 값과 저장 후 재열기로, 한글은 같은 소유 문서의
COM read-back과 저장 파일 지문으로 검증합니다. 같은 선호를 새 PowerPoint의
제목·본문에도 적용하고 저장 후 다시 열어 확인합니다. Word·한글·PowerPoint 일반 편집에서는
`정렬해줘`, `글자 크기 맞춰줘`, `강조 방식 맞춰줘`처럼 값 하나를 생략한 요청에만
승인된 파일/앱 기본값을 채우고, 정확한 적용값을 미리보기로 다시 승인받은 뒤
네이티브 값으로 재확인합니다.

기본 한글 보고서 생성은 별도 소유 프로세스에서 실행합니다. HWP 저장 응답이
기본 60초 안에 끝나지 않으면 부분 파일과 확인된 소유 프로세스만 정리하고
`timeout`으로 진단하며, 성공한 앞 단계는 보존해 `실패한 워크플로 이어서`로
재개할 수 있습니다. 한글 보안 설정이나 파일 접근 모듈은 자동 변경하지 않습니다.
공식 Automation 보안 모듈 등록과 실제 파일을 워크플로 미리보기 전에 읽기
전용으로 확인하므로, 현재처럼 모듈이 없으면 승인 카드·Excel 분석·영구 상태·
HWP 프로세스를 만들지 않고 `environment_error`로 즉시 안내합니다. 사용자가
승인할 때 환경을 한 번 더 확인하며, 등록이 있으면 같은 이름으로
`RegisterModule`을 통과한 뒤에만 파일 저장을 시작합니다.

상세 내용은 [11단계 명시적 사용자 선호 학습](docs/PROTOTYPE11_STAGE11_USER_LEARNING.md)에
정리했습니다.

## Prototype 1.1 10단계: 앱 간 문서 워크플로

연결된 Excel에서 `이 엑셀을 분석해서 보고서와 5장짜리 PPT 만들어줘.`라고
요청하면 승인 미리보기 뒤 Excel을 읽기 전용으로 분석하고 기본 Word 보고서와
정확히 5장인 PowerPoint 요약을 생성합니다. `한글 보고서와 5장짜리 PPT`라고
명시하면 Word 대신 `.hwp` 보고서를 만듭니다. `Word와 한글 보고서`를 함께
요청하면 `.docx`, `.hwp`, PowerPoint를 각각 독립 단계로 만들고 검증합니다.
Excel 편집 문맥에서는 `이거 보고서랑 발표자료 만들어줘`처럼 분석 표현을
생략할 수도 있습니다. 이때 `이거`는 선택 셀이 아니라 현재 연결 Excel 전체를
뜻하며 미리보기에 그 범위를 명시합니다. 보고서·발표자료·생성 동사가 함께
있을 때만 업무 요청으로 처리하고, 질문형 문장은 실행하지 않습니다.
미리보기와 취소는
영구 상태 파일을 만들지 않고, 승인 순간 원본과 출력 경로를 다시 확인한 뒤
최초 상태를 저장합니다. 실행
중 실패하면 `실패한 워크플로 이어서`로 성공 단계는 건너뛰고 실패 단계만 다시
실행합니다. 취소·승인 대기 상태는 재개 대상으로 선택하지 않으며 기존 파일은
덮어쓰지 않습니다.

여러 표시 시트에 같은 키 열이 있으면 실제 키 값은 저장하지 않은 채 일치 수와
1:1·1:N 같은 관계 후보를 보고합니다. 범주 2~20개와 숫자 측정 열이 있는
시트는 합계 상위 5개의 제한 피벗 요약을 만들고 Word·한글·PowerPoint
인사이트에 사용합니다. 금액 열을 키로 오인하거나 모호한 관계로 행을 자동
조인하지 않습니다.

검증 완료한 복합 업무는 보고서 형식·PPT 장수·허용 단계 순서만 재사용 후보로
만듭니다. `이 워크플로 기억해`를 별도로 승인하면 `지난번처럼 해줘`로 현재
연결 Excel에서 같은 구조를 다시 사용할 수 있습니다. 스킬에는 원본/산출물
경로와 문서 내용이 저장되지 않습니다. 재생할 때마다 현재 Excel과 새 출력
경로를 다시 검증하고 전체 작업을 다시 승인받으며, 현재 명령에 적은 형식·장수는
기억한 값보다 우선합니다.

완료 직후에는 `방금 만든 보고서 열어줘`, `최근 만든 발표자료 보여줘`로 가장
최근 검증 산출물을 정확한 네이티브 앱에서 열고 맨 앞으로 가져올 수 있습니다.
저장된 경로와 SHA-256 지문이 현재 파일과 일치해야 하며, 파일이 수정·이동·
삭제됐거나 Word·한글 보고서가 모두 있어 대상이 모호하면 임의로 열지 않습니다.
`방금 만든 보고서를 편집 문서로 연결해줘`처럼 `편집`과 `연결`을 함께
명시하면 같은 검증을 통과한 파일만 새 편집 세션으로 전환합니다. GUI도 새
세션과 문맥을 먼저 반영하며, 인계에 실패하면 기존 Excel 세션을 그대로
유지합니다. 다음 편집은 새 문서에서 사용자가 현재 선택한 범위에 적용됩니다.
소유 문서 실검에서는 Word 제목 Range와 PowerPoint 제목 Shape를 선택한 뒤
후속 글자 크기 변경을 승인해 두 앱 모두 +2pt read-back과 `ready` 복귀를
확인했습니다.

상세 설계와 실제 Office 검증 결과는
[10단계 앱 간 문서 워크플로](docs/PROTOTYPE11_STAGE10_DOCUMENT_WORKFLOW.md)에
정리했습니다.

실행본 위치는 소스에 개인 절대 경로로 고정하지 않습니다. 운영 환경에서 지정한
`<JARVIS_RUNTIME>` 아래의 `current/Jarvis`와 `previous/Jarvis.zip` 정책은
[RULEBOOK](RULEBOOK.md)을 따릅니다.

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
- Excel·한글·Word·PowerPoint 로컬 파일을 네이티브 앱에 연결하는 편집 세션
- 연결 문서의 현재 시트·선택 Range·커서·슬라이드·Shape를 읽는 편집 문맥
- Excel·한글·Word·PowerPoint 선택 대상의 변경 미리보기·승인·
  실행·재읽기 검증 편집
- 같은 선택 대상의 짧은 후속 명령, 미리보기 다시 작성, 직전 JARVIS 편집의
  구조화된 복원과 재읽기 검증
- 앱·문서 전환 시 이전 문맥 폐기, 네 앱 100회 연속 편집과 COM·프로세스
  안정성 검증
- Excel VBA 프로젝트·모듈·프로시저 읽기, 위험 분석, 백업 기반 코드 수정과
  별도 매크로 실행 승인

프로그램 실행 매크로는 매번 확인을 받습니다. 셸·스크립트·시스템 관리
명령과 셸 연결·리디렉션 문자는 차단합니다.

### Excel

실행 중인 Excel의 활성 통합문서와 시트에서 셀 값·수식 입력, 합계, 범위
서식, 조건부 서식, 필터, 찾기·바꾸기, 행 정렬과 행·열 삽입을 지원합니다. 승인 대기 중
문서 상태가 달라지면 실행하지 않고, 실행 뒤 결과를 다시 읽어 검증합니다.

매크로 사용 통합문서 `.xlsm/.xlsb`에서는 VBA 프로젝트와 모듈·프로시저
목록, 코드 읽기·정적 분석, 변경 diff를 제공합니다. 코드 수정은 원본 `.bas`
백업과 승인이 필수이며, 매크로 실행은 별도 승인, Shell·파일 삭제·네트워크·
레지스트리 포함 코드는 두 번 확인합니다. 실제 앱 검증은 현재 Excel 보안
센터의 VBA 프로젝트 접근 설정으로 대기 중입니다.

### 한글(HWP)

화면에 보이는 활성 한글 문서에서 텍스트 입력, 글자 모양, 문단 정렬,
찾기·바꾸기, 선택 문장 교체·로컬 축약·격식체 변환을 제공합니다. 대상이
불명확하거나 보호 상태인 경우 실행하지 않습니다. 승인된 굵기·글자 크기·
문단 정렬 선호가 있으면 `강조 방식 맞춰줘`, `글자 크기 맞춰줘`, `정렬해줘`
같이 값 하나를 생략한 요청의 미리보기에만 보완하며, 명시한 값이 항상 우선합니다.

### Word

현재 선택 Range의 텍스트 교체, 굵기·글자 크기, 문단 정렬, Style·
표 셀 확인과 저장을 지원합니다. 연결된 문서와 선택 문자 Range가 정확히
같을 때만 실행하고 교체·서식 결과를 다시 읽습니다.

### PowerPoint

현재 슬라이드에서 하나의 Shape 또는 텍스트 선택을 대상으로 텍스트 교체,
글자 크기·굵기·정렬, Shape 이동·크기 변경, Placeholder 확인과 앞
슬라이드의 같은 역할 Shape 스타일 복사를 지원합니다.

Prototype 1.0 편집모드는 파일 선택·드롭, 이미 열린 문서 연결, 정확한 경로
확인, COM-free 단일 세션, 선택적 좌우 창 배치와 네 앱의 읽기 전용 현재 문맥
인식과 네 앱의 최소 실제 편집까지 구현했습니다. 세부 안전 경계는
[3단계 파일·세션 문서](docs/PROTOTYPE1_STAGE3_FILE_SESSION.md)와
[4단계 편집 문맥 문서](docs/PROTOTYPE1_STAGE4_EDIT_CONTEXT.md),
[5단계 최소 실제 편집 문서](docs/PROTOTYPE1_STAGE5_MINIMAL_EDITING.md),
[6단계 Word·PowerPoint 편집 문서](docs/PROTOTYPE1_STAGE6_WORD_POWERPOINT_EDITING.md),
[7단계 연속 수정·재작성·되돌리기 문서](docs/PROTOTYPE1_STAGE7_CONTINUOUS_EDITING.md),
[8단계 4개 앱 통합 검증 문서](docs/PROTOTYPE1_STAGE8_INTEGRATION_VALIDATION.md),
[9단계 Excel VBA 편집 문서](docs/PROTOTYPE11_STAGE9_EXCEL_VBA.md),
[10단계 앱 간 문서 워크플로 문서](docs/PROTOTYPE11_STAGE10_DOCUMENT_WORKFLOW.md),
[11단계 명시적 사용자 선호 학습 문서](docs/PROTOTYPE11_STAGE11_USER_LEARNING.md),
[12단계 자가 진단 문서](docs/PROTOTYPE11_STAGE12_SELF_DIAGNOSIS.md)를
참조하세요.

앱 열기·닫기의 대상이 등록되어 있지 않으면 실행 전에 Windows의 제한된 앱
목록을 한 번 다시 확인합니다. 복구 결과에는 `실행 시작 전`, `요청 대상 동일`,
`최대 1회`라는 구조화된 증거가 포함됩니다. 재분석 결과가 달라지거나 실행이 이미
시작된 경우에는 중복 동작을 막기 위해 자동 재시도하지 않습니다. 앱 이름과 경로,
사용자 문장 원문은 복구 진단에 저장하지 않습니다.

같은 공통 계약은 연결 문서 focus 복구에도 적용됩니다. 실제 편집 요청이 들어온
경우에만 연결된 Excel·한글·Word·PowerPoint 창을 한 번 활성화할 수 있으며,
동일 문서 fingerprint를 재확인한 뒤에만 미리보기 또는 승인된 실행을 계속합니다.
Windows가 focus를 거부하면 문서 창을 한 번 선택하라는 안내만 하고 편집하지
않습니다.

연결 문서를 앱에서 찾지 못한 경우에도 같은 계약 아래에서 열린 문서 목록·ROT·
Excel 창을 한 번만 다시 읽어 같은 경로의 문서 창을 찾습니다. 찾으면 저장된 창
handle만 갱신하고 동일 문서 fingerprint를 재확인한 뒤 계속합니다. 최초 편집
요청에서 문서가 완전히 닫힌 경우에는 저장된 파일의 신원이 그대로일 때만 정확한
경로를 한 번 다시 열고, 새 창 handle·foreground·문서 fingerprint를 확인한 뒤
새 미리보기를 만듭니다. 미저장 문서, 상태 조회, 이미 만들어진 미리보기 승인,
파일 교체·삭제에는 자동 재열기를 하지 않습니다. 승인 중 문서가 닫히면 기존
미리보기를 폐기하고, 사용자가 같은 편집 명령을 다시 말해야 합니다.

## 테스트

복원된 테스트는 다음 그룹으로 실행합니다.

사용자가 EXE에서 대표 기능과 안전 경계를 직접 확인할 때는
[직접 시험 문장 100개](docs/PROTOTYPE1_MANUAL_TEST_100.md)를 순서대로 사용합니다.

```powershell
.venv\Scripts\python.exe -m tests.test_runner unit
.venv\Scripts\python.exe -m tests.test_runner integration
.venv\Scripts\python.exe -m tests.test_runner windows
.venv\Scripts\python.exe -m tests.test_runner live
.venv\Scripts\python.exe -m tests.test_runner all
.venv\Scripts\python.exe -m verification.product_goal_acceptance --automated-only
```

2026-07-18 다중 시트 관계·피벗 요약 보강 기준으로 전체 자동 테스트
882개 중 879개가 통과했고(unit 344·integration 332·windows 203), 실제 외부
AI 자격 증명과 호출 승인이 필요한 라이브 테스트 3개만 건너뛰었으며 실패는
0개입니다. 8단계는 Excel·한글·Word·PowerPoint 각각
100/100회와 적용·검증·Undo를 통과했습니다. 9단계 소유 `.xlsm`은 프로젝트
탐색, 코드 읽기·분석, 백업·수정·복원, 별도 매크로 실행과 실제 셀 결과까지
통과했습니다. 10단계 승인형 다중 시트 Word·한글 문서 워크플로, 11단계 명시적
선호 학습과 실제 보고서 서식 read-back, 12단계 개인정보 제한 진단 프로브도 모두
성공했습니다. 로컬 UI에서는 방향키 모드 전환, 모달 Tab 순환, Esc 종료와
원래 버튼 포커스 복귀를 실제 브라우저 접근성 트리로 확인했습니다.

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
- [Prototype 1.0 1단계 기술 검증](docs/PROTOTYPE1_STAGE1_TECHNICAL_VALIDATION.md)
- [Prototype 1.0 2단계 편집 계약](docs/PROTOTYPE1_STAGE2_EDIT_CONTRACT.md)
- [Prototype 1.0 3단계 파일·세션](docs/PROTOTYPE1_STAGE3_FILE_SESSION.md)
- [Prototype 1.0 4단계 편집 문맥](docs/PROTOTYPE1_STAGE4_EDIT_CONTEXT.md)
- [Prototype 1.0 5단계 최소 실제 편집](docs/PROTOTYPE1_STAGE5_MINIMAL_EDITING.md)
- [Prototype 1.0 6단계 Word·PowerPoint 편집](docs/PROTOTYPE1_STAGE6_WORD_POWERPOINT_EDITING.md)
- [Prototype 1.0 7단계 연속 수정·재작성·되돌리기](docs/PROTOTYPE1_STAGE7_CONTINUOUS_EDITING.md)
- [Prototype 1.0 8단계 4개 앱 통합 검증](docs/PROTOTYPE1_STAGE8_INTEGRATION_VALIDATION.md)
- [Prototype 1.1 9단계 Excel VBA 편집](docs/PROTOTYPE11_STAGE9_EXCEL_VBA.md)
- [Prototype 1.1 10단계 앱 간 문서 워크플로](docs/PROTOTYPE11_STAGE10_DOCUMENT_WORKFLOW.md)
- [Prototype 1.1 11단계 명시적 사용자 선호 학습 v1](docs/PROTOTYPE11_STAGE11_USER_LEARNING.md)
- [Prototype 1.1 12단계 자가 진단·안전한 개선 제안](docs/PROTOTYPE11_STAGE12_SELF_DIAGNOSIS.md)
- [Prototype 1.0 직접 시험 문장 100개](docs/PROTOTYPE1_MANUAL_TEST_100.md)
- [감사 상태](AUDIT_REPORT.md)
- [미해결 사항 해소 계획](REMEDIATION_PLAN.md)
