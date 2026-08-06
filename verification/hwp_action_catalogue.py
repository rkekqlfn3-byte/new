"""Find out which 한글 ribbon commands can actually be automated.

한글 exposes its ribbon through ``HAction.Run("<name>")``, which would cover
far more of the ribbon than writing an operation per command.  The reason
that is not simply done is that guessing is dangerous: while adding table
support, two guessed names opened a modal dialog that blocked automation
until the 한글 process had to be killed, and a third silently did nothing.

So the names are not trusted, they are measured.  Every candidate runs
against a real 한글 on a fresh document and is recorded as one of:

``changed``   the document differed afterwards — usable
``undoable``  ``changed`` and a single Undo put it back — usable and safe
``dialog``    it opened a window a person has to answer — not automatable
``noop``      it returned success and changed nothing — not usable
``failed``    it returned False
``error``     the call raised

A watchdog thread closes any window that appears, so a modal cannot hang
the run; the caller still bounds the whole thing and cleans up any 한글
process left behind.

This writes a catalogue, not a feature.  What it is for is deciding whether
covering the ribbon this way is realistic before any of it is built.
"""

from __future__ import annotations

import argparse
import json
import threading
import time
from pathlib import Path

from verification.stray_processes import HWP_PROCESS_NAMES, StrayProcessGuard

REPORT_PATH = Path(__file__).with_name("hwp_action_catalogue.json")

# Never probed. Printing sends paper out of a real printer; the file and quit
# families write to disk or close the document out from under the run; macro
# playback executes whatever happens to be recorded. None of these can be
# judged by "did the document change", and getting one wrong costs the user
# something outside this process.
NEVER_PROBE = frozenset({
    "Print", "PrintPreview", "FilePrint", "FileQuit", "FileClose",
    "FileSave", "FileSaveAs", "FileSaveAll", "FileNew", "FileOpen",
    "Exit", "Quit", "MacroPlay", "MacroRepeat", "MacroPause", "ScriptRun",
    "Mailing", "MailMerge", "SendMail", "FileVersion", "RecoverFile",
})

# What the document must look like for a group's verdict to mean anything.
# The first run judged the table commands against a document containing no
# table, so all six failures said nothing about those actions.
SETUP_TEXT = "text"
SETUP_TABLE = "table"

# Candidates drawn from the ribbon tabs a reader actually sees. None of these
# is assumed to work — that is the whole question this probe answers.
CANDIDATES: dict[str, tuple[str, tuple[str, ...]]] = {
    "글자 모양": (SETUP_TEXT, (
        "CharShapeBold", "CharShapeItalic", "CharShapeUnderline",
        "CharShapeStrikeout", "CharShapeSuperscript", "CharShapeSubscript",
        "CharShapeHeight", "CharShapeHeightIncrease", "CharShapeHeightDecrease",
        "CharShapeTextColorRed", "CharShapeTextColorBlue",
        "CharShapeTextColorBlack", "CharShapeOutline", "CharShapeShadow",
        "CharShapeEmboss", "CharShapeEngrave", "CharShapeSmallCaps",
        "CharShapeSpacingIncrease", "CharShapeSpacingDecrease",
        "CharShapeWidthIncrease", "CharShapeWidthDecrease",
        "CharShapeNormal", "CharShapeSuperscriptOrNormal",
        "CharShapeSubscriptOrNormal", "CharShapeUnderlineBottom",
        "CharShapeCenterline", "CharShapeShadeColor", "CharShapeFontName",
        "CharShapeTypeface", "CharShapeLang",
    )),
    "문단 모양": (SETUP_TEXT, (
        "ParagraphShapeAlignLeft", "ParagraphShapeAlignCenter",
        "ParagraphShapeAlignRight", "ParagraphShapeAlignJustify",
        "ParagraphShapeAlignDistribute", "ParagraphShapeAlignDivision",
        "ParagraphShapeIndentPositive", "ParagraphShapeIndentNegative",
        "ParagraphShapeIndentAtCaret", "ParagraphShapeDecreaseMargin",
        "ParagraphShapeIncreaseMargin", "ParagraphShapeDecreaseLineSpacing",
        "ParagraphShapeIncreaseLineSpacing", "ParagraphShapeProtect",
        "ParagraphShapeKeepLinesTogether", "ParagraphShapeWithNext",
        "ParagraphShapePageBreakBefore", "ParagraphShapeWidowOrphan",
    )),
    "편집": (SETUP_TEXT, (
        "Copy", "Cut", "Paste", "PasteSpecial", "SelectAll", "Undo", "Redo",
        "DeleteBack", "Delete", "Erase", "DeleteLine", "DeleteLineEnd",
        "DeleteWord", "DeleteWordBack", "Select", "SelectColumn",
        "SelectLine", "SelectWord", "SelectParagraph", "CopyPage",
        "DeletePage", "PastePage", "SwapCase",
    )),
    "입력": (SETUP_TEXT, (
        "BreakPage", "BreakColumn", "BreakLine", "BreakSection",
        "InsertFootnote", "InsertEndnote", "InsertFieldDate",
        "InsertFieldTime", "InsertFieldDateCode", "InsertPageNumber",
        "InsertLine", "InsertSoftHyphen", "InsertFixedWidthSpace",
        "InsertNonBreakingSpace", "InsertDateCode", "InsertCpNo",
        "HyperlinkInsert", "HyperlinkDelete", "InsertFieldMemo",
        "DeleteFieldMemo", "InsertBookmark", "InsertCrossRef",
        "InsertAutoNum", "InsertSpace", "InsertTab", "InsertEndnoteNum",
        "InsertFootnoteNum",
    )),
    "서식": (SETUP_TEXT, (
        "StyleShortcut1", "StyleShortcut2", "StyleShortcut3",
        "StyleTemplate", "StyleClearCharStyle", "CopyShape", "PasteShape",
        "FormatNormal", "ParagraphNumberBullet", "Numbering", "Bullet",
        "OutlineNumber", "StyleCurrent",
    )),
    "쪽": (SETUP_TEXT, (
        "PageNumPos", "PageNumberInsert", "PageHiding", "HeaderFooter",
        "PageBorderFill", "MultiColumn", "PageSetup", "ColumnDelete",
        "HeaderDelete", "FooterDelete", "PageNumberDelete", "Watermark",
        "SectionDefine", "SectionDelete", "PagePosition",
    )),
    "보기": (SETUP_TEXT, (
        "ViewOptionParagraphMark", "ViewOptionCtrlMark", "ViewOptionGuideLine",
        "ViewOptionMemo", "ViewOptionPicture", "ViewZoomFitWidth",
        "ViewZoomFitPage", "ViewZoomNormal", "ViewZoomRibon", "ViewIdiom",
        "ViewTabBar", "ViewStatusBar", "ViewRuler", "FullScreen",
        "SplitMainWindow", "SplitAll", "SplitHorz", "SplitVert",
    )),
    "검토": (SETUP_TEXT, (
        "SpellingCheck", "WordCount", "TrackChangeApply", "TrackChangeView",
        "CommentInsert", "CommentDelete", "CommentModify", "CommentNext",
        "CommentPrev", "HanjaAutoConvert", "HanjaFromHangul",
        "HangulFromHanja", "Translate", "AutoSpell", "AutoChangeHangul",
    )),
    "도구": (SETUP_TEXT, (
        "QuickCorrect", "QuickCorrectRun", "QuickCorrectSound",
        "FindDlg", "RepeatFind", "RepeatFindBack", "GotoDlg",
        "SortDlg", "Calculate", "CalculateBlock", "CharCount",
        "DocumentInfo", "PrivateInfoProtect",
    )),
    "이동": (SETUP_TEXT, (
        "MoveDocBegin", "MoveDocEnd", "MoveLineBegin", "MoveLineEnd",
        "MovePageDown", "MovePageUp", "MoveNextParaBegin",
        "MovePrevParaBegin", "MoveNextWord", "MovePrevWord",
        "MoveScrollUp", "MoveScrollDown", "MoveTopLevelBegin",
        "MoveTopLevelEnd", "MoveViewBegin", "MoveViewEnd",
    )),
    "표": (SETUP_TABLE, (
        "TableCellBorderAll", "TableCellBorderNone", "TableCellBorderOutside",
        "TableCellBorderInside", "TableCellBorderLeft", "TableCellBorderRight",
        "TableCellBorderTop", "TableCellBorderBottom",
        "TableAutoFitContents", "TableAutoFitWindow",
        "TableDistributeCellWidth", "TableDistributeCellHeight",
        "TableSubtractRow", "TableSubtractCol", "TableAppendRow",
        "TableAppendCol", "TableInsertLeftColumn", "TableInsertRightColumn",
        "TableInsertUpperRow", "TableInsertLowerRow", "TableDeleteRow",
        "TableDeleteColumn", "TableMergeCell", "TableSplitCell",
        "TableCellBlock", "TableCellBlockRow", "TableCellBlockCol",
        "TableColBegin", "TableColEnd", "TableCellAlignCenter",
        "TableCellAlignLeft", "TableCellAlignRight", "TableFormula",
        "TableFormulaSumAuto", "TableFormulaAvgAuto",
    )),
}

SEED_TEXT = "가나다라마바사아자차카타파하 1234567890"


class DialogWatchdog:
    """Close any window 한글 opens, so a modal cannot stop the run.

    A modal dialog runs its own message loop, so the thread that called
    ``Run`` is blocked inside it and cannot close anything.  This watches
    from outside and posts the close, which is the only way back.
    """

    CLASS_DIALOG = "#32770"

    def __init__(self, process_id: int, main_window: int):
        self._process_id = int(process_id)
        self._main_window = int(main_window)
        self._stop = threading.Event()
        self._seen = 0
        self._thread: threading.Thread | None = None

    @property
    def dialogs_closed(self) -> int:
        return self._seen

    def reset(self) -> None:
        self._seen = 0

    def _unexpected_windows(self):
        import win32con  # noqa: F401  (imported for callers' clarity)
        import win32gui
        import win32process

        found = []

        def visit(handle, _):
            if not win32gui.IsWindowVisible(handle):
                return True
            if handle == self._main_window:
                return True
            try:
                _, process_id = win32process.GetWindowThreadProcessId(handle)
            except Exception:
                return True
            if int(process_id) != self._process_id:
                return True
            found.append(handle)
            return True

        try:
            win32gui.EnumWindows(visit, None)
        except Exception:
            return []
        return found

    def _run(self) -> None:
        import win32con
        import win32gui

        while not self._stop.wait(0.15):
            for handle in self._unexpected_windows():
                self._seen += 1
                try:
                    win32gui.PostMessage(handle, win32con.WM_CLOSE, 0, 0)
                except Exception:
                    continue

    def __enter__(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
        return False


# Reading the text alone reports 굵게 as doing nothing, because bold is not
# in the text. The first run of this probe made exactly that mistake, so the
# snapshot carries the character and paragraph shapes as well.
CHAR_FIELDS = (
    "Bold", "Italic", "UnderlineType", "StrikeOutType", "SuperScript",
    "SubScript", "Height", "TextColor", "ShadeColor", "Spacing",
    "OutLineType", "ShadowType", "FaceNameHangul",
)
PARA_FIELDS = (
    "AlignType", "HeadingType", "LineSpacing", "LineSpacingType",
    "LeftMargin", "RightMargin", "Indentation",
)


def _shape_state(hwp, action_name: str, set_name: str, fields) -> tuple:
    try:
        shape = getattr(hwp.HParameterSet, set_name)
        hwp.HAction.GetDefault(action_name, shape.HSet)
    except Exception:
        return ()
    values = []
    for field in fields:
        try:
            values.append((field, int(getattr(shape, field))))
        except Exception:
            continue
    return tuple(values)


def _document_state(hwp) -> tuple:
    """Enough of the document to tell whether a command did anything."""
    try:
        text = str(hwp.GetTextFile("UNICODE", "") or "")
    except Exception:
        text = ""
    # The caret is deliberately not part of this. Counting where the caret
    # sits as a change reported BreakLine and CharShapeSpacingIncrease as
    # working when neither altered the document, and both then failed once
    # they were wired to an operation that reads the document instead.
    return (
        text,
        _shape_state(hwp, "CharShape", "HCharShape", CHAR_FIELDS),
        _shape_state(hwp, "ParagraphShape", "HParaShape", PARA_FIELDS),
    )


def _seed(hwp, setup: str) -> None:
    """Put the document into the state the group's verdict assumes."""
    hwp.XHwpDocuments.Item(0).Clear(option=1)
    action = hwp.CreateAction("InsertText")
    parameters = action.CreateSet()
    parameters.SetItem("Text", SEED_TEXT)
    action.Execute(parameters)
    if setup == SETUP_TABLE:
        table = hwp.HParameterSet.HTableCreation
        hwp.HAction.GetDefault("TableCreate", table.HSet)
        table.Rows, table.Cols = 3, 3
        table.CreateItemArray("ColWidth", 3)
        table.CreateItemArray("RowHeight", 3)
        hwp.HAction.Execute("TableCreate", table.HSet)
        return
    hwp.HAction.Run("MoveDocBegin")
    hwp.HAction.Run("SelectAll")


def _classify(hwp, watchdog, name: str, setup: str = SETUP_TEXT) -> dict:
    """Run one candidate on a prepared document and say what it did."""
    _seed(hwp, setup)
    before = _document_state(hwp)
    watchdog.reset()

    started = time.monotonic()
    try:
        returned = bool(hwp.HAction.Run(name))
        outcome = None
    except Exception as error:
        returned = False
        outcome = f"error:{type(error).__name__}"
    elapsed = round(time.monotonic() - started, 2)

    # Give the watchdog a beat to notice a window opened right at the end.
    time.sleep(0.2)
    dialogs = watchdog.dialogs_closed
    if outcome is None:
        if dialogs:
            outcome = "dialog"
        elif not returned:
            outcome = "failed"
        else:
            outcome = "changed" if _document_state(hwp) != before else "noop"

    undoable = None
    if outcome == "changed":
        try:
            hwp.HAction.Run("Undo")
            undoable = _document_state(hwp) == before
        except Exception:
            undoable = False
    return {
        "action": name,
        "outcome": outcome,
        "undoable": undoable,
        "dialogs": dialogs,
        "seconds": elapsed,
    }




def run_catalogue(groups=None, on_progress=None) -> dict:
    from engine.app_actions.com_lifecycle import com_apartment
    from engine.app_actions.hwp_adapter import create_owned_hwp_application
    from engine.workflows.business_workflow import _registered_hwp_security_module

    wanted = {
        name: items
        for name, items in CANDIDATES.items()
        if not groups or name in groups
    }
    pending = [
        (group, setup, name)
        for group, (setup, names) in wanted.items()
        for name in names
        if name not in NEVER_PROBE
    ]
    records: list[dict] = []
    index = 0
    restarts = 0
    # Some commands leave 한글 in a state where even clearing the document
    # fails — a split window and full screen both do it. That is a verdict
    # about the command, not a reason to lose the remaining candidates, so
    # the session restarts and the run carries on past it.
    #
    # The old process has to be gone before a new one starts. Asking it to
    # close and carrying on regardless is how one run ended with 23 한글
    # processes still resident: each restart left its predecessor behind.
    # require_clear stops the run instead, which loses the remaining
    # candidates but leaves nothing hidden on the reader's desktop.
    guard = StrayProcessGuard(HWP_PROCESS_NAMES)
    while index < len(pending):
        if restarts:
            guard.require_clear()
            time.sleep(2.0)
        with com_apartment(None):
            lease = None
            try:
                lease = create_owned_hwp_application()
                hwp = lease.application
                hwp.RegisterModule(
                    "FilePathCheckDLL", _registered_hwp_security_module()
                )
                try:
                    hwp.XHwpWindows.Item(0).Visible = True
                except Exception:
                    # A restart races the previous process shutting down, and
                    # a window that will not show is not a reason to lose the
                    # candidates that have not been tried yet.
                    pass
                window = int(hwp.XHwpWindows.Active_XHwpWindow.WindowHandle)
                import win32process

                _, process_id = win32process.GetWindowThreadProcessId(window)
                with DialogWatchdog(process_id, window) as watchdog:
                    while index < len(pending):
                        group, setup, name = pending[index]
                        try:
                            record = _classify(hwp, watchdog, name, setup)
                        except Exception as error:
                            record = {
                                "action": name,
                                "outcome": "destabilised",
                                "undoable": None,
                                "dialogs": 0,
                                "seconds": 0.0,
                                "detail": type(error).__name__,
                            }
                            restarts += 1
                        record["group"] = group
                        records.append(record)
                        index += 1
                        print(
                            f"  {record['outcome']:12s} "
                            f"{'undo' if record['undoable'] else '    '} "
                            f"{group} / {name}",
                            flush=True,
                        )
                        if on_progress is not None and index % 25 == 0:
                            on_progress(records)
                        if record["outcome"] == "destabilised":
                            break
            finally:
                if lease is not None:
                    try:
                        lease.cleanup()
                    except Exception:
                        pass

    # A finished run still owns whatever it started. The last session is
    # closed by its lease above, but a 한글 that refused to go is exactly
    # the case this has to report rather than leave for the reader to find.
    guard.require_clear()

    counts: dict[str, int] = {}
    for record in records:
        counts[record["outcome"]] = counts.get(record["outcome"], 0) + 1
    usable = [r for r in records if r["outcome"] == "changed"]
    return {
        "schema_version": 2,
        "restarts": restarts,
        "total": len(records),
        "counts": counts,
        "undoable": sum(1 for r in usable if r["undoable"]),
        "records": records,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--group", action="append", dest="groups")
    parser.add_argument(
        "--names-file",
        help="Probe names read out of 한글 itself (verification.hwp_action_names)",
    )
    parser.add_argument("--report", default=str(REPORT_PATH))
    args = parser.parse_args(argv)

    if args.names_file:
        supplied = json.loads(Path(args.names_file).read_text(encoding="utf-8"))
        names = tuple(supplied.get("names") or supplied)
        CANDIDATES.clear()
        CANDIDATES["한글 목록"] = (SETUP_TEXT, names)
    destination = Path(args.report)

    def save(records):
        """Keep partial results: a four-hour run must survive a crash."""
        destination.write_text(
            json.dumps(
                {"schema_version": 2, "partial": True, "records": records},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    report = run_catalogue(args.groups, on_progress=save)
    destination.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("\ntotal:", report["total"])
    for outcome, count in sorted(report["counts"].items()):
        print(f"  {outcome}: {count}")
    print("  undoable:", report["undoable"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
