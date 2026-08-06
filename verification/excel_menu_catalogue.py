"""Find which Excel menu commands can be automated, in Excel's own words.

``ExecuteMso`` needed identifiers extracted from Office binaries, and only
181 of 37,666 candidates turned out to be real — Excel does not keep its
command set as plain text the way 한글 does.

``CommandBars`` is the better source.  Excel enumerates its own menus:
4,294 controls, 1,238 distinct Korean captions, each with a caption, a
shortcut where it has one, whether it is currently available, and an
``Execute`` that runs it.  The Korean name comes from Excel, so the wording
a reader would use is not something this project has to invent and keep in
step.

Each candidate runs on a seeded workbook and is recorded as one of:

``changed``   the sheet differed afterwards — usable
``undoable``  ``changed`` and a single Undo put it back — usable and safe
``dialog``    it opened a window a person has to answer
``noop``      it ran and changed nothing measurable
``failed``    the call raised

Captions naming the file, print, macro, security, external-data and account
families are excluded from being run at all rather than filtered afterwards:
a menu item called 저장 does not need to be tried to know what it does, and
trying it costs the user something outside this process.
"""

from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path

from verification.stray_processes import EXCEL_PROCESS_NAMES, StrayProcessGuard

REPORT_PATH = Path(__file__).with_name("excel_menu_catalogue.json")

KOREAN = re.compile("[가-힣]")

# Excel puts zero-width characters inside some captions — 제한된 액세스 is
# really 제한된 액​세스. Matching a deny-list against the raw caption let that
# one through, so an invisible character was enough to defeat the filter.
INVISIBLE = re.compile("[​-‏﻿­]")
BUTTON_TYPE = 1
POPUP_TYPE = 10
MAX_DEPTH = 3

# Whole menus whose items are excluded regardless of their own caption.
# Caption matching alone let 웹 보관 파일 (*.mht) through because it names
# neither 저장 nor 웹 페이지; where an item lives says more than what it is
# called.
NEVER_RUN_PATHS = ("파일", "도구", "데이터", "보안", "매크로", "추가 기능")

# Caption fragments that must never be executed to find out what they do.
NEVER_RUN = (
    "저장", "열기", "닫기", "인쇄", "미리 보기", "끝내기", "종료", "보내기",
    "메일", "매크로", "보안", "암호", "권한", "액세스", "자격", "체크 아웃",
    "체크 인", "버전", "게시", "웹 페이지", "가져오기", "내보내기", "연결",
    "새로 고침", "쿼리", "업로드", "다운로드", "설치", "추가 기능", "옵션",
    "도움말", "정보", "사용자 지정", "새로 만들기", "복구", "공유", "보호",
    "서명", "신뢰", "온라인", "계정", "로그", "등록", "라이선스", "파일 검색",
    "작업 영역", "외부 데이터", "OLE", "ODBC", "XML", "VBA", "Visual Basic",
)

SEED = (
    ("A1", "이름"), ("B1", "수량"),
    ("A2", "가"), ("B2", 3),
    ("A3", "나"), ("B3", 1),
    ("A4", "가"), ("B4", 2),
)
SELECTION = "A1:B4"


def _clean(caption: str) -> str:
    text = INVISIBLE.sub("", str(caption or "")).replace("&", "")
    return re.sub(r"\s+", " ", text).strip()


def _walk(controls, path: str, found: list, depth: int = 0) -> None:
    try:
        count = int(controls.Count)
    except Exception:
        return
    for index in range(1, count + 1):
        try:
            control = controls.Item(index)
            caption = _clean(control.Caption)
            kind = int(control.Type)
        except Exception:
            continue
        if kind == POPUP_TYPE:
            if depth < MAX_DEPTH:
                try:
                    _walk(control.Controls, f"{path}/{caption}", found, depth + 1)
                except Exception:
                    pass
            continue
        if not caption or not KOREAN.search(caption):
            continue
        try:
            enabled = bool(control.Enabled)
            shortcut = str(control.ShortcutText or "")
        except Exception:
            continue
        found.append(
            {
                "caption": caption,
                "path": path,
                "shortcut": shortcut,
                "enabled": enabled,
            }
        )


def enumerate_commands(application) -> list[dict]:
    """Every menu command Excel will name in Korean, deduplicated."""
    found: list[dict] = []
    bars = application.CommandBars
    for index in range(1, int(bars.Count) + 1):
        try:
            bar = bars.Item(index)
            _walk(bar.Controls, str(bar.Name), found)
        except Exception:
            continue
    unique: dict[str, dict] = {}
    for item in found:
        unique.setdefault(item["caption"], item)
    return list(unique.values())


def is_probeable(item: dict) -> bool:
    if not item.get("enabled"):
        return False
    if any(word in item["path"] for word in NEVER_RUN_PATHS):
        return False
    return not any(word in item["caption"] for word in NEVER_RUN)


def _sheet_state(sheet) -> tuple:
    values = []
    for row in range(1, 7):
        for column in range(1, 4):
            try:
                values.append(str(sheet.Cells(row, column).Value))
            except Exception:
                values.append("")
    marks = []
    for name in ("A1", "A2", "B2"):
        try:
            cell = sheet.Range(name)
        except Exception:
            continue
        for reader in (
            lambda c: c.Font.Bold,
            lambda c: c.Font.Italic,
            lambda c: c.Font.Underline,
            lambda c: c.Font.Size,
            lambda c: c.Interior.Color,
            lambda c: c.MergeCells,
            lambda c: c.WrapText,
            lambda c: c.HorizontalAlignment,
            lambda c: c.Borders.LineStyle,
            lambda c: c.NumberFormat,
        ):
            try:
                marks.append(str(reader(cell)))
            except Exception:
                marks.append("")
    return (tuple(values), tuple(marks))


def _seed(sheet) -> None:
    sheet.Cells.Clear()
    for address, value in SEED:
        sheet.Range(address).Value = value
    sheet.Range(SELECTION).Select()


def _find_control(application, caption: str):
    bars = application.CommandBars
    for index in range(1, int(bars.Count) + 1):
        try:
            bar = bars.Item(index)
        except Exception:
            continue
        found: list = []
        try:
            _walk_for(bar.Controls, caption, found)
        except Exception:
            continue
        if found:
            return found[0]
    return None


def _walk_for(controls, caption: str, found: list, depth: int = 0) -> None:
    if found or depth > MAX_DEPTH:
        return
    try:
        count = int(controls.Count)
    except Exception:
        return
    for index in range(1, count + 1):
        if found:
            return
        try:
            control = controls.Item(index)
            current = _clean(control.Caption)
            kind = int(control.Type)
        except Exception:
            continue
        if kind == POPUP_TYPE:
            try:
                _walk_for(control.Controls, caption, found, depth + 1)
            except Exception:
                pass
            continue
        if current == caption:
            found.append(control)
            return


def _classify(application, sheet, watchdog, item: dict) -> dict:
    _seed(sheet)
    before = _sheet_state(sheet)
    watchdog.reset()
    control = _find_control(application, item["caption"])
    if control is None:
        return {**item, "outcome": "not_found", "undoable": None}
    started = time.monotonic()
    try:
        control.Execute()
        outcome = None
    except Exception as error:
        outcome = f"failed:{type(error).__name__}"
    elapsed = round(time.monotonic() - started, 2)

    time.sleep(0.15)
    dialogs = watchdog.dialogs_closed
    if outcome is None:
        if dialogs:
            outcome = "dialog"
        else:
            outcome = "changed" if _sheet_state(sheet) != before else "noop"

    undoable = None
    if outcome == "changed":
        try:
            application.Undo()
            undoable = _sheet_state(sheet) == before
        except Exception:
            undoable = False
    return {
        **item,
        "outcome": outcome,
        "undoable": undoable,
        "dialogs": dialogs,
        "seconds": elapsed,
    }


def run() -> dict:
    import win32com.client as client
    import win32process

    from engine.app_actions.com_lifecycle import com_apartment
    from verification.hwp_action_catalogue import DialogWatchdog

    records: list[dict] = []
    pending: list[dict] | None = None
    index = 0
    guard = StrayProcessGuard(EXCEL_PROCESS_NAMES)
    while pending is None or index < len(pending):
        with com_apartment(None):
            application = None
            try:
                application = client.DispatchEx("Excel.Application")
                application.Visible = True
                application.DisplayAlerts = False
                workbook = application.Workbooks.Add()
                sheet = workbook.Sheets(1)
                window = int(application.Hwnd)
                _, process_id = win32process.GetWindowThreadProcessId(window)
                if pending is None:
                    pending = [
                        item
                        for item in enumerate_commands(application)
                        if is_probeable(item)
                    ]
                    print("probeable:", len(pending), flush=True)
                with DialogWatchdog(process_id, window) as watchdog:
                    while index < len(pending):
                        item = pending[index]
                        try:
                            record = _classify(
                                application, sheet, watchdog, item
                            )
                        except Exception as error:
                            record = {
                                **item,
                                "outcome": "destabilised",
                                "undoable": None,
                                "detail": type(error).__name__,
                            }
                        records.append(record)
                        index += 1
                        line = (
                            f"  {record['outcome']:16s} "
                            f"{'undo' if record['undoable'] else '    '} "
                            f"{record['caption'][:26]}"
                        )
                        # A caption Excel can hold but this console cannot
                        # print must not end the run.
                        print(
                            line.encode("ascii", "backslashreplace").decode(),
                            flush=True,
                        )
                        if record["outcome"] == "destabilised":
                            break
                try:
                    workbook.Close(SaveChanges=False)
                except Exception:
                    pass
            finally:
                if application is not None:
                    try:
                        application.Quit()
                    except Exception:
                        pass
        if pending is not None and index >= len(pending):
            break
        guard.require_clear()

    guard.require_clear()

    counts: dict[str, int] = {}
    for record in records:
        counts[record["outcome"]] = counts.get(record["outcome"], 0) + 1
    return {
        "schema_version": 1,
        "total": len(records),
        "counts": counts,
        "undoable": sum(1 for record in records if record.get("undoable")),
        "records": records,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", default=str(REPORT_PATH))
    args = parser.parse_args(argv)

    report = run()
    Path(args.report).write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("\ntotal:", report["total"])
    for outcome, count in sorted(report["counts"].items(), key=lambda x: -x[1]):
        print(f"  {outcome}: {count}")
    print("  undoable:", report["undoable"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
