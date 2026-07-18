import tempfile
import unittest
from pathlib import Path

from engine.edit_mode import EditModeController, EditSessionManager
from engine.learning import (
    BusinessWorkflowSkillManager,
    UserPreferenceLearningManager,
)
from engine.parser import CommandParser
from engine.workflows import WorkflowExecutor
from engine.workflows.business_workflow import file_fingerprint
from tests.integration.test_stage10_workflow_flow import (
    Analyzer,
    Context,
    Intake,
    NoLayout,
    Registry,
)
from engine.app_actions.excel_vba_adapter import ExcelVbaAdapter
from tests.integration.test_stage9_vba_flow import (
    Context as VbaContext,
    Intake as VbaIntake,
    NoLayout as VbaNoLayout,
    Registry as VbaRegistry,
)
from tests.windows.test_excel_vba_actions import (
    FakeApplication,
    FakeComponent,
    FakeProject,
    FakeWorkbook,
)
from tests.integration.test_stage6_edit_flow import (
    FakeIntakeManager as OfficeIntake,
    FakeLayoutManager as OfficeLayout,
    FakeNativeAdapter as OfficeNativeAdapter,
    FakeRegistry as OfficeRegistry,
    StaticContextManager as OfficeContext,
)


class RecordingWriter:
    def __init__(self):
        self.calls = 0
        self.contexts = []

    def run(self, context, work_product):
        self.calls += 1
        self.contexts.append(dict(context))
        path = Path(context["output_path"])
        path.write_bytes(f"artifact-{self.calls}".encode("ascii"))
        verification = {"exists": True, "format": path.suffix[1:]}
        if path.suffix.casefold() == ".pptx":
            verification.update({
                "slide_count": int(context["slide_count"]),
                "expected_slide_count": int(context["slide_count"]),
            })
        return {
            "path": str(path),
            "fingerprint": file_fingerprint(path),
            "verification": verification,
        }


class Stage11UserLearningFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.source = self.root / "매출.xlsx"
        self.source.write_bytes(b"stage11-excel-fixture")
        self.learning = UserPreferenceLearningManager(
            self.root / "user-style-preferences.json"
        )
        self.analyzer = Analyzer()
        self.word = RecordingWriter()
        self.ppt = RecordingWriter()
        executor = WorkflowExecutor(
            self.root / "workflow-state",
            analyzer=self.analyzer,
            word_writer=self.word,
            powerpoint_writer=self.ppt,
        )
        self.controller = EditModeController(
            intake_manager=Intake(),
            session_manager=EditSessionManager(),
            layout_manager=NoLayout(),
            context_manager=Context(),
            native_action_registry=Registry(),
            workflow_executor=executor,
            workflow_skill_manager=BusinessWorkflowSkillManager(
                self.root / "business-workflow-skills.json"
            ),
            user_learning_manager=self.learning,
        )
        self.parser = CommandParser(edit_mode_controller=self.controller)
        self.session = self.controller.connect_file(str(self.source))

    def tearDown(self):
        self.temp_dir.cleanup()

    def command(self, text, request_id):
        return self.parser.execute_command_result(
            text,
            mode="edit",
            session_id="stage11-chat",
            edit_context={
                "edit_session_id": self.session["session_id"],
                "document_fingerprint": self.session["document_fingerprint"],
                "context_fingerprint": "A" * 64,
                "request_id": request_id,
            },
        )

    def resolve(self, result, option_id="apply"):
        confirmation = result["data"]["confirmation"]
        return self.parser.resolve_pending_confirmation(
            "stage11-chat",
            confirmation_id=confirmation["confirmation_id"],
            option_id=option_id,
        )

    def learn_ppt_count(self, value=7, prefix="learn"):
        results = []
        for index in range(1, 4):
            results.append(self.command(
                f"PPT는 항상 {value}장으로 만들어줘",
                f"{prefix}-{index}",
            ))
        return results

    def activate_app_preference(self, preference, value, app_id="word"):
        candidate = None
        for index in range(1, 4):
            candidate = self.learning.record_evidence(
                preference,
                value,
                scope_kind="app",
                scope_id=app_id,
                evidence_id=f"active-{preference}-{index}",
            )
        return self.learning.activate(candidate["candidate_id"])

    def test_three_observations_then_approval_apply_default_to_next_workflow(self):
        first, second, third = self.learn_ppt_count()
        self.assertTrue(first["success"])
        self.assertTrue(second["success"])
        self.assertEqual("confirmation_required", third["status"])
        self.assertIsNone(
            self.learning.resolve("ppt_slide_count", app_id="powerpoint")
        )

        activated = self.resolve(third)
        self.assertTrue(activated["success"])
        self.assertEqual(
            7,
            self.learning.resolve(
                "ppt_slide_count", app_id="powerpoint"
            )["value"],
        )

        preview = self.command(
            "이 엑셀을 분석해서 보고서와 PPT 만들어줘.",
            "workflow-preferred-slides",
        )
        self.assertEqual("confirmation_required", preview["status"])
        completed = self.resolve(preview)
        self.assertTrue(completed["success"])
        observations = completed["data"]["observations"]
        self.assertEqual(7, observations["slide_count"])
        self.assertEqual(7, self.ppt.contexts[-1]["slide_count"])
        self.assertEqual(
            7, observations["applied_preferences"]["ppt_slide_count"]
        )

    def test_explicit_workflow_value_overrides_active_default(self):
        _, _, third = self.learn_ppt_count()
        self.resolve(third)
        preview = self.command(
            "이 엑셀을 분석해서 보고서와 5장짜리 PPT 만들어줘.",
            "workflow-explicit-slides",
        )
        completed = self.resolve(preview)
        self.assertTrue(completed["success"])
        self.assertEqual(5, completed["data"]["observations"]["slide_count"])
        self.assertEqual(5, self.ppt.contexts[-1]["slide_count"])
        self.assertNotIn(
            "ppt_slide_count",
            completed["data"]["observations"]["applied_preferences"],
        )

    def test_confirmed_word_formatting_defaults_are_visible_and_forwarded(self):
        self.activate_app_preference("emphasis_style", "bold")
        self.activate_app_preference("font_scale", "larger")
        self.activate_app_preference("paragraph_align", "center")

        preview = self.command(
            "이 엑셀을 분석해서 보고서와 PPT 만들어줘.",
            "workflow-word-formatting-defaults",
        )

        self.assertEqual("confirmation_required", preview["status"])
        self.assertIn("적용할 학습 기본값", preview["message"])
        self.assertIn("글자 강조=굵게", preview["message"])
        pending = self.parser.pending_confirmation_manager.active_record(
            "stage11-chat"
        )
        prepared = pending["payload"]["prepared_action"]
        applied = prepared["metadata"]["applied_user_preferences"]
        self.assertEqual("bold", applied["emphasis_style"])
        self.assertEqual("larger", applied["font_scale"])
        self.assertEqual("center", applied["paragraph_align"])

        completed = self.resolve(preview)

        self.assertTrue(completed["success"])
        preferences = self.word.contexts[-1]["preferences"]
        self.assertEqual("bold", preferences["emphasis_style"])
        self.assertEqual("larger", preferences["font_scale"])
        self.assertEqual("center", preferences["paragraph_align"])

    def test_cancelled_candidate_does_not_activate(self):
        _, _, third = self.learn_ppt_count(prefix="cancel")
        cancelled = self.resolve(third, option_id="cancel")
        self.assertFalse(cancelled["success"])
        self.assertEqual("user_cancelled", cancelled["error_type"])
        self.assertIsNone(
            self.learning.resolve("ppt_slide_count", app_id="powerpoint")
        )
        candidates = self.learning.list_candidates(include_observing=True)
        self.assertEqual("dismissed", candidates[0]["status"])

    def test_conflicting_active_default_is_explained_and_replaced_only_after_approval(self):
        _, _, initial = self.learn_ppt_count(value=7, prefix="initial-seven")
        self.resolve(initial)

        responses = [
            self.command(
                "PPT는 항상 5장으로 만들어줘",
                f"replacement-five-{index}",
            )
            for index in range(1, 10)
        ]

        self.assertTrue(all(item["success"] for item in responses[:-1]))
        replacement = responses[-1]
        self.assertEqual("confirmation_required", replacement["status"])
        self.assertIn("현재 승인 기본값: 7", replacement["message"])
        self.assertIn("승인 시 새 기본값: 5", replacement["message"])
        self.assertIn("기존 기본값을 교체", replacement["message"])
        self.assertEqual(
            7,
            self.learning.resolve(
                "ppt_slide_count", app_id="powerpoint"
            )["value"],
        )

        completed = self.resolve(replacement)

        self.assertTrue(completed["success"])
        self.assertIn("사용자 승인으로 교체했습니다", completed["message"])
        self.assertEqual(
            5,
            self.learning.resolve(
                "ppt_slide_count", app_id="powerpoint"
            )["value"],
        )

    def test_cancelling_replacement_explains_that_existing_default_was_kept(self):
        _, _, initial = self.learn_ppt_count(value=7, prefix="keep-seven")
        self.resolve(initial)
        replacement = None
        for index in range(1, 10):
            replacement = self.command(
                "PPT는 항상 5장으로 만들어줘",
                f"cancel-five-{index}",
            )
        self.assertEqual("confirmation_required", replacement["status"])

        cancelled = self.resolve(replacement, option_id="cancel")

        self.assertFalse(cancelled["success"])
        self.assertIn("기존에 승인한 기본값은 그대로 유지", cancelled["message"])
        self.assertEqual(
            "kept_existing",
            cancelled["data"]["preference_resolution"]["status"],
        )
        self.assertEqual(
            7,
            self.learning.resolve(
                "ppt_slide_count", app_id="powerpoint"
            )["value"],
        )


class Stage11VbaPreferenceFlowTests(unittest.TestCase):
    def test_confirmed_vba_pattern_fills_only_an_omitted_change_kind(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = root / "macro.xlsm"
            path.write_bytes(b"fixture")
            manager = UserPreferenceLearningManager(root / "preferences.json")
            for index in range(1, 4):
                candidate = manager.record_evidence(
                    "vba_edit_pattern",
                    "fix_last_row",
                    scope_kind="app",
                    scope_id="excel",
                    evidence_id=f"vba-pattern-{index}",
                )
            manager.activate(candidate["candidate_id"])
            original = (
                "Public Sub FindLast()\n"
                "lastRow = Cells(Rows.Count, 1).End(xlUp).Row\n"
                "End Sub"
            )
            component = FakeComponent("Module1", original)
            workbook = FakeWorkbook(path, FakeProject(component))
            application = FakeApplication(workbook)
            adapter = ExcelVbaAdapter(
                application_getter=lambda: application,
                process_counter=lambda: 1,
                discovery_retry_delay=0,
                backup_dir=root / "backups",
            )
            controller = EditModeController(
                intake_manager=VbaIntake(),
                session_manager=EditSessionManager(),
                layout_manager=VbaNoLayout(),
                context_manager=VbaContext(),
                native_action_registry=VbaRegistry(adapter),
                user_learning_manager=manager,
            )
            parser = CommandParser(edit_mode_controller=controller)
            session = controller.connect_file(str(path))
            result = parser.execute_command_result(
                "Module1 VBA 코드를 수정해줘",
                mode="edit",
                session_id="stage11-vba",
                edit_context={
                    "edit_session_id": session["session_id"],
                    "document_fingerprint": session["document_fingerprint"],
                    "context_fingerprint": "9" * 64,
                    "request_id": "stage11-vba-default",
                },
            )
            self.assertEqual("confirmation_required", result["status"])
            pending = parser.pending_confirmation_manager.active_record("stage11-vba")
            prepared = pending["payload"]["prepared_action"]
            self.assertEqual(
                "fix_last_row",
                prepared["metadata"]["applied_user_preference"]["value"],
            )
            confirmation = result["data"]["confirmation"]
            applied = parser.resolve_pending_confirmation(
                "stage11-vba",
                confirmation_id=confirmation["confirmation_id"],
                option_id="apply",
            )
            self.assertTrue(applied["success"])
            self.assertIn("ActiveSheet.Rows.Count", component.CodeModule.code)


class Stage11CrossAppLearningFlowTests(unittest.TestCase):
    @staticmethod
    def _activate(manager, preference, value, app_type):
        candidate = None
        for index in range(1, 4):
            candidate = manager.record_evidence(
                preference,
                value,
                scope_kind="app",
                scope_id=app_type,
                evidence_id=f"edit-default-{preference}-{index}",
            )
        manager.activate(candidate["candidate_id"])

    def _formatting_preview(self, root, app_type, preference, value, command):
        suffix = {
            "word": ".docx",
            "powerpoint": ".pptx",
            "hwp": ".hwp",
        }[app_type]
        path = root / f"formatting-default{suffix}"
        path.write_bytes(b"fixture")
        manager = UserPreferenceLearningManager(root / f"{app_type}-preferences.json")
        self._activate(manager, preference, value, app_type)
        context = OfficeContext(app_type, "선택된 문장")
        native = OfficeNativeAdapter(path, context)
        controller = EditModeController(
            intake_manager=OfficeIntake(path, app_type),
            session_manager=EditSessionManager(),
            layout_manager=OfficeLayout(),
            context_manager=context,
            native_action_registry=OfficeRegistry(native),
            user_learning_manager=manager,
        )
        parser = CommandParser(edit_mode_controller=controller)
        session = controller.connect_file(str(path))
        result = parser.execute_command_result(
            command,
            mode="edit",
            session_id=f"stage11-formatting-{app_type}",
            edit_context={
                "edit_session_id": session["session_id"],
                "document_fingerprint": session["document_fingerprint"],
                "context_fingerprint": "C" * 64,
                "request_id": f"formatting-{app_type}-{preference}",
            },
        )
        return parser, native, result

    def test_word_session_can_collect_and_confirm_report_style_evidence(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = root / "report.docx"
            path.write_bytes(b"fixture")
            manager = UserPreferenceLearningManager(root / "preferences.json")
            context = OfficeContext("word", "선택된 보고서 문장")
            native = OfficeNativeAdapter(path, context)
            controller = EditModeController(
                intake_manager=OfficeIntake(path, "word"),
                session_manager=EditSessionManager(),
                layout_manager=OfficeLayout(),
                context_manager=context,
                native_action_registry=OfficeRegistry(native),
                user_learning_manager=manager,
            )
            parser = CommandParser(edit_mode_controller=controller)
            session = controller.connect_file(str(path))
            results = []
            for index in range(1, 4):
                results.append(parser.execute_command_result(
                    "보고서 문체는 격식체로 해줘",
                    mode="edit",
                    session_id="stage11-word",
                    edit_context={
                        "edit_session_id": session["session_id"],
                        "document_fingerprint": session["document_fingerprint"],
                        "context_fingerprint": "C" * 64,
                        "request_id": f"stage11-word-{index}",
                    },
                ))
            self.assertTrue(results[0]["success"])
            self.assertTrue(results[1]["success"])
            self.assertEqual("confirmation_required", results[2]["status"])
            confirmation = results[2]["data"]["confirmation"]
            activated = parser.resolve_pending_confirmation(
                "stage11-word",
                confirmation_id=confirmation["confirmation_id"],
                option_id="apply",
            )
            self.assertTrue(activated["success"])
            resolved = manager.resolve(
                "report_tone", workflow_id="business_report", app_id="word"
            )
            self.assertEqual("formal", resolved["value"])
            self.assertEqual(0, native.executed)

    def test_omitted_word_alignment_uses_confirmed_default_in_preview(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            parser, native, result = self._formatting_preview(
                Path(temp_dir),
                "word",
                "paragraph_align",
                "center",
                "정렬해줘",
            )

            self.assertEqual("confirmation_required", result["status"])
            self.assertIn("학습 기본값 가운데 정렬", result["message"])
            pending = parser.pending_confirmation_manager.active_record(
                "stage11-formatting-word"
            )
            prepared = pending["payload"]["prepared_action"]
            self.assertEqual("set_paragraph_format", prepared["operation"])
            self.assertEqual(
                "center",
                prepared["metadata"]["applied_user_preference"]["value"],
            )
            native_params = prepared["arguments"]["native_prepared_action"]["params"]
            self.assertEqual("가운데", native_params["alignment"])

    def test_omitted_powerpoint_font_size_uses_confirmed_default(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            parser, native, result = self._formatting_preview(
                Path(temp_dir),
                "powerpoint",
                "font_scale",
                "smaller",
                "글자 크기 맞춰줘",
            )

            self.assertEqual("confirmation_required", result["status"])
            pending = parser.pending_confirmation_manager.active_record(
                "stage11-formatting-powerpoint"
            )
            prepared = pending["payload"]["prepared_action"]
            self.assertEqual("set_text_format", prepared["operation"])
            native_params = prepared["arguments"]["native_prepared_action"]["params"]
            self.assertEqual(-2.0, native_params["font_size_delta"])
            self.assertEqual(
                "smaller",
                prepared["metadata"]["applied_user_preference"]["value"],
            )

    def test_omitted_hwp_font_size_uses_confirmed_default(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            parser, native, result = self._formatting_preview(
                Path(temp_dir),
                "hwp",
                "font_scale",
                "smaller",
                "글자 크기 맞춰줘",
            )

            self.assertEqual(
                "confirmation_required", result["status"], result
            )
            self.assertIn("학습 기본값 작게", result["message"])
            pending = parser.pending_confirmation_manager.active_record(
                "stage11-formatting-hwp"
            )
            prepared = pending["payload"]["prepared_action"]
            self.assertEqual("set_text_format", prepared["operation"])
            native_params = prepared["arguments"]["native_prepared_action"]["params"]
            self.assertEqual(-2.0, native_params["font_size_delta"])
            self.assertEqual(
                "smaller",
                prepared["metadata"]["applied_user_preference"]["value"],
            )

    def test_omitted_hwp_emphasis_and_alignment_use_confirmed_defaults(self):
        cases = (
            (
                "emphasis_style",
                "bold",
                "강조 방식 맞춰줘",
                "set_text_format",
                "bold",
                True,
            ),
            (
                "paragraph_align",
                "center",
                "정렬해줘",
                "set_paragraph_format",
                "alignment",
                "가운데",
            ),
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for index, (
                preference,
                value,
                command,
                operation,
                native_key,
                native_value,
            ) in enumerate(cases):
                with self.subTest(preference=preference):
                    case_root = root / str(index)
                    case_root.mkdir()
                    parser, native, result = self._formatting_preview(
                        case_root,
                        "hwp",
                        preference,
                        value,
                        command,
                    )
                    self.assertEqual(
                        "confirmation_required", result["status"], result
                    )
                    pending = parser.pending_confirmation_manager.active_record(
                        "stage11-formatting-hwp"
                    )
                    prepared = pending["payload"]["prepared_action"]
                    self.assertEqual(operation, prepared["operation"])
                    native_params = prepared["arguments"][
                        "native_prepared_action"
                    ]["params"]
                    self.assertEqual(native_value, native_params[native_key])
                    self.assertEqual(
                        value,
                        prepared["metadata"][
                            "applied_user_preference"
                        ]["value"],
                    )

    def test_explicit_alignment_does_not_report_learned_default(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            parser, native, result = self._formatting_preview(
                Path(temp_dir),
                "word",
                "paragraph_align",
                "center",
                "오른쪽 정렬해줘",
            )

            self.assertEqual("confirmation_required", result["status"])
            pending = parser.pending_confirmation_manager.active_record(
                "stage11-formatting-word"
            )
            prepared = pending["payload"]["prepared_action"]
            self.assertNotIn(
                "applied_user_preference", prepared["metadata"]
            )
            native_params = prepared["arguments"]["native_prepared_action"]["params"]
            self.assertEqual("오른쪽", native_params["alignment"])


if __name__ == "__main__":
    unittest.main()
