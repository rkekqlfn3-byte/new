"""Owned-fixture live probe for Stage 7 continuous Word editing and undo."""

from __future__ import annotations

import argparse
import gc
import json
import multiprocessing
import queue
import shutil
import tempfile
import time
import uuid
from pathlib import Path

from engine.app_actions.word_adapter import WordAdapter
from engine.edit_mode import EditContextManager, EditModeController, EditSessionManager
from engine.edit_mode.context import NativeDocumentContextReader
from engine.edit_mode.native_bridge import NativeDocumentBridge
from engine.parser import CommandParser
from verification.prototype1_stage3_probe import (
    APP_SPECS,
    _process_ids,
    _wait_for_processes,
)
from verification.prototype1_stage6_probe import _NoLayout, _OwnedOfficeProvider, _Registry


ROOT = Path(__file__).resolve().parent.parent
REPORT_PATH = ROOT / "verification" / "prototype1_stage7_report.json"


def _edit_context(controller, session, request_id):
    context = controller.context(session["session_id"])
    return {
        "edit_session_id": session["session_id"],
        "document_fingerprint": session["document_fingerprint"],
        "context_fingerprint": context["context_fingerprint"],
        "request_id": request_id,
    }


def _approve(parser, controller, session, command, request_id):
    chat_id = "stage7-owned-fixture"
    preview = parser.execute_command_result(
        command,
        mode="edit",
        session_id=chat_id,
        edit_context=_edit_context(controller, session, request_id),
    )
    if preview.get("status") != "confirmation_required":
        raise RuntimeError(
            "편집 미리보기 생성 실패: "
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
        raise RuntimeError(f"연속 편집 검증 실패: {result.get('status')}")
    return result


def _undo(parser, controller, session):
    result = parser.execute_command_result(
        "방금 거 취소해",
        mode="edit",
        session_id="stage7-owned-fixture",
        edit_context=_edit_context(controller, session, "stage7-word-undo"),
    )
    if not result.get("success") or not result.get("verified"):
        raise RuntimeError(f"실행 취소 검증 실패: {result.get('status')}")
    return result


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
    temp_dir = tempfile.mkdtemp(prefix="jarvis-stage7-word-")
    path = Path(temp_dir) / f"stage7-{uuid.uuid4().hex}.docx"
    original = (
        "7단계 연속 편집 검증을 위해 충분히 긴 원문을 준비하고 선택 영역이 매번 "
        "유지되는지 확인하며 각 수정 결과가 다음 요청의 정확한 문맥으로 이어지는지 "
        "검증하고 중간에 다른 문서나 다른 선택 대상으로 넘어가지 않는지 살피면서 "
        "다섯 번의 연속 수정이 모두 끝난 뒤 마지막 편집만 직전 상태로 안전하게 "
        "복원되는지 다시 읽어 확인합니다."
    )
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

        stage = "prepare_controller"
        provider = _OwnedOfficeProvider("word", application, document)
        context_manager = EditContextManager(providers=(provider,))
        adapter = WordAdapter(
            application_getter=lambda: application,
            require_visible=False,
        )
        sessions = EditSessionManager()
        session = sessions.connect({
            "app_type": "word",
            "file_path": str(path),
            "document_name": path.name,
            "window_handle": 0,
            "launch_requested": True,
        })
        controller = EditModeController(
            session_manager=sessions,
            context_manager=context_manager,
            native_action_registry=_Registry("word", adapter),
            layout_manager=_NoLayout(),
        )
        parser = CommandParser(edit_mode_controller=controller)

        stage = "five_continuous_edits"
        sequence = []
        before_lengths = []
        after_lengths = []
        for index, command in enumerate(
            ["조금 줄여줘", "조금 더", "조금 더", "조금 더", "조금 더"],
            start=1,
        ):
            stage = f"continuous_edit_{index}"
            before_lengths.append(len(str(application.Selection.Text or "")))
            result = _approve(
                parser, controller, session, command, f"stage7-word-{index}"
            )
            sequence.append(int(result["data"].get("sequence_count") or 0))
            after_lengths.append(len(str(application.Selection.Text or "")))

        stage = "verified_undo"
        text_before_undo = str(application.Selection.Text or "")
        undone = _undo(parser, controller, session)
        text_after_undo = str(application.Selection.Text or "")
        status = controller.status()["session"]
        verified = all((
            sequence == [1, 2, 3, 4, 5],
            all(after < before for before, after in zip(before_lengths, after_lengths)),
            len(text_after_undo) > len(text_before_undo),
            undone["data"].get("operation") == "undo_last_edit",
            status.get("last_action") is None,
            status.get("undo_record") is None,
            status.get("state") == "ready",
        ))
        return {
            "status": "passed" if verified else "failed",
            "owned_fixture_only": True,
            "user_process_protected": True,
            "continuous_edit_count": len(sequence),
            "sequence_counts_verified": sequence == [1, 2, 3, 4, 5],
            "selection_retained_verified": all(
                after < before for before, after in zip(before_lengths, after_lengths)
            ),
            "verified_undo": len(text_after_undo) > len(text_before_undo),
            "continuation_cleared_after_undo": (
                status.get("last_action") is None and status.get("undo_record") is None
            ),
            "session_returned_ready": status.get("state") == "ready",
        }
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


def _probe_worker(output):
    try:
        output.put(_probe_word())
    except BaseException as error:
        output.put({
            "status": "failed",
            "error_type": type(error).__name__,
            "message": str(error),
            "user_process_protected": True,
        })


def _run_isolated(timeout=60):
    spec = APP_SPECS["word"]
    baseline = _process_ids(spec["processes"])
    if baseline:
        return {"status": "skipped_user_processes_running", "user_process_protected": True}
    context = multiprocessing.get_context("spawn")
    output = context.Queue(maxsize=1)
    process = context.Process(target=_probe_worker, args=(output,))
    process.start()
    process.join(timeout)
    if process.is_alive():
        process.terminate()
        process.join(5)
        return {
            "status": "failed",
            "stage": "owned_fixture_timeout",
            "error_type": "TimeoutError",
            "message": f"Word 소유 문서 검증이 {timeout}초 안에 응답하지 않았습니다.",
            "user_process_protected": True,
        }
    try:
        return output.get(timeout=2)
    except queue.Empty:
        return {
            "status": "failed",
            "stage": "owned_fixture_worker",
            "error_type": "RuntimeError",
            "message": "소유 문서 검증 프로세스가 결과 없이 종료됐습니다.",
            "user_process_protected": True,
        }


def run_probe():
    result = _run_isolated()
    attempted = result["status"] not in {
        "unavailable",
        "skipped_user_processes_running",
    }
    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "probe": "prototype1_stage7_continuous_word_editing_and_undo",
        "success": not attempted or result["status"] == "passed",
        "user_documents_modified": False,
        "paths_or_text_reported": False,
        "results": {"word": result},
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=REPORT_PATH)
    args = parser.parse_args(argv)
    report = run_probe()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
