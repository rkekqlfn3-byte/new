"""Find which Excel ribbon commands can actually be automated.

Excel exposes its ribbon through ``CommandBars.ExecuteMso(idMso)``, the same
shape as 한글's ``HAction.Run``, and it is a better place to start: Excel
also answers ``GetEnabledMso(idMso)``, which says whether a command exists
and is available **without running it**.  An invented name raises there, so
the candidate list can be validated before anything touches a workbook.

The names still are not invented.  They are read out of the installed Office
modules, and only the ones Excel itself accepts are kept — 181 of 37,666
extracted strings, which is the honest measure of how much of Excel's
command set lives in those binaries as plain text.  It is far from all of
it; a name Excel does not confirm is not probed on the chance that it works.

Each surviving command runs on a seeded workbook and is recorded as one of:

``changed``   the sheet differed afterwards — usable
``undoable``  ``changed`` and a single Undo put it back — usable and safe
``dialog``    it opened a window a person has to answer — not automatable
``noop``      it ran and changed nothing measurable
``failed``    the call raised

Nothing is saved and the workbook is closed without saving, so the run
cannot leave a file behind.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
from pathlib import Path

REPORT_PATH = Path(__file__).with_name("excel_command_catalogue.json")

OFFICE_ROOTS = (
    r"C:\Program Files\Microsoft Office\root\Office16",
    r"C:\Program Files (x86)\Microsoft Office\root\Office16",
)

# Commands that must never be run to find out what they do. Printing puts
# paper through a real printer; the file family writes to disk or replaces
# the workbook under the run; macro recording and code windows execute or
# expose whatever is there; the import and query families reach the network.
NEVER_PROBE = frozenset({
    "FileNew", "FileOpen", "FileOpenUsingBackstage", "FileSave", "FileSaveAs",
    "FileClose", "FilePrint", "PrintPreview", "FilePrintQuick", "FileExit",
    "MacroRecord", "MacroPlay", "ViewCode", "ViewScript", "AdvertisePublishAs",
    "WebPagePreview", "Camera", "AddInManager", "ImportAccessFileLegacy",
    "ImportTextFileLegacy", "NewWebQueryLegacy", "XlNewDcwConnLegacy",
    "XlNewOdataConnLegacy", "XlNewSqlConnLegacy", "XlNewXmlConnLegacy",
    "PowerQueryDataFromKusto", "PowerQueryLaunchQueryEditor",
    "PowerQueryRecentSources", "GetTransformExistingConnections",
    "RefreshServerFile", "RefreshAll", "ConvertToExcelValues", "PythonObject",
    "SpeakOnEnter", "Translate", "Translator", "TranslationPane",
    "AccessibilityChecker",
})

_ASCII = re.compile(rb"([A-Z][A-Za-z0-9]{4,40})\x00")
_WIDE = re.compile(rb"((?:[A-Za-z0-9]\x00){5,41})\x00\x00")

# A small block with values, a formula and a couple of formats, so that most
# commands have something to act on and something to show afterwards.
SEED = (
    ("A1", "이름"), ("B1", "수량"),
    ("A2", "가"), ("B2", 3),
    ("A3", "나"), ("B3", 1),
    ("A4", "가"), ("B4", 2),
)
SELECTION = "A1:B4"


def _office_module_paths():
    for root in OFFICE_ROOTS:
        base = Path(root)
        if not base.is_dir():
            continue
        for name in os.listdir(base):
            if name.lower().endswith((".dll", ".exe")):
                yield base / name


def extracted_names() -> tuple[str, ...]:
    """Candidate identifiers found in the installed Office modules."""
    found: set[str] = set()
    for path in _office_module_paths():
        try:
            if path.stat().st_size > 250 * 1024 * 1024:
                continue
            blob = path.read_bytes()
        except OSError:
            continue
        if b"FreezePanes" not in blob and b"F\x00r\x00e\x00e\x00z\x00e" not in blob:
            continue
        found.update(
            match.group(1).decode("ascii") for match in _ASCII.finditer(blob)
        )
        for match in _WIDE.finditer(blob):
            try:
                found.add(match.group(1).decode("utf-16-le"))
            except UnicodeDecodeError:
                continue
    return tuple(sorted(name for name in found if name[:1].isupper()))


def _sheet_state(sheet) -> tuple:
    """Enough of the sheet to tell whether a command did anything."""
    values = []
    for row in range(1, 7):
        for column in range(1, 4):
            cell = sheet.Cells(row, column)
            try:
                values.append(str(cell.Value))
            except Exception:
                values.append("")
    marks = []
    for name in ("A1", "A2", "B2"):
        cell = sheet.Range(name)
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
        ):
            try:
                marks.append(str(reader(cell)))
            except Exception:
                marks.append("")
    try:
        marks.append(str(sheet.Columns(1).ColumnWidth))
        marks.append(str(sheet.Rows(1).RowHeight))
    except Exception:
        pass
    return (tuple(values), tuple(marks))


def _seed(sheet, application) -> None:
    sheet.Cells.Clear()
    for address, value in SEED:
        sheet.Range(address).Value = value
    try:
        application.ActiveWindow.FreezePanes = False
    except Exception:
        pass
    sheet.Range(SELECTION).Select()


def _classify(application, workbook, sheet, watchdog, name: str) -> dict:
    _seed(sheet, application)
    before = _sheet_state(sheet)
    watchdog.reset()
    started = time.monotonic()
    try:
        application.CommandBars.ExecuteMso(name)
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
        "command": name,
        "outcome": outcome,
        "undoable": undoable,
        "dialogs": dialogs,
        "seconds": elapsed,
    }


def run(names=None) -> dict:
    import win32com.client as client
    import win32process

    from engine.app_actions.com_lifecycle import com_apartment
    from verification.hwp_action_catalogue import DialogWatchdog

    records: list[dict] = []
    index = 0
    pending: list[str] | None = None
    # A command can leave Excel refusing every call that follows, which made
    # one bad name look like the end of the run. Restart and carry on past it.
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
                bars = application.CommandBars

                if pending is None:
                    candidates = names if names is not None else extracted_names()
                    pending = []
                    for name in candidates:
                        if name in NEVER_PROBE:
                            continue
                        try:
                            bars.GetEnabledMso(name)
                        except Exception:
                            continue
                        pending.append(name)
                    print("validated:", len(pending), flush=True)

                with DialogWatchdog(process_id, window) as watchdog:
                    while index < len(pending):
                        name = pending[index]
                        try:
                            record = _classify(
                                application, workbook, sheet, watchdog, name
                            )
                        except Exception as error:
                            record = {
                                "command": name,
                                "outcome": "destabilised",
                                "undoable": None,
                                "dialogs": 0,
                                "seconds": 0.0,
                                "detail": type(error).__name__,
                            }
                        records.append(record)
                        index += 1
                        print(
                            f"  {record['outcome']:16s} "
                            f"{'undo' if record['undoable'] else '    '} {name}",
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
