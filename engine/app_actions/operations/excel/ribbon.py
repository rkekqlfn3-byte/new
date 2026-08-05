"""Run an Excel ribbon command that has no operation of its own.

Excel exposes its ribbon through ``CommandBars.ExecuteMso(idMso)``, the same
shape as 한글's ``HAction.Run``, with one advantage worth using:
``GetEnabledMso`` says whether a command exists and is available **without
running it**, so an invented name is rejected before anything touches the
workbook.

The names come out of the installed Office modules and only the ones Excel
itself confirmed are kept.  Each command here also ran against a real Excel
by ``verification/excel_command_catalogue.py`` and changed the sheet, and a
single Undo put the sheet back.  Commands that opened a dialog, changed
nothing, or left Excel refusing further calls are not in this table.

Commands that already have an operation are deliberately absent — alignment
is ``format_range``'s job, and two routes to one edit is how ``글머리표
없애줘`` ended up offering to delete a table in 한글.  A test keeps this
table and the rest of the registry disjoint.
"""

from __future__ import annotations

from dataclasses import dataclass

from engine.app_actions.base import (
    AppActionBlocked,
    AppActionError,
    AppActionVerificationError,
    PreparedAction,
)
from engine.app_actions.operations.excel.base import ExcelOperation

# How many cells the before/after snapshot reads. A command is judged by the
# selection it acts on, and reading an unbounded region would make a large
# selection slow to preview.
MAX_SNAPSHOT_CELLS = 400


@dataclass(frozen=True, slots=True)
class ExcelCommand:
    command: str
    label: str
    # How people ask for it, kept beside the command so a new entry cannot be
    # added without saying what someone would call it.
    words: tuple[str, ...] = ()


# Verified 2026-08-06 against a real Excel: changed the sheet and undid
# cleanly. See verification/excel_command_catalogue.json for the full run.
EXCEL_COMMANDS: dict[str, ExcelCommand] = {
    "italic": ExcelCommand("Italic", "기울임", ("기울임", "이탤릭", "기울여")),
    "underline": ExcelCommand("Underline", "밑줄", ("밑줄", "언더라인")),
    "subscript": ExcelCommand("Subscript", "아래 첨자", ("아래 첨자", "아래첨자")),
    "wrap_text": ExcelCommand(
        "WrapText", "자동 줄바꿈", ("자동 줄바꿈", "줄바꿈", "텍스트 줄 바꿈")
    ),
    "merge_cells": ExcelCommand(
        "MergeCells", "셀 병합", ("셀 병합", "셀 합치", "병합해")
    ),
    "border_outside": ExcelCommand(
        "BorderOutside", "바깥 테두리", ("바깥 테두리", "외곽 테두리", "테두리 두르")
    ),
    "border_inside": ExcelCommand("BorderInside", "안쪽 테두리", ("안쪽 테두리",)),
    "border_left": ExcelCommand("BorderLeft", "왼쪽 테두리", ("왼쪽 테두리",)),
    "border_right": ExcelCommand("BorderRight", "오른쪽 테두리", ("오른쪽 테두리",)),
    "border_top": ExcelCommand("BorderTop", "위쪽 테두리", ("위쪽 테두리",)),
    "fill_down": ExcelCommand(
        "FillDown", "아래로 채우기", ("아래로 채우", "아래쪽 채우")
    ),
    "fill_right": ExcelCommand(
        "FillRight", "오른쪽으로 채우기", ("오른쪽으로 채우", "오른쪽 채우")
    ),
    "fill_left": ExcelCommand("FillLeft", "왼쪽으로 채우기", ("왼쪽으로 채우",)),
    "fill_up": ExcelCommand("FillUp", "위로 채우기", ("위로 채우",)),
    "clear_contents": ExcelCommand(
        "ClearContents", "내용 지우기", ("내용 지우", "값만 지우")
    ),
    "clear_all": ExcelCommand(
        "ClearAll", "모두 지우기", ("모두 지우", "서식까지 지우")
    ),
}


def selection_state(adapter, application) -> tuple:
    """Enough of the selection to tell whether the command did anything."""
    try:
        selection = application.Selection
    except Exception:
        return ()
    marks: list[str] = []
    seen = 0
    try:
        cells = adapter._range_cells(selection)
    except Exception:
        return ()
    for cell in cells:
        seen += 1
        if seen > MAX_SNAPSHOT_CELLS:
            break
        for reader in (
            lambda c: c.Value,
            lambda c: c.Font.Italic,
            lambda c: c.Font.Underline,
            lambda c: c.Font.Subscript,
            lambda c: c.WrapText,
            lambda c: c.MergeCells,
            lambda c: c.Borders.LineStyle,
        ):
            try:
                marks.append(str(reader(cell)))
            except Exception:
                marks.append("")
    return tuple(marks)


def undo_excel_command(adapter, application, prepared):
    """Excel's own Undo, then prove the selection is back where it started."""
    adapter._ensure_undo_context(application, prepared)
    before = tuple(prepared.params.get("before_state") or ())
    application.Undo()
    if selection_state(adapter, application) != before:
        raise AppActionVerificationError(
            "Excel 리본 명령 복원 결과가 실행 전 상태와 다릅니다."
        )
    return adapter._result(
        prepared,
        {"state": "after"},
        {"state": "before"},
        changed=True,
    )


class RunExcelCommandOperation(ExcelOperation):
    name = "run_excel_command"
    undo = staticmethod(undo_excel_command)

    def prepare(self, adapter, application, params):
        key = str(params.get("excel_command") or "").strip().casefold()
        entry = EXCEL_COMMANDS.get(key)
        if entry is None:
            raise AppActionBlocked(
                "확인되지 않은 Excel 기능입니다. 실제 Excel에서 동작을 확인한 "
                "기능만 실행합니다."
            )
        _, sheet, base = adapter._common_context(application)
        try:
            # Address is a property here, not a call: invoking it raises
            # "'str' object is not callable" and read as Excel refusing.
            address = str(application.Selection.Address).replace("$", "")
        except Exception as error:
            raise AppActionBlocked(
                "Excel에서 적용할 범위를 읽지 못했습니다. 셀을 선택하고 다시 요청해주세요."
            ) from error
        state = selection_state(adapter, application)
        snapshot = {
            **base,
            "operation": self.name,
            "target": address,
            "excel_command": key,
            "state": state,
        }
        return PreparedAction(
            app=self.app,
            operation=self.name,
            document_id=base["document_id"],
            workbook_name=base["workbook_name"],
            sheet=base["sheet"],
            target=f"{address} · {entry.label}",
            params={
                "excel_command": key,
                "command": entry.command,
                "range": address,
                "before_state": list(state),
            },
            current_state={"address": address, "cells": len(state)},
            estimated_changes=1,
            destructive=key in {"clear_all", "clear_contents"},
            reversible=True,
            verification_method="read_selection_state",
            context_fingerprint=adapter._state_fingerprint(snapshot),
            prepared_at=adapter._created_at(),
            metadata={"application_hwnd": base["application_hwnd"]},
        )

    def execute(self, adapter, application, prepared):
        current = self.prepare(adapter, application, prepared.params)
        adapter._ensure_same_context(current, prepared)
        entry = EXCEL_COMMANDS[str(current.params["excel_command"])]
        before = tuple(current.params["before_state"])
        try:
            application.CommandBars.ExecuteMso(entry.command)
            after = selection_state(adapter, application)
            if after == before:
                raise AppActionVerificationError(
                    f"Excel에서 {entry.label}이(가) 적용되지 않았습니다."
                )
        except Exception as error:
            if isinstance(error, AppActionError):
                raise
            raise AppActionVerificationError(
                f"Excel {entry.label} 적용 또는 검증에 실패했습니다."
            ) from error
        return adapter._result(
            current,
            {"cells": len(before)},
            {"cells": len(after)},
            changed=True,
        )


__all__ = [
    "EXCEL_COMMANDS",
    "ExcelCommand",
    "RunExcelCommandOperation",
    "selection_state",
    "undo_excel_command",
]
