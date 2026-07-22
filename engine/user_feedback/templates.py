"""Local Korean copy for execution feedback; no AI call is required."""

ERROR_GUIDANCE = {
    "validation_error": (
        "요청 내용을 안전하게 확정하지 못했어요.",
        "대상이나 조건을 조금 더 구체적으로 말해주세요.",
    ),
    "verification_error": (
        "작업 결과를 확인하지 못했어요.",
        "문서를 확인한 뒤 같은 작업을 다시 요청해주세요.",
    ),
    "target_not_found": (
        "작업할 대상을 찾지 못했어요.",
        "앱·문서·선택 영역을 확인해주세요.",
    ),
    "environment_error": (
        "현재 컴퓨터 환경 때문에 작업을 시작하지 못했어요.",
        "안내된 환경 설정을 확인한 뒤 다시 시도해주세요.",
    ),
    "timeout": (
        "앱의 응답을 기다리다 안전하게 멈췄어요.",
        "앱 상태를 확인한 뒤 다시 시도해주세요.",
    ),
    "busy": (
        "다른 작업을 처리하고 있어요.",
        "현재 작업이 끝난 뒤 다시 요청해주세요.",
    ),
    "execution_error": (
        "작업을 끝내지 못했어요.",
        "표시된 해결 안내를 확인한 뒤 다시 시도해주세요.",
    ),
    "unknown": (
        "예상하지 못한 문제로 작업을 멈췄어요.",
        "상세 로그를 확인하거나 다시 시도해주세요.",
    ),
}

ACTION_LABELS = {
    "command": "요청",
    "command_api": "요청",
    "edit": "문서 편집",
    "confirmation": "확인 응답",
    "skill_executor": "학습된 작업",
    "dynamic_code_preflight": "동적 작업 안전 검사",
    "automatic_recovery": "대상 복구",
    "write_cell": "셀 값·수식 입력",
    "sum_column_to_cell": "합계·평균 입력",
    "format_range": "셀 서식 변경",
    "apply_conditional_format": "조건부 서식 적용",
    "format_matching_values": "조건 일치 셀 서식 변경",
    "filter_range": "필터 변경",
    "find_replace": "찾기·바꾸기",
    "sort_range": "표 정렬",
    "insert_rows": "행 삽입",
    "insert_columns": "열 삽입",
    "read_selection": "선택 영역 읽기",
    "undo_last_edit": "직전 편집 되돌리기",
    "set_text_format": "글자 서식 변경",
    "set_paragraph_format": "문단 서식 변경",
    "replace_selection": "선택 텍스트 변경",
    "replace_shape_text": "슬라이드 텍스트 변경",
}
