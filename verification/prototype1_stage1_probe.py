"""Prototype 1.0 stage-1 Office/HWP feasibility and ownership probe.

The default inventory mode is read-only.  Live mode refuses to touch an
application when a matching user process is already running, creates only a
dedicated blank document, verifies selection/read/write/undo behavior, and
then closes the application it created.
"""

from __future__ import annotations

import argparse
import gc
import hashlib
import json
import os
import platform
import re
import struct
import subprocess
import sys
import tempfile
import time
import uuid
import winreg
from datetime import datetime
from pathlib import Path

import psutil

from engine.version import runtime_info

ROOT = Path(__file__).resolve().parent.parent
REPORT_PATH = ROOT / "verification" / "prototype1_stage1_report.json"

APP_SPECS = {
    "excel": {
        "display_name": "Microsoft Excel",
        "progid": "Excel.Application",
        "process_names": ("excel.exe",),
    },
    "hwp": {
        "display_name": "한컴 한글",
        "progid": "HWPFrame.HwpObject",
        "process_names": ("hwp.exe", "hwp64.exe"),
    },
    "word": {
        "display_name": "Microsoft Word",
        "progid": "Word.Application",
        "process_names": ("winword.exe",),
    },
    "powerpoint": {
        "display_name": "Microsoft PowerPoint",
        "progid": "PowerPoint.Application",
        "process_names": ("powerpnt.exe",),
    },
}

REQUIRED_LIVE_CHECKS = {
    "excel": (
        "dedicated_instance",
        "selection_read",
        "temporary_edit_verified",
        "undo_verified",
        "save_reopen_verified",
        "process_cleanup_verified",
    ),
    "hwp": (
        "dedicated_instance",
        "selection_read",
        "temporary_edit_verified",
        "undo_verified",
        "rot_cleanup_verified",
        "process_cleanup_verified",
    ),
    "word": (
        "dedicated_instance",
        "selection_read",
        "temporary_edit_verified",
        "undo_verified",
        "save_reopen_verified",
        "process_cleanup_verified",
    ),
    "powerpoint": (
        "dedicated_instance",
        "selection_read",
        "temporary_edit_verified",
        "undo_verified",
        "save_reopen_verified",
        "process_cleanup_verified",
    ),
}

PE_MACHINE_BITS = {
    0x014C: 32,  # IMAGE_FILE_MACHINE_I386
    0x8664: 64,  # IMAGE_FILE_MACHINE_AMD64
    0xAA64: 64,  # IMAGE_FILE_MACHINE_ARM64
}


def extract_server_executable(command: str | None) -> str | None:
    """Extract the registered EXE or DLL path from a COM server command."""
    text = os.path.expandvars(str(command or "").strip())
    if not text:
        return None
    if text.startswith('"'):
        closing_quote = text.find('"', 1)
        if closing_quote > 1:
            return os.path.normpath(text[1:closing_quote])
    match = re.match(r"(?i)(.+?\.(?:exe|dll))(?=\s|$)", text)
    if not match:
        return None
    return os.path.normpath(match.group(1).strip())


def live_checks_passed(app_name: str, details: dict) -> bool:
    """Return true only when every required live capability is explicit."""
    return all(details.get(key) is True for key in REQUIRED_LIVE_CHECKS[app_name])


def executable_bits(path: str | None) -> int | None:
    """Read a Windows PE machine type without loading the executable."""
    if not path:
        return None
    try:
        with Path(path).open("rb") as stream:
            if stream.read(2) != b"MZ":
                return None
            stream.seek(0x3C)
            pe_offset = int.from_bytes(stream.read(4), "little")
            stream.seek(pe_offset)
            if stream.read(4) != b"PE\0\0":
                return None
            machine = int.from_bytes(stream.read(2), "little")
    except (OSError, ValueError):
        return None
    return PE_MACHINE_BITS.get(machine)


def _registry_default(path: str, access: int) -> str | None:
    try:
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, path, 0, access) as key:
            value, _ = winreg.QueryValueEx(key, None)
    except OSError:
        return None
    return str(value).strip() or None


def _registry_registration(progid: str) -> dict:
    views = (
        ("64", winreg.KEY_READ | getattr(winreg, "KEY_WOW64_64KEY", 0)),
        ("32", winreg.KEY_READ | getattr(winreg, "KEY_WOW64_32KEY", 0)),
    )
    registrations = []
    seen = set()
    for view_name, access in views:
        clsid = _registry_default(f"{progid}\\CLSID", access)
        if not clsid:
            continue
        local_server = _registry_default(f"CLSID\\{clsid}\\LocalServer32", access)
        inproc_server = _registry_default(f"CLSID\\{clsid}\\InprocServer32", access)
        signature = (clsid.casefold(), local_server, inproc_server)
        if signature in seen:
            continue
        seen.add(signature)
        registrations.append(
            {
                "registry_view": view_name,
                "clsid": clsid,
                "local_server": local_server,
                "inproc_server": inproc_server,
            }
        )
    server_command = next(
        (
            item.get("local_server") or item.get("inproc_server")
            for item in registrations
            if item.get("local_server") or item.get("inproc_server")
        ),
        None,
    )
    executable = extract_server_executable(server_command)
    return {
        "registered": bool(registrations),
        "registrations": registrations,
        "server_path": executable,
        "server_exists": bool(executable and Path(executable).is_file()),
        "server_bits": executable_bits(executable),
        "server_file_version": _file_version(executable),
    }


def _file_version(path: str | None) -> str | None:
    if not path or not Path(path).is_file():
        return None
    try:
        import win32api

        info = win32api.GetFileVersionInfo(path, "\\")
        ms = int(info["FileVersionMS"])
        ls = int(info["FileVersionLS"])
        return ".".join(
            str(value)
            for value in (ms >> 16, ms & 0xFFFF, ls >> 16, ls & 0xFFFF)
        )
    except Exception:
        return None


def _processes(process_names: tuple[str, ...]) -> list[dict]:
    wanted = {name.casefold() for name in process_names}
    result = []
    for process in psutil.process_iter(["pid", "name", "exe"]):
        try:
            if str(process.info.get("name") or "").casefold() not in wanted:
                continue
            result.append(
                {
                    "pid": int(process.info["pid"]),
                    "name": str(process.info.get("name") or ""),
                    "executable": str(process.info.get("exe") or "") or None,
                }
            )
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            continue
    return sorted(result, key=lambda item: item["pid"])


def _process_ids(process_names: tuple[str, ...]) -> set[int]:
    return {item["pid"] for item in _processes(process_names)}


def _wait_for_process_baseline(
    process_names: tuple[str, ...], baseline: set[int], timeout: float = 8.0
) -> set[int]:
    deadline = time.monotonic() + timeout
    remaining = set()
    while time.monotonic() < deadline:
        remaining = _process_ids(process_names) - baseline
        if not remaining:
            return set()
        time.sleep(0.1)
    return _process_ids(process_names) - baseline


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _runtime_artifacts() -> dict:
    roots = []
    explicit = os.environ.get("JARVIS_RUNTIME_ROOT")
    if explicit:
        roots.append(Path(explicit))
    roots.extend(
        (
            Path.home() / "OneDrive" / "Desktop" / "JARVIS_RUNTIME",
            Path.home() / "Desktop" / "JARVIS_RUNTIME",
        )
    )
    root = next((candidate for candidate in roots if candidate.is_dir()), roots[0])
    candidates = {
        "current_exe": root / "current" / "Jarvis" / "Jarvis.exe",
        "previous_zip": root / "previous" / "Jarvis.zip",
    }
    result = {"runtime_root": str(root), "artifacts": {}}
    for name, path in candidates.items():
        entry = {"path": str(path), "exists": path.is_file()}
        if path.is_file():
            entry.update({"size": path.stat().st_size, "sha256": _sha256(path)})
        result["artifacts"][name] = entry
    return result


def _git_identity() -> dict:
    result = dict(runtime_info())
    try:
        branch = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=ROOT,
            capture_output=True,
            check=True,
            text=True,
            timeout=5,
        ).stdout.strip()
        exact_tag = subprocess.run(
            ["git", "describe", "--tags", "--exact-match", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            check=False,
            text=True,
            timeout=5,
        ).stdout.strip()
        result.update({"branch": branch, "exact_tag": exact_tag or None})
    except (OSError, subprocess.SubprocessError):
        result.update({"branch": None, "exact_tag": None})
    return result


def collect_inventory() -> dict:
    applications = {}
    for app_name, spec in APP_SPECS.items():
        applications[app_name] = {
            "display_name": spec["display_name"],
            "progid": spec["progid"],
            **_registry_registration(spec["progid"]),
            "running_processes": _processes(spec["process_names"]),
            "live_probe": {"status": "not_requested"},
        }
    return {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": _git_identity(),
        "environment": {
            "platform": platform.platform(),
            "python_version": platform.python_version(),
            "python_bits": struct.calcsize("P") * 8,
            "executable": sys.executable,
        },
        "runtime_backup": _runtime_artifacts(),
        "applications": applications,
    }


def _safe_close(document, *args, **kwargs) -> None:
    if document is None:
        return
    try:
        document.Close(*args, **kwargs)
    except Exception:
        pass


def _safe_quit(application) -> None:
    if application is None:
        return
    try:
        application.Quit()
    except Exception:
        pass


def _excel_live_probe() -> dict:
    import pythoncom
    import win32com.client

    marker = f"JARVIS_STAGE1_{uuid.uuid4().hex}"
    application = workbook = sheet = reopened = None
    stage = "initialize_com"
    pythoncom.CoInitialize()
    try:
        stage = "start_excel"
        application = win32com.client.DispatchEx("Excel.Application")
        application.Visible = False
        application.DisplayAlerts = False
        application.EnableEvents = False
        stage = "create_workbook"
        workbook = application.Workbooks.Add()
        sheet = workbook.Worksheets(1)
        sheet.Range("A1").Select()
        address = application.Selection.Address
        selection_reference = str(address(False, False) if callable(address) else address)
        selection_reference = selection_reference.replace("$", "")
        stage = "temporary_edit_and_undo"
        target_cell = sheet.Range("A1")
        before_value = target_cell.Value2
        target_cell.Value2 = marker
        temporary_edit_verified = str(target_cell.Value2 or "") == marker
        # A direct COM assignment is not reliably added to Excel's Undo stack.
        # Application.Undo could therefore undo an unrelated user action.
        native_undo_error = "not attempted: direct COM writes do not own the Undo stack"
        native_undo_verified = False
        target_cell.Value2 = before_value
        undo_verified = target_cell.Value2 == before_value
        undo_strategy = "snapshot_restore"

        with tempfile.TemporaryDirectory(prefix="jarvis-stage1-excel-") as temp_dir:
            stage = "save_and_reopen"
            path = str(Path(temp_dir) / "stage1.xlsx")
            sheet.Range("A1").Value2 = marker
            workbook.SaveAs(path, FileFormat=51)
            _safe_close(workbook, SaveChanges=False)
            workbook = None
            sheet = None
            reopened = application.Workbooks.Open(
                path, UpdateLinks=0, ReadOnly=True, AddToMru=False
            )
            save_reopen_verified = (
                str(reopened.Worksheets(1).Range("A1").Value2 or "") == marker
            )
            _safe_close(reopened, SaveChanges=False)
            reopened = None
        return {
            "application_version": str(application.Version),
            "dedicated_instance": True,
            "selection_reference": selection_reference,
            "selection_read": selection_reference.upper() == "A1",
            "temporary_edit_verified": temporary_edit_verified,
            "undo_verified": undo_verified,
            "undo_strategy": undo_strategy,
            "native_undo_verified": native_undo_verified,
            "native_undo_error": native_undo_error,
            "save_reopen_verified": save_reopen_verified,
        }
    except Exception as error:
        raise RuntimeError(f"{stage}: {type(error).__name__}: {error}") from error
    finally:
        _safe_close(reopened, SaveChanges=False)
        reopened = None
        sheet = None
        _safe_close(workbook, SaveChanges=False)
        workbook = None
        _safe_quit(application)
        application = None
        gc.collect()
        pythoncom.CoUninitialize()


def _word_live_probe() -> dict:
    import pythoncom
    import win32com.client

    marker = f"JARVIS_STAGE1_{uuid.uuid4().hex}"
    application = document = reopened = None
    stage = "initialize_com"
    pythoncom.CoInitialize()
    try:
        stage = "start_word"
        application = win32com.client.DispatchEx("Word.Application")
        application.Visible = False
        application.DisplayAlerts = 0
        stage = "create_document"
        document = application.Documents.Add()
        stage = "temporary_edit_and_undo"
        selection = application.Selection
        selection.Text = marker
        document.Content.Select()
        selected_text = str(application.Selection.Text or "").rstrip("\r\x07")
        temporary_edit_verified = marker in str(document.Content.Text or "")
        document.Undo()
        undo_verified = marker not in str(document.Content.Text or "")

        with tempfile.TemporaryDirectory(prefix="jarvis-stage1-word-") as temp_dir:
            stage = "save_and_reopen"
            path = str(Path(temp_dir) / "stage1.docx")
            document.Content.Text = marker
            document.SaveAs2(path, FileFormat=12, AddToRecentFiles=False)
            _safe_close(document, SaveChanges=0)
            document = None
            reopened = application.Documents.Open(
                path,
                ConfirmConversions=False,
                ReadOnly=True,
                AddToRecentFiles=False,
                Visible=False,
            )
            save_reopen_verified = marker in str(reopened.Content.Text or "")
            _safe_close(reopened, SaveChanges=0)
            reopened = None
        return {
            "application_version": str(application.Version),
            "dedicated_instance": True,
            "selection_reference": {"start": 0, "end": len(selected_text)},
            "selection_read": selected_text == marker,
            "temporary_edit_verified": temporary_edit_verified,
            "undo_verified": undo_verified,
            "undo_strategy": "native",
            "native_undo_verified": undo_verified,
            "save_reopen_verified": save_reopen_verified,
        }
    except Exception as error:
        raise RuntimeError(f"{stage}: {type(error).__name__}: {error}") from error
    finally:
        _safe_close(reopened, SaveChanges=0)
        reopened = None
        _safe_close(document, SaveChanges=0)
        document = None
        _safe_quit(application)
        application = None
        gc.collect()
        pythoncom.CoUninitialize()


def _powerpoint_live_probe() -> dict:
    import pythoncom
    import win32com.client

    marker = f"JARVIS_STAGE1_{uuid.uuid4().hex}"
    application = presentation = reopened = slide = shape = None
    stage = "initialize_com"
    pythoncom.CoInitialize()
    try:
        stage = "start_powerpoint"
        application = win32com.client.DispatchEx("PowerPoint.Application")
        application.Visible = -1
        stage = "create_presentation"
        presentation = application.Presentations.Add(-1)
        slide = presentation.Slides.Add(1, 12)
        shape = slide.Shapes.AddTextbox(1, 20, 20, 400, 60)
        application.ActiveWindow.View.GotoSlide(1)
        stage = "read_selection"
        shape.Select()
        selection_type = int(application.ActiveWindow.Selection.Type)
        stage = "temporary_edit"
        before_text = str(shape.TextFrame.TextRange.Text or "")
        shape.TextFrame.TextRange.Text = marker
        stage = "read_selected_shape"
        selected_text = str(
            application.ActiveWindow.Selection.ShapeRange(1).TextFrame.TextRange.Text
            or ""
        )
        temporary_edit_verified = str(shape.TextFrame.TextRange.Text or "") == marker
        stage = "undo"
        # PowerPoint may undo the preceding shape operation instead of a direct
        # TextRange assignment.  Never consume the shared application Undo stack.
        native_undo_error = "not attempted: COM text writes do not own the Undo stack"
        native_undo_verified = False
        shape.TextFrame.TextRange.Text = before_text
        undo_verified = str(shape.TextFrame.TextRange.Text or "") == before_text
        undo_strategy = "snapshot_restore"

        with tempfile.TemporaryDirectory(prefix="jarvis-stage1-powerpoint-") as temp_dir:
            stage = "save_and_reopen"
            path = str(Path(temp_dir) / "stage1.pptx")
            shape.TextFrame.TextRange.Text = marker
            presentation.SaveAs(path, 24)
            _safe_close(presentation)
            presentation = None
            slide = None
            shape = None
            reopened = application.Presentations.Open(
                path, ReadOnly=True, Untitled=False, WithWindow=False
            )
            reopened_text = str(
                reopened.Slides(1).Shapes(1).TextFrame.TextRange.Text or ""
            )
            save_reopen_verified = reopened_text == marker
            _safe_close(reopened)
            reopened = None
        return {
            "application_version": str(application.Version),
            "dedicated_instance": True,
            "selection_type": selection_type,
            "selection_read": selected_text == marker,
            "temporary_edit_verified": temporary_edit_verified,
            "undo_verified": undo_verified,
            "undo_strategy": undo_strategy,
            "native_undo_verified": native_undo_verified,
            "native_undo_error": native_undo_error,
            "save_reopen_verified": save_reopen_verified,
        }
    except Exception as error:
        raise RuntimeError(f"{stage}: {type(error).__name__}: {error}") from error
    finally:
        shape = None
        slide = None
        _safe_close(reopened)
        reopened = None
        _safe_close(presentation)
        presentation = None
        _safe_quit(application)
        application = None
        gc.collect()
        pythoncom.CoUninitialize()


def _hwp_rot_names() -> set[str]:
    import pythoncom

    context = pythoncom.CreateBindCtx(0)
    running_table = pythoncom.GetRunningObjectTable()
    names = set()
    for moniker in running_table.EnumRunning():
        try:
            name = str(moniker.GetDisplayName(context, moniker))
        except Exception:
            continue
        if name.startswith("!HwpObject."):
            names.add(name)
    return names


def _hwp_live_probe() -> dict:
    import pythoncom
    import win32com.client

    from engine.app_actions.hwp_adapter import HwpAdapter

    marker = f"JARVIS_STAGE1_{uuid.uuid4().hex}"
    hwp = None
    before_rot = set()
    after_rot = set()
    stage = "initialize_com"
    pythoncom.CoInitialize()
    try:
        stage = "start_hwp"
        before_rot = _hwp_rot_names()
        hwp = win32com.client.Dispatch("HWPFrame.HwpObject")
        created_rot = _hwp_rot_names() - before_rot
        if not created_rot:
            raise RuntimeError("전용 HwpObject 생성을 확인하지 못했습니다.")
        stage = "temporary_edit"
        adapter = HwpAdapter(object_getter=lambda: hwp, require_visible=False)
        prepared = adapter.prepare("insert_text", {"text": marker})
        result = adapter.execute(prepared)
        temporary_edit_verified = bool(result.get("verified")) and marker in str(
            hwp.GetTextFile("UNICODE", "") or ""
        )
        stage = "read_selection_and_undo"
        hwp.HAction.Run("SelectAll")
        selected_text = str(hwp.GetTextFile("UNICODE", "saveblock") or "")
        hwp.HAction.Run("Undo")
        undo_verified = marker not in str(hwp.GetTextFile("UNICODE", "") or "")
        version = str(getattr(hwp, "Version", "") or "") or None
        return {
            "application_version": version,
            "dedicated_instance": True,
            "selection_read": marker in selected_text,
            "selected_length": len(selected_text),
            "temporary_edit_verified": temporary_edit_verified,
            "undo_verified": undo_verified,
            "undo_strategy": "native",
            "native_undo_verified": undo_verified,
            "created_rot_entries": len(created_rot),
        }
    except Exception as error:
        raise RuntimeError(f"{stage}: {type(error).__name__}: {error}") from error
    finally:
        if hwp is not None:
            try:
                hwp.Clear(1)
            except Exception:
                pass
            try:
                hwp.Quit()
            except Exception:
                pass
        hwp = None
        gc.collect()
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            after_rot = _hwp_rot_names()
            if after_rot == before_rot:
                break
            time.sleep(0.1)
        _HWP_LAST_ROT_CLEANUP["verified"] = after_rot == before_rot
        pythoncom.CoUninitialize()


_HWP_LAST_ROT_CLEANUP = {"verified": False}

LIVE_PROBES = {
    "excel": _excel_live_probe,
    "hwp": _hwp_live_probe,
    "word": _word_live_probe,
    "powerpoint": _powerpoint_live_probe,
}


def run_live_probe(app_name: str, inventory_entry: dict) -> dict:
    spec = APP_SPECS[app_name]
    if not inventory_entry.get("registered"):
        return {"status": "unavailable", "reason": "COM ProgID가 등록되지 않았습니다."}
    baseline_processes = _process_ids(spec["process_names"])
    if baseline_processes:
        return {
            "status": "skipped_user_processes_running",
            "reason": "기존 사용자 프로세스를 보호하기 위해 실제 검증을 실행하지 않았습니다.",
            "baseline_pids": sorted(baseline_processes),
        }

    started = time.monotonic()
    details = {}
    error = None
    try:
        details = LIVE_PROBES[app_name]()
    except Exception as caught:
        error = {
            "type": type(caught).__name__,
            "message": str(caught),
        }
    remaining = _wait_for_process_baseline(spec["process_names"], baseline_processes)
    details["process_cleanup_verified"] = not remaining
    if app_name == "hwp":
        details["rot_cleanup_verified"] = bool(_HWP_LAST_ROT_CLEANUP["verified"])
    passed = error is None and live_checks_passed(app_name, details)
    result = {
        "status": "passed" if passed else "failed",
        "duration_ms": round((time.monotonic() - started) * 1000, 2),
        **details,
    }
    if remaining:
        result["remaining_created_pids"] = sorted(remaining)
    if error:
        result["error"] = error
    return result


def build_report(selected_apps: tuple[str, ...], live: bool) -> dict:
    report = collect_inventory()
    report.update(
        {
            "stage": "prototype-1.0-stage-1",
            "live_requested": bool(live),
            "selected_apps": list(selected_apps),
        }
    )
    if live:
        for app_name in selected_apps:
            entry = report["applications"][app_name]
            entry["live_probe"] = run_live_probe(app_name, entry)

    selected_entries = [report["applications"][name] for name in selected_apps]
    report["inventory_passed"] = all(
        entry.get("registered") for entry in selected_entries
    )
    if live:
        report["live_passed"] = all(
            entry["live_probe"].get("status") == "passed"
            for entry in selected_entries
        )
        report["passed"] = report["inventory_passed"] and report["live_passed"]
    else:
        report["live_passed"] = None
        report["passed"] = report["inventory_passed"]
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live",
        action="store_true",
        help="전용 빈 문서를 생성해 읽기·임시 변경·Undo·정리를 검증합니다.",
    )
    parser.add_argument(
        "--apps",
        nargs="+",
        choices=tuple(APP_SPECS),
        default=tuple(APP_SPECS),
        help="검증할 앱. 기본값은 네 앱 전체입니다.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=REPORT_PATH,
        help="JSON 보고서 경로입니다.",
    )
    args = parser.parse_args(argv)
    selected_apps = tuple(dict.fromkeys(args.apps))
    report = build_report(selected_apps, args.live)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
