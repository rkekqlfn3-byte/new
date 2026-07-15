import re

from engine.action_registry import app_target_actions
from engine.document_reader import resolve_file_path
from engine.learning_schema import normalize_learning_metadata


class ResultValidator:
    def __init__(self, owner, preflight):
        self.owner = owner
        self.preflight = preflight

    def validate(self, result):
        parser = self.owner
        default_response = "으응? 무슨 말인지 잘 못 알아들었어.. 단어나 동의어를 사전에 먼저 등록해줄래? 🥺"
        issues = []

        if not isinstance(result, dict):
            response = str(result).strip() if result else default_response
            return response, [], ["AI 응답이 올바른 JSON 객체 형식이 아닙니다."]

        enforce_app_candidates = "_allowed_app_candidates" in result
        allowed_apps = {
            str(name).strip().casefold()
            for name in (result.get("_allowed_app_candidates") or [])
            if str(name).strip()
        }
        enforce_macro_candidates = "_allowed_learned_candidates" in result
        allowed_macros = {
            (str(pair[0]).strip().casefold(), str(pair[1]).strip().casefold())
            for pair in (result.get("_allowed_learned_candidates") or [])
            if isinstance(pair, (list, tuple)) and len(pair) == 2
        }

        response = result.get("response", default_response)
        if not isinstance(response, str):
            response = str(response) if response is not None else default_response

        raw_actions = result.get("actions")
        if raw_actions is None and result.get("action"):
            raw_actions = [result]
        if raw_actions is None:
            raw_actions = []
        if not isinstance(raw_actions, list):
            return response, [], ["actions 값이 배열 형식이 아닙니다."]

        if len(raw_actions) > 8:
            issues.append("한 번에 실행 가능한 동작은 최대 8개입니다. 초과 동작은 제외했습니다.")
            raw_actions = raw_actions[:8]

        valid_actions = []
        allowed_actions = {
            "open_app", "action_plan", "dynamic_code", "use_learned_macro",
            "adapted_macro", "read_and_analyze", "none"
        }

        for index, raw_action in enumerate(raw_actions, start=1):
            prefix = f"{index}번 동작"
            if not isinstance(raw_action, dict):
                issues.append(f"{prefix}: 객체 형식이 아닙니다.")
                continue

            act = dict(raw_action)
            action = act.get("action")
            if action not in allowed_actions:
                issues.append(f"{prefix}: 알 수 없는 action '{action}'입니다.")
                continue

            for field, limit in (
                ("target", 500), ("app_name", 100), ("macro_name", 100),
                ("description", 500),
            ):
                if field in act and not isinstance(act[field], str):
                    act[field] = str(act[field] or "")
                if isinstance(act.get(field), str):
                    act[field] = act[field].strip()[:limit]

            if action == "none":
                valid_actions.append({"action": "none"})
                continue

            if action == "open_app":
                resolved = parser._resolve_registered_app(act.get("target"))
                if not resolved:
                    issues.append(f"{prefix}: 등록된 앱 또는 웹사이트 target이 아닙니다.")
                    continue
                noun, path = resolved
                if enforce_app_candidates and noun.casefold() not in allowed_apps:
                    issues.append(f"{prefix}: AI 요청 후보에 없던 앱 또는 웹사이트입니다.")
                    continue
                act["target"] = noun
                act["_resolved_path"] = path

            elif action == "read_and_analyze":
                resolved_path = resolve_file_path(act.get("target"))
                if not resolved_path:
                    issues.append(f"{prefix}: 실제로 존재하는 파일 경로를 찾을 수 없습니다.")
                    continue
                act["target"] = resolved_path

            elif action == "use_learned_macro":
                learned = parser._get_learned_macro(act.get("app_name"), act.get("macro_name"))
                if not learned or not (learned.get("code") or learned.get("plan")):
                    issues.append(f"{prefix}: 등록된 학습 매크로를 찾을 수 없습니다.")
                    continue
                macro_key = (
                    act.get("app_name", "").casefold(),
                    act.get("macro_name", "").casefold(),
                )
                if enforce_macro_candidates and macro_key not in allowed_macros:
                    issues.append(f"{prefix}: AI 요청 후보에 없던 학습 매크로입니다.")
                    continue

            elif action == "adapted_macro":
                learned = parser._get_learned_macro(act.get("app_name"), act.get("macro_name"))
                if not learned:
                    issues.append(f"{prefix}: 응용할 원본 학습 매크로를 찾을 수 없습니다.")
                    continue
                macro_key = (
                    act.get("app_name", "").casefold(),
                    act.get("macro_name", "").casefold(),
                )
                if enforce_macro_candidates and macro_key not in allowed_macros:
                    issues.append(f"{prefix}: AI 요청 후보에 없던 학습 매크로입니다.")
                    continue
                code_issue = self.validate_generated_code(act)
                if code_issue:
                    issues.append(f"{prefix}: {code_issue}")
                    continue

            elif action == "dynamic_code":
                code_issue = self.validate_generated_code(act, require_external_target=True)
                if code_issue:
                    issues.append(f"{prefix}: {code_issue}")
                    continue

            elif action == "action_plan":
                raw_learning = act.get("learning", {})
                if not isinstance(raw_learning, dict):
                    issues.append(f"{prefix}: learning 값이 객체 형식이 아닙니다.")
                    continue
                learning = normalize_learning_metadata(act)
                required_learning = {
                    "verbs": learning.get("verbs"),
                    "utterances": learning.get("utterances"),
                }
                missing_learning = [
                    name for name, value in required_learning.items() if not value
                ]
                if not str(act.get("macro_name", "")).strip():
                    missing_learning.append("macro_name")
                if missing_learning:
                    issues.append(
                        f"{prefix}: 학습 정보가 비어 있습니다: {', '.join(missing_learning)}"
                    )
                    continue
                act["learning"] = learning
                act["plan"] = self.preflight.ensure_input_focus_steps(act.get("plan"))
                slot_names = [
                    item.get("name") for item in learning.get("slots", [])
                    if isinstance(item, dict) and item.get("name")
                ]
                plan_issue = parser.action_executor.validate_plan(
                    act.get("plan"), slot_names=slot_names
                )
                if plan_issue:
                    issues.append(f"{prefix}: {plan_issue}")
                    continue
                if enforce_app_candidates:
                    candidate_issue = self.validate_plan_app_candidates(
                        act.get("plan"), learning, allowed_apps
                    )
                    if candidate_issue:
                        issues.append(f"{prefix}: {candidate_issue}")
                        continue

            valid_actions.append(act)

        return response, valid_actions, issues

    def validate_plan_app_candidates(self, plan, learning, allowed_apps):
        parser = self.owner
        slot_values = {
            str(item.get("name", "")): str(item.get("value", ""))
            for item in learning.get("slots", [])
            if isinstance(item, dict) and item.get("name")
        }
        for index, step in enumerate(plan or [], start=1):
            if not isinstance(step, dict) or step.get("action") not in app_target_actions():
                continue
            target = str(step.get("target", "")).strip()
            target = re.sub(
                r"\{([0-9a-zA-Z가-힣_]+)\}",
                lambda match: slot_values.get(match.group(1), match.group(0)),
                target,
            )
            if "{" in target:
                continue
            resolved = parser._resolve_registered_app(target)
            if resolved and resolved[0].casefold() not in allowed_apps:
                return f"행동 계획 {index}단계가 AI 요청 후보에 없던 앱을 사용합니다."
        return None

    @staticmethod
    def validate_generated_code(act, require_external_target=False):
        code = act.get("code")
        if not isinstance(code, str) or not code.strip():
            return "실행할 파이썬 code가 비어 있습니다."

        steps = act.get("explanation_steps")
        if not isinstance(steps, list) or not steps:
            return "explanation_steps가 비어 있습니다."
        for step in steps:
            if (
                not isinstance(step, dict)
                or not str(step.get("step", "")).strip()
                or not str(step.get("code_snippet", "")).strip()
            ):
                return "explanation_steps 항목 형식이 올바르지 않습니다."

        try:
            compile(code, "<jarvis-generated-code>", "exec")
        except SyntaxError as error:
            return f"파이썬 문법 오류가 있습니다: {error.msg} (줄 {error.lineno})"
        lowered_code = code.casefold()
        if "oleobj" in lowered_code or re.search(
            r"\.release\s*\(", lowered_code
        ):
            return (
                "COM 내부 포인터를 직접 Release()하면 이중 해제 위험이 있습니다. "
                "일반 참조 정리와 소유권 기반 종료를 사용해야 합니다."
            )
        uses_com = any(
            token in lowered_code
            for token in ("win32com", "comtypes", "pythoncom")
        )
        if uses_com and not all(
            token in lowered_code
            for token in (
                "coinitialize(",
                "couninitialize(",
                "try:",
                "finally:",
            )
        ):
            return (
                "COM 자동화 코드는 작업 스레드에서 pythoncom.CoInitialize()를 호출하고 "
                "try/finally에서 pythoncom.CoUninitialize()를 반드시 호출해야 합니다."
            )
        descriptor = " ".join(
            str(act.get(field, ""))
            for field in ("target", "app_name", "macro_name", "description")
        ).casefold()
        excel_specific = (
            "excel" in descriptor
            or "엑셀" in descriptor
            or "excel.application" in code.casefold()
        )
        hwp_specific = (
            "hwp" in descriptor
            or "한글" in descriptor
            or "hwpframe.hwpobject" in lowered_code
        )
        if (excel_specific or hwp_specific) and re.search(
            r"\bdispatch(?:ex)?\s*\(", code, flags=re.IGNORECASE
        ):
            return (
                "동적 Office 코드는 새 Application을 만들지 말고 실행 중인 사용자 "
                "인스턴스에 연결해야 합니다."
            )
        if (excel_specific or hwp_specific) and re.search(
            r"\.quit\s*\(", code, flags=re.IGNORECASE
        ):
            return "사용자가 실행한 Office 인스턴스에 Quit()을 호출할 수 없습니다."
        if excel_specific and re.search(
            r"\bGetForegroundWindow\s*\(", code, flags=re.IGNORECASE
        ):
            return (
                "Excel 창 제목은 현재 전면 창 API로 읽을 수 없습니다. "
                "win32com.client.GetActiveObject('Excel.Application')의 "
                "ActiveWindow.Caption을 사용해야 합니다."
            )
        learning = act.get("learning", {})
        if require_external_target:
            if (
                not isinstance(learning, dict)
                or learning.get("argument_mode") != "json"
                or not isinstance(learning.get("slots"), list)
            ):
                return (
                    "동적 코드는 learning.argument_mode=json과 slots 배열을 제공해야 하며, "
                    "슬롯 값은 sys.argv로 받아야 합니다."
                )
            has_slots = any(
                isinstance(item, dict) and str(item.get("name", "")).strip()
                for item in learning.get("slots", [])
            )
            if has_slots and "sys.argv" not in code:
                return "슬롯이 있는 동적 코드는 값을 sys.argv로 받아야 합니다."
            if has_slots and "json.loads" not in code:
                return "JSON 슬롯 매크로는 json.loads(sys.argv[1])로 인자를 읽어야 합니다."
        return None
