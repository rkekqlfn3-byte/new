"""Open a 한글 dialog the automation cannot fill in itself.

183 한글 commands answer ``HAction.Run`` by opening a window, and a window
is where automation stops.  Opening it anyway is still worth doing: the
reader asked for 글자 모양, and putting them in front of 글자 모양 with one
sentence is most of the work even if the last part is theirs.

Two measured facts shape this.

*Opening blocks the caller.*  ``HAction.Run("CharShape")`` had not returned
after 25 seconds — the calling thread sits inside 한글's modal loop until
someone closes the window.  So the call is made on a worker thread with the
application marshalled to it, and Jarvis's own thread returns at once.  With
the dialog open, a read on the main thread still answered in 0.0 seconds.

*The window names itself in Korean.*  The window that appeared was titled
글자 모양, so what the reader is looking at is read off the window rather
than written down here and left to drift.

This operation does not claim to have done anything.  It reports that a
window is open and that the rest is the reader's, because that is true: it
cannot see what they do in there, and it cannot undo it.
"""

from __future__ import annotations

import threading
import time

from engine.app_actions.base import (
    AppActionBlocked,
    AppActionVerificationError,
    PreparedAction,
)
from engine.app_actions.operations.hwp.base import HwpOperation

# Every command the action catalogue saw open a window, minus the file,
# print, macro, certificate, password, personal-information, online and
# import/export families: those are excluded from being offered
# at all rather than filtered later, because a window is not the only thing
# they can do. Generated from verification/hwp_action_catalogue_full.json.
DIALOG_ACTIONS = frozenset({
    "AddFieldBibliography", "AddHanjaWord", "Average", "Bookmark",
    "ChangeRome", "ChangeRomeToName", "CharShape",
    "CharShapeDialogWithoutBorder", "CharShapeHeight",
    "CharShapeHeightSpin", "CharShapeLang", "CharShapeSpacing",
    "CharShapeTBTitle", "CharShapeTypeFace", "CharShapeWidth",
    "ComposeChars", "ComposeCharsEdit", "ConvertCase",
    "ConvertFullHalfWidth", "ConvertHiraGata", "ConvertJianFan",
    "ConvertToHangul", "DeleteCtrls", "DocSummaryInfo",
    "DrawObjCreatorArc", "DrawObjCreatorObject", "DrawObjPart",
    "DrawObjTemplateLoad", "DutmalChars", "EditFieldBibliography",
    "FormObjHanjaBusu", "FormObjHanjaMean", "FormObjInputCodeTable",
    "HanThDIC", "HancomAsset", "HeaderFooter", "HimKbdChange",
    "Hyperlink", "HyperlinkJump", "ImageTileGroupTextArt", "IndexMark",
    "InputCodeTable", "InputDateStyle", "InputHanja", "InputHanjaMean",
    "InputPersonsNameHanja", "InsertCCLMark", "InsertChart",
    "InsertConnectLineArcBoth", "InsertCrossReference",
    "InsertFieldTemplate", "InsertHyperlink", "InsertIdiom",
    "InsertKOGLMark", "InsertRevision", "InsertRevisionHyperlink",
    "InsertRevisionLeftMove", "InsertRevisionRightMove",
    "InsertRevisionSimpleChange", "InsertRevisionTransfer", "Jajun",
    "LabelAdd", "LabelTemplate", "LinkDocument", "MakeContents",
    "MakeIndex", "MasterPage", "MasterPageType", "MemoShape",
    "MetaTag_delete_DOC", "ModifySection", "MultiColumn", "NewNumber",
    "OutlineNumber", "PageBorder", "PageNumPos", "ParaNumberBullet",
    "ParaShapeLineSpace", "ParaShapeNextSpace", "ParaShapePrevSpace",
    "ParagraphShape", "PasteSpecial", "Presentation", "PresentationRange",
    "PstGradientType", "PstScrChangeType", "SearchForeign",
    "SetLineNumbers", "Shape", "ShapeObjGuideLine",
    "ShapeObjInsertCaptionNum", "ShapeObjLock", "ShapeObjSelect",
    "ShapeObjShear", "ShapeObjTableSelCell", "ShapeObjUngroup",
    "ShapeObjUnlockAll", "SmartSearchMode", "Sort", "SpellChecker",
    "SpellingCheck"
})

# How long to wait for the window to appear before saying it did not.
APPEAR_TIMEOUT_SECONDS = 4.0
APPEAR_POLL_SECONDS = 0.15


def hwp_windows(process_id: int, main_window: int) -> list[tuple[int, str]]:
    """Visible windows of the 한글 process other than the document itself."""
    import win32gui
    import win32process

    found: list[tuple[int, str]] = []

    def visit(handle, _):
        if not win32gui.IsWindowVisible(handle) or handle == main_window:
            return True
        try:
            _, owner = win32process.GetWindowThreadProcessId(handle)
        except Exception:
            return True
        if int(owner) == int(process_id):
            found.append((handle, str(win32gui.GetWindowText(handle) or "")))
        return True

    try:
        win32gui.EnumWindows(visit, None)
    except Exception:
        return []
    return found


def open_on_worker(application, action: str) -> threading.Thread:
    """Run the opening call somewhere it is allowed to stay blocked.

    The application is marshalled into the worker, because a COM object
    belongs to the apartment that created it and using it raw from another
    thread is undefined.
    """
    import pythoncom
    import win32com.client

    stream = pythoncom.CoMarshalInterThreadInterfaceInStream(
        pythoncom.IID_IDispatch, application._oleobj_
    )

    def worker():
        pythoncom.CoInitialize()
        try:
            proxy = win32com.client.Dispatch(
                pythoncom.CoGetInterfaceAndReleaseStream(
                    stream, pythoncom.IID_IDispatch
                )
            )
            proxy.HAction.Run(action)
        except Exception:
            # The reader closing the window is a normal end, and so is 한글
            # refusing the command. Neither is Jarvis's failure to report.
            pass
        finally:
            pythoncom.CoUninitialize()

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    return thread


class OpenHwpDialogOperation(HwpOperation):
    name = "open_hwp_dialog"

    def prepare(self, adapter, hwp, params):
        action = str(params.get("dialog_action") or "").strip()
        if action not in DIALOG_ACTIONS:
            raise AppActionBlocked(
                "실제 한글에서 창이 열리는 것으로 확인된 기능만 열 수 있습니다."
            )
        _, base, document_text, selection = adapter._context(hwp)
        window = int(base["window_handle"])
        import win32process

        _, process_id = win32process.GetWindowThreadProcessId(window)
        already = hwp_windows(process_id, window)
        if already:
            raise AppActionBlocked(
                f"한글에 이미 열린 창이 있습니다: {already[0][1] or '이름 없는 창'}. "
                "먼저 닫아주세요."
            )
        snapshot = {
            **base,
            "operation": self.name,
            "dialog_action": action,
            "target": adapter._target(base, selection),
        }
        return PreparedAction(
            app=self.app,
            operation=self.name,
            document_id=base["document_id"],
            workbook_name=base["document_name"],
            sheet="현재 문서",
            target=f"{adapter._target(base, selection)} · 설정 창 열기",
            params={"dialog_action": action, "process_id": int(process_id)},
            current_state={
                "document_length": len(document_text),
                "document_digest": base["text_digest"],
            },
            estimated_changes=0,
            destructive=False,
            # Opening a window changes nothing, and what the reader does in
            # it is outside anything this operation can see or restore.
            reversible=False,
            verification_method="read_dialog_window",
            context_fingerprint=adapter._state_fingerprint(snapshot),
            prepared_at=adapter._created_at(),
            metadata={"window_handle": window, "opens_dialog": True},
        )

    def run(self, adapter, hwp, current):
        action = str(current.params["dialog_action"])
        process_id = int(current.params["process_id"])
        _, base, _, _ = adapter._context(hwp)
        window = int(base["window_handle"])

        open_on_worker(hwp, action)
        deadline = time.monotonic() + APPEAR_TIMEOUT_SECONDS
        opened: list[tuple[int, str]] = []
        while time.monotonic() < deadline:
            opened = hwp_windows(process_id, window)
            if opened:
                break
            time.sleep(APPEAR_POLL_SECONDS)
        if not opened:
            raise AppActionVerificationError(
                "한글에서 설정 창이 열리지 않았습니다. "
                "커서 위치나 선택 영역을 바꾸고 다시 요청해주세요."
            )
        title = opened[0][1] or "설정 창"
        return adapter._result(
            current,
            {"dialog": "닫힘"},
            {
                "dialog": title,
                "note": (
                    f"‘{title}’ 창을 열었습니다. 여기서부터는 창에서 직접 "
                    "설정해주세요. 자비스가 대신 눌러드릴 수는 없고, "
                    "창에서 한 변경은 되돌려드릴 수도 없습니다."
                ),
            },
            True,
        )


__all__ = ["DIALOG_ACTIONS", "OpenHwpDialogOperation", "hwp_windows", "open_on_worker"]
