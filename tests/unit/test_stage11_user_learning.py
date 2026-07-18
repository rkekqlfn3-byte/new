import json
import tempfile
import unittest
from pathlib import Path

from engine.edit_mode.stage11 import StructuredPreferenceIntentAnalyzer
from engine.learning import UserPreferenceLearningError, UserPreferenceLearningManager


class Stage11UserLearningManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.path = Path(self.temp_dir.name) / "preferences.json"
        self.manager = UserPreferenceLearningManager(self.path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def record(self, value=5, index=1, **scope):
        return self.manager.record_evidence(
            "summary_lines",
            value,
            scope_kind=scope.get("scope_kind", "workflow"),
            scope_id=scope.get("scope_id", "business_report"),
            evidence_id=f"evidence-{index}",
        )

    def test_one_observation_never_changes_a_default(self):
        result = self.record(index=1)
        self.assertEqual("observing", result["status"])
        self.assertFalse(result["needs_confirmation"])
        self.assertIsNone(
            self.manager.resolve("summary_lines", workflow_id="business_report")
        )

    def test_three_consistent_observations_require_confirmation_before_activation(self):
        self.record(index=1)
        self.record(index=2)
        candidate = self.record(index=3)
        self.assertEqual("candidate", candidate["status"])
        self.assertTrue(candidate["needs_confirmation"])
        self.assertEqual(1.0, candidate["confidence"])
        self.assertIsNone(
            self.manager.resolve("summary_lines", workflow_id="business_report")
        )

        active = self.manager.activate(candidate["candidate_id"], expected_value=5)
        self.assertTrue(active["user_confirmed"])
        resolved = self.manager.resolve(
            "summary_lines", workflow_id="business_report"
        )
        self.assertEqual(5, resolved["value"])
        self.assertEqual("workflow", resolved["resolved_scope"])

    def test_duplicate_request_id_is_not_counted_twice(self):
        first = self.record(index=1)
        duplicate = self.record(index=1)
        self.assertFalse(first["duplicate_evidence"])
        self.assertTrue(duplicate["duplicate_evidence"])
        self.assertEqual(1, duplicate["evidence_count"])

    def test_conflicting_values_do_not_promote_weak_pattern(self):
        for index, value in enumerate((5, 7, 5, 7), 1):
            result = self.record(value=value, index=index)
        self.assertEqual("observing", result["status"])
        self.assertEqual(0.5, result["confidence"])

    def test_scope_precedence_is_file_workflow_app_global(self):
        source = Path(self.temp_dir.name) / "sales.xlsx"
        source.write_bytes(b"fixture")
        scopes = (
            ("global", "global", 4, "global"),
            ("app", "word", 5, "app"),
            ("workflow", "business_report", 6, "workflow"),
            ("file", str(source), 7, "file"),
        )
        for scope_kind, scope_id, value, prefix in scopes:
            for index in range(1, 4):
                candidate = self.manager.record_evidence(
                    "summary_lines",
                    value,
                    scope_kind=scope_kind,
                    scope_id=scope_id,
                    evidence_id=f"{prefix}-{index}",
                )
            self.manager.activate(candidate["candidate_id"])

        resolved = self.manager.resolve(
            "summary_lines",
            app_id="word",
            workflow_id="business_report",
            file_path=source,
        )
        self.assertEqual(7, resolved["value"])
        self.assertEqual("file", resolved["resolved_scope"])
        source.unlink()
        resolved_without_file = self.manager.resolve(
            "summary_lines", app_id="word", workflow_id="business_report"
        )
        self.assertEqual(6, resolved_without_file["value"])

    def test_dismissed_candidate_needs_three_new_observations_before_reappearing(self):
        for index in range(1, 4):
            candidate = self.record(index=index)
        self.assertTrue(self.manager.dismiss(candidate["candidate_id"]))
        self.assertEqual("dismissed", self.record(index=4)["status"])
        self.assertEqual("dismissed", self.record(index=5)["status"])
        self.assertEqual("candidate", self.record(index=6)["status"])

    def test_persistence_round_trip_keeps_only_confirmed_active_values(self):
        for index in range(1, 4):
            candidate = self.record(index=index)
        self.manager.activate(candidate["candidate_id"])
        reloaded = UserPreferenceLearningManager(self.path)
        self.assertEqual(
            5,
            reloaded.resolve(
                "summary_lines", workflow_id="business_report"
            )["value"],
        )
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertNotIn("command", json.dumps(raw))

    def test_deactivation_and_validation_boundaries(self):
        with self.assertRaises(UserPreferenceLearningError):
            self.record(value=0, index=1)
        with self.assertRaises(UserPreferenceLearningError):
            self.manager.record_evidence(
                "unknown", "value", scope_kind="global", scope_id="global",
                evidence_id="bad-1",
            )
        for index in range(1, 4):
            candidate = self.record(index=index)
        self.manager.activate(candidate["candidate_id"])
        self.assertTrue(self.manager.deactivate(
            "summary_lines", scope_kind="workflow", scope_id="business_report"
        ))
        self.assertIsNone(
            self.manager.resolve("summary_lines", workflow_id="business_report")
        )


class Stage11PreferenceIntentTests(unittest.TestCase):
    def setUp(self):
        self.analyzer = StructuredPreferenceIntentAnalyzer()
        self.context = {
            "app_type": "excel",
            "file_path": str(Path("C:/work/sales.xlsx")),
        }

    def test_parses_all_representative_preference_types(self):
        cases = {
            "보고서 요약은 5줄로 해줘": ("summary_lines", 5),
            "보고서 문체는 항상 격식체로 해줘": ("report_tone", "formal"),
            "보고서 제목은 명사형으로 해줘": ("title_style", "noun"),
            "숫자는 천 단위 콤마로 표시해줘": ("number_format", "thousands"),
            "표는 머리글을 굵게 하는 서식으로 해줘": ("table_style", "header_bold"),
            "PPT는 항상 7장으로 만들어줘": ("ppt_slide_count", 7),
            '출력 폴더는 "C:\\Reports"로 저장해줘': (
                "preferred_output_dir", "C:\\Reports"
            ),
            "문서 생성은 항상 확인해줘": (
                "confirmation_actions", ["document_creation"]
            ),
            "분석 먼저 보고서 다음 PPT 순서로 해줘": (
                "workflow_order",
                ["analyze_excel", "create_word_report", "create_powerpoint_summary"],
            ),
            "VBA는 원본 시트를 백업하는 방식으로 해줘": (
                "vba_edit_pattern", "backup_active_sheet"
            ),
        }
        for command, expected in cases.items():
            with self.subTest(command=command):
                intent = self.analyzer.analyze(command, self.context)
                self.assertEqual(expected, (intent.preference, intent.value))

    def test_file_scope_and_deactivation_are_explicit(self):
        intent = self.analyzer.analyze(
            "이 파일만 보고서 요약 4줄 기본값 취소", self.context
        )
        self.assertTrue(intent.deactivate)
        self.assertEqual("file", intent.scope_kind)
        self.assertTrue(intent.scope_id.endswith("sales.xlsx"))
        ppt = self.analyzer.analyze("PPT 기본 장수 선호 취소", self.context)
        self.assertTrue(ppt.deactivate)
        self.assertEqual("ppt_slide_count", ppt.preference)
        self.assertIsNone(ppt.value)

    def test_unrelated_edit_command_is_not_learning(self):
        self.assertIsNone(self.analyzer.analyze("B2에 10 입력해줘", self.context))

    def test_preview_feedback_accepts_loose_correction_only_in_feedback_path(self):
        text = "그거 말고 좀 더 간결하게 다시 작성해줘"
        self.assertIsNone(self.analyzer.analyze(text, self.context))

        intent = self.analyzer.analyze_feedback(text, self.context)

        self.assertEqual("report_tone", intent.preference)
        self.assertEqual("concise", intent.value)
        self.assertEqual("file", intent.scope_kind)
        self.assertTrue(intent.scope_id.endswith("sales.xlsx"))

    def test_preview_feedback_generalizes_only_when_user_explicitly_says_so(self):
        intent = self.analyzer.analyze_feedback(
            "앞으로는 좀 더 친근하게 해줘", self.context
        )

        self.assertEqual("friendly", intent.value)
        self.assertEqual("workflow", intent.scope_kind)
        self.assertEqual("business_report", intent.scope_id)
        self.assertIsNone(
            self.analyzer.analyze_feedback("이 미리보기 좋아", self.context)
        )


if __name__ == "__main__":
    unittest.main()
