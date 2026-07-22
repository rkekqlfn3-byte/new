# JARVIS Goal Phase 6 환경 상태 보고서

작성일: 2026-07-23

상태: 사용자 환경 조치 대기

## 읽기 전용 확인 결과

- `HKCU\Software\HNC\HwpAutomation\Modules`에서 실제 파일이 존재하는 공식 한글
  Automation 파일 접근 보안 모듈 등록 이름을 찾지 못했다.
- 제품 Goal 판정의 다른 자동 축은 통과했지만 `workflow_hwp`만
  `environment_blocked`로 유지됐다.
- 기존 HWP watchdog은 모듈 부재를 실행 전에 분류하고 부분 산출물을 남기지 않는
  안전 계약을 통과했다.
- Excel VBA 실제 probe는 현재 필수 자동 증거에서 통과 상태다.

## 자동으로 하지 않은 작업

- 보안 모듈 다운로드·설치·등록.
- 레지스트리 생성·수정.
- 한글 또는 Excel 보안 설정 우회.
- VBA 프로젝트 개체 모델 신뢰 설정 변경.

## 수용을 계속하려면 필요한 사용자 작업

1. 한컴 공식 Automation 안내에 따라 파일 접근 보안 모듈을 직접 설치·등록한다.
2. 설치가 끝난 뒤 JARVIS를 다시 시작한다.
3. HWP 전용 및 Word+HWP 동시 workflow probe를 다시 실행한다.
4. 생성·read-back·부분 파일 부재·소유 프로세스 정리를 재확인한다.

이 환경 조치 전에는 JARVIS가 한글 생성 성공을 주장하거나 Prototype Goal을
`accepted`로 올리지 않는다.
