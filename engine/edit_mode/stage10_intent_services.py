"""Focused natural-language services for Stage 10 workflow intents."""

from __future__ import annotations

import re


class ExplicitJoinIntentService:
    """Parse one complete, content-free explicit sheet join request."""

    FUNCTION_NAMES = {
        "합계": "sum",
        "합산": "sum",
        "평균": "average",
        "건수": "count",
        "개수": "count",
        "최솟값": "minimum",
        "최소값": "minimum",
        "최소": "minimum",
        "최댓값": "maximum",
        "최대값": "maximum",
        "최대": "maximum",
    }

    def __init__(self, analyzer):
        self.analyzer = analyzer

    @staticmethod
    def _error(message):
        return {
            "join_requested": True,
            "join_plan": None,
            "join_error": message,
        }

    def parse(self, command):
        raw = re.sub(r"\s+", " ", str(command or "")).strip()
        lowered = raw.casefold()
        requested = "시트" in lowered and bool(
            re.search(r"(?:조인|\bjoin\b|결합)", lowered)
        )
        if not requested:
            return {"join_requested": False, "join_plan": None}
        identity = self._identity(raw)
        if isinstance(identity, dict):
            return identity
        pair, join_type, left_key, right_key = identity
        aggregations = self._aggregations(raw, pair)
        if isinstance(aggregations, dict):
            return aggregations
        left_aggregations, right_aggregations = aggregations
        plan = {
            "left_sheet": self.analyzer._join_token(pair.group("left")),
            "right_sheet": self.analyzer._join_token(pair.group("right")),
            "left_key": left_key,
            "right_key": right_key,
            "join_type": join_type,
        }
        self._attach_aggregations(plan, "left", left_aggregations)
        self._attach_aggregations(plan, "right", right_aggregations)
        return {"join_requested": True, "join_plan": plan}

    def _identity(self, raw):
        token = self.analyzer.JOIN_NAME_TOKEN
        mapped = re.search(
            rf"(?P<left>{token})\s*시트의\s*"
            rf"(?P<left_key>{token})\s*(?:열\s*)?(?:와|과|하고)\s*"
            rf"(?P<right>{token})\s*시트의\s*"
            rf"(?P<right_key>{token})\s*(?:열\s*)?(?:을|를)?\s*"
            rf"(?:키\s*)?(?:로|으로|기준(?:으로)?)",
            raw,
            re.IGNORECASE,
        )
        pair = mapped or re.search(
            rf"(?P<left>{token})\s*시트(?:와|과|하고)\s*"
            rf"(?P<right>{token})\s*시트(?:를|을)?",
            raw,
            re.IGNORECASE,
        )
        if pair is None:
            return self._error(
                "조인할 두 시트를 '고객 시트와 주문 시트'처럼 지정하거나, "
                "키 이름이 다르면 '고객 시트의 고객ID와 주문 시트의 "
                "구매자ID로'처럼 양쪽 키를 모두 지정해주세요. 공백이 있는 "
                "시트명과 키는 따옴표로 묶어주세요."
            )
        suffix = raw[pair.end():]
        join_type_match = re.search(
            r"(?P<type>내부|이너|inner|왼쪽|좌측|left)\s*"
            r"(?:조인|join|결합)",
            suffix,
            re.IGNORECASE,
        )
        if join_type_match is None:
            return self._error(
                "조인 방식을 '내부 조인' 또는 '왼쪽 조인'으로 명시해주세요."
            )
        keys = self._keys(mapped, suffix, join_type_match, token)
        if isinstance(keys, dict):
            return keys
        join_type_text = join_type_match.group("type").casefold()
        join_type = "inner" if join_type_text in {"내부", "이너", "inner"} else "left"
        return pair, join_type, keys[0], keys[1]

    def _keys(self, mapped, suffix, join_type_match, token):
        if mapped is not None:
            return (
                self.analyzer._join_token(mapped.group("left_key")),
                self.analyzer._join_token(mapped.group("right_key")),
            )
        patterns = (
            rf"(?P<key>{token})\s*(?:열\s*)?(?:을|를)?\s*기준(?:으로)?\s*$",
            rf"(?P<key>{token})\s*(?:열\s*)?(?:을|를)?\s*(?:키\s*)?(?:로|으로)\s*$",
        )
        fragments = (
            suffix[:join_type_match.start()].strip(),
            suffix[join_type_match.end():].strip(),
        )
        for fragment in fragments:
            for pattern in patterns:
                match = re.search(pattern, fragment, re.IGNORECASE)
                if match is not None:
                    key = self.analyzer._join_token(match.group("key"))
                    return key, key
        return self._error(
            "두 시트에 공통으로 있는 키 열을 '고객ID 기준으로'처럼 "
            "정확히 지정해주세요."
        )

    def _aggregations(self, raw, pair):
        if "집계" not in raw:
            return [], []
        token = self.analyzer.JOIN_NAME_TOKEN
        matches = list(re.finditer(
            rf"(?P<sheet>{token})\s*시트의\s*"
            rf"(?P<column>{token})\s*(?:열\s*)?(?:을|를|은|는)\s*"
            r"(?P<function>합계|합산|평균|건수|개수|최솟값|최소값|최소|"
            r"최댓값|최대값|최대)(?:로)?\s*집계",
            raw,
            re.IGNORECASE,
        ))
        if not matches:
            return self._error(
                "집계 조인은 '주문 시트의 매출을 합계 집계해서'처럼 "
                "시트·열·집계 함수를 정확히 지정해주세요. 여러 열은 "
                "각 집계마다 해당 시트명을 반복해주세요."
            )
        left_sheet = self.analyzer._join_token(pair.group("left"))
        right_sheet = self.analyzer._join_token(pair.group("right"))
        left, right = [], []
        for match in matches:
            sheet = self.analyzer._join_token(match.group("sheet"))
            if sheet.casefold() == left_sheet.casefold():
                target = left
            elif sheet.casefold() == right_sheet.casefold():
                target = right
            else:
                return self._error(
                    "집계 시트는 조인에 지정한 왼쪽 또는 오른쪽 시트와 "
                    "정확히 같아야 합니다. 각 집계의 시트명을 다시 확인해주세요."
                )
            target.append({
                "column": self.analyzer._join_token(match.group("column")),
                "function": self.FUNCTION_NAMES[match.group("function").casefold()],
            })
        if len(left) > 5 or len(right) > 5:
            return self._error(
                "한 요청에서 왼쪽과 오른쪽 집계는 각각 5개까지 지정할 수 있습니다."
            )
        return left, right

    @staticmethod
    def _attach_aggregations(plan, side, aggregations):
        if aggregations:
            plan[f"{side}_aggregation"] = (
                aggregations[0] if len(aggregations) == 1 else aggregations
            )


class WorkflowOutputSelectionService:
    """Resolve report/presentation output flags without routing a workflow."""

    def __init__(self, analyzer):
        self.analyzer = analyzer

    def parse(self, command, *, default_report_format):
        word = any(term in command for term in ("word", "워드"))
        hwp = any(term in command for term in ("한글", "hwp", "hwpx"))
        report_format = "both" if word and hwp else "hwp" if hwp else "word" if word else default_report_format
        slide_count = self._slide_count(command)
        flags = self._output_flags(command)
        if flags["presentation_excluded"]:
            slide_count = None
        return {
            "slide_count": slide_count,
            "explicit_slide_count": slide_count is not None,
            "report_format": report_format,
            "explicit_report_format": bool(word or hwp),
            "include_presentation": flags["include_presentation"],
            "explicit_include_presentation": flags["presentation_explicit"],
            "presentation_selection_error": flags["presentation_error"],
            "include_report": flags["include_report"],
            "explicit_include_report": flags["report_explicit"],
            "report_selection_error": flags["report_error"],
        }

    def _slide_count(self, command):
        for pattern in (
            r"(?:ppt|파워포인트|프레젠테이션|발표자료|슬라이드).*?(\d{1,2})\s*장",
            r"(\d{1,2})\s*장(?:짜리)?\s*(?:ppt|파워포인트|프레젠테이션|발표자료|슬라이드)",
        ):
            match = re.search(pattern, command)
            if match:
                return int(match.group(1))
        return None

    def _output_flags(self, command):
        report_only = re.search(self.analyzer.REPORT_ONLY_PATTERNS[0], command, re.IGNORECASE)
        presentation_exclusion = re.search(self.analyzer.REPORT_ONLY_PATTERNS[1], command, re.IGNORECASE)
        presentation_only = re.search(self.analyzer.PRESENTATION_ONLY_PATTERNS[0], command, re.IGNORECASE)
        report_exclusion = re.search(self.analyzer.PRESENTATION_ONLY_PATTERNS[1], command, re.IGNORECASE)
        has_presentation = any(term in command for term in self.analyzer.CREATE_SLIDE_TERMS)
        has_report = any(term in command for term in self.analyzer.CREATE_REPORT_TERMS)
        presentation_excluded = bool(report_only or presentation_exclusion)
        report_excluded = bool(presentation_only or report_exclusion)
        presentation_error = None
        if report_only and has_presentation and not presentation_exclusion:
            presentation_error = (
                "보고서만 생성과 PowerPoint 생성 요청이 함께 있습니다. "
                "보고서만 또는 보고서와 PowerPoint 중 하나로 다시 말해주세요."
            )
        report_error = None
        if presentation_only and has_report and not report_exclusion:
            report_error = (
                "PowerPoint만 생성과 보고서 생성 요청이 함께 있습니다. "
                "PowerPoint만 또는 보고서와 PowerPoint 중 하나로 다시 말해주세요."
            )
        if presentation_excluded and report_excluded:
            report_error = (
                "보고서와 PowerPoint를 모두 제외할 수 없습니다. "
                "만들 산출물을 하나 이상 지정해주세요."
            )
        return {
            "presentation_excluded": presentation_excluded,
            "presentation_explicit": presentation_excluded or has_presentation,
            "presentation_error": presentation_error,
            "include_presentation": False if presentation_excluded else (True if has_presentation else None),
            "include_report": False if report_excluded else (True if has_report else None),
            "report_explicit": bool(report_excluded or has_report),
            "report_error": report_error,
        }


class WorkflowIntentRoutingService:
    """Route workflow-language branches and build their user preview."""

    def __init__(self, analyzer, intent_factory):
        self.analyzer = analyzer
        self.intent = intent_factory

    def analyze(self, text, context):
        if str(context.get("app_type") or "").casefold() != "excel":
            return None
        raw = re.sub(r"\s+", " ", str(text or "")).strip()
        command = raw.casefold()
        candidates = self.analyzer._relationship_candidate_params(raw)
        fixed = self._fixed_intent(command, candidates)
        if fixed is not None:
            return fixed
        artifact = self._artifact_intent(command)
        if artifact is not None:
            return artifact
        if any(term in command for term in self.analyzer.REUSE_TERMS):
            return self._reuse_intent(command, raw, context)
        return self._creation_intent(command, raw, context, candidates)

    def _fixed_intent(self, command, candidates):
        routes = (
            (self.analyzer.FORGET_TERMS, "deactivate_business_workflow_skill", "승인된 복합 업무 재사용 스킬 해제"),
            (self.analyzer.REMEMBER_TERMS, "activate_business_workflow_skill", "검증된 최근 복합 업무 구조를 재사용 스킬로 활성화"),
            (self.analyzer.RESUME_TERMS, "resume_business_workflow", "저장된 성공 단계는 건너뛰고 실패한 문서 워크플로 단계부터 재개"),
        )
        for terms, operation, description in routes:
            if any(term in command for term in terms):
                return self.intent(operation, description)
        if (
            any(term in command for term in self.analyzer.RELATIONSHIP_INSPECTION_TERMS)
            and not candidates.get("relationship_candidate_requested")
        ):
            return self.intent(
                "inspect_excel_relationships",
                "현재 Excel의 시트 간 조인 키 후보를 읽기 전용으로 검사",
            )
        return None

    def _artifact_intent(self, command):
        recent = any(term in command for term in self.analyzer.RECENT_ARTIFACT_TERMS)
        connect = "편집" in command and "연결" in command
        opening = any(term in command for term in self.analyzer.OPEN_ARTIFACT_TERMS)
        if not recent or not (opening or connect):
            return None
        kind = label = None
        if any(term in command for term in ("ppt", "파워포인트", "발표자료", "슬라이드")):
            kind, label = "presentation", "최근 검증 PowerPoint 발표자료"
        elif any(term in command for term in ("보고서", "리포트")):
            if any(term in command for term in ("한글", "hwp", "hwpx")):
                kind, label = "hwp_report", "최근 검증 한글 보고서"
            elif any(term in command for term in ("word", "워드", "docx")):
                kind, label = "word_report", "최근 검증 Word 보고서"
            else:
                kind, label = "report", "최근 검증 보고서"
        if not kind:
            return None
        operation = "connect_recent_workflow_artifact" if connect else "open_recent_workflow_artifact"
        action = "열기·전면 포커스 후 편집 대상으로 전환" if connect else "열기 및 전면 포커스"
        return self.intent(operation, f"{label} {action}", {"artifact_kind": kind})

    def _reuse_intent(self, command, raw, context):
        params = self.analyzer._creation_params(command, default_report_format=None)
        params.update(self.analyzer._join_params(raw))
        params.update(self.analyzer._source_scope_params(raw, context))
        params["reuse_approved_skill"] = True
        return self.intent(
            "create_business_workflow",
            "승인된 지난 복합 업무 구조를 현재 Excel에서 새 산출물로 재사용",
            params,
        )

    def _creation_intent(self, command, raw, context, candidates):
        join = self.analyzer._join_params(raw)
        if candidates.get("relationship_candidate_requested") and join.get("join_requested"):
            candidates["relationship_candidate_error"] = (
                "후보 번호와 시트·키 직접 지정은 한 요청에 함께 사용할 수 없습니다."
            )
        scope = self.analyzer._source_scope_params(raw, context)
        creation = self.analyzer._creation_params(command, default_report_format="word")
        flags = self._creation_flags(command, join, candidates, scope, creation)
        if not flags["recognized"]:
            return None
        params = creation
        params.update(join)
        params.update(candidates)
        params.update(scope)
        if params.get("relationship_candidate_requested"):
            params["join_requested"] = True
        params["contextual_current_document"] = flags["contextual"]
        description = self._description(params)
        return self.intent("create_business_workflow", description, params)

    def _creation_flags(self, command, join, candidates, scope, creation):
        has_analysis = (
            any(term in command for term in ("분석", "요약", "analy"))
            or bool(join.get("join_requested"))
            or bool(candidates.get("relationship_candidate_requested"))
            or bool(scope.get("source_scope_requested"))
        )
        has_report = any(term in command for term in self.analyzer.CREATE_REPORT_TERMS)
        has_slides = any(term in command for term in self.analyzer.CREATE_SLIDE_TERMS)
        action = any(term in command for term in self.analyzer.CREATE_ACTION_TERMS)
        contextual = any(term in command for term in self.analyzer.CURRENT_DOCUMENT_TERMS) and action
        question = any(term in command for term in ("어떻게", "방법", "만드는 법", "작성법", "뭐야", "무엇"))
        report_only = creation.get("include_presentation") is False
        presentation_only = creation.get("include_report") is False
        explicit_report = report_only and has_report and action and not question
        explicit_slides = presentation_only and has_slides and action and not question
        recognized = (
            (has_analysis or contextual or explicit_report or explicit_slides)
            and ((has_report and has_slides) or report_only or presentation_only)
            and not question
        )
        return {
            "recognized": recognized,
            "contextual": bool(contextual or explicit_report or explicit_slides),
        }

    def _description(self, params):
        report_label = {"word": "Word", "hwp": "한글", "both": "Word·한글"}[
            str(params["report_format"])
        ]
        source = (
            "현재 선택 Excel 범위만 읽기 전용 분석"
            if params.get("source_scope_requested")
            else "현재 연결 Excel 전체 읽기 전용 분석"
            if params["contextual_current_document"]
            else "Excel 읽기 전용 분석"
        )
        slides = params["slide_count"]
        if params.get("include_report") is False:
            description = f"{source} → PowerPoint 요약만 생성 · 보고서 제외"
            if slides is not None:
                description = f"{source} → PowerPoint {slides}장 요약만 생성 · 보고서 제외"
        elif params.get("include_presentation") is False:
            description = f"{source} → {report_label} 보고서만 생성 · PowerPoint 제외"
        else:
            description = f"{source} → {report_label} 보고서 → PowerPoint 요약 생성"
            if slides is not None:
                description = f"{source} → {report_label} 보고서 → PowerPoint {slides}장 요약 생성"
        return self._join_description(params, description)

    def _join_description(self, params, description):
        if params.get("relationship_candidate_requested"):
            index = params.get("relationship_candidate_index")
            label = {"inner": "내부", "left": "왼쪽"}.get(
                params.get("relationship_candidate_join_type"), "미지정"
            )
            return f"관계 후보 {index or '?'}번을 {label} 조인 후 {description}"
        plan = params.get("join_plan")
        if not plan:
            return description
        label = "내부" if plan["join_type"] == "inner" else "왼쪽"
        key = f"{plan['left_key']} 기준"
        if plan["left_key"] != plan["right_key"]:
            key = f"{plan['left_key']} ↔ {plan['right_key']} 키 매핑으로"
        parts = []
        for field, sheet in (
            ("left_aggregation", plan["left_sheet"]),
            ("right_aggregation", plan["right_sheet"]),
        ):
            parts.extend(
                f"{sheet}/{item['column']} "
                f"{self.analyzer._aggregation_function_label(item['function'])}"
                for item in self.analyzer._aggregation_items(plan.get(field))
            )
        aggregation = f"{' · '.join(parts)} 집계 후 " if parts else ""
        return (
            f"{plan['left_sheet']}·{plan['right_sheet']} 시트를 "
            f"{key} {aggregation}{label} 조인 후 {description}"
        )
