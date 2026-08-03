"""Owned-fixture live probe for Stage 6 Word and PowerPoint editing."""

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

import psutil

from engine.app_actions.powerpoint_adapter import PowerPointAdapter
from engine.app_actions.word_adapter import WordAdapter
from engine.edit_mode import EditContextManager, EditModeController, EditSessionManager
from engine.edit_mode.context import NativeDocumentContextReader
from engine.edit_mode.native_bridge import NativeDocumentBridge
from engine.learning import UserPreferenceLearningManager
from engine.parser import CommandParser
from verification.prototype1_stage3_probe import (
    APP_SPECS,
    _process_ids,
    _wait_for_processes,
)
from verification.source_identity import source_identity

ROOT = Path(__file__).resolve().parent.parent
REPORT_PATH = ROOT / "verification" / "prototype1_stage6_report.json"


class _NoLayout:
    enabled = False

    def arrange(self, session_id, document_handle):
        return {"success": False, "status": "skipped", "arranged": False}

    def set_enabled(self, enabled):
        self.enabled = bool(enabled)
        return self.enabled


class _Registry:
    def __init__(self, app_type, adapter):
        self.app_type = app_type
        self.adapter = adapter

    def get(self, target):
        if str(target).casefold() != self.app_type:
            raise RuntimeError("다른 앱 어댑터 요청을 차단했습니다.")
        return self.adapter


class _OwnedOfficeProvider:
    def __init__(self, app_type, application, document):
        self.app_type = app_type
        self.application = application
        self.document = document

    def capture(self, session):
        method = getattr(NativeDocumentContextReader, f"_capture_{self.app_type}")
        return method(self.application, self.document)


def _session_parser(
    app_type,
    path,
    context_manager,
    native_adapter,
    user_learning_manager=None,
):
    sessions = EditSessionManager()
    session = sessions.connect({
        "app_type": app_type,
        "file_path": str(path),
        "document_name": path.name,
        "window_handle": 0,
        "launch_requested": True,
    })
    controller = EditModeController(
        session_manager=sessions,
        context_manager=context_manager,
        native_action_registry=_Registry(app_type, native_adapter),
        layout_manager=_NoLayout(),
        user_learning_manager=user_learning_manager,
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


def _approve(parser, controller, session, command, request_id):
    chat_id = "stage6-owned-fixture"
    preview = parser.execute_command_result(
        command,
        mode="edit",
        session_id=chat_id,
        edit_context=_edit_context(controller, session, request_id),
    )
    if preview.get("status") != "confirmation_required":
        raise RuntimeError(f"편집 미리보기 생성 실패: {preview.get('status')}")
    confirmation = preview["data"]["confirmation"]
    result = parser.resolve_pending_confirmation(
        chat_id,
        confirmation_id=confirmation["confirmation_id"],
        option_id="apply",
    )
    if not result.get("success") or not result.get("verified"):
        raise RuntimeError(f"승인된 편집 검증 실패: {result.get('status')}")
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
    learning_manager = None
    temp_dir = tempfile.mkdtemp(prefix="jarvis-stage6-word-")
    path = Path(temp_dir) / f"stage6-{uuid.uuid4().hex}.docx"
    original = "6단계 Word 선택 문장입니다."
    replacement = "6단계 Word 승인 편집이 검증됐습니다."
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
        manager = EditContextManager(providers=(provider,))
        adapter = WordAdapter(
            application_getter=lambda: application,
            require_visible=False,
        )
        learning_manager = UserPreferenceLearningManager(
            Path(temp_dir) / "word-direct-edit-preferences.json"
        )
        session, controller, parser = _session_parser(
            "word",
            path,
            manager,
            adapter,
            user_learning_manager=learning_manager,
        )

        stage = "approved_replace"
        replaced = _approve(
            parser,
            controller,
            session,
            f'선택 문장을 "{replacement}"으로 바꿔줘',
            "stage6-word-replace",
        )
        text_verified = str(document.Range(0, len(replacement)).Text) == replacement

        stage = "observe_collapsed_word_formatting_correction"
        post_action = dict(
            controller.session_manager.continuation_state(
                session["session_id"]
            ).get("last_action") or {}
        )
        application.Selection.SetRange(0, 0)
        cursor_session, cursor_context = controller._capture_context(
            controller.session_manager.current()
        )
        collapsed_contract_matched = (
            controller._defer_word_collapsed_cursor(
                cursor_session, post_action, cursor_context
            )
        )
        collapsed = controller.status()
        collapsed_deferred = bool(
            collapsed.get("session", {}).get("last_action")
        ) and not collapsed.get("direct_edit_feedback")
        application.Selection.SetRange(0, len(replacement))
        reselected = controller.status()
        reselect_deferred = bool(
            reselected.get("session", {}).get("last_action")
        ) and not reselected.get("direct_edit_feedback")
        previous_bold = int(application.Selection.Font.Bold)
        changed_bold = 0 if previous_bold else -1
        application.Selection.Font.Bold = changed_bold
        observed = controller.status()
        feedback = dict(observed.get("direct_edit_feedback") or {})
        expected_emphasis = "bold" if changed_bold else "regular"
        matching_candidates = [
            item
            for item in learning_manager.list_candidates(
                include_observing=True
            )
            if item.get("preference") == "emphasis_style"
            and item.get("proposed_value") == expected_emphasis
        ]
        direct_observation_verified = (
            collapsed_deferred
            and reselect_deferred
            and feedback.get("recorded") is True
            and feedback.get("source") == "verified_direct_edit"
            and feedback.get("observation_kind") == "formatting"
            and feedback.get("preference") == "emphasis_style"
            and feedback.get("value") == expected_emphasis
            and feedback.get("raw_content_stored") is False
            and len(matching_candidates) == 1
            and int(matching_candidates[0].get("evidence_count") or 0) == 1
        )

        stage = "approved_font"
        application.Selection.SetRange(0, len(replacement))
        before_size = float(application.Selection.Font.Size)
        formatted = _approve(
            parser,
            controller,
            session,
            "글자를 조금 크게 해줘",
            "stage6-word-font",
        )
        font_verified = abs(float(application.Selection.Font.Size) - (before_size + 2)) < 0.01

        stage = "approved_save"
        saved = _approve(
            parser, controller, session, "저장해줘", "stage6-word-save"
        )
        save_verified = bool(document.Saved) and path.is_file()
        verified = all((
            text_verified,
            direct_observation_verified,
            font_verified,
            save_verified,
            replaced["verified"],
            formatted["verified"],
            saved["verified"],
        ))
        return {
            "status": "passed" if verified else "failed",
            "owned_fixture_only": True,
            "user_process_protected": True,
            "preview_approval_verified": True,
            "selection_replace_readback_verified": text_verified,
            "collapsed_word_direct_observation_verified": (
                direct_observation_verified
            ),
            "word_collapsed_cursor_deferred": collapsed_deferred,
            "word_collapsed_cursor_contract_matched": (
                collapsed_contract_matched
            ),
            "word_reselection_deferred": reselect_deferred,
            "word_direct_feedback_recorded": (
                feedback.get("recorded") is True
            ),
            "word_direct_candidate_recorded": (
                len(matching_candidates) == 1
            ),
            "font_readback_verified": font_verified,
            "save_readback_verified": save_verified,
            "session_returned_ready": controller.status()["session"]["state"] == "ready",
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
        parser = controller = provider = learning_manager = None
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

    application = presentation = provider = controller = parser = None
    learning_manager = None
    first = second = shape = None
    temp_dir = tempfile.mkdtemp(prefix="jarvis-stage6-powerpoint-")
    path = Path(temp_dir) / f"stage6-{uuid.uuid4().hex}.pptx"
    replacement = "6단계 PowerPoint 편집 검증"
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
        second.Shapes.Title.TextFrame.TextRange.Font.Size = 28
        presentation.SaveAs(str(path), 24)
        application.ActiveWindow.View.GotoSlide(2)
        shape = second.Shapes.Title
        shape.Select()

        stage = "prepare_controller"
        provider = _OwnedOfficeProvider("powerpoint", application, presentation)
        manager = EditContextManager(providers=(provider,))
        adapter = PowerPointAdapter(application_getter=lambda: application)
        learning_manager = UserPreferenceLearningManager(
            Path(temp_dir) / "powerpoint-direct-edit-preferences.json"
        )
        session, controller, parser = _session_parser(
            "powerpoint",
            path,
            manager,
            adapter,
            user_learning_manager=learning_manager,
        )

        stage = "approved_replace"
        replaced = _approve(
            parser,
            controller,
            session,
            f'제목을 "{replacement}"으로 바꿔줘',
            "stage6-ppt-replace",
        )
        text_verified = str(shape.TextFrame.TextRange.Text) == replacement

        stage = "observe_direct_shape_formatting_correction"
        previous_bold = int(shape.TextFrame.TextRange.Font.Bold)
        changed_bold = 0 if previous_bold else -1
        shape.TextFrame.TextRange.Font.Bold = changed_bold
        shape.Select()
        observed_status = controller.status()
        feedback = dict(observed_status.get("direct_edit_feedback") or {})
        expected_value = "bold" if changed_bold else "regular"
        direct_observation_verified = (
            feedback.get("recorded") is True
            and feedback.get("source") == "verified_direct_edit"
            and feedback.get("observation_kind") == "formatting"
            and feedback.get("preference") == "emphasis_style"
            and feedback.get("value") == expected_value
            and feedback.get("raw_content_stored") is False
            and len(
                learning_manager.list_candidates(include_observing=True)
            ) == 1
        )

        stage = "approved_move"
        shape.Select()
        before_left = float(shape.Left)
        moved = _approve(
            parser,
            controller,
            session,
            "Shape를 오른쪽으로 10pt 이동해줘",
            "stage6-ppt-move",
        )
        move_verified = abs(float(shape.Left) - (before_left + 10)) < 0.05

        stage = "approved_previous_style"
        shape.Select()
        matched = _approve(
            parser,
            controller,
            session,
            "앞 슬라이드와 같은 스타일로 맞춰줘",
            "stage6-ppt-style",
        )
        style_verified = (
            abs(float(shape.TextFrame.TextRange.Font.Size) - 34) < 0.01
            and int(shape.TextFrame.TextRange.Font.Bold) == -1
        )
        verified = all((
            text_verified,
            direct_observation_verified,
            move_verified,
            style_verified,
            replaced["verified"],
            moved["verified"],
            matched["verified"],
        ))
        return {
            "status": "passed" if verified else "failed",
            "owned_fixture_only": True,
            "user_process_protected": True,
            "preview_approval_verified": True,
            "shape_text_readback_verified": text_verified,
            "collapsed_shape_direct_observation_verified": (
                direct_observation_verified
            ),
            "shape_move_readback_verified": move_verified,
            "previous_style_readback_verified": style_verified,
            "session_returned_ready": controller.status()["session"]["state"] == "ready",
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
        parser = controller = provider = learning_manager = None
        shape = second = first = None
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


PROBES = {"word": _probe_word, "powerpoint": _probe_powerpoint}


def _probe_worker(app_type, output):
    try:
        output.put(PROBES[app_type]())
    except BaseException as error:
        output.put({
            "status": "failed",
            "error_type": type(error).__name__,
            "message": str(error),
            "user_process_protected": True,
        })


def _stop_created_processes(process_names, baseline):
    for process_id in _process_ids(process_names) - baseline:
        try:
            process = psutil.Process(process_id)
            process.terminate()
            process.wait(timeout=3)
        except psutil.TimeoutExpired:
            try:
                process.kill()
                process.wait(timeout=3)
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.TimeoutExpired):
                pass
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass


def _run_isolated(app_type, timeout=50):
    spec = APP_SPECS[app_type]
    baseline = _process_ids(spec["processes"])
    if baseline:
        return {"status": "skipped_user_processes_running", "user_process_protected": True}
    context = multiprocessing.get_context("spawn")
    output = context.Queue(maxsize=1)
    process = context.Process(target=_probe_worker, args=(app_type, output))
    process.start()
    process.join(timeout)
    if process.is_alive():
        process.terminate()
        process.join(5)
        _stop_created_processes(spec["processes"], baseline)
        return {
            "status": "failed",
            "stage": "owned_fixture_timeout",
            "error_type": "TimeoutError",
            "message": f"{app_type} 소유 문서 검증이 {timeout}초 안에 응답하지 않았습니다.",
            "user_process_protected": True,
            "owned_process_cleanup_verified": not (
                _process_ids(spec["processes"]) - baseline
            ),
        }
    try:
        return output.get(timeout=2)
    except queue.Empty:
        _stop_created_processes(spec["processes"], baseline)
        return {
            "status": "failed",
            "stage": "owned_fixture_worker",
            "error_type": "RuntimeError",
            "message": "소유 문서 검증 프로세스가 결과 없이 종료됐습니다.",
            "user_process_protected": True,
        }


def run_probe(apps=("word", "powerpoint")):
    results = {app: _run_isolated(app) for app in apps}
    attempted = [
        value
        for value in results.values()
        if value["status"] not in {"unavailable", "skipped_user_processes_running"}
    ]
    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "source": source_identity(ROOT),
        "probe": "prototype1_stage6_owned_fixture_word_powerpoint_editing",
        "success": all(value["status"] == "passed" for value in attempted),
        "user_documents_modified": False,
        "paths_or_text_reported": False,
        "results": results,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apps",
        nargs="+",
        choices=tuple(PROBES),
        default=tuple(PROBES),
    )
    parser.add_argument("--output", type=Path, default=REPORT_PATH)
    args = parser.parse_args(argv)
    report = run_probe(tuple(dict.fromkeys(args.apps)))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
