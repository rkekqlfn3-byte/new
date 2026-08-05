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

REPORT_PATH = Path(__file__).with_name("hwp_action_catalogue.json")

# Candidates drawn from the ribbon tabs a reader actually sees. None of these
# is assumed to work — that is the whole question this probe answers.
CANDIDATES: dict[str, tuple[str, ...]] = {
    "글자 모양": (
        "CharShapeBold", "CharShapeItalic", "CharShapeUnderline",
        "CharShapeStrikeout", "CharShapeSuperscript", "CharShapeSubscript",
        "CharShapeHeight", "CharShapeHeightIncrease", "CharShapeHeightDecrease",
        "CharShapeTextColorRed", "CharShapeOutline", "CharShapeShadow",
        "CharShapeSpacingIncrease", "CharShapeSpacingDecrease",
    ),
    "문단 모양": (
        "ParagraphShapeAlignLeft", "ParagraphShapeAlignCenter",
        "ParagraphShapeAlignRight", "ParagraphShapeAlignJustify",
        "ParagraphShapeAlignDistribute",
        "ParagraphShapeIndentPositive", "ParagraphShapeIndentNegative",
        "ParagraphShapeLineSpacingIncrease",
        "ParagraphShapeLineSpacingDecrease",
    ),
    "편집": (
        "Copy", "Cut", "Paste", "SelectAll", "Undo", "Redo",
        "DeleteBack", "Delete", "Erase",
    ),
    "입력": (
        "BreakPage", "BreakColumn", "BreakLine",
        "InsertFootnote", "InsertEndnote", "InsertFieldDate",
        "InsertFieldTime", "InsertPageNumber", "InsertLine",
        "HyperlinkInsert", "InsertFieldMemo",
    ),
    "쪽": (
        "PageNumPos", "PageNumberInsert", "HeaderFooter",
        "PageBorderFill", "MultiColumn", "PageSetup",
    ),
    "보기": (
        "ViewOptionParagraphMark", "ViewOptionCtrlMark",
        "ViewZoomFitWidth", "ViewZoomFitPage", "ViewZoomNormal",
    ),
    "검토": (
        "SpellingCheck", "WordCount", "TrackChangeApply",
        "CommentInsert", "CommentDelete",
    ),
    "이동": (
        "MoveDocBegin", "MoveDocEnd", "MoveLineBegin", "MoveLineEnd",
        "MovePageDown", "MovePageUp", "MoveNextParaBegin",
    ),
    "표": (
        "TableCellBorderAll", "TableCellBorderNone",
        "TableAutoFitContents", "TableAutoFitWindow",
        "TableDistributeCellWidth", "TableDistributeCellHeight",
    ),
    "스타일": (
        "StyleShortcut1", "StyleShortcut2", "StyleTemplate",
    ),
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


def _classify(hwp, watchdog, name: str) -> dict:
    """Run one candidate on a clean document and say what it did."""
    hwp.XHwpDocuments.Item(0).Clear(option=1)
    action = hwp.CreateAction("InsertText")
    parameters = action.CreateSet()
    parameters.SetItem("Text", SEED_TEXT)
    action.Execute(parameters)
    hwp.HAction.Run("MoveDocBegin")
    hwp.HAction.Run("SelectAll")
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


def run_catalogue(groups=None) -> dict:
    from engine.app_actions.com_lifecycle import com_apartment
    from engine.app_actions.hwp_adapter import create_owned_hwp_application
    from engine.workflows.business_workflow import _registered_hwp_security_module

    wanted = {
        name: items
        for name, items in CANDIDATES.items()
        if not groups or name in groups
    }
    records: list[dict] = []
    # Cleanup must happen inside the apartment: once it exits, CoUninitialize
    # invalidates the proxy and Quit can no longer be delivered.
    with com_apartment(None):
        lease = None
        try:
            lease = create_owned_hwp_application()
            hwp = lease.application
            hwp.RegisterModule(
                "FilePathCheckDLL", _registered_hwp_security_module()
            )
            hwp.XHwpWindows.Item(0).Visible = True
            window = int(hwp.XHwpWindows.Active_XHwpWindow.WindowHandle)
            import win32process

            _, process_id = win32process.GetWindowThreadProcessId(window)
            with DialogWatchdog(process_id, window) as watchdog:
                for group, names in wanted.items():
                    for name in names:
                        record = _classify(hwp, watchdog, name)
                        record["group"] = group
                        records.append(record)
                        print(
                            f"  {record['outcome']:8s} "
                            f"{'undo' if record['undoable'] else '    '} "
                            f"{group} / {name}",
                            flush=True,
                        )
        finally:
            if lease is not None:
                try:
                    lease.cleanup()
                except Exception:
                    pass

    counts: dict[str, int] = {}
    for record in records:
        counts[record["outcome"]] = counts.get(record["outcome"], 0) + 1
    usable = [r for r in records if r["outcome"] == "changed"]
    return {
        "schema_version": 1,
        "total": len(records),
        "counts": counts,
        "undoable": sum(1 for r in usable if r["undoable"]),
        "records": records,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--group", action="append", dest="groups")
    parser.add_argument("--report", default=str(REPORT_PATH))
    args = parser.parse_args(argv)

    report = run_catalogue(args.groups)
    Path(args.report).write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("\ntotal:", report["total"])
    for outcome, count in sorted(report["counts"].items()):
        print(f"  {outcome}: {count}")
    print("  undoable:", report["undoable"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
