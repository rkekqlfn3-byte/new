"""Pure Excel and HWP command parsers with no runtime side effects."""

from __future__ import annotations

import re

EXCEL_WRITE_COMMAND_RE = re.compile(
    r"^(?:엑셀|excel)(?:에서)?\s+"
    r"(?P<cell>\$?[a-z]{1,3}\$?[1-9]\d{0,6})\s*(?:셀)?\s*(?:에|으로)\s*"
    r"(?P<value>.+?)\s*(?P<particle>을|를)?\s*"
    r"(?:입력해주세요|입력해\s*줘|입력해줘|입력해|입력|"
    r"넣어주세요|넣어\s*줘|넣어줘|넣어|"
    r"써주세요|써\s*줘|써줘|써|"
    r"적어주세요|적어\s*줘|적어줘|적어)\s*[.!]?$",
    re.IGNORECASE,
)
EXCEL_SUM_RANGE_COMMAND_RE = re.compile(
    r"^(?:엑셀|excel)(?:에서)?\s+"
    r"(?P<source>\$?[a-z]{1,3}\$?[1-9]\d{0,6}:\$?[a-z]{1,3}\$?[1-9]\d{0,6})\s*(?:범위)?\s*(?:을|를)?\s*"
    r"(?:더해서|합산해서|합계(?:를)?(?:\s*구해서)?|평균(?:을)?(?:\s*구해서)?)\s*"
    r"(?P<target>\$?[a-z]{1,3}\$?[1-9]\d{0,6})\s*(?:셀)?\s*(?:에|으로)\s*"
    r"(?:넣어주세요|넣어\s*줘|넣어줘|넣어|입력해주세요|입력해\s*줘|입력해줘|입력해)\s*[.!]?$",
    re.IGNORECASE,
)
EXCEL_SUM_COLUMN_COMMAND_RE = re.compile(
    r"^(?:엑셀|excel)(?:에서)?\s+"
    r"(?P<source>.+?)\s*열\s*(?:을|를|의)?\s*"
    r"(?:더해서|합산해서|합계(?:를)?(?:\s*구해서)?|평균(?:을)?(?:\s*구해서)?)\s*"
    r"(?P<target>\$?[a-z]{1,3}\$?[1-9]\d{0,6})\s*(?:셀)?\s*(?:에|으로)\s*"
    r"(?:넣어주세요|넣어\s*줘|넣어줘|넣어|입력해주세요|입력해\s*줘|입력해줘|입력해)\s*[.!]?$",
    re.IGNORECASE,
)
EXCEL_FORMAT_PREFIX_RE = re.compile(
    r"^(?:엑셀|excel)(?:에서)?\s+"
    r"(?P<source>.+?)(?:\s*열)?(?:에서|\s+범위에서)\s*"
    r"(?P<threshold>[+-]?(?:\d[\d,]*(?:\.\d*)?|\.\d+))\s*"
    r"(?P<operator>이상|초과|이하|미만|같은|같음|다른|>=|<=|==|!=|<>|>|<|=)"
    r"(?P<rest>.+)$",
    re.IGNORECASE,
)
EXCEL_FORMAT_PREFERENCE_KEY = "excel.conditional_coloring"
EXCEL_FORMAT_METHODS = {
    "conditional_format": "apply_conditional_format",
    "direct_format": "format_matching_values",
}


def parse_native_excel_write_command(user_input):
    match = EXCEL_WRITE_COMMAND_RE.fullmatch(str(user_input or "").strip())
    if not match:
        return None
    value = match.group("value").strip()
    if not value:
        return None
    return {
        "action": "app_command",
        "target": "excel",
        "operation": "write_cell",
        "params": {
            "cell": match.group("cell").upper().replace("$", ""),
            "value": value,
            "value_type": "formula" if value.startswith("=") else "auto",
        },
    }

def parse_native_excel_sum_command(user_input):
    text = str(user_input or "").strip()
    fixed_words = (
        "현재 합계값만", "현재 평균값만", "현재 값만", "숫자로 고정", "값으로 고정",
        "고정값으로", "고정 값으로",
    )
    parse_text = text
    for word in fixed_words:
        parse_text = parse_text.replace(word, " ")
    parse_text = re.sub(r"\s+", " ", parse_text).strip()
    match = EXCEL_SUM_RANGE_COMMAND_RE.fullmatch(parse_text)
    params = None
    if match:
        params = {
            "source_range": match.group("source").upper().replace("$", ""),
            "target_cell": match.group("target").upper().replace("$", ""),
        }
    else:
        match = EXCEL_SUM_COLUMN_COMMAND_RE.fullmatch(parse_text)
        if match:
            params = {
                "column_name": match.group("source").strip(),
                "target_cell": match.group("target").upper().replace("$", ""),
            }
    if params is None:
        return None
    params["result_mode"] = (
        "value" if any(word in text for word in fixed_words) else "formula"
    )
    params["aggregation"] = "average" if "평균" in text else "sum"
    return {
        "action": "app_command",
        "target": "excel",
        "operation": "sum_column_to_cell",
        "params": params,
    }

def parse_native_excel_format_command(user_input):
    text = str(user_input or "").strip()
    if not any(word in text for word in ("표시", "칠해", "채워")):
        return None
    match = EXCEL_FORMAT_PREFIX_RE.fullmatch(text)
    if not match:
        return None
    rest = match.group("rest")
    colors = (
        "노란색", "노랑", "빨간색", "빨강", "초록색", "녹색",
        "초록", "파란색", "파랑", "주황색", "주황", "회색",
    )
    color = next((item for item in colors if item in rest), None)
    if not color:
        return None
    operator_aliases = {"같은": "eq", "같음": "eq", "다른": "ne"}
    operator = operator_aliases.get(match.group("operator"), match.group("operator"))
    source = match.group("source").strip()
    params = {
        "operator": operator,
        "threshold": match.group("threshold"),
        "color": color,
    }
    if re.fullmatch(
        r"\$?[a-z]{1,3}\$?[1-9]\d{0,6}:\$?[a-z]{1,3}\$?[1-9]\d{0,6}",
        source,
        re.IGNORECASE,
    ):
        params["source_range"] = source.upper().replace("$", "")
    else:
        params["column_name"] = source

    persistent_words = (
        "조건부 서식", "조건부서식", "자동으로", "계속 적용", "값이 바뀌면",
        "값이 바뀌어도", "계속 표시", "인 동안",
    )
    direct_words = (
        "지금만", "현재 값만", "한 번만", "한번만", "이번만",
        "현재 보이는 값만",
    )
    if any(word in text for word in persistent_words):
        operation = "apply_conditional_format"
    elif any(word in text for word in direct_words):
        operation = "format_matching_values"
    else:
        operation = "choose_format_method"
    request = {
        "action": "app_command",
        "target": "excel",
        "operation": operation,
        "params": params,
    }
    if operation != "choose_format_method" and any(
        word in text for word in ("앞으로", "항상", "무조건", "기억해")
    ):
        method = next(
            key for key, value in EXCEL_FORMAT_METHODS.items()
            if value == operation
        )
        request["_preference_selection"] = {
            "key": EXCEL_FORMAT_PREFERENCE_KEY,
            "method": method,
            "remember": True,
        }
    return request

def parse_native_excel_range_format_command(user_input):
    text = str(user_input or "").strip()
    if not re.match(r"^(?:엑셀|excel)(?:에서)?\s+", text, re.IGNORECASE):
        return None
    range_match = re.search(
        r"\$?[a-z]{1,3}\$?[1-9]\d{0,6}:\$?[a-z]{1,3}\$?[1-9]\d{0,6}",
        text,
        re.IGNORECASE,
    )
    if not range_match:
        return None
    params = {"range": range_match.group(0).upper().replace("$", "")}
    if any(word in text for word in ("굵게 해제", "굵지 않게", "볼드 해제")):
        params["bold"] = False
    elif any(word in text for word in ("굵게", "볼드")):
        params["bold"] = True
    size_match = re.search(
        r"(?:글자|폰트)\s*(?:크기|사이즈)(?:를|을)?\s*([0-9]+(?:\.[0-9]+)?)",
        text,
    )
    if size_match:
        params["font_size"] = size_match.group(1)
    alignment_words = (
        (("가운데", "중앙"), "center"),
        (("왼쪽", "좌측"), "left"),
        (("오른쪽", "우측"), "right"),
    )
    if "정렬" in text:
        for words, value in alignment_words:
            if any(word in text for word in words):
                params["alignment"] = value
                break
    colors = (
        "노란색", "노랑", "빨간색", "빨강", "초록색", "녹색",
        "초록", "파란색", "파랑", "주황색", "주황", "회색",
    )
    color = next((item for item in colors if item in text), None)
    if color:
        color_position = text.find(color)
        prefix = text[max(0, color_position - 14):color_position]
        if any(word in prefix for word in ("글자색", "글자 색", "폰트색", "폰트 색")):
            params["font_color"] = color
        elif any(
            word in text
            for word in ("배경", "채우", "칠해", "색으로", "색을")
        ):
            params["fill_color"] = color
    if set(params) == {"range"}:
        return None
    return {
        "action": "app_command",
        "target": "excel",
        "operation": "format_range",
        "params": params,
    }

def parse_native_excel_filter_command(user_input):
    text = str(user_input or "").strip()
    if not re.match(r"^(?:엑셀|excel)(?:에서)?\s+", text, re.IGNORECASE):
        return None
    if "필터" not in text:
        return None
    if any(word in text for word in ("해제", "초기화", "지워", "풀어", "모두 표시")):
        return {
            "action": "app_command",
            "target": "excel",
            "operation": "filter_range",
            "params": {"clear": True},
        }
    body = re.sub(r"^(?:엑셀|excel)(?:에서)?\s+", "", text, flags=re.IGNORECASE)
    column_match = re.match(r"(?P<column>.+?)\s*열(?:에서|을|를|로)?\s*(?P<rest>.+)", body)
    if not column_match:
        return None
    column = column_match.group("column").strip()
    rest = re.sub(
        r"\s*(?:으로\s*)?필터(?:링)?(?:해주세요|해\s*줘|해줘|해|적용해줘|적용)?\s*[.!]?$",
        "",
        column_match.group("rest").strip(),
    ).strip()
    rest = re.sub(r"^(?:에서|중에서|기준으로)\s*", "", rest).strip()
    numeric = re.search(
        r"(?P<value>[+-]?(?:\d[\d,]*(?:\.\d*)?|\.\d+))\s*"
        r"(?P<operator>이상|초과|이하|미만|같은|같음|다른|>=|<=|==|!=|<>|>|<|=)",
        rest,
    )
    if numeric:
        value = numeric.group("value")
        operator = {"같은": "eq", "같음": "eq", "다른": "ne"}.get(
            numeric.group("operator"), numeric.group("operator")
        )
    else:
        value = re.sub(r"(?:인|인 것|값)?\s*만$", "", rest).strip()
        value = value.strip("\"'")
        operator = "eq"
    if not value:
        return None
    return {
        "action": "app_command",
        "target": "excel",
        "operation": "filter_range",
        "params": {
            "clear": False,
            "column_name": column,
            "operator": operator,
            "value": value,
        },
    }

def parse_native_excel_find_replace_command(user_input):
    text = str(user_input or "").strip()
    if not re.match(r"^(?:엑셀|excel)(?:에서)?\s+", text, re.IGNORECASE):
        return None
    if not any(word in text for word in ("바꿔", "바꾸", "교체")):
        return None
    whole_cell = any(word in text for word in ("정확히 일치", "셀 전체 일치"))
    match_case = "대소문자 구분" in text
    parse_text = text
    for phrase in ("정확히 일치하는", "정확히 일치", "셀 전체 일치", "대소문자 구분해서"):
        parse_text = parse_text.replace(phrase, "")
    body = re.sub(
        r"^(?:엑셀|excel)(?:에서)?\s+", "", parse_text, flags=re.IGNORECASE
    ).strip()
    range_match = re.match(
        r"(?P<range>\$?[a-z]{1,3}\$?[1-9]\d{0,6}:\$?[a-z]{1,3}\$?[1-9]\d{0,6})"
        r"\s*(?:범위)?에서\s+(?P<rest>.+)$",
        body,
        re.IGNORECASE,
    )
    if range_match:
        scope = "range"
        address = range_match.group("range").upper().replace("$", "")
        rest = range_match.group("rest")
    else:
        sheet_match = re.match(r"현재\s*시트에서\s+(?P<rest>.+)$", body)
        if not sheet_match:
            return None
        scope = "current_sheet"
        address = None
        rest = sheet_match.group("rest")
    replace_match = re.match(
        r"(?P<find>.+?)(?:을|를)\s+(?P<replace>.+?)(?:으로|로)\s*"
        r"(?:바꿔주세요|바꿔\s*줘|바꿔줘|바꿔|바꾸어주세요|바꾸기|교체해주세요|교체해줘|교체)\s*[.!]?$",
        rest,
    )
    if not replace_match:
        return None
    find_value = replace_match.group("find").strip().strip("\"'")
    replace_value = replace_match.group("replace").strip().strip("\"'")
    if not find_value:
        return None
    params = {
        "scope": scope,
        "find": find_value,
        "replace": replace_value,
        "whole_cell": whole_cell,
        "match_case": match_case,
    }
    if address:
        params["range"] = address
    return {
        "action": "app_command",
        "target": "excel",
        "operation": "find_replace",
        "params": params,
    }

def parse_native_excel_sort_command(user_input):
    text = str(user_input or "").strip()
    if not re.match(r"^(?:엑셀|excel)(?:에서)?\s+", text, re.IGNORECASE):
        return None
    if "정렬" not in text:
        return None
    direction_match = re.search(r"(오름차순|내림차순|낮은\s*순|높은\s*순)", text)
    if not direction_match:
        return None
    body = re.sub(r"^(?:엑셀|excel)(?:에서)?\s+", "", text, flags=re.IGNORECASE)
    table_range = None
    table_match = re.match(
        r"(?P<range>\$?[a-z]{1,3}\$?[1-9]\d{0,6}:\$?[a-z]{1,3}\$?[1-9]\d{0,6})"
        r"\s*(?:표)?에서\s+(?P<rest>.+)$",
        body,
        re.IGNORECASE,
    )
    if table_match:
        table_range = table_match.group("range").upper().replace("$", "")
        body = table_match.group("rest")
    column_match = re.match(r"(?P<column>.+?)\s*열(?:을|를|\s*기준으로)?", body)
    if not column_match:
        return None
    direction_text = re.sub(r"\s+", "", direction_match.group(1))
    params = {
        "column_name": column_match.group("column").strip(),
        "direction": (
            "descending" if direction_text in {"내림차순", "높은순"}
            else "ascending"
        ),
    }
    if table_range:
        params["table_range"] = table_range
    return {
        "action": "app_command",
        "target": "excel",
        "operation": "sort_range",
        "params": params,
    }


def parse_native_excel_clarification_command(user_input):
    """Recognize incomplete Excel intents without inventing missing values."""
    text = str(user_input or "").strip()
    if not re.match(r"^(?:엑셀|excel)(?:에서)?\s+", text, re.IGNORECASE):
        return None
    lowered = text.casefold()
    reason = None
    message = None
    if any(word in lowered for word in ("합계", "합산", "더해", "더해서", "평균")):
        ranges = re.findall(
            r"\$?[a-z]{1,3}\$?[1-9]\d{0,6}:\$?[a-z]{1,3}\$?[1-9]\d{0,6}",
            text,
            re.IGNORECASE,
        )
        cells = re.findall(
            r"(?<!:)\b\$?[a-z]{1,3}\$?[1-9]\d{0,6}\b(?!\s*:)",
            text,
            re.IGNORECASE,
        )
        if not ranges and "열" not in text:
            reason = "missing_range"
            label = "평균을 구할" if "평균" in lowered else "합계할"
            example = "평균을" if "평균" in lowered else "합계를"
            message = (
                f"{label} 범위와 결과를 넣을 셀을 함께 알려주세요. "
                f"예: 엑셀에서 A2:A10 {example} A11에 넣어줘"
            )
        elif not cells:
            reason = "missing_destination"
            example = "평균을" if "평균" in lowered else "합계를"
            message = (
                f"{example.rstrip('을를')} 결과를 넣을 셀이 필요해요. "
                f"예: 엑셀에서 A2:A10 {example} A11에 넣어줘"
            )
    elif "필터" in lowered:
        reason = "missing_filter_condition"
        message = (
            "필터를 적용할 열과 조건을 알려주세요. "
            "예: 엑셀에서 상태 열을 완료로 필터해줘"
        )
    elif "정렬" in lowered:
        has_direction = any(
            word in lowered for word in ("오름차순", "내림차순", "낮은 순", "높은 순")
        )
        if "열" not in text:
            reason = "missing_sort_key"
            message = (
                "정렬 기준 열과 방향을 알려주세요. "
                "예: 엑셀에서 매출 열을 내림차순으로 정렬해줘"
            )
        elif not has_direction:
            reason = "unsafe_default"
            message = (
                "정렬 방향을 임의로 정하지 않았어요. 오름차순 또는 내림차순을 "
                "포함해 다시 말해주세요."
            )
    if not reason:
        return None
    return {
        "action": "clarification_request",
        "target": "excel",
        "operation": "request_missing_information",
        "params": {"reason": reason, "message": message},
    }

def parse_native_hwp_find_replace_command(user_input):
    text = str(user_input or "").strip()
    if not re.match(r"^(?:한글|hwp)(?:에서|에)?\s+", text, re.IGNORECASE):
        return None
    if not any(word in text for word in ("바꿔", "바꾸", "교체")):
        return None
    body = re.sub(
        r"^(?:한글|hwp)(?:에서|에)?\s+", "", text, flags=re.IGNORECASE
    ).strip()
    scope = None
    scope_patterns = (
        (r"^(?:현재\s*)?선택(?:한|된)?\s*영역에서\s+", "selection"),
        (r"^(?:현재\s*)?문서(?:\s*전체)?에서\s+", "document"),
    )
    for pattern, value in scope_patterns:
        if re.match(pattern, body):
            scope = value
            body = re.sub(pattern, "", body).strip()
            break
    match = re.match(
        r"(?P<find>.+?)(?:을|를)\s+(?P<replace>.*?)(?:으로|로)\s*"
        r"(?:바꿔주세요|바꿔\s*줘|바꿔줘|바꿔|바꾸어주세요|바꾸기|교체해주세요|교체해줘|교체)\s*[.!]?$",
        body,
    )
    if not match:
        return None
    find_value = match.group("find").strip().strip("\"'")
    replace_value = match.group("replace").strip().strip("\"'")
    if not find_value:
        return None
    return {
        "action": "app_command",
        "target": "hwp",
        "operation": "find_replace" if scope else "choose_hwp_replace_scope",
        "params": {
            "scope": scope,
            "find": find_value,
            "replace": replace_value,
            "match_case": "대소문자 구분" in text,
        },
    }

def parse_native_hwp_text_format_command(user_input):
    text = str(user_input or "").strip()
    if not re.match(r"^(?:한글|hwp)(?:에서|에)?\s+", text, re.IGNORECASE):
        return None
    if "선택" not in text:
        return None
    params = {}
    if any(word in text for word in ("굵게", "볼드")):
        params["bold"] = True
    size_match = re.search(
        r"(?:글자|폰트)\s*(?:크기|사이즈)(?:를|을)?\s*([0-9]+(?:\.[0-9]+)?)",
        text,
    )
    if size_match:
        params["font_size"] = size_match.group(1)
    colors = (
        "검은색", "검정", "빨간색", "빨강", "초록색", "녹색",
        "초록", "파란색", "파랑", "노란색", "노랑", "주황색",
        "주황", "회색",
    )
    color = next((item for item in colors if item in text), None)
    if color and any(word in text for word in ("글자색", "글자 색", "폰트색", "색으로")):
        params["text_color"] = color
    if not params:
        return None
    return {
        "action": "app_command",
        "target": "hwp",
        "operation": "set_text_format",
        "params": params,
    }

def parse_native_hwp_paragraph_format_command(user_input):
    text = str(user_input or "").strip()
    if not re.match(r"^(?:한글|hwp)(?:에서|에)?\s+", text, re.IGNORECASE):
        return None
    if "문단" not in text or "정렬" not in text:
        return None
    alignment = None
    for words, value in (
        (("가운데", "중앙"), "center"),
        (("왼쪽", "좌측"), "left"),
        (("오른쪽", "우측"), "right"),
        (("양쪽", "배분"), "justify"),
    ):
        if any(word in text for word in words):
            alignment = value
            break
    if not alignment:
        return None
    return {
        "action": "app_command",
        "target": "hwp",
        "operation": "set_paragraph_format",
        "params": {"alignment": alignment},
    }

def parse_native_hwp_insert_command(user_input):
    text = str(user_input or "").strip()
    match = re.match(
        r"^(?:한글|hwp)(?:에서|에)?\s+"
        r"(?:현재\s+)?(?:커서(?:\s*위치)?|선택(?:한|된)?\s*영역)\s*(?:에|에다가)\s*"
        r"(?P<value>.+?)\s*(?:을|를)?\s*"
        r"(?:입력해주세요|입력해\s*줘|입력해줘|입력해|입력|"
        r"넣어주세요|넣어\s*줘|넣어줘|넣어|써주세요|써\s*줘|써줘|써|삽입해줘|삽입해)\s*[.!]?$",
        text,
        re.IGNORECASE,
    )
    if not match:
        return None
    value = match.group("value").strip()
    if value.endswith(("을", "를")):
        value = value[:-1].rstrip()
    value = value.strip("\"'")
    if not value:
        return None
    return {
        "action": "app_command",
        "target": "hwp",
        "operation": "insert_text",
        "params": {"text": value},
    }

def parse_native_hwp_save_command(user_input):
    text = str(user_input or "").strip()
    if not re.match(r"^(?:한글|hwp)(?:에서|에)?\s+", text, re.IGNORECASE):
        return None
    if "pdf" not in text.casefold() or "저장" not in text:
        return None
    path_match = re.search(
        r"[\"']?(?P<path>[a-zA-Z]:[\\/][^\"'\r\n]+?\.pdf)[\"']?(?=\s*(?:로|으로|에|$))",
        text,
        re.IGNORECASE,
    )
    params = {"format": "PDF"}
    if path_match:
        params["path"] = path_match.group("path").strip()
    return {
        "action": "app_command",
        "target": "hwp",
        "operation": "save_as",
        "params": params,
    }
