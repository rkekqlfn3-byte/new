import hashlib
import tempfile
import unittest
from pathlib import Path

from engine.edit_mode import EditModeController, EditSessionManager
from engine.parser import CommandParser
from engine.workflows import HwpSecurityModuleUnavailable, WorkflowExecutor
from engine.workflows.business_workflow import file_fingerprint
from engine.learning import (
    BusinessWorkflowSkillManager,
    UserPreferenceLearningManager,
)


def digest(value):
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest().upper()


class Intake:
    def __init__(self):
        self.opened = []
        self.call_count = 0
        self.fail_on_call = None

    def connect_file(self, file_path):
        self.call_count += 1
        if self.call_count == self.fail_on_call:
            raise RuntimeError("simulated handoff rediscovery failure")
        path = Path(file_path).resolve()
        suffix = path.suffix.casefold()
        app_type = {
            ".xlsx": "excel",
            ".docx": "word",
            ".hwp": "hwp",
            ".pptx": "powerpoint",
        }[suffix]
        self.opened.append(str(path))
        return {
            "app_type": app_type,
            "file_path": str(path),
            "document_name": path.name,
            "window_handle": 100 + len(self.opened),
            "active_container": "매출",
            "selection_reference": "A1:B3",
            "launch_requested": len(self.opened) > 1,
        }


class NoLayout:
    enabled = False

    def arrange(self, session_id, document_handle):
        return {"success": False, "status": "skipped", "arranged": False}

    def set_enabled(self, enabled):
        self.enabled = bool(enabled)
        return self.enabled


class Activator:
    def __init__(self):
        self.handles = []

    def activate(self, handle):
        self.handles.append(int(handle))
        return {"success": True, "status": "focused", "focused": True}


class Context:
    def __init__(self):
        self.fail_app_type = None

    def capture(self, session):
        value = session.to_dict() if hasattr(session, "to_dict") else dict(session)
        app_type = str(value["app_type"])
        if app_type == self.fail_app_type:
            raise RuntimeError("simulated handoff context failure")
        if app_type == "excel":
            active_container = "매출"
            selection_reference = "A1:B3"
            selection_kind = "range"
            target = {"sheet_name": "매출", "address": "A1:B3"}
            cursor_reference = "A1"
        else:
            active_container = None
            selection_reference = "0:0"
            selection_kind = "cursor"
            target = {"start": 0, "end": 0}
            cursor_reference = "0"
        return {
            "schema_version": 1,
            "session_id": value["session_id"],
            "app_type": app_type,
            "file_path": value["file_path"],
            "document_name": value["document_name"],
            "document_fingerprint": value["document_fingerprint"],
            "context_fingerprint": "A" * 64,
            "read_only": False,
            "modified": False,
            "captured_at": "2026-07-17T12:00:00+09:00",
            "active_container": active_container,
            "selection_reference": selection_reference,
            "selection_kind": selection_kind,
            "target": target,
            "selected_text_preview": "",
            "selected_text_length": 0,
            "selected_text_digest": digest(""),
            "cursor_reference": cursor_reference,
        }


class Registry:
    def get(self, target):
        if str(target).casefold() != "excel":
            raise AssertionError(target)
        return object()  # Stage 10 does not invoke a cell-edit adapter.


class Analyzer:
    def __init__(self):
        self.calls = 0
        self.contexts = []

    def run(self, context):
        self.calls += 1
        self.contexts.append(dict(context))
        value = {
            "title": "매출 분석",
            "metrics": [{
                "name": "매출", "count": 2, "sum": 300,
                "average": 150, "minimum": 100, "maximum": 200,
            }],
            "tables": [{
                "name": "매출", "headers": ["지역", "매출"],
                "rows": [["서울", 100], ["부산", 200]],
                "total_rows": 2, "included_rows": 2,
            }],
            "charts": [],
            "insights": ["부산 매출이 가장 높습니다."],
            "source_files": [context["source_path"]],
        }
        join_plan = dict(context.get("join_plan") or {})
        if join_plan:
            value["tables"].append({
                "name": "고객↔주문 내부 조인",
                "headers": ["고객/고객ID", "주문/매출"],
                "rows": [[1, 300]],
                "total_rows": 1,
                "included_rows": 1,
                "derived": True,
                "join": {
                    **join_plan,
                    "cardinality": "one_to_many",
                    "output_rows": 1,
                    "included_rows": 1,
                    "truncated": False,
                },
            })
            value["insights"].insert(
                0, "승인한 내부 조인을 읽기 전용으로 검증했습니다."
            )
        return value


class Writer:
    def __init__(self, fail_times=0, slide_count=None):
        self.fail_times = fail_times
        self.slide_count = slide_count
        self.calls = 0

    def run(self, context, work_product):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise RuntimeError("simulated Office failure")
        path = Path(context["output_path"])
        path.write_bytes(path.suffix.encode("ascii"))
        verification = {"exists": True, "format": path.suffix[1:]}
        if self.slide_count is not None:
            verification["slide_count"] = self.slide_count
        return {
            "path": str(path),
            "fingerprint": file_fingerprint(path),
            "verification": verification,
        }


class BlockingHwpWriter(Writer):
    def __init__(self):
        super().__init__()
        self.preflight_calls = 0

    def preflight(self):
        self.preflight_calls += 1
        raise HwpSecurityModuleUnavailable(
            "한글 공식 보안 모듈을 설치·등록한 뒤 다시 요청해주세요."
        )


class Stage10WorkflowFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.source = self.root / "매출.xlsx"
        self.source.write_bytes(b"stage10-excel-fixture")
        self.analyzer = Analyzer()
        self.word = Writer()
        self.hwp = Writer()
        self.ppt = Writer(fail_times=1, slide_count=5)
        executor = WorkflowExecutor(
            self.root / "workflow-state",
            analyzer=self.analyzer,
            word_writer=self.word,
            hwp_writer=self.hwp,
            powerpoint_writer=self.ppt,
        )
        self.executor = executor
        self.intake = Intake()
        self.activator = Activator()
        self.workflow_skills = BusinessWorkflowSkillManager(
            self.root / "business-workflow-skills.json"
        )
        self.context_manager = Context()
        self.controller = EditModeController(
            intake_manager=self.intake,
            session_manager=EditSessionManager(),
            layout_manager=NoLayout(),
            context_manager=self.context_manager,
            native_action_registry=Registry(),
            workflow_executor=executor,
            workflow_skill_manager=self.workflow_skills,
            user_learning_manager=UserPreferenceLearningManager(
                self.root / "user-style-preferences.json"
            ),
            window_activator=self.activator,
        )
        self.parser = CommandParser(edit_mode_controller=self.controller)
        self.session = self.controller.connect_file(str(self.source))
        self.intake.opened.clear()
        self.activator.handles.clear()

    def tearDown(self):
        self.temp_dir.cleanup()

    def command(self, text, request_id):
        return self.parser.execute_command_result(
            text,
            mode="edit",
            session_id="stage10-chat",
            edit_context={
                "edit_session_id": self.session["session_id"],
                "document_fingerprint": self.session["document_fingerprint"],
                "context_fingerprint": "A" * 64,
                "request_id": request_id,
            },
        )

    def approve(self, result):
        confirmation = result["data"]["confirmation"]
        return self.parser.resolve_pending_confirmation(
            "stage10-chat",
            confirmation_id=confirmation["confirmation_id"],
            option_id="apply",
        )

    def cancel(self, result):
        confirmation = result["data"]["confirmation"]
        return self.parser.resolve_pending_confirmation(
            "stage10-chat",
            confirmation_id=confirmation["confirmation_id"],
            option_id="cancel",
        )

    def test_cancelled_preview_creates_no_state_and_is_not_resumable(self):
        preview = self.command(
            "이 엑셀을 분석해서 보고서와 7장짜리 PPT 만들어줘.",
            "stage10-cancel-preview",
        )
        self.assertEqual("confirmation_required", preview["status"])
        self.assertEqual([], list(self.executor.store_dir.glob("*.json")))

        cancelled = self.cancel(preview)
        self.assertEqual("user_cancelled", cancelled["error_type"])
        self.assertEqual([], list(self.executor.store_dir.glob("*.json")))
        self.assertIsNone(self.executor.latest_for_source(self.source))

        resume = self.command("실패한 워크플로 이어서", "stage10-no-resume")
        self.assertFalse(resume["success"])
        self.assertEqual("blocked", resume["status"])

    def test_preview_failure_and_resume_from_only_failed_step(self):
        preview = self.command(
            "이 엑셀을 분석해서 보고서와 5장짜리 PPT 만들어줘.",
            "stage10-create",
        )
        self.assertEqual("confirmation_required", preview["status"])
        self.assertEqual(0, self.analyzer.calls)
        self.assertEqual(0, self.word.calls)
        self.assertEqual(0, self.ppt.calls)
        self.assertEqual([], list(self.executor.store_dir.glob("*.json")))

        failed = self.approve(preview)
        self.assertFalse(failed["success"])
        self.assertIn("create_powerpoint_summary", failed["message"])
        self.assertEqual(1, self.analyzer.calls)
        self.assertEqual(1, self.word.calls)
        self.assertEqual(1, self.ppt.calls)

        cancelled_retry = self.command(
            "실패한 워크플로 이어서", "stage10-resume-cancel"
        )
        self.assertEqual("confirmation_required", cancelled_retry["status"])
        self.assertEqual(
            "user_cancelled", self.cancel(cancelled_retry)["error_type"]
        )
        self.assertEqual(
            "failed", self.executor.latest_for_source(self.source)["status"]
        )

        retry = self.command("실패한 워크플로 이어서", "stage10-resume")
        self.assertEqual("confirmation_required", retry["status"])
        completed = self.approve(retry)
        self.assertTrue(completed["success"], completed)
        self.assertTrue(completed["verified"])
        self.assertEqual("resume_business_workflow", completed["data"]["operation"])
        observations = completed["data"]["observations"]
        self.assertEqual(2, len(observations["created_files"]))
        self.assertEqual(5, observations["verification_results"]["create_powerpoint_summary"]["slide_count"])
        self.assertEqual(1, self.analyzer.calls)
        self.assertEqual(1, self.word.calls)
        self.assertEqual(2, self.ppt.calls)
        self.assertFalse(completed["data"]["undo_available"])
        self.assertEqual("ready", self.controller.status()["session"]["state"])

    def test_explicit_hwp_report_is_previewed_and_completed_without_word(self):
        self.ppt.fail_times = 0
        preview = self.command(
            "이 엑셀을 분석해서 한글 보고서와 5장짜리 PPT 만들어줘.",
            "stage10-hwp-create",
        )

        self.assertEqual("confirmation_required", preview["status"])
        self.assertIn("한글", preview["message"])
        completed = self.approve(preview)

        self.assertTrue(completed["success"])
        observations = completed["data"]["observations"]
        self.assertEqual("hwp", observations["report_format"])
        self.assertEqual(".hwp", Path(observations["output_paths"]["report"]).suffix)
        self.assertEqual(0, self.word.calls)
        self.assertEqual(1, self.hwp.calls)
        self.assertEqual(1, self.ppt.calls)
        self.assertIn("한글 보고서", completed["message"])

        handoff = self.command(
            "방금 만든 한글 보고서를 편집 문서로 연결해줘",
            "stage10-hwp-handoff",
        )

        self.assertTrue(handoff["success"], handoff)
        connected = handoff["data"]["edit_session_handoff"]
        self.assertEqual("hwp", connected["app_type"])
        self.assertEqual(
            str(Path(observations["output_paths"]["report"]).resolve()).casefold(),
            connected["file_path"].casefold(),
        )
        self.assertEqual(
            connected["session_id"],
            self.controller.status()["session"]["session_id"],
        )
        self.assertEqual("ready", self.controller.status()["session"]["state"])

    def test_missing_hwp_environment_blocks_before_confirmation_or_analysis(self):
        blocking_hwp = BlockingHwpWriter()
        self.executor.hwp_writer = blocking_hwp

        blocked = self.command(
            "이 엑셀을 분석해서 한글 보고서와 5장짜리 PPT 만들어줘.",
            "stage10-hwp-preflight-block",
        )

        self.assertFalse(blocked["success"])
        self.assertEqual("environment_error", blocked["error_type"])
        self.assertNotEqual("confirmation_required", blocked["status"])
        self.assertIn("설치·등록", blocked["message"])
        diagnostic = blocked["data"]["diagnostic_context"]
        self.assertEqual(
            "https://developer.hancom.com/hwpautomation",
            diagnostic["setup_guide_url"],
        )
        self.assertEqual(
            r"HKCU\Software\HNC\HwpAutomation\Modules",
            diagnostic["registry_location"],
        )
        self.assertFalse(diagnostic["automatic_install_attempted"])
        self.assertEqual(1, blocking_hwp.preflight_calls)
        self.assertEqual(0, self.analyzer.calls)
        self.assertEqual([], list(self.executor.store_dir.glob("*.json")))
        self.assertEqual("ready", self.controller.status()["session"]["state"])

    def test_word_and_hwp_reports_are_previewed_and_completed_together(self):
        self.ppt.fail_times = 0
        preview = self.command(
            "이 엑셀을 분석해서 Word와 한글 보고서, 5장짜리 PPT 만들어줘.",
            "stage10-both-create",
        )

        self.assertEqual("confirmation_required", preview["status"])
        self.assertIn("Word·한글 보고서", preview["message"])
        self.assertIn("Word:", preview["message"])
        self.assertIn("한글:", preview["message"])
        completed = self.approve(preview)

        self.assertTrue(completed["success"], completed)
        observations = completed["data"]["observations"]
        self.assertEqual("both", observations["report_format"])
        self.assertEqual(
            {"report_word", "report_hwp", "presentation"},
            set(observations["output_paths"]),
        )
        self.assertEqual(3, len(observations["created_files"]))
        self.assertEqual(1, self.word.calls)
        self.assertEqual(1, self.hwp.calls)
        self.assertEqual(1, self.ppt.calls)
        self.assertIn("Word·한글 보고서", completed["message"])

        ambiguous = self.command(
            "방금 만든 보고서를 편집 문서로 연결해줘",
            "stage10-both-ambiguous-handoff",
        )

        self.assertFalse(ambiguous["success"])
        self.assertIn("지정해주세요", ambiguous["message"])
        current = self.controller.status()["session"]
        self.assertEqual("excel", current["app_type"])
        self.assertEqual("ready", current["state"])

    def test_verified_workflow_becomes_approved_content_free_reusable_skill(self):
        self.ppt.fail_times = 0
        first_preview = self.command(
            "이 엑셀을 분석해서 Word 보고서와 5장짜리 PPT 만들어줘.",
            "workflow-skill-first",
        )
        first = self.approve(first_preview)

        self.assertTrue(first["success"])
        self.assertIn("성공한 단계 구조만 재사용 후보", first["message"])
        candidate = self.workflow_skills.latest_candidate()
        self.assertIsNotNone(candidate)
        self.assertIsNone(self.workflow_skills.active_skill())
        self.assertFalse(
            self.workflow_skills.status()["raw_paths_or_content_stored"]
        )

        remember = self.command(
            "이 워크플로 기억해",
            "workflow-skill-remember",
        )
        self.assertEqual("confirmation_required", remember["status"])
        self.assertIn("Excel 분석 → Word 보고서 → PowerPoint 5장", remember["message"])
        activated = self.approve(remember)
        self.assertTrue(activated["success"])
        self.assertIn("재사용 스킬에 활성화", activated["message"])

        reuse = self.command("지난번처럼 해줘", "workflow-skill-reuse")
        self.assertEqual("confirmation_required", reuse["status"])
        self.assertIn("승인된 재사용 스킬", reuse["message"])
        self.assertIn("현재 Excel 재검증", reuse["message"])
        reused = self.approve(reuse)

        self.assertTrue(reused["success"])
        self.assertTrue(reused["data"]["observations"]["workflow_skill_reused"])
        self.assertIn("모든 단계를 다시 검증", reused["message"])
        self.assertEqual(2, self.analyzer.calls)
        self.assertEqual(2, self.word.calls)
        self.assertEqual(2, self.ppt.calls)
        self.assertEqual(
            2,
            self.workflow_skills.active_skill()["verified_success_count"],
        )

    def test_reuse_requires_active_skill_and_current_explicit_values_override_it(self):
        self.ppt.fail_times = 0
        missing = self.command("지난번처럼 해줘", "workflow-skill-missing")
        self.assertFalse(missing["success"])
        self.assertEqual("blocked", missing["status"])

        first = self.approve(self.command(
            "이 엑셀을 분석해서 Word 보고서와 5장짜리 PPT 만들어줘.",
            "workflow-skill-override-first",
        ))
        self.assertTrue(first["success"])
        self.approve(self.command(
            "이 워크플로 기억해",
            "workflow-skill-override-remember",
        ))

        override = self.command(
            "지난번처럼 한글 보고서와 7장짜리 PPT로 해줘",
            "workflow-skill-explicit-override",
        )
        pending = self.parser.pending_confirmation_manager.active_record(
            "stage10-chat"
        )
        plan = pending["payload"]["prepared_action"]["arguments"]["workflow_plan"]

        self.assertEqual("confirmation_required", override["status"])
        self.assertEqual("hwp", plan["report_format"])
        self.assertEqual(7, plan["slide_count"])
        self.assertTrue(plan["explicit_slide_count"])

    def test_cancelling_workflow_skill_activation_keeps_reuse_disabled(self):
        self.ppt.fail_times = 0
        completed = self.approve(self.command(
            "이 엑셀을 분석해서 Word 보고서와 5장짜리 PPT 만들어줘.",
            "workflow-skill-cancel-first",
        ))
        self.assertTrue(completed["success"])
        remember = self.command(
            "이 워크플로 기억해",
            "workflow-skill-cancel-remember",
        )

        cancelled = self.cancel(remember)
        reuse = self.command(
            "지난번처럼 해줘",
            "workflow-skill-cancel-reuse",
        )

        self.assertEqual("user_cancelled", cancelled["error_type"])
        self.assertIsNone(self.workflow_skills.active_skill())
        self.assertIsNone(self.workflow_skills.latest_candidate())
        self.assertFalse(reuse["success"])
        self.assertEqual("blocked", reuse["status"])

    def test_current_excel_reference_routes_to_workflow_without_learning_slide_default(self):
        self.ppt.fail_times = 0
        preview = self.command(
            "이거 보고서랑 5장짜리 발표자료 만들어줘",
            "contextual-workflow-create",
        )

        self.assertEqual("confirmation_required", preview["status"])
        self.assertIn("현재 연결 Excel 전체", preview["message"])
        completed = self.approve(preview)
        self.assertTrue(completed["success"])
        self.assertEqual(1, self.analyzer.calls)

        explicit = self.command(
            "현재 엑셀을 한글 보고서와 발표자료 7장으로 정리해줘",
            "contextual-workflow-explicit",
        )
        pending = self.parser.pending_confirmation_manager.active_record(
            "stage10-chat"
        )
        plan = pending["payload"]["prepared_action"]["arguments"]["workflow_plan"]

        self.assertEqual("confirmation_required", explicit["status"])
        self.assertEqual("hwp", plan["report_format"])
        self.assertEqual(7, plan["slide_count"])
        preference_candidates = self.controller._learning_manager().list_candidates(
            include_observing=True
        )
        self.assertFalse(any(
            item.get("preference") == "ppt_slide_count"
            for item in preference_candidates
        ))

    def test_explicit_join_requires_complete_contract_and_is_not_learned(self):
        self.ppt.fail_times = 0
        incomplete = self.command(
            "고객 시트와 주문 시트를 고객ID로 조인해서 "
            "Word 보고서와 5장짜리 PPT 만들어줘",
            "join-incomplete",
        )

        self.assertFalse(incomplete["success"])
        self.assertEqual("validation_error", incomplete["error_type"])
        self.assertEqual("blocked", incomplete["status"])
        self.assertNotIn("confirmation", incomplete.get("data") or {})
        self.assertEqual(0, self.analyzer.calls)

        preview = self.command(
            "고객 시트의 고객ID와 주문 시트의 구매자ID로 내부 조인해서 "
            "Word 보고서와 5장짜리 PPT 만들어줘",
            "join-complete",
        )
        pending = self.parser.pending_confirmation_manager.active_record(
            "stage10-chat"
        )
        plan = pending["payload"]["prepared_action"]["arguments"]["workflow_plan"]

        self.assertEqual("confirmation_required", preview["status"])
        self.assertIn("읽기 전용 내부 조인", preview["message"])
        self.assertEqual("inner", plan["join_plan"]["join_type"])
        self.assertEqual("고객ID", plan["join_plan"]["left_key"])
        self.assertEqual("구매자ID", plan["join_plan"]["right_key"])
        self.assertIn("고객ID ↔ 구매자ID", preview["message"])
        self.assertEqual(0, self.analyzer.calls)

        completed = self.approve(preview)
        observations = completed["data"]["observations"]

        self.assertTrue(completed["success"], completed)
        self.assertEqual(1, self.analyzer.calls)
        self.assertEqual(
            plan["join_plan"], self.analyzer.contexts[-1]["join_plan"]
        )
        self.assertEqual(
            1,
            observations["verification_results"]["analyze_excel"][
                "join_summary_count"
            ],
        )
        self.assertIn("시트명과 키는 재사용 스킬로 저장하지 않았습니다", completed["message"])
        self.assertIsNone(self.workflow_skills.latest_candidate())

    def test_explicit_right_sum_aggregation_is_previewed_and_executed(self):
        self.ppt.fail_times = 0
        preview = self.command(
            "고객 시트의 고객ID와 주문 시트의 구매자ID로 주문 시트의 "
            "매출을 합계 집계해서 왼쪽 조인해서 Word 보고서와 "
            "5장짜리 PPT 만들어줘",
            "join-aggregate",
        )
        pending = self.parser.pending_confirmation_manager.active_record(
            "stage10-chat"
        )
        plan = pending["payload"]["prepared_action"]["arguments"]["workflow_plan"]

        self.assertEqual("confirmation_required", preview["status"])
        self.assertEqual(
            {"column": "매출", "function": "sum"},
            plan["join_plan"]["right_aggregation"],
        )
        self.assertIn("오른쪽 집계 주문/매출 합계", preview["message"])

        completed = self.approve(preview)

        self.assertTrue(completed["success"], completed)
        self.assertEqual(
            plan["join_plan"], self.analyzer.contexts[-1]["join_plan"]
        )
        self.assertIn("주문/매출 합계 집계", completed["message"])
        self.assertIsNone(self.workflow_skills.latest_candidate())

    def test_recent_verified_workflow_artifact_opens_exact_file_and_focuses(self):
        self.ppt.fail_times = 0
        completed = self.approve(self.command(
            "이거 보고서랑 5장짜리 발표자료 만들어줘",
            "recent-artifact-create",
        ))
        report_path = completed["data"]["observations"]["output_paths"]["report"]

        opened = self.command(
            "방금 만든 보고서 열어줘",
            "recent-artifact-open",
        )

        self.assertTrue(opened["success"], opened)
        self.assertEqual(
            "open_recent_workflow_artifact",
            opened["data"]["operation"],
        )
        self.assertEqual([str(Path(report_path).resolve())], self.intake.opened)
        self.assertEqual([101], self.activator.handles)
        self.assertIn("맨 앞으로", opened["message"])
        self.assertEqual("ready", self.controller.status()["session"]["state"])

    def test_recent_artifact_changed_after_prepare_fails_closed(self):
        self.ppt.fail_times = 0
        completed = self.approve(self.command(
            "이거 보고서랑 5장짜리 발표자료 만들어줘",
            "changed-artifact-create",
        ))
        report_path = Path(
            completed["data"]["observations"]["output_paths"]["report"]
        )
        report_path.write_bytes(b"user-changed-after-workflow")

        blocked = self.command(
            "방금 만든 보고서 열어줘",
            "changed-artifact-open",
        )

        self.assertFalse(blocked["success"])
        self.assertEqual([], self.intake.opened)
        self.assertIn("이동·수정·삭제", blocked["message"])

    def test_recent_artifacts_can_explicitly_replace_the_edit_session(self):
        self.ppt.fail_times = 0
        completed = self.approve(self.command(
            "이거 보고서랑 5장짜리 발표자료 만들어줘",
            "handoff-artifact-create",
        ))
        report_path = completed["data"]["observations"]["output_paths"]["report"]
        source_session_id = self.session["session_id"]

        handoff = self.command(
            "방금 만든 보고서를 편집 문서로 연결해줘",
            "handoff-artifact-connect",
        )

        self.assertTrue(handoff["success"], handoff)
        self.assertEqual(
            "connect_recent_workflow_artifact",
            handoff["data"]["operation"],
        )
        self.assertEqual(
            source_session_id,
            handoff["data"]["source_edit_session_id"],
        )
        connected = handoff["data"]["edit_session_handoff"]
        self.assertEqual("word", connected["app_type"])
        self.assertEqual(
            str(Path(report_path).resolve()).casefold(),
            connected["file_path"].casefold(),
        )
        self.assertEqual(
            connected["session_id"],
            handoff["data"]["edit_session_id"],
        )
        self.assertEqual("word", connected["context"]["app_type"])
        self.assertEqual(
            connected["session_id"],
            self.controller.status()["session"]["session_id"],
        )
        self.assertEqual(
            "ready",
            self.controller.status()["session"]["state"],
        )
        self.assertIn("새 편집 대상으로 연결", handoff["message"])

        self.session = self.controller.connect_file(str(self.source))
        presentation_handoff = self.command(
            "방금 만든 발표자료를 편집 문서로 연결해줘",
            "handoff-presentation-connect",
        )

        self.assertTrue(presentation_handoff["success"], presentation_handoff)
        presentation = presentation_handoff["data"]["edit_session_handoff"]
        presentation_path = completed["data"]["observations"]["output_paths"][
            "presentation"
        ]
        self.assertEqual("powerpoint", presentation["app_type"])
        self.assertEqual(
            str(Path(presentation_path).resolve()).casefold(),
            presentation["file_path"].casefold(),
        )
        self.assertEqual(
            presentation["session_id"],
            self.controller.status()["session"]["session_id"],
        )
        self.assertEqual(
            "ready",
            self.controller.status()["session"]["state"],
        )

    def test_failed_handoff_rediscovery_keeps_source_session_ready(self):
        self.ppt.fail_times = 0
        self.approve(self.command(
            "이거 보고서랑 5장짜리 발표자료 만들어줘",
            "handoff-failure-create",
        ))
        source_session_id = self.session["session_id"]
        self.intake.fail_on_call = self.intake.call_count + 2

        failed = self.command(
            "방금 만든 보고서를 편집 문서로 연결해줘",
            "handoff-failure-connect",
        )

        self.assertFalse(failed["success"])
        current = self.controller.status()["session"]
        self.assertEqual(source_session_id, current["session_id"])
        self.assertEqual("excel", current["app_type"])
        self.assertEqual("ready", current["state"])

        self.context_manager.fail_app_type = "word"
        context_failed = self.command(
            "방금 만든 보고서를 편집 문서로 연결해줘",
            "handoff-context-failure-connect",
        )

        self.assertFalse(context_failed["success"])
        current = self.controller.status()["session"]
        self.assertEqual(source_session_id, current["session_id"])
        self.assertEqual("excel", current["app_type"])
        self.assertEqual("ready", current["state"])


if __name__ == "__main__":
    unittest.main()
