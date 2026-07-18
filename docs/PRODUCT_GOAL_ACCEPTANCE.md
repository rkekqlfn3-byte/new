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
현재 HWP 파일 저장이 정상 완료되거나, 공식 Automation 보안 모듈이 없으면
미리보기 확인·분석·영구 상태·HWP 실행 전에 구조화된 `environment_error`로
차단하며 승인 시 환경을 재검사하고, 등록 뒤 저장 COM이
멈춘 환경에서는 60초 안에 `timeout`으로 복귀해 부분 파일과 소유 프로세스를
정리하는 watchdog probe도 안전 실행·실패 분류 축의 필수 증거다.
Stage 6도 Word와 PowerPoint 대상이 모두 있어야 하며, Word 결과에는 접힌
Range→동일 Range 재선택 뒤 직접 서식 교정, PowerPoint 결과에는 단일 Shape
직접 서식 교정의 구조화 증거 기록 검사가 각각 포함돼야 한다.
Stage 11은 승인된 기존값을 상충 후보가 승인 전까지 바꾸지 않는지, 교체
미리보기가 현재값·후보값을 설명하는지, 취소 시 기존값 유지와 승인 시 이전값
기록이 모두 성립하는지도 필수로 검사한다.
또한 검증된 복합 업무를 경로·내용 없이 구조만 저장하는지, 재생 계획이 새
산출물 경로를 쓰는지, 실제 Word·PowerPoint 재생과 기존 산출물 보존이
성립하는지도 요구한다. Excel 편집 문맥의 `이거 보고서랑 발표자료 만들어줘`가
현재 연결 Excel 전체 업무로 라우팅되고 일회성 PPT 장수가 장기 선호로 새지
않는 검사도 필수다. 가장 최근 검증 Word·PowerPoint 산출물을 정확한 경로·
파일 지문으로 열고 각각 전면 포커스하는 실제 Office 검사도 요구한다. 또한
명시적 연결 요청으로 최근 Word·PowerPoint 산출물이 각각 새 편집 세션이 되고
경로·지문·세션 ID·`ready` 상태가 모두 일치하는 실제 Office 검사도 요구한다.
인계 뒤 선택한 Word Range와 PowerPoint Shape에 승인된 후속 편집이 적용되어
read-back되고 같은 세션이 `ready`로 복귀하는 증거도 필수다.
Word workflow 실제 probe는 두 표시 시트에서 제한 피벗 요약 2개와 셀 값·경로·
산출물 없이 같은 머리글 `담당자`의 1:1 후보와 이름이 다른
`구매자ID ↔ 고객ID`의 1:N 검토 후보를 함께 찾아
`relationship_inspection_verified`를 남긴다. 이어서 사용자가
명시·승인한 `항목ID ↔ 참조항목ID` 매핑 내부 조인 1개·결과 3행을 만들고
왼쪽 중복 5행을 `매출 합계 + 수량 평균 + 담당자 건수 + 매출 최솟값 + 매출
최댓값` 4그룹으로 사전 집계하고, 오른쪽 중복 4행을 `비용 합계 + 비용 평균 +
항목 건수 + 비용 최솟값 + 비용 최댓값` 3그룹으로 집계해
`mapped_join_keys_verified`·`aggregated_join_verified`·
`multi_aggregation_join_verified`와 분석 검증에 기록한 뒤,
원본 불변 상태에서 보고서와 발표자료가 재열기 검증되는지도 요구한다.
단위·통합 계약은 `고객ID ↔ 구매자ID`처럼 이름이 다른 ID형 열도 겹치는 키
3개 이상·양쪽 포함률 80% 이상·최소 한쪽 거의 고유 조건에서만 검토 후보로
제시하고, 약한 겹침과 일반 다대다를 거부하며 복수 대응 열을 모호하다고 표시하는
검사를 추가로 요구한다. 어떤 후보도 실제 조인을 자동 실행해서는 안 된다.
후보 번호 선택은 현재 편집 세션·같은 파일 지문·10분 이내에서만 허용하고,
번호·내부/왼쪽 방식·Word/한글 보고서·PPT를 완전히 말한 경우에도 정확한 시트와
양쪽 키가 채워진 별도 고위험 미리보기를 다시 승인해야 한다. 실제 Word probe는
두 번째 상이명 후보의 메모리 해석이 파일·산출물을 만들지 않았다는
`numbered_relationship_candidate_verified`도 남겨야 한다.
같은 실제 워크플로의 모든 단계에 내용·경로 없는 선행조건·부작용·완료 증거·
중복 방지 키가 고정되고 저장 상태와 일치한다는 `step_contracts_verified`도
남겨야 한다. 실행기와 학습 스킬이 같은 내용 없는 허용 레시피를 사용한다는
`registered_step_recipe_verified`도 필수다.
같은 Word 실제 probe는 `Word 보고서만 만들어줘`를 자연어로 해석한 별도 승인
계획에서 PowerPoint 단계·경로·작성기 호출 없이 Word 한 개만 생성·재열기 검증한
`explicit_report_only_recipe_verified`도 남겨야 한다.
같은 probe는 `PPT만 5장으로 만들어줘`를 별도 승인 계획으로 해석하고 Word·한글
단계·경로·작성기 호출 없이 정확히 5장인 PowerPoint 하나만 생성·재열기 검증한
`explicit_presentation_only_recipe_verified`도 남겨야 한다.
Stage 11 실제 재사용 probe도 승인된 내용 없는 스킬 레시피가 공용 레지스트리와
일치하고 새 산출물 재생이 같은 계약을 검증했다는
`workflow_skill_registered_recipe_verified`를 남겨야 한다.
같은 Word probe에서 `매출!B1:E4`만 읽는 별도 워크플로가 범위 밖 시트를
제외하고 `explicit_selection_scope_verified`를 남기며, Word와 정확히 5장인
PowerPoint를 재열기 검증하는 `selection_scope_cross_app_outputs_verified`도
필수다.

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

판정은 다음 넷 중 하나다.

- `environment_blocked`: 자동 회귀와 다른 probe는 통과했지만, 한글 실제 생성이
  공식 Automation 보안 모듈 부재로 정확히 차단됐다. 이 상태는 성공이 아니며
  설치 가이드만 표시하고 자동 설치·레지스트리 변경을 하지 않는다.
- `automated_failed`: 자동 회귀나 필수 소유 문서 probe가 실패했다.
- `automated_pass_manual_pending`: 자동 범위는 통과했지만 사람만 검증할 수 있는
  항목이 남았다.
- `accepted`: 자동 증거와 세 수동 항목이 모두 명시적으로 통과했다.

CLI 종료 코드는 각각 `3`, `1`, `2`, `0`이다. `--automated-only`에서도 환경
차단은 통과로 바꾸지 않고 종료 코드 `3`을 유지한다.

개발 단계에서는 Python 소스로만 실행한다. 이 게이트는 EXE를 만들지 않는다.
