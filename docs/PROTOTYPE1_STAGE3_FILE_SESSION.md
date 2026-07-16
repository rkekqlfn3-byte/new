# Prototype 1.0 3단계 파일 전달과 편집 세션 고정

## 구현 목표

사용자가 로컬 Excel·한글·Word·PowerPoint 파일을 선택하거나 이미 열어 둔
문서를 지정하면, JARVIS가 실제 네이티브 앱에서 같은 경로의 문서가 열린 것을
확인한 뒤 하나의 편집 세션으로 고정한다.

2단계에서 정의한 공통 편집 계약의 인계 순서에 따라 이번 단계는 파일 전달과
세션 생성까지 담당한다. 문서 내용 읽기와 실제 편집 연산은 4단계 앱 문맥
어댑터에서 활성화한다.

## 사용자 흐름

```text
편집 탭 선택
→ 파일 선택 또는 파일 드롭
→ 파일 존재·확장자·앱 설치 확인
→ 네이티브 앱으로 문서 열기
→ 열린 문서의 전체 경로 재확인
→ COM-free EditSession 생성
→ 파일명·앱·현재 컨테이너·선택 참조 표시
```

이미 열린 문서는 `현재 열린 문서 연결`로 연결한다. 여러 앱의 문서가 동시에
활성 상태라면 앱을 Excel·한글·Word·PowerPoint 중 하나로 지정할 수 있다.
후보가 여전히 여러 개이면 임의로 선택하지 않고 연결을 거부한다.

## 지원 형식

| 앱 | 확장자 |
|---|---|
| Excel | `.xlsx .xlsm .xlsb .xls` |
| 한글 | `.hwp .hwpx` |
| Word | `.docx .docm .doc` |
| PowerPoint | `.pptx .pptm .ppt` |

지원하지 않는 확장자, 존재하지 않는 파일, 저장되지 않은 문서, 설치되지 않은
앱은 네이티브 앱 실행 전에 차단한다.

## 파일 전달 보호 경계

- 파일 내용은 브라우저나 외부 서버로 업로드하지 않는다.
- 네이티브 파일 선택 창은 Windows Common Dialog를 사용한다.
- 브라우저 드롭에서는 파일명·크기·로컬 경로 힌트만 전달한다.
- 브라우저가 경로를 숨기면 현재 Explorer 선택 항목과 정확히 하나가 일치할
  때만 경로를 복구한다. 일치하지 않으면 파일 선택 버튼을 안내한다.
- `os.startfile` 이후 실제 Office/HWP 문서의 `FullName`이 선택 경로와
  일치해야 세션을 만든다.
- Office가 Application 객체를 ROT에 즉시 등록하지 않는 경우에는 파일 경로
  문서 moniker를 사용해 해당 Workbook·Document·Presentation만 재탐색한다.
- 사용자가 열어 둔 문서나 앱은 연결 해제 시 닫거나 종료하지 않는다.

## EditSession

```json
{
  "session_id": "edit-...",
  "app_type": "excel",
  "file_path": "C:\\...\\매출현황.xlsx",
  "document_name": "매출현황.xlsx",
  "document_fingerprint": "64자리 SHA-256",
  "window_handle": 12345,
  "active_container": "7월 실적",
  "selection_reference": "B3:F18",
  "launch_requested": true,
  "state": "ready",
  "revision": 2
}
```

세션에는 COM 객체를 저장하지 않는다. `document_fingerprint`는 파일 내용이
아니라 정규화된 경로와 운영체제 파일 식별값으로 계산한다. 같은 경로의 파일이
외부에서 교체되면 요청 실행 전에 `stale_context`로 차단한다. 문서 내부의
선택·내용 변경 fingerprint는 4단계 문맥 어댑터가 별도로 계산한다.

JARVIS는 한 번에 하나의 활성 편집 세션만 유지한다. 새 문서를 연결하면 기존
READY 세션을 먼저 해제하며, 진행 중인 실행 상태는 강제로 교체하지 않는다.

## 편집 요청 연결

화면은 현재 세션의 다음 두 값만 편집 요청에 자동 첨부한다.

```json
{
  "edit_session_id": "edit-...",
  "document_fingerprint": "..."
}
```

백엔드는 활성 세션 ID, fingerprint, 현재 파일 식별값이 모두 일치해야 요청을
받는다. 3단계에서는 세션 검증까지만 수행하고 실제 내용 변경 요청에는 다음
앱 어댑터 단계가 필요하다는 `blocked` 결과를 반환한다. 명령모드로 폴백하지
않는다.

## 창 자동 배치

문서 연결 성공 뒤 보조 기능으로 한 번만 배치를 시도한다.

```text
문서 창: 현재 문서 모니터 작업 영역의 68%
JARVIS: 같은 모니터 작업 영역의 32%
```

- 문서가 있는 모니터의 작업 영역을 사용한다.
- 문서 최소 600px, JARVIS 최소 320px을 보장하지 못하면 건너뛴다.
- 세션당 한 번만 실행하므로 사용자가 이후 직접 이동한 위치를 덮어쓰지 않는다.
- 화면에서 자동 배치를 끌 수 있고 선택값은 사용자 데이터의 원자적 JSON
  설정에 유지한다.
- 창 탐색·이동·결과 검증 중 어느 단계가 실패해도 문서 세션은 유지한다.

## API

- `choose_and_connect_edit_document`: 네이티브 파일 선택과 연결
- `connect_dropped_edit_document`: 드롭 파일 경로 확인과 연결
- `connect_active_edit_document`: 이미 열린 문서 연결
- `get_edit_session_status`: 현재 세션과 자동 배치 설정 조회
- `disconnect_edit_document`: 세션만 해제하고 앱은 보존
- `set_edit_auto_layout`: 창 자동 배치 사용 여부 변경

## 실제 검증

`verification/prototype1_stage3_probe.py`는 기존 앱 프로세스가 없을 때만 전용
임시 문서를 생성한다. 파일 실행, 정확한 경로 재탐색, JSON 세션 생성, 요청
binding, 연결 해제, 소유 프로세스 정리를 검사한다.

- Excel: 임시 `.xlsx` 연결·세션·정리 통과
- Word: 임시 `.docx` 연결·세션·정리 통과
- PowerPoint: 임시 `.pptx` 연결·세션·정리 통과
- 한글: 사용자 문서가 열려 있어 임시 변경 검증을 건너뛰고, 읽기 전용 활성
  문서 탐색과 세션 생성·해제만 통과

검증 보고서에는 사용자 문서 경로나 내용은 기록하지 않는다.

## 3단계 완료 기준

- 명령·질문·편집 세 모드가 화면과 백엔드에서 구분됨
- 지원 파일 선택과 드롭 진입점 제공
- 네 앱 설치 상태와 정확한 열린 문서 경로 확인
- 이미 열린 문서 연결과 다중 후보 차단
- COM-free 단일 세션과 파일 교체 감지
- 연결 상태를 편집 요청 계약에 자동 포함
- 연결 해제 시 사용자 앱·문서 보존
- 자동 창 배치 실패가 편집 연결 실패로 전파되지 않음
- 단위·통합·실제 Office/HWP 안전 검증 통과

4단계에서는 세션의 파일 경로와 앱 유형으로 매 요청마다 문서를 다시 찾아,
현재 선택 영역·컨테이너·텍스트 일부·문서 상태 fingerprint를 읽는
`EditContextManager`와 앱별 읽기 어댑터를 연결한다.
