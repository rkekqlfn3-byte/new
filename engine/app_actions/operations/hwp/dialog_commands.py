"""한글 commands that would otherwise open a dialog.

``HAction.Run`` opens a window for these, and a window is where automation
stops.  The same commands run without one when the parameters the dialog
would have collected are filled in and ``Execute`` is called instead —
which is how 쪽 설정 has always worked here.

Nothing is guessed.  한글 names the parameter set for each command through
``CreateAction(name).SetID``, and the fields of that set come out of COM
type information, so ``InsertHyperlink`` says ``HyperLink`` and that set
says ``Text`` and ``Command``.

The check that these worked is the control chain, not the body text.  쪽
번호 and 머리말 were both recorded as broken earlier in the day because
they were judged by whether the document's text had changed, and neither
puts anything there — they add a control (``pgnp``, ``head``).  That was
the fourth time in one day that too narrow an observation called a working
command broken.
"""

from __future__ import annotations

from engine.app_actions.base import (
    AppActionBlocked,
    AppActionError,
    AppActionVerificationError,
    PreparedAction,
)
from engine.app_actions.operations.hwp.base import HwpOperation

# 한글's four-letter control identifiers, used to tell that the command
# actually added what it was asked for.
BOOKMARK_CONTROL = "bokm"
HEADER_CONTROL = "head"
PAGE_NUMBER_CONTROL = "pgnp"
MAX_TEXT = 200


def control_ids(hwp) -> list[str]:
    """Every control in the document, in order."""
    found: list[str] = []
    control = hwp.HeadCtrl
    while control is not None:
        try:
            found.append(str(control.CtrlID))
        except Exception:
            pass
        try:
            control = control.Next
        except Exception:
            break
    return found


def _clean(value, label: str, limit: int = MAX_TEXT) -> str:
    text = str(value or "").strip()
    if not text:
        raise AppActionBlocked(f"{label}을(를) 알려주세요.")
    if len(text) > limit:
        raise AppActionBlocked(f"{label}이(가) 너무 깁니다. {limit}자 이내로 줄여주세요.")
    return text


class _DialogCommand(HwpOperation):
    """Shared shape: fill the dialog's parameters, execute, count controls."""

    action = ""
    set_id = ""
    control = ""
    label = ""

    def fill(self, parameters, params) -> dict:
        """Put the request into the parameter set; return what to show."""
        raise NotImplementedError

    def prepare(self, adapter, hwp, params):
        _, base, document_text, selection = adapter._context(hwp)
        described = self.fill_preview(params)
        before = control_ids(hwp)
        snapshot = {
            **base,
            "operation": self.name,
            "target": adapter._target(base, selection),
            "controls": len(before),
        }
        return PreparedAction(
            app=self.app,
            operation=self.name,
            document_id=base["document_id"],
            workbook_name=base["document_name"],
            sheet="현재 문서",
            target=f"{adapter._target(base, selection)} · {described}",
            params={**self.stored_params(params), "expected_controls": len(before) + 1},
            current_state={
                "controls": len(before),
                "document_length": len(document_text),
                "document_digest": base["text_digest"],
            },
            estimated_changes=1,
            destructive=False,
            reversible=True,
            verification_method="read_control_chain",
            context_fingerprint=adapter._state_fingerprint(snapshot),
            prepared_at=adapter._created_at(),
            metadata={"window_handle": base["window_handle"]},
        )

    def fill_preview(self, params) -> str:
        return self.label

    def stored_params(self, params) -> dict:
        return {}

    def run(self, adapter, hwp, current):
        before = int(current.current_state["controls"])
        expected = int(current.params["expected_controls"])
        try:
            parameters = getattr(hwp.HParameterSet, f"H{self.set_id}")
            hwp.HAction.GetDefault(self.action, parameters.HSet)
            self.fill(parameters, current.params)
            if not hwp.HAction.Execute(self.action, parameters.HSet):
                raise AppActionBlocked(
                    f"한글이 현재 위치에서 {self.label}을(를) 허용하지 않았습니다."
                )
            after = control_ids(hwp)
            if len(after) != expected or self.control not in after:
                raise AppActionVerificationError(
                    f"한글에 {self.label}이(가) 들어가지 않았습니다."
                )
        except Exception as error:
            try:
                adapter._undo(hwp)
            except Exception:
                pass
            if isinstance(error, AppActionError):
                raise
            raise AppActionVerificationError(
                f"한글 {self.label} 추가 또는 검증에 실패했습니다."
            ) from error
        _, after_base, _, _ = adapter._context(hwp)
        return adapter._result(
            current,
            {"controls": before},
            {"controls": expected, "document_digest": after_base["text_digest"]},
            True,
        )


class InsertHyperlinkOperation(_DialogCommand):
    name = "insert_hyperlink"
    action = "InsertHyperlink"
    set_id = "HyperLink"
    control = "%hlk"
    label = "하이퍼링크"

    def fill_preview(self, params) -> str:
        return f"하이퍼링크 “{_clean(params.get('text'), '링크에 보일 글')}”"

    def stored_params(self, params) -> dict:
        return {
            "text": _clean(params.get("text"), "링크에 보일 글"),
            "url": _clean(params.get("url") or params.get("address"), "링크 주소", 2000),
        }

    def fill(self, parameters, params) -> None:
        parameters.Text = str(params["text"])
        parameters.Command = str(params["url"])

    def run(self, adapter, hwp, current):
        # A hyperlink puts its text into the document rather than adding a
        # control the chain reports, so it is verified by the text instead.
        before_text = str(current.current_state.get("document_length") or 0)
        try:
            parameters = hwp.HParameterSet.HHyperLink
            hwp.HAction.GetDefault(self.action, parameters.HSet)
            self.fill(parameters, current.params)
            if not hwp.HAction.Execute(self.action, parameters.HSet):
                raise AppActionBlocked(
                    "한글이 현재 위치에서 하이퍼링크를 허용하지 않았습니다."
                )
            _, after_base, after_text, _ = adapter._context(hwp)
            if str(current.params["text"]) not in after_text:
                raise AppActionVerificationError(
                    "한글 문서에 하이퍼링크 글자가 들어가지 않았습니다."
                )
        except Exception as error:
            try:
                adapter._undo(hwp)
            except Exception:
                pass
            if isinstance(error, AppActionError):
                raise
            raise AppActionVerificationError(
                "한글 하이퍼링크 추가 또는 검증에 실패했습니다."
            ) from error
        return adapter._result(
            current,
            {"document_length": before_text},
            {
                "document_length": len(after_text),
                "document_digest": after_base["text_digest"],
            },
            True,
        )


class InsertBookmarkOperation(_DialogCommand):
    name = "insert_bookmark"
    action = "Bookmark"
    set_id = "BookMark"
    control = BOOKMARK_CONTROL
    label = "책갈피"

    def fill_preview(self, params) -> str:
        return f"책갈피 “{_clean(params.get('name'), '책갈피 이름')}”"

    def stored_params(self, params) -> dict:
        return {"name": _clean(params.get("name"), "책갈피 이름")}

    def fill(self, parameters, params) -> None:
        parameters.name = str(params["name"])


class InsertPageNumberOperation(_DialogCommand):
    name = "insert_page_number"
    action = "PageNumPos"
    set_id = "PageNumPos"
    control = PAGE_NUMBER_CONTROL
    label = "쪽 번호"

    def fill(self, parameters, params) -> None:
        # 한글's own default position. Which DrawPos value lands where was
        # not measured, and an unverified position is not offered.
        return None


class InsertHeaderOperation(_DialogCommand):
    name = "insert_header"
    action = "HeaderFooter"
    set_id = "HeaderFooter"
    control = HEADER_CONTROL
    label = "머리말"

    def fill(self, parameters, params) -> None:
        return None


__all__ = [
    "InsertBookmarkOperation",
    "InsertHeaderOperation",
    "InsertHyperlinkOperation",
    "InsertPageNumberOperation",
    "control_ids",
]
