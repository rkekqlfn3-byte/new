import json
import tempfile
import unittest
from pathlib import Path

from engine.learning import (
    BusinessWorkflowSkillError,
    BusinessWorkflowSkillManager,
    workflow_skill_template,
)


def verified_result(*, report_format="word", slide_count=5, workflow_id="workflow-1"):
    report_step = {
        "word": ["create_word_report"],
        "hwp": ["create_hwp_report"],
        "both": ["create_word_report", "create_hwp_report"],
    }[report_format]
    return {
        "verified": True,
        "status": "completed",
        "workflow_id": workflow_id,
        "report_format": report_format,
        "slide_count": slide_count,
        "successful_steps": [
            "analyze_excel",
            *report_step,
            "create_powerpoint_summary",
        ],
        # These values must never enter the reusable structural store.
        "source_path": r"C:\Private\고객매출.xlsx",
        "output_paths": {"report": r"C:\Private\고객보고서.docx"},
        "work_product_summary": {"title": "비밀 고객 매출"},
    }


class BusinessWorkflowSkillManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.path = Path(self.temp_dir.name) / "workflow-skills.json"
        self.manager = BusinessWorkflowSkillManager(self.path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_verified_result_stages_only_a_content_free_structural_candidate(self):
        candidate = self.manager.record_verified_success(
            verified_result(),
            evidence_id="workflow-1",
        )

        self.assertTrue(candidate["needs_confirmation"])
        self.assertEqual("candidate", candidate["status"])
        self.assertFalse(candidate["raw_paths_or_content_stored"])
        self.assertEqual("word", candidate["template"]["report_format"])
        self.assertTrue(candidate["template"]["requires_approval_each_run"])
        raw = self.path.read_text(encoding="utf-8")
        self.assertNotIn("Private", raw)
        self.assertNotIn("고객", raw)
        self.assertNotIn("workflow-1", raw)

    def test_activation_reload_and_deactivation_are_explicit(self):
        candidate = self.manager.record_verified_success(
            verified_result(report_format="both", slide_count=7),
            evidence_id="workflow-activate",
        )
        self.assertIsNone(self.manager.active_skill())

        active = self.manager.activate(candidate["candidate_id"])
        self.assertTrue(active["user_confirmed"])
        self.assertEqual("both", active["template"]["report_format"])
        reloaded = BusinessWorkflowSkillManager(self.path)
        self.assertEqual(7, reloaded.active_skill()["template"]["slide_count"])

        self.assertTrue(reloaded.deactivate())
        self.assertIsNone(reloaded.active_skill())

    def test_duplicate_evidence_does_not_inflate_verified_success_count(self):
        first = self.manager.record_verified_success(
            verified_result(),
            evidence_id="same-workflow",
        )
        duplicate = self.manager.record_verified_success(
            verified_result(),
            evidence_id="same-workflow",
        )
        self.assertFalse(first["duplicate_evidence"])
        self.assertTrue(duplicate["duplicate_evidence"])
        self.assertEqual(1, duplicate["verified_success_count"])

    def test_unverified_or_tampered_templates_fail_closed(self):
        invalid = verified_result()
        invalid["verified"] = False
        with self.assertRaises(BusinessWorkflowSkillError):
            workflow_skill_template(invalid)

        candidate = self.manager.record_verified_success(
            verified_result(),
            evidence_id="tamper-source",
        )
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        raw["candidates"][candidate["candidate_id"]]["template"][
            "requires_approval_each_run"
        ] = False
        self.path.write_text(json.dumps(raw), encoding="utf-8")

        reloaded = BusinessWorkflowSkillManager(self.path)
        self.assertEqual([], reloaded.status()["candidates"])


if __name__ == "__main__":
    unittest.main()
