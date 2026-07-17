# Prototype 1.0 4단계 편집 문맥 인식

## 구현 목표

연결된 Excel·한글·Word·PowerPoint 문서에서 사용자가 현재 보고 있거나
선택한 대상을 읽기 전용으로 캡처한다. 문서 파일 식별값과 선택 문맥 식별값은
서로 분리하며, 캡처가 끝난 뒤 COM 객체를 세션이나 API 결과에 남기지 않는다.

## 문맥 수집 흐름

```text
현재 EditSession 확인
→ 파일 식별 document_fingerprint 재검증
→ 앱별 ContextProvider 선택
→ 파일 경로로 네이티브 문서 재탐색
→ 연결 문서가 현재 활성 문서인지 확인
→ 선택 영역·컨테이너·텍스트 일부 읽기
→ JSON-only EditContext 생성
→ context_fingerprint 계산
```

문서가 닫혔거나, 다른 문서가 활성화됐거나, 같은 경로의 파일이 교체되면
문맥을 추측하지 않고 `stale_context`로 차단한다.

## 앱별 문맥

| 앱 | 읽는 값 |
|---|---|
| Excel | 활성 시트, 셀 범위 주소, 작은 범위 값 미리보기, 읽기 전용·수정 상태 |
| 한글 | 선택 좌표 또는 커서 위치, 선택 텍스트 일부, 편집·수정 상태 |
| Word | Selection Start/End, 페이지, 표 셀 좌표, 선택 텍스트 일부 |
| PowerPoint | 슬라이드 번호, 선택 유형, Shape ID·이름, 텍스트 Range |

Excel 값 미리보기는 최대 25개 셀만 읽고, 모든 앱의 UI 텍스트 미리보기는
공백 정규화 뒤 최대 240자로 제한한다. 전체 선택 텍스트는 API에 싣지 않으며
길이와 SHA-256 digest만 fingerprint 입력에 사용한다.

## EditContext 계약

```json
{
  "session_id": "edit-...",
  "app_type": "excel",
  "document_fingerprint": "문서 파일 식별 SHA-256",
  "context_fingerprint": "현재 선택 문맥 SHA-256",
  "active_container": "7월 실적",
  "selection_reference": "B3:F18",
  "selection_kind": "range",
  "target": {
    "sheet_name": "7월 실적",
    "address": "B3:F18"
  },
  "selected_text_preview": "...",
  "cursor_reference": "B3:F18"
}
```

`context_fingerprint`에는 앱·파일·컨테이너·선택 참조·구조화 target·선택
텍스트 digest·읽기 전용/수정 상태가 들어간다. 캡처 시각은 제외하므로 같은
선택과 같은 내용이면 반복 조회해도 fingerprint가 같다. 선택 위치나 선택된
내용이 달라지면 fingerprint가 달라진다.

## API와 UI

- `get_edit_session_status`: 세션과 최초 현재 문맥을 함께 조회
- `get_edit_context`: 연결된 세션의 문맥을 다시 캡처
- 연결 성공 응답: `context` 또는 `context_error` 포함
- 편집 화면: 앱 문서명과 별도로 현재 시트/슬라이드·선택 참조·텍스트 일부 표시
- 문서 연결, 편집 탭 진입, 창 focus, 수동 새로고침, 편집 명령 전과 처리 후에만
  문맥을 갱신하며 상시 polling은 수행하지 않음
- 편집 명령 전 문맥 갱신을 완료하지 못하면 요청을 보내지 않고, 요청 시점
  `context_fingerprint`와 prepare 시점 문맥이 다르면 미리보기 전에 차단

편집 요청은 기존 `document_fingerprint`로 세션을 검증한 뒤 현재 문맥을 다시
읽는다. 4단계에서는 그 문맥을 결과에 넣되 문서 변경은 수행하지 않고 5단계
앱 편집 어댑터가 필요하다는 `blocked` 결과를 반환한다.

## 안전 원칙

- 문서의 셀, 텍스트, 도형, 선택 위치를 변경하는 COM 호출을 하지 않는다.
- COM 참조는 한 번의 캡처 호출 지역에서만 사용하고 즉시 해제한다.
- API와 세션에는 JSON 값만 저장한다.
- 다른 활성 문서의 선택을 연결 문서의 문맥으로 오인하지 않는다.
- 문맥 미리보기는 로컬 UI에서만 사용하며 검증 보고서에 경로나 내용을 기록하지 않는다.

## 검증

`tests/unit/test_edit_context.py`는 네 앱의 문맥 추출값, JSON 직렬화, 동일 선택
fingerprint 안정성, 선택·텍스트 변경 감지, 다른 문서와 파일 교체 차단을
검사한다. `tests/integration/test_edit_session_api.py`는 연결·상태·문맥 API와
편집 요청 인계를 한 세션에서 확인한다.

`verification/prototype1_stage4_probe.py`는 현재 열려 있는 저장 문서를 수정하지
않고 두 번 읽어 동일 선택의 fingerprint 안정성과 JSON 직렬화를 확인한다.
문서 경로와 선택 텍스트는 결과에 출력하지 않는다.

`--owned-fixtures` 옵션은 해당 앱의 사용자 프로세스가 전혀 없을 때만 JARVIS
소유 임시 문서를 만든다. Excel `B3:F18`, 한글 전체 선택, Word Range,
PowerPoint Shape를 읽고, 두 번째 선택으로 바꿨을 때 fingerprint가 달라지는지
확인한 뒤 JARVIS가 연 문서와 프로세스만 정리한다.

현재 검증 PC에서는 임시 `.docx`를 셸로 다시 여는 3단계 경로가 ROT 등록 시간
초과로 실패하므로, Word 4단계 검증은 JARVIS 소유 Word 인스턴스를 열린 채로
유지하고 같은 `NativeDocumentContextReader`의 Range 추출과 공통 manager를
검증한다. 결과의 `shell_rot_rediscovery_verified`로 이 환경 제한을 구분한다.

## 4단계 완료 기준

- 앱별 `ContextProvider`와 공통 `EditContextManager` 구현
- `document_fingerprint`와 `context_fingerprint` 분리
- 네 앱의 컨테이너·선택 참조·선택 텍스트 일부 읽기
- 동일 선택 fingerprint 안정성과 선택 변경 감지
- 닫힌 문서·다른 활성 문서·교체된 파일 차단
- 현재 문맥 UI 표시와 자동 갱신
- 문서 무수정·COM-free·JSON-only 경계 검증
