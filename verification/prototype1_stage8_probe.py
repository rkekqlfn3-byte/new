"""Owned-fixture four-app stability and Prototype 1.0 completion probe."""

from __future__ import annotations

import argparse
import gc
import json
import multiprocessing
import os
import queue
import shutil
import tempfile
import time
import uuid
from pathlib import Path

import psutil

from engine.app_actions.excel_adapter import ExcelAdapter
from engine.app_actions.hwp_adapter import HwpAdapter
from engine.app_actions.powerpoint_adapter import PowerPointAdapter
from engine.app_actions.word_adapter import WordAdapter
from engine.edit_mode import EditContextManager, EditModeController, EditSessionManager
from engine.edit_mode.native_bridge import NativeDocumentBridge
from engine.parser import CommandParser
from verification.prototype1_stage3_probe import (
    APP_SPECS,
    _process_ids,
    _wait_for_processes,
)
from verification.prototype1_stage5_probe import (
    _NoLayout,
    _OwnedExcelProvider,
    _OwnedHwpProvider,
    _Registry,
    _stop_created_processes,
)
from verification.prototype1_stage6_probe import _OwnedOfficeProvider
from verification.source_identity import source_identity


ROOT = Path(__file__).resolve().parent.parent
REPORT_PATH = ROOT / "verification" / "prototype1_stage8_report.json"
APP_TYPES = ("excel", "hwp", "word", "powerpoint")
STRESS_COMMANDS = 100
_PROGRESS_QUEUE = None


def _trace(app_type, message):
    if _PROGRESS_QUEUE is not None:
        try:
            _PROGRESS_QUEUE.put_nowait({
                "app": app_type,
                "stage": str(message),
                "time": time.monotonic(),
            })
        except Exception:
            pass
    if os.environ.get("JARVIS_STAGE8_TRACE") == "1":
        print(f"[stage8:{app_type}] {message}", flush=True)


def _interface_count(pythoncom):
    counter = getattr(pythoncom, "_GetInterfaceCount", None)
    return int(counter()) if callable(counter) else None


def _memory_bytes(process_id):
    if not process_id:
        return None
    try:
        return int(psutil.Process(process_id).memory_info().rss)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return None


def _owned_process_id(spec, baseline):
    created = _process_ids(spec["processes"]) - baseline
    return next(iter(created)) if len(created) == 1 else None


def _resource_metrics(pythoncom, process_id, interface_before, memory_before):
    gc.collect()
    interface_after = _interface_count(pythoncom)
    memory_after = _memory_bytes(process_id)
    interface_growth = (
        interface_after - interface_before
        if interface_before is not None and interface_after is not None
        else None
    )
    memory_growth = (
        memory_after - memory_before
        if memory_before is not None and memory_after is not None
        else None
    )
    return {
        "interface_growth": interface_growth,
        "memory_growth_mb": (
            round(memory_growth / (1024 * 1024), 2)
            if memory_growth is not None
            else None
        ),
        "com_reference_stable": (
            interface_growth is None or interface_growth <= 64
        ),
        "memory_stable": memory_growth is None or memory_growth <= 256 * 1024 * 1024,
    }


def _session_parser(app_type, path, provider, native_adapter, window_handle=0):
    sessions = EditSessionManager()
    session = sessions.connect({
        "app_type": app_type,
        "file_path": str(path),
        "document_name": path.name,
        "window_handle": int(window_handle or 0),
        "launch_requested": True,
    })
    controller = EditModeController(
        session_manager=sessions,
        context_manager=EditContextManager(providers=(provider,)),
        native_action_registry=_Registry(app_type, native_adapter),
        layout_manager=_NoLayout(),
    )
    return session, controller, CommandParser(edit_mode_controller=controller)


def _edit_context(controller, session, request_id):
    context = controller.context(session["session_id"])
    return {
        "edit_session_id": session["session_id"],
        "document_fingerprint": session["document_fingerprint"],
        "context_fingerprint": context["context_fingerprint"],
        "request_id": request_id,
    }


def _approve(parser, controller, session, command, request_id, chat_id):
    preview = parser.execute_command_result(
        command,
        mode="edit",
        session_id=chat_id,
        edit_context=_edit_context(controller, session, request_id),
    )
    if preview.get("status") != "confirmation_required":
        raise RuntimeError(
            "미리보기 생성 실패: "
            f"{preview.get('status')} / {preview.get('error_type')} / "
            f"{preview.get('message')}"
        )
    confirmation = preview["data"]["confirmation"]
    result = parser.resolve_pending_confirmation(
        chat_id,
        confirmation_id=confirmation["confirmation_id"],
        option_id="apply",
    )
    if not result.get("success") or not result.get("verified"):
        raise RuntimeError(f"승인 편집 검증 실패: {result.get('status')}")
    return result


def _undo(parser, controller, session, request_id, chat_id):
    result = parser.execute_command_result(
        "방금 거 취소해",
        mode="edit",
        session_id=chat_id,
        edit_context=_edit_context(controller, session, request_id),
    )
    if not result.get("success") or not result.get("verified"):
        raise RuntimeError(f"직전 편집 복원 실패: {result.get('status')}")
    return result


def _base_result(stress_verified, resources, instance_count, prototype_verified):
    passed = all((
        stress_verified == STRESS_COMMANDS,
        resources["com_reference_stable"],
        resources["memory_stable"],
        instance_count == 1,
        prototype_verified,
    ))
    return {
        "status": "passed" if passed else "failed",
        "owned_fixture_only": True,
        "user_process_protected": True,
        "stress_commands": STRESS_COMMANDS,
        "stress_verified": stress_verified,
        "single_document_instance_verified": instance_count == 1,
        "com_reference_stable": resources["com_reference_stable"],
        "com_interface_growth": resources["interface_growth"],
        "memory_stable": resources["memory_stable"],
        "memory_growth_mb": resources["memory_growth_mb"],
        "prototype_apply_verify_undo": prototype_verified,
    }


def _probe_excel():
    import pythoncom
    import win32com.client

    spec = APP_SPECS["excel"]
    baseline = _process_ids(spec["processes"])
    if baseline:
        return {"status": "skipped_user_processes_running", "user_process_protected": True}
    if not NativeDocumentBridge().is_available("excel"):
        return {"status": "unavailable"}
    application = workbook = sheet = provider = controller = parser = None
    temp_dir = tempfile.mkdtemp(prefix="jarvis-stage8-excel-")
    path = Path(temp_dir) / f"stage8-{uuid.uuid4().hex}.xlsx"
    stage = "start_excel"
    pythoncom.CoInitialize()
    try:
        application = win32com.client.DispatchEx("Excel.Application")
        application.Visible = False
        application.DisplayAlerts = False
        application.EnableEvents = False
        workbook = application.Workbooks.Add()
        sheet = workbook.Worksheets(1)
        sheet.Range("B1:B3").Value2 = (("값",), (10,), (20,))
        workbook.SaveAs(str(path), FileFormat=51)
        adapter = ExcelAdapter(
            application_getter=lambda: application,
            process_counter=lambda: 1,
        )
        process_id = _owned_process_id(spec, baseline)
        interface_before = _interface_count(pythoncom)
        memory_before = _memory_bytes(process_id)
        stage = "stress_100"
        verified = 0
        for index in range(STRESS_COMMANDS):
            stage = f"stress_100_{index + 1}"
            prepared = adapter.prepare(
                "write_cell",
                {"cell": "A1", "value": index, "value_type": "number"},
            )
            verified += int(adapter.execute(prepared)["verified"])
            if (index + 1) % 20 == 0:
                _trace("excel", f"stress {index + 1}/{STRESS_COMMANDS}")
        prepared = None
        resources = _resource_metrics(
            pythoncom, process_id, interface_before, memory_before
        )

        stage = "prototype_flow"
        sheet.Range("B2:B3").Select()
        provider = _OwnedExcelProvider(application, workbook)
        session, controller, parser = _session_parser(
            "excel",
            path,
            provider,
            adapter,
            int(application.Hwnd or 0),
        )
        applied = _approve(
            parser,
            controller,
            session,
            "이거 합계 내줘",
            "stage8-excel-apply",
            "stage8-excel",
        )
        formula_verified = str(sheet.Range("B4").Formula).upper() == "=SUM(B2:B3)"
        undone = _undo(
            parser,
            controller,
            session,
            "stage8-excel-undo",
            "stage8-excel",
        )
        undo_verified = sheet.Range("B4").Value2 in {None, ""}
        status = controller.status()["session"]
        prototype_verified = all((
            applied["verified"],
            formula_verified,
            undone["verified"],
            undo_verified,
            status["state"] == "ready",
            status["undo_record"] is None,
        ))
        return _base_result(
            verified,
            resources,
            int(application.Workbooks.Count),
            prototype_verified,
        )
    except Exception as error:
        return {
            "status": "failed",
            "stage": stage,
            "error_type": type(error).__name__,
            "message": str(error),
            "user_process_protected": True,
        }
    finally:
        parser = controller = provider = sheet = None
        if workbook is not None:
            try:
                workbook.Close(SaveChanges=False)
            except Exception:
                pass
        workbook = None
        if application is not None:
            try:
                application.Quit()
            except Exception:
                pass
        application = None
        gc.collect()
        pythoncom.CoUninitialize()
        _wait_for_processes(spec["processes"], baseline)
        shutil.rmtree(temp_dir, ignore_errors=True)


def _probe_hwp():
    import pythoncom
    import win32com.client

    spec = APP_SPECS["hwp"]
    baseline = _process_ids(spec["processes"])
    if baseline:
        return {"status": "skipped_user_processes_running", "user_process_protected": True}
    if not NativeDocumentBridge().is_available("hwp"):
        return {"status": "unavailable"}
    hwp = provider = controller = parser = None
    owned_pid = None
    original = (
        "8단계 통합 검증을 위해 선택한 문장을 안전하게 줄이고 다시 복원하는 "
        "전체 편집 흐름을 확인합니다."
    )
    temp_dir = tempfile.mkdtemp(prefix="jarvis-stage8-hwp-")
    path = Path(temp_dir) / f"stage8-{uuid.uuid4().hex}.hwp"
    # The installed HWP has no automation file-checker module. Use this file
    # only as a private session identity while the real edits occur in the
    # dedicated unsaved HwpObject; never click through a security dialog.
    path.write_bytes(b"JARVIS-owned-unsaved-HWP-fixture-identity")
    stage = "start_hwp"
    pythoncom.CoInitialize()
    try:
        _trace("hwp", "dispatch")
        hwp = win32com.client.Dispatch("HWPFrame.HwpObject")
        created = _process_ids(spec["processes"]) - baseline
        if len(created) == 1:
            owned_pid = next(iter(created))
        _trace("hwp", "insert_fixture")
        parameter = hwp.HParameterSet.HInsertText
        hwp.HAction.GetDefault("InsertText", parameter.HSet)
        parameter.Text = original
        hwp.HAction.Execute("InsertText", parameter.HSet)
        _trace("hwp", "owned_unsaved_fixture_ready")
        _trace("hwp", "select_fixture")
        hwp.HAction.Run("SelectAll")
        adapter = HwpAdapter(
            object_getter=lambda: hwp,
            require_visible=False,
            owned_unsaved_document_path=path,
        )
        interface_before = _interface_count(pythoncom)
        memory_before = _memory_bytes(owned_pid)
        stage = "stress_100"
        verified = 0
        for index in range(STRESS_COMMANDS):
            stage = f"stress_100_{index + 1}"
            prepared = adapter.prepare(
                "set_text_format",
                {"bold": bool(index % 2)},
            )
            verified += int(adapter.execute(prepared)["verified"])
            if (index + 1) % 20 == 0:
                _trace("hwp", f"stress {index + 1}/{STRESS_COMMANDS}")
        prepared = None
        resources = _resource_metrics(
            pythoncom, owned_pid, interface_before, memory_before
        )

        stage = "prototype_flow"
        hwp.HAction.Run("SelectAll")
        provider = _OwnedHwpProvider(hwp, file_path=path)
        window = hwp.XHwpWindows.Active_XHwpWindow
        session, controller, parser = _session_parser(
            "hwp",
            path,
            provider,
            adapter,
            int(window.WindowHandle or 0),
        )
        applied = _approve(
            parser,
            controller,
            session,
            "이 부분 좀 줄여줘",
            "stage8-hwp-apply",
            "stage8-hwp",
        )
        shortened = original not in str(hwp.GetTextFile("UNICODE", "") or "")
        undone = _undo(
            parser,
            controller,
            session,
            "stage8-hwp-undo",
            "stage8-hwp",
        )
        restored = original in str(hwp.GetTextFile("UNICODE", "") or "")
        status = controller.status()["session"]
        prototype_verified = all((
            applied["verified"],
            shortened,
            undone["verified"],
            restored,
            status["state"] == "ready",
            status["undo_record"] is None,
        ))
        result = _base_result(
            verified,
            resources,
            int(hwp.XHwpDocuments.Count),
            prototype_verified,
        )
        result["owned_unsaved_hwp_fixture"] = True
        result["file_security_setting_changed"] = False
        return result
    except Exception as error:
        return {
            "status": "failed",
            "stage": stage,
            "error_type": type(error).__name__,
            "message": str(error),
            "user_process_protected": True,
        }
    finally:
        parser = controller = provider = hwp = None
        gc.collect()
        pythoncom.CoUninitialize()
        if owned_pid is not None:
            try:
                process = psutil.Process(owned_pid)
                process.terminate()
                process.wait(timeout=3)
            except psutil.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        _wait_for_processes(spec["processes"], baseline)
        shutil.rmtree(temp_dir, ignore_errors=True)


def _probe_word():
    import pythoncom
    import win32com.client

    spec = APP_SPECS["word"]
    baseline = _process_ids(spec["processes"])
    if baseline:
        return {"status": "skipped_user_processes_running", "user_process_protected": True}
    if not NativeDocumentBridge().is_available("word"):
        return {"status": "unavailable"}
    application = document = provider = controller = parser = None
    original = "8단계 Word 통합 검증 제목"
    temp_dir = tempfile.mkdtemp(prefix="jarvis-stage8-word-")
    path = Path(temp_dir) / f"stage8-{uuid.uuid4().hex}.docx"
    stage = "start_word"
    pythoncom.CoInitialize()
    try:
        application = win32com.client.DispatchEx("Word.Application")
        application.Visible = False
        application.DisplayAlerts = 0
        document = application.Documents.Add()
        document.Content.Text = original
        document.SaveAs2(str(path), FileFormat=12, AddToRecentFiles=False)
        document.Activate()
        application.Selection.SetRange(0, len(original))
        adapter = WordAdapter(
            application_getter=lambda: application,
            require_visible=False,
        )
        process_id = _owned_process_id(spec, baseline)
        interface_before = _interface_count(pythoncom)
        memory_before = _memory_bytes(process_id)
        stage = "stress_100"
        verified = 0
        for index in range(STRESS_COMMANDS):
            stage = f"stress_100_{index + 1}"
            prepared = adapter.prepare(
                "set_text_format",
                {
                    "document_path": str(path),
                    "font_size": 10 if index % 2 == 0 else 12,
                },
            )
            verified += int(adapter.execute(prepared)["verified"])
            if (index + 1) % 20 == 0:
                _trace("word", f"stress {index + 1}/{STRESS_COMMANDS}")
        prepared = None
        resources = _resource_metrics(
            pythoncom, process_id, interface_before, memory_before
        )

        stage = "prototype_flow"
        application.Selection.SetRange(0, len(original))
        provider = _OwnedOfficeProvider("word", application, document)
        session, controller, parser = _session_parser(
            "word", path, provider, adapter
        )
        before_size = float(application.Selection.Font.Size)
        applied = _approve(
            parser,
            controller,
            session,
            "제목을 조금 더 크게",
            "stage8-word-apply",
            "stage8-word",
        )
        enlarged = abs(float(application.Selection.Font.Size) - (before_size + 2)) < 0.01
        undone = _undo(
            parser,
            controller,
            session,
            "stage8-word-undo",
            "stage8-word",
        )
        restored = abs(float(application.Selection.Font.Size) - before_size) < 0.01
        status = controller.status()["session"]
        prototype_verified = all((
            applied["verified"],
            enlarged,
            undone["verified"],
            restored,
            status["state"] == "ready",
            status["undo_record"] is None,
        ))
        return _base_result(
            verified,
            resources,
            int(application.Documents.Count),
            prototype_verified,
        )
    except Exception as error:
        return {
            "status": "failed",
            "stage": stage,
            "error_type": type(error).__name__,
            "message": str(error),
            "user_process_protected": True,
        }
    finally:
        parser = controller = provider = None
        if document is not None:
            try:
                document.Close(SaveChanges=False)
            except Exception:
                pass
        document = None
        if application is not None:
            try:
                application.Quit()
            except Exception:
                pass
        application = None
        gc.collect()
        pythoncom.CoUninitialize()
        _wait_for_processes(spec["processes"], baseline)
        shutil.rmtree(temp_dir, ignore_errors=True)


def _probe_powerpoint():
    import pythoncom
    import win32com.client

    spec = APP_SPECS["powerpoint"]
    baseline = _process_ids(spec["processes"])
    if baseline:
        return {"status": "skipped_user_processes_running", "user_process_protected": True}
    if not NativeDocumentBridge().is_available("powerpoint"):
        return {"status": "unavailable"}
    application = presentation = first = second = shape = None
    provider = controller = parser = None
    temp_dir = tempfile.mkdtemp(prefix="jarvis-stage8-powerpoint-")
    path = Path(temp_dir) / f"stage8-{uuid.uuid4().hex}.pptx"
    stage = "start_powerpoint"
    pythoncom.CoInitialize()
    try:
        application = win32com.client.DispatchEx("PowerPoint.Application")
        application.Visible = True
        presentation = application.Presentations.Add()
        first = presentation.Slides.Add(1, 1)
        second = presentation.Slides.Add(2, 1)
        first.Shapes.Title.TextFrame.TextRange.Text = "앞 슬라이드 제목"
        first.Shapes.Title.TextFrame.TextRange.Font.Size = 34
        first.Shapes.Title.TextFrame.TextRange.Font.Bold = -1
        second.Shapes.Title.TextFrame.TextRange.Text = "현재 슬라이드 제목"
        second.Shapes.Title.TextFrame.TextRange.Font.Size = 24
        second.Shapes.Title.TextFrame.TextRange.Font.Bold = 0
        presentation.SaveAs(str(path), 24)
        application.ActiveWindow.View.GotoSlide(2)
        shape = second.Shapes.Title
        shape.Select()
        adapter = PowerPointAdapter(application_getter=lambda: application)
        process_id = _owned_process_id(spec, baseline)
        interface_before = _interface_count(pythoncom)
        memory_before = _memory_bytes(process_id)
        stage = "stress_100"
        verified = 0
        for index in range(STRESS_COMMANDS):
            stage = f"stress_100_{index + 1}"
            prepared = adapter.prepare(
                "move_shape",
                {
                    "document_path": str(path),
                    "dx": 1 if index % 2 == 0 else -1,
                    "dy": 0,
                },
            )
            verified += int(adapter.execute(prepared)["verified"])
            if (index + 1) % 20 == 0:
                _trace("powerpoint", f"stress {index + 1}/{STRESS_COMMANDS}")
        prepared = None
        resources = _resource_metrics(
            pythoncom, process_id, interface_before, memory_before
        )

        stage = "prototype_flow"
        shape.Select()
        provider = _OwnedOfficeProvider("powerpoint", application, presentation)
        session, controller, parser = _session_parser(
            "powerpoint", path, provider, adapter
        )
        before_size = float(shape.TextFrame.TextRange.Font.Size)
        before_bold = int(shape.TextFrame.TextRange.Font.Bold)
        applied = _approve(
            parser,
            controller,
            session,
            "앞 장이랑 같은 형식으로",
            "stage8-ppt-apply",
            "stage8-powerpoint",
        )
        matched = (
            abs(float(shape.TextFrame.TextRange.Font.Size) - 34) < 0.01
            and int(shape.TextFrame.TextRange.Font.Bold) == -1
        )
        undone = _undo(
            parser,
            controller,
            session,
            "stage8-ppt-undo",
            "stage8-powerpoint",
        )
        restored = (
            abs(float(shape.TextFrame.TextRange.Font.Size) - before_size) < 0.01
            and int(shape.TextFrame.TextRange.Font.Bold) == before_bold
        )
        status = controller.status()["session"]
        prototype_verified = all((
            applied["verified"],
            matched,
            undone["verified"],
            restored,
            status["state"] == "ready",
            status["undo_record"] is None,
        ))
        return _base_result(
            verified,
            resources,
            int(application.Presentations.Count),
            prototype_verified,
        )
    except Exception as error:
        return {
            "status": "failed",
            "stage": stage,
            "error_type": type(error).__name__,
            "message": str(error),
            "user_process_protected": True,
        }
    finally:
        parser = controller = provider = shape = second = first = None
        if presentation is not None:
            try:
                presentation.Close()
            except Exception:
                pass
        presentation = None
        if application is not None:
            try:
                application.Quit()
            except Exception:
                pass
        application = None
        gc.collect()
        pythoncom.CoUninitialize()
        _wait_for_processes(spec["processes"], baseline)
        shutil.rmtree(temp_dir, ignore_errors=True)


PROBES = {
    "excel": _probe_excel,
    "hwp": _probe_hwp,
    "word": _probe_word,
    "powerpoint": _probe_powerpoint,
}


def _probe_worker(app_type, output, progress):
    global _PROGRESS_QUEUE
    _PROGRESS_QUEUE = progress
    try:
        output.put(PROBES[app_type]())
    except BaseException as error:
        output.put({
            "status": "failed",
            "error_type": type(error).__name__,
            "message": str(error),
            "user_process_protected": True,
        })


def _run_isolated(app_type, timeout=None):
    timeout = int(timeout or (360 if app_type == "hwp" else 120))
    spec = APP_SPECS[app_type]
    baseline = _process_ids(spec["processes"])
    if baseline:
        return {"status": "skipped_user_processes_running", "user_process_protected": True}
    context = multiprocessing.get_context("spawn")
    output = context.Queue(maxsize=1)
    progress = context.Queue()
    process = context.Process(
        target=_probe_worker,
        args=(app_type, output, progress),
    )
    process.start()
    deadline = time.monotonic() + timeout
    last_stage = "worker_started"
    while process.is_alive() and time.monotonic() < deadline:
        process.join(min(0.5, max(0.0, deadline - time.monotonic())))
        while True:
            try:
                update = progress.get_nowait()
            except queue.Empty:
                break
            if isinstance(update, dict) and update.get("stage"):
                last_stage = str(update["stage"])
    if process.is_alive():
        process.terminate()
        process.join(5)
        _stop_created_processes(spec["processes"], baseline)
        return {
            "status": "failed",
            "stage": "owned_fixture_timeout",
            "last_worker_stage": last_stage,
            "error_type": "TimeoutError",
            "message": f"{app_type} 100회 소유 문서 검증이 시간 안에 끝나지 않았습니다.",
            "user_process_protected": True,
            "owned_process_cleanup_verified": not (
                _process_ids(spec["processes"]) - baseline
            ),
        }
    try:
        result = output.get(timeout=2)
    except queue.Empty:
        result = {
            "status": "failed",
            "stage": "owned_fixture_worker",
            "error_type": "RuntimeError",
            "message": "소유 문서 검증 프로세스가 결과 없이 종료됐습니다.",
            "user_process_protected": True,
        }
    zombies = _process_ids(spec["processes"]) - baseline
    result["owned_process_cleanup_verified"] = not zombies
    if zombies:
        _stop_created_processes(spec["processes"], baseline)
        result["status"] = "failed"
        result.setdefault("stage", "owned_process_cleanup")
        result.setdefault("message", "검증 뒤 소유 Office/HWP 프로세스가 남았습니다.")
    return result


def run_probe(apps=APP_TYPES, timeout=None):
    requested = tuple(dict.fromkeys(apps))
    results = {
        app: (
            _run_isolated(app, timeout=timeout)
            if timeout is not None
            else _run_isolated(app)
        )
        for app in requested
    }
    attempted = [
        result
        for result in results.values()
        if result["status"] not in {
            "unavailable",
            "skipped_user_processes_running",
        }
    ]
    all_four_passed = (
        set(results) == set(APP_TYPES)
        and all(results[app]["status"] == "passed" for app in APP_TYPES)
    )
    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "source": source_identity(ROOT),
        "probe": "prototype1_stage8_four_app_stability",
        "success": all(item["status"] == "passed" for item in attempted),
        "prototype_1_0_ready": all_four_passed,
        "stress_commands_per_app": STRESS_COMMANDS,
        "user_documents_modified": False,
        "paths_or_text_reported": False,
        "results": results,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apps",
        nargs="+",
        choices=APP_TYPES,
        default=APP_TYPES,
    )
    parser.add_argument("--output", type=Path, default=REPORT_PATH)
    parser.add_argument(
        "--timeout",
        type=int,
        default=None,
        help="Per-app owned-worker timeout override in seconds.",
    )
    args = parser.parse_args(argv)
    report = run_probe(tuple(args.apps), timeout=args.timeout)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
