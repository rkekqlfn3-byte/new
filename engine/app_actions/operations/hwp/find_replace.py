"""Find and replace text in the Hanword selection or whole document."""

from __future__ import annotations

from engine.app_actions.base import (
    AppActionBlocked,
    AppActionError,
    AppActionVerificationError,
    PreparedAction,
)
from engine.app_actions.office_helpers import count_hwp_matches, replace_hwp_text
from engine.app_actions.operations.hwp.base import MAX_REPLACE_MATCHES, HwpOperation


class FindReplaceOperation(HwpOperation):
    name = "find_replace"

    match_count = staticmethod(count_hwp_matches)
    replace_text = staticmethod(replace_hwp_text)

    def prepare(self, adapter, hwp, params):
        _, base, document_text, selection = adapter._context(hwp)
        scope = str(params.get("scope") or "").strip().casefold()
        aliases = {
            "선택": "selection", "선택 영역": "selection", "selection": "selection",
            "문서": "document", "현재 문서": "document", "document": "document",
        }
        scope = aliases.get(scope, scope)
        if scope not in {"selection", "document"}:
            raise AppActionBlocked("한글 찾기·바꾸기 범위는 선택 영역 또는 현재 문서여야 합니다.")
        if scope == "selection" and not selection["has_selection"]:
            raise AppActionBlocked("찾기·바꾸기에 사용할 텍스트를 한글에서 먼저 선택해주세요.")
        find = str(params.get("find") if params.get("find") is not None else "")
        replace = str(
            params.get("replace") if params.get("replace") is not None else ""
        )
        if not find:
            raise AppActionBlocked("한글에서 찾을 문자열은 비워둘 수 없습니다.")
        if len(find) > 1000 or len(replace) > 1000:
            raise AppActionBlocked("찾을 문자열과 바꿀 문자열은 각각 1,000자 이하여야 합니다.")
        match_case = bool(params.get("match_case"))
        scope_text = selection["text"] if scope == "selection" else document_text
        count = self.match_count(scope_text, find, match_case)
        if count > MAX_REPLACE_MATCHES:
            raise AppActionBlocked(
                f"한 번에 바꿀 수 있는 항목은 최대 {MAX_REPLACE_MATCHES:,}개입니다."
            )
        expected_scope = self.replace_text(scope_text, find, replace, match_case)
        target = "선택 영역" if scope == "selection" else "현재 문서 전체"
        snapshot = {
            **base,
            "operation": self.name,
            "target": target,
            "scope": scope,
            "find_digest": adapter._digest_text(find),
            "replace_digest": adapter._digest_text(replace),
            "match_case": match_case,
            "matching_count": count,
        }
        noop = count == 0
        return PreparedAction(
            app=self.app,
            operation=self.name,
            document_id=base["document_id"],
            workbook_name=base["document_name"],
            sheet="현재 문서",
            target=target,
            params={
                "scope": scope,
                "find": find,
                "replace": replace,
                "match_case": match_case,
                "expected_scope_digest": adapter._digest_text(expected_scope),
            },
            current_state={
                "matching_count": count,
                "scope_length": len(scope_text),
                "has_selection": selection["has_selection"],
                "selected_length": selection["text_length"],
                "selected_digest": selection["text_digest"],
                "document_digest": base["text_digest"],
            },
            estimated_changes=count,
            destructive=not noop,
            reversible=True,
            verification_method="read_hwp_text_after_replace",
            context_fingerprint=adapter._state_fingerprint(snapshot),
            prepared_at=adapter._created_at(),
            noop=noop,
            metadata={"window_handle": base["window_handle"]},
        )

    def run(self, adapter, hwp, current):
        if current.noop:
            before = {"matching_count": 0}
            return adapter._result(current, before, before, False)
        _, before_base, before_text, selection = adapter._context(hwp)
        scope_text = (
            selection["text"] if current.params["scope"] == "selection" else before_text
        )
        expected = self.replace_text(
            scope_text,
            current.params["find"],
            current.params["replace"],
            current.params["match_case"],
        )
        try:
            find_replace = hwp.HParameterSet.HFindReplace
            hwp.HAction.GetDefault("AllReplace", find_replace.HSet)
            find_replace.Direction = hwp.FindDir("AllDoc")
            find_replace.FindString = current.params["find"]
            find_replace.ReplaceString = current.params["replace"]
            find_replace.ReplaceMode = 1
            find_replace.FindType = 1
            find_replace.MatchCase = 1 if current.params["match_case"] else 0
            is_selection = current.params["scope"] == "selection"
            find_replace.IgnoreMessage = 0 if is_selection else 1
            if is_selection:
                hwp.SetMessageBoxMode(0x20000)
            try:
                hwp.HAction.Execute("AllReplace", find_replace.HSet)
            finally:
                if is_selection:
                    hwp.SetMessageBoxMode(0xF0000)
            _, after_base, after_text, after_selection = adapter._context(hwp)
            actual = after_selection["text"] if is_selection else after_text
            if adapter._digest_text(actual) != adapter._digest_text(expected):
                raise AppActionVerificationError("한글 찾기·바꾸기 결과가 요청과 다릅니다.")
        except Exception as error:
            try:
                hwp.SetMessageBoxMode(0xF0000)
            except Exception:
                pass
            try:
                adapter._undo(hwp)
            except Exception:
                pass
            if isinstance(error, AppActionError):
                raise
            raise AppActionVerificationError(
                "한글 찾기·바꾸기 실행 또는 검증에 실패했습니다."
            ) from error
        return adapter._result(
            current,
            {
                "matching_count": current.current_state["matching_count"],
                "text_digest": before_base["text_digest"],
            },
            {
                "replaced_count": current.current_state["matching_count"],
                "text_digest": after_base["text_digest"],
            },
            True,
        )


__all__ = ["FindReplaceOperation"]
