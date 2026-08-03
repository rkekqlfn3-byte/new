"""Stage 10 regression tests for user-owned learned-skill run policy."""

import json
import os
import tempfile
import unittest
from unittest import mock

from engine.builtins import BuiltinMacros
from engine.llm_engine import LLMEngine
from engine.managers.dict_manager import DictionaryManager
from engine.parser import CommandParser
from engine.skills import (
    SkillRunPolicyService,
    ensure_run_policy_fields,
    parse_run_directive,
)


def safe_plan_skill(**overrides):
    skill = {
        "state": "active",
        "code": "",
        "plan": [{"action": "wait", "seconds": 0.01}],
        "learning": {
            "intent": "SAFE_REPEAT",
            "verbs": ["실행"],
            "utterances": ["안전 반복"],
            "slots": [],
        },
        "run_policy": "confirm",
        "run_policy_history": [],
        "verified_success_count": 5,
        "consecutive_verified_success": 5,
    }
    skill.update(overrides)
    return skill


class RunPolicyUnitTests(unittest.TestCase):
    def setUp(self):
        self.service = SkillRunPolicyService()

    def test_new_skill_defaults_to_confirm_with_system_audit(self):
        skill = {}
        changed = ensure_run_policy_fields(
            skill,
            changed_by="system_default",
            changed_at="2026-07-14T12:00:00+09:00",
        )
        self.assertTrue(changed)
        self.assertEqual("confirm", skill["run_policy"])
        self.assertEqual("system_default", skill["run_policy_history"][0]["changed_by"])

    def test_unverifiable_skill_cannot_auto_run(self):
        skill = safe_plan_skill(
            plan=[{"action": "hotkey", "keys": ["ctrl", "s"]}],
            run_policy="auto",
        )
        assessment = self.service.assess(skill)
        self.assertTrue(assessment.requires_confirmation)
        self.assertFalse(assessment.eligible_for_auto)
        self.assertIn("no_verification_method", assessment.forced_reasons)

    def test_python_fallback_always_requires_confirmation(self):
        skill = safe_plan_skill(
            run_policy="auto",
            execution_profile={
                "primary_route": "action_plan",
                "fallback_routes": ["python"],
                "verification_required": True,
                "max_fallback_attempts": 1,
            },
            code="print('fallback')",
        )
        assessment = self.service.assess(skill, "이번에는 묻지 마")
        self.assertTrue(assessment.requires_confirmation)
        self.assertIn("dynamic_python_fallback", assessment.forced_reasons)

    def test_five_verified_successes_offer_but_never_apply_auto(self):
        skill = safe_plan_skill(run_policy="confirm")
        assessment = self.service.assess(skill)
        self.assertTrue(assessment.suggest_auto)
        self.assertTrue(assessment.requires_confirmation)
        self.assertEqual("confirm", skill["run_policy"])

    def test_destructive_work_still_confirms_under_auto(self):
        skill = safe_plan_skill(
            run_policy="auto",
            plan=[{
                "action": "write_text_file",
                "target": "C:/Temp/report.txt",
                "text": "value",
                "overwrite": True,
            }],
        )
        assessment = self.service.assess(skill, "이번에는 묻지 마")
        self.assertTrue(assessment.requires_confirmation)
        self.assertIn("file_overwrite", assessment.forced_reasons)

    def test_explicit_directives_override_only_safe_policy(self):
        confirm_skill = safe_plan_skill(
            run_policy="confirm", consecutive_verified_success=0
        )
        self.assertFalse(
            self.service.assess(confirm_skill, "안전 반복 바로 실행해").requires_confirmation
        )
        auto_skill = safe_plan_skill(run_policy="auto")
        self.assertTrue(
            self.service.assess(auto_skill, "안전 반복 확인하고 실행해").requires_confirmation
        )
        preview = self.service.assess(auto_skill, "안전 반복 실행하지 말고 미리 보여줘")
        self.assertTrue(preview.preview_only)
        self.assertEqual("preview", parse_run_directive("미리 보여줘"))


class RunPolicyIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="jarvis-run-policy-")
        self.path = os.path.join(self.temp_dir.name, "dictionaries.json")
        self.parser = CommandParser()
        self.parser.dict_mgr = DictionaryManager(self.path)
        self.parser.llm_engine = LLMEngine(self.parser.dict_mgr)
        self.parser.builtins = BuiltinMacros(
            self.parser.dict_mgr, self.parser.action_executor
        )
        self.parser.action_executor.noun_dict = self.parser.dict_mgr.noun_dict

    def tearDown(self):
        self.temp_dir.cleanup()

    def install(self, skill=None, name="안전반복", phrase="안전 반복"):
        skill = skill or safe_plan_skill()
        self.parser.dict_mgr.learned_macros = {"시스템": {name: skill}}
        self.parser.dict_mgr.macro_dict[name] = {
            "name": name,
            "type": "learned",
            "app": "시스템",
            "state": "active",
            "synonyms": [phrase],
        }
        self.parser.dict_mgr.save()
        self.parser.template_matcher.invalidate()
        return skill

    def test_learning_approval_persists_confirm_as_the_default_policy(self):
        self.parser.pending_macros = [{
            "app": "시스템",
            "name": "새동적작업",
            "desc": "새 동적 Python 스킬",
            "code": "print('done')",
            "plan": [],
            "steps": [],
            "target": "",
            "learning": {
                "intent": "NEW_DYNAMIC_SKILL",
                "verbs": ["실행해"],
                "utterances": ["새 동적 작업 실행해"],
                "slots": [],
            },
            "verification_status": "manual_confirmation_required",
        }]
        self.parser.approve_pending_learning()

        saved = self.parser.dict_mgr.learned_macros["시스템"]["새동적작업"]
        self.assertEqual("confirm", saved["run_policy"])
        self.assertEqual(
            "system_default", saved["run_policy_history"][0]["changed_by"]
        )

    def test_declining_auto_offer_runs_once_and_keeps_confirm(self):
        skill = self.install()
        waiting = self.parser.execute_command_result(
            "안전 반복", session_id="policy-decline"
        )
        self.assertEqual("confirmation_required", waiting["status"])
        options = waiting["data"]["confirmation"]["options"]
        self.assertIn("enable_auto", {item["id"] for item in options})
        completed = self.parser.resolve_pending_confirmation(
            "policy-decline",
            waiting["data"]["confirmation"]["confirmation_id"],
            "run_once",
        )
        self.assertTrue(completed["success"])
        self.assertEqual("confirm", skill["run_policy"])

    def test_user_approval_changes_auto_and_records_required_audit(self):
        skill = self.install()
        waiting = self.parser.execute_command_result(
            "안전 반복", session_id="policy-approve"
        )
        completed = self.parser.resolve_pending_confirmation(
            "policy-approve",
            waiting["data"]["confirmation"]["confirmation_id"],
            "enable_auto",
        )
        self.assertTrue(completed["success"])
        self.assertEqual("auto", skill["run_policy"])
        audit = skill["run_policy_history"][-1]
        self.assertEqual({
            "previous_policy", "new_policy", "changed_by", "changed_at", "reason"
        }, set(audit))
        self.assertEqual("confirm", audit["previous_policy"])
        self.assertEqual("auto", audit["new_policy"])
        self.assertEqual("user", audit["changed_by"])

        direct = self.parser.execute_command_result(
            "안전 반복", session_id="policy-auto"
        )
        self.assertTrue(direct["success"])
        self.assertNotEqual("confirmation_required", direct["status"])

    def test_verified_results_accumulate_without_automatic_promotion(self):
        skill = safe_plan_skill(
            verified_success_count=0,
            consecutive_verified_success=0,
        )
        self.install(skill)
        for _ in range(5):
            self.parser.dict_mgr.record_learned_macro_result(
                "시스템", "안전반복", True
            )
            self.parser.dict_mgr.record_learned_macro_verification(
                "시스템", "안전반복", True
            )
        self.assertEqual(5, skill["consecutive_verified_success"])
        self.assertEqual("confirm", skill["run_policy"])
        self.assertTrue(self.parser.skill_run_policy.assess(skill).suggest_auto)

    def test_preview_does_not_execute(self):
        self.install()
        with mock.patch.object(self.parser.skill_executor, "execute") as execute:
            result = self.parser.execute_command_result(
                "안전 반복 실행하지 말고 미리 보여줘",
                session_id="policy-preview",
            )
        self.assertTrue(result["success"])
        self.assertEqual("learned_macro_preview", result["action"])
        self.assertFalse(result["data"]["executed"])
        execute.assert_not_called()

    def test_explicit_run_now_and_confirm_are_applied_by_parser(self):
        skill = safe_plan_skill(
            verified_success_count=0,
            consecutive_verified_success=0,
        )
        self.install(skill)
        direct = self.parser.execute_command_result(
            "안전 반복 바로 실행해", session_id="policy-run-now"
        )
        self.assertTrue(direct["success"])
        self.assertNotEqual("confirmation_required", direct["status"])

        skill["run_policy"] = "auto"
        skill["consecutive_verified_success"] = 5
        waiting = self.parser.execute_command_result(
            "안전 반복 확인하고 실행해", session_id="policy-force-confirm"
        )
        self.assertEqual("confirmation_required", waiting["status"])

    def test_blocked_skill_cannot_bypass_with_run_now_instruction(self):
        skill = safe_plan_skill(
            code=(
                "import winreg\n"
                "winreg.SetValueEx(None, 'x', 0, 1, 'y')"
            ),
            plan=[],
            learning={
                "intent": "REGISTRY_CHANGE",
                "verbs": ["변경"],
                "utterances": ["위험 변경"],
                "slots": [],
            },
            run_policy="auto",
        )
        self.install(skill, name="위험변경", phrase="위험 변경")
        with mock.patch.object(self.parser.macro_runner, "run") as run:
            blocked = self.parser.execute_command_result(
                "위험 변경 바로 실행해", session_id="policy-blocked"
            )
        self.assertEqual("blocked", blocked["status"])
        run.assert_not_called()

    def test_schema3_skill_migrates_to_confirm_without_changing_quality_state(self):
        legacy_path = os.path.join(self.temp_dir.name, "legacy.json")
        with open(legacy_path, "w", encoding="utf-8") as output:
            json.dump({
                "schema_version": 3,
                "noun_dictionary": {},
                "macro_dictionary": {},
                "learned_macros": {
                    "시스템": {
                        "기존행동": {
                            "state": "needs_review",
                            "code": "",
                            "plan": [{"action": "wait", "seconds": 0.01}],
                            "learning": {
                                "intent": "LEGACY",
                                "verbs": ["실행"],
                                "utterances": ["기존 행동"],
                                "slots": [],
                            },
                        }
                    }
                },
            }, output, ensure_ascii=False)
        manager = DictionaryManager(legacy_path)
        migrated = manager.learned_macros["시스템"]["기존행동"]
        self.assertEqual("confirm", migrated["run_policy"])
        self.assertEqual("migration", migrated["run_policy_history"][0]["changed_by"])
        self.assertEqual("needs_review", migrated["state"])


if __name__ == "__main__":
    unittest.main()
