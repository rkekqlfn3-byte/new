"""Find which 한글 dialogs can be driven without the dialog.

``HAction.Run`` opens a window for 183 of the commands the action catalogue
tried, and a window is where automation stops: it can be opened but only a
person can answer it.  That is not the whole story, though — 쪽 설정 is one
of those commands and Jarvis already performs it, by filling in the same
parameters the dialog would have collected and executing with them:

    pset = hwp.HParameterSet.HSecDef
    hwp.HAction.GetDefault("PageSetup", pset.HSet)
    pset.PageDef.LeftMargin = ...
    hwp.HAction.Execute("PageSetup", pset.HSet)

The missing piece was knowing which parameter set belongs to which command.
한글 answers that itself: ``CreateAction(name).SetID`` returns it, so
``MultiColumn`` says ``ColDef`` and ``InsertHyperlink`` says ``HyperLink``.
No guessing is involved.

This probe asks, for every command that opened a dialog, whether
``GetDefault`` then ``Execute`` runs it without one.  It does not try to set
any parameter — a command that cannot even be executed with its own defaults
will not become reachable by filling fields in, and one that can is worth
the per-feature work of finding out what its fields mean.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from verification.hwp_action_catalogue import (
    SETUP_TEXT,
    DialogWatchdog,
    _document_state,
    _seed,
)
from verification.stray_processes import HWP_PROCESS_NAMES, StrayProcessGuard

REPORT_PATH = Path(__file__).with_name("hwp_parameter_catalogue.json")


def _parameter_set(hwp, set_id: str):
    """The parameter object 한글 named for this command, or nothing."""
    if not set_id:
        return None
    return getattr(hwp.HParameterSet, f"H{set_id}", None)


def _classify(hwp, watchdog, name: str) -> dict:
    """Whether this command runs from its own defaults, without a dialog."""
    record = {"action": name, "set_id": "", "outcome": "unknown"}
    try:
        action = hwp.CreateAction(name)
        set_id = str(action.SetID or "")
    except Exception as error:
        record["outcome"] = f"error:{type(error).__name__}"
        return record
    record["set_id"] = set_id
    if not set_id:
        # No parameter set means there is nothing for a caller to fill in;
        # the dialog is the only way this command collects its input.
        record["outcome"] = "no_parameter_set"
        return record

    _seed(hwp, SETUP_TEXT)
    parameters = _parameter_set(hwp, set_id)
    if parameters is None:
        record["outcome"] = "set_unavailable"
        return record
    before = _document_state(hwp)
    watchdog.reset()
    try:
        hwp.HAction.GetDefault(name, parameters.HSet)
    except Exception as error:
        record["outcome"] = f"default_failed:{type(error).__name__}"
        return record
    try:
        returned = bool(hwp.HAction.Execute(name, parameters.HSet))
    except Exception as error:
        record["outcome"] = f"execute_failed:{type(error).__name__}"
        return record

    time.sleep(0.2)
    if watchdog.dialogs_closed:
        record["outcome"] = "still_dialog"
        return record
    if not returned:
        record["outcome"] = "refused"
        return record
    changed = _document_state(hwp) != before
    record["outcome"] = "parameterised" if changed else "noop"
    if changed:
        try:
            hwp.HAction.Run("Undo")
            record["undoable"] = _document_state(hwp) == before
        except Exception:
            record["undoable"] = False
    return record


def run(names) -> dict:
    from engine.app_actions.com_lifecycle import com_apartment
    from engine.app_actions.hwp_adapter import create_owned_hwp_application
    from engine.workflows.business_workflow import _registered_hwp_security_module

    records: list[dict] = []
    index = 0
    restarts = 0
    guard = StrayProcessGuard(HWP_PROCESS_NAMES)
    # A command that kills 한글 made every one of the 120 candidates after it
    # report the same COM error, which read as 120 verdicts and was one.
    while index < len(names):
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
                    pass
                window = int(hwp.XHwpWindows.Active_XHwpWindow.WindowHandle)
                import win32process

                _, process_id = win32process.GetWindowThreadProcessId(window)
                with DialogWatchdog(process_id, window) as watchdog:
                    while index < len(names):
                        name = names[index]
                        try:
                            record = _classify(hwp, watchdog, name)
                        except Exception as error:
                            record = {
                                "action": name,
                                "outcome": f"crashed:{type(error).__name__}",
                            }
                        records.append(record)
                        index += 1
                        print(
                            f"  {record['outcome']:22s} "
                            f"{record.get('set_id',''):16s} {name}",
                            flush=True,
                        )
                        broken = record["outcome"].startswith(
                            ("crashed", "error:", "execute_failed", "default_failed")
                        )
                        if broken:
                            restarts += 1
                            break
            finally:
                if lease is not None:
                    try:
                        lease.cleanup()
                    except Exception:
                        pass

    guard.require_clear()

    counts: dict[str, int] = {}
    for record in records:
        counts[record["outcome"]] = counts.get(record["outcome"], 0) + 1
    return {"schema_version": 2, "restarts": restarts,
            "total": len(records), "counts": counts, "records": records}


def dialog_actions(path) -> tuple[str, ...]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return tuple(
        record["action"]
        for record in data["records"]
        if record.get("outcome") == "dialog"
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from-catalogue", required=True)
    parser.add_argument("--report", default=str(REPORT_PATH))
    args = parser.parse_args(argv)

    names = dialog_actions(args.from_catalogue)
    print("dialog commands:", len(names))
    report = run(names)
    Path(args.report).write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("\ntotal:", report["total"])
    for outcome, count in sorted(report["counts"].items(), key=lambda x: -x[1]):
        print(f"  {outcome}: {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
