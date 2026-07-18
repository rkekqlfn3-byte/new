# Prototype 1.0 6단계 Word·PowerPoint 편집

## 구현 목표

4단계의 읽기 전용 편집 문맥과 5단계의 `EditPreparedAction` 승인 계약을
그대로 재사용하고 Word와 PowerPoint 네이티브 어댑터만 확장한다.

```text
현재 문서·선택 대상 읽기
→ 허용된 Word Range / PowerPoint Shape 작업으로 변환
→ 네이티브 PreparedAction과 변경 전·후 미리보기
→ 사용자 승인
→ 문서·선택·내용·서식 fingerprint 재검증
→ 네이티브 실행
→ 변경 대상 재읽기
→ 검증 성공 시만 committed
```

모든 쓰기는 승인이 필요하다. 현재 선택 내용, Word Style·표 셀,
PowerPoint Placeholder·위치·크기 조회는 문서를 바꾸지 않고 즉시 반환한다.

## Word 허용 작업

- 현재 선택 Range 텍스트 교체
- 선택 텍스트 굵기·절대 글자 크기·±2pt 조정
- 현재 문단의 왼쪽·가운데·오른쪽·양쪽 정렬
- 현재 Style, 굵기, 글자 크기, 문단 정렬 조회
- 표 안 선택의 행·열·셀 Range 조회
- 현재 문서 저장

텍스트 교체와 서식은 문서 전체 digest, 선택 `Start:End`, 선택
텍스트 digest와 서식 스냅샷을 준비 시점과 승인 시점에 비교한다.
표 셀은 행·열도 같아야 한다. 실행 실패나 재읽기 불일치 시 텍스트·
굵기·글자 크기·문단 정렬을 작업 전 스냅샷으로 복원한다.

저장은 파일을 즉시 바꾸는 비가역 작업이므로 별도 승인을 받고, 저장 실패를
롤백 성공으로 보고하지 않는다.

## PowerPoint 허용 작업

- 현재 슬라이드의 단일 Shape 전체 또는 선택 텍스트 교체
- 선택 텍스트 굵기·절대 글자 크기·±2pt 조정과 문단 정렬
- Shape를 한 번에 최대 500pt 이동
- Shape를 현재 크기의 0.5~2배로 조정
- Shape의 Placeholder 유형과 위치·크기·텍스트 서식 조회
- 앞 슬라이드에서 같은 Placeholder 유형 또는 같은 역할 이름의 Shape
  하나를 찾아 글자 이름·크기·색·굵기·문단 정렬 복사

슬라이드 ID, Shape ID, 선택 유형, 선택 텍스트 위치·길이·digest,
서식과 위치·크기를 함께 fingerprint한다. 앞 슬라이드에서 같은 역할
Shape가 없거나 둘 이상이면 임의로 고르지 않고 차단한다. Shape가 슬라이드
밖으로 나가는 이동·크기 변경도 차단한다.

## 제외 범위

- 전체 프레젠테이션 디자인과 다중 슬라이드 재설계
- SmartArt 생성·재구성
- Word 고급 필드, 각주, 미주, 목차 재생성
- 복수 Shape·복수 문서·비활성 문서의 자동 일괄 편집
- 불명확한 스타일 추측과 선택 밖 대상 자동 확장

## 검증

- `tests/unit/test_stage6_editing.py`: Word·PowerPoint 대표 명령, 제목 글자 크기와
  Shape 크기 구분, JSON-only 작업, 다른 Range·Shape 차단, 비가역 저장 처리
- `tests/integration/test_stage6_edit_flow.py`: 두 앱 모두 미리보기 → 승인 →
  단 한 번 실행 → 재읽기 검증 → `ready` 복귀
- `tests/windows/test_word_powerpoint_actions.py`: 가짜 COM 객체로 텍스트·서식·
  정렬·이동·크기·스타일 복사와 승인 사이 문맥 변경 차단 검증
- `verification/prototype1_stage6_probe.py`: 사용자 Office 프로세스가 없을 때만
  격리 프로세스에서 JARVIS 소유 임시 Word/PPTX를 생성·편집·재읽기·정리.
  Word 문장 교체 뒤 접힌 Range와 동일 Range 재선택의 직접 굵기 교정,
  PowerPoint 제목 교체 뒤 같은 단일 Shape의 직접 굵기 교정이 각각 파일 범위
  학습 증거로 정확히 한 번 기록되는지 확인

2026-07-16 실제 소유 문서 probe에서 Word 선택 문장 교체·글자 크기·
저장과 PowerPoint 제목 교체·Shape 이동·앞 슬라이드 스타일 복사가 모두
미리보기·승인·실행·재읽기·`ready` 복귀까지 통과했다. 검증 보고서에
문서 경로나 내용을 기록하지 않았고 종료 후 생성된 Office 프로세스가
남지 않았다. 자동 회귀는 525개 중 522개 통과, 외부 AI 3개 skip,
실패 0개였다.

2026-07-18 보강 probe에서는 Word 문장 교체 후 접힌 Range→같은 Range 재선택과
PowerPoint 제목 교체 후 같은 단일 Shape에서 각각 굵기를 직접 바꿔 구조화된
서식 선호 증거가 원문 없이 한 번 기록되는 것까지 통과했다.
최신 전체 자동 회귀는 910개 중 907개 통과(unit 367·integration 337·
windows 203), 외부 AI 라이브 3개 skip, 실패 0개다.

## 6단계 완료 기준

- Excel·한글·Word·PowerPoint 모두 현재 대상 읽기 → 허용 편집 →
  네이티브 재읽기 검증 흐름 사용
- Word 문서·Range·표 셀과 PowerPoint 슬라이드·Shape·텍스트 선택 고정
- 모든 쓰기에 변경 미리보기와 명시적 승인
- 승인 사이 변경된 문서·선택·서식·위치 차단
- 검증 실패 시 가역 작업 스냅샷 복원, 비가역 Word 저장은 복구 성공 오보 금지
- 사용자 문서·기존 Office 프로세스를 건드리지 않는 실제 소유 fixture 검증
