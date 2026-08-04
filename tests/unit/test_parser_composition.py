"""Architecture contracts for the lightweight CommandParser facade."""

import ast
import inspect
import unittest
from dataclasses import fields
from pathlib import Path
from unittest import mock

from engine.ai_actions import AIActionHandler
from engine.app_actions import AppCommandRouter
from engine.confirmation import ConfirmationRegistry
from engine.confirmation.handlers import HANDLERS
from engine.edit_mode.controller import EditModeController
from engine.local_commands import LocalCommandAnalyzer
from engine.parser import CommandParser
from engine.pipeline import CommandPipeline
from engine.runtime_ports import confirmation_port_kinds, confirmation_runtime_port
from engine.runtime_services import ParserRuntimeServices
from engine.skills import (
    CandidateRecordingService,
    LearnedReplayService,
    SkillExecutionServices,
    SkillExecutor,
    SkillLearningService,
)
from verification.maintenance_audit import retained_parent_reference_paths

ROOT = Path(__file__).resolve().parents[2]
PARSER_PATH = ROOT / "engine" / "parser.py"
PARSER_LINE_BUDGET = 1000

COMPOSED_MANAGER_TYPES = (
    CommandPipeline,
    AppCommandRouter,
    ConfirmationRegistry,
    AIActionHandler,
    LocalCommandAnalyzer,
    CandidateRecordingService,
    SkillLearningService,
    LearnedReplayService,
    SkillExecutor,
    EditModeController,
)

FORBIDDEN_BACK_REFERENCE_NAMES = frozenset({"owner", "parser", "_parser"})


class CommandParserCompositionTests(unittest.TestCase):
    def test_parser_stays_within_facade_line_budget(self):
        line_count = len(PARSER_PATH.read_text(encoding="utf-8").splitlines())
        self.assertLessEqual(
            line_count,
            PARSER_LINE_BUDGET,
            f"CommandParser facade grew to {line_count} lines; split the new responsibility",
        )

    def test_native_parser_aliases_are_not_exposed_by_facade(self):
        parser = CommandParser()
        aliases = (
            "_parse_native_excel_write_command",
            "_parse_native_excel_sum_command",
            "_parse_native_excel_format_command",
            "_parse_native_excel_range_format_command",
            "_parse_native_excel_filter_command",
            "_parse_native_excel_clarification_command",
            "_parse_native_excel_find_replace_command",
            "_parse_native_excel_sort_command",
            "_parse_native_hwp_find_replace_command",
            "_parse_native_hwp_text_format_command",
            "_parse_native_hwp_paragraph_format_command",
            "_parse_native_hwp_insert_command",
            "_parse_native_hwp_save_command",
        )
        self.assertTrue(all(not hasattr(parser, name) for name in aliases))
        self.assertFalse(any(
            name.startswith("_parse_native_")
            for name in vars(CommandParser)
        ))

    def test_confirmation_services_are_grouped_under_one_registry(self):
        parser = CommandParser()

        self.assertIsNotNone(parser.confirmations.pending)
        self.assertIsNotNone(parser.confirmations.factory)
        self.assertIsNotNone(parser.confirmations.responses)
        self.assertIsNotNone(parser.confirmations.dispatcher)
        self.assertFalse(hasattr(parser, "pending_confirmation_manager"))
        self.assertFalse(hasattr(parser, "confirmation_factory"))
        self.assertFalse(hasattr(parser, "confirmation_response_handler"))
        self.assertFalse(hasattr(parser, "confirmation_dispatcher"))

    def test_composed_managers_do_not_retain_parser_as_owner(self):
        parser = CommandParser()
        children = (
            parser.command_pipeline,
            parser.app_command_router,
            parser.confirmations,
            parser.confirmations.factory,
            parser.confirmations.responses,
            parser.confirmations.dispatcher,
            parser.ai_action_handler,
            parser.local_command_analyzer,
            parser.candidate_recording_service,
            parser.skill_learning_service,
            parser.learned_replay_service,
            parser.skill_executor,
            parser.skill_run_policy,
            parser.builtins,
            parser.edit_mode_controller,
        )

        for child in children:
            for name in FORBIDDEN_BACK_REFERENCE_NAMES:
                self.assertFalse(hasattr(child, name), type(child).__name__)

    def test_composed_managers_do_not_retain_parser_by_identity(self):
        parser = CommandParser()
        children = {
            "pipeline": parser.command_pipeline,
            "app_router": parser.app_command_router,
            "confirmations": parser.confirmations,
            "confirmation_factory": parser.confirmations.factory,
            "confirmation_responses": parser.confirmations.responses,
            "confirmation_dispatcher": parser.confirmations.dispatcher,
            "ai_actions": parser.ai_action_handler,
            "local_commands": parser.local_command_analyzer,
            "candidate_recording": parser.candidate_recording_service,
            "skill_learning": parser.skill_learning_service,
            "learned_replay": parser.learned_replay_service,
            "skill_executor": parser.skill_executor,
            "skill_policy": parser.skill_run_policy,
            "builtins": parser.builtins,
            "edit_mode": parser.edit_mode_controller,
        }

        violations = {
            name: retained_parent_reference_paths(child, parser)
            for name, child in children.items()
        }
        violations = {
            name: paths for name, paths in violations.items() if paths
        }
        self.assertEqual({}, violations)

    def test_runtime_services_copy_only_explicit_dependencies(self):
        parser = CommandParser()
        runtime = parser._runtime_services()

        self.assertIsInstance(runtime, ParserRuntimeServices)
        self.assertFalse(hasattr(runtime, "parser"))
        self.assertFalse(hasattr(runtime, "owner"))
        self.assertEqual((), retained_parent_reference_paths(runtime, parser))
        self.assertIs(runtime.dict_mgr, parser.dict_mgr)
        self.assertIs(runtime.confirmations, parser.confirmations)

    def test_runtime_service_fields_have_explicit_types(self):
        annotations = ParserRuntimeServices.__annotations__
        self.assertNotIn("Any", repr(annotations))
        self.assertEqual(set(annotations), {
            field.name for field in fields(ParserRuntimeServices)
        })

    def test_confirmation_handler_receives_only_its_declared_runtime_port(self):
        runtime = CommandParser()._runtime_services()
        port = confirmation_runtime_port(runtime, "prepared_pdf_file_action")

        self.assertEqual(frozenset({"pdf_task_service"}), port.granted_names)
        self.assertIs(port.pdf_task_service, runtime.pdf_task_service)
        with self.assertRaises(AttributeError):
            _ = port.action_executor
        with self.assertRaises(AttributeError):
            port.pdf_task_service = object()
        self.assertEqual((), retained_parent_reference_paths(port, runtime))

    def test_every_confirmation_handler_has_an_explicit_runtime_grant(self):
        self.assertEqual(frozenset(HANDLERS), confirmation_port_kinds())

    def test_parser_dispatches_through_runtime_services_not_itself(self):
        parser = CommandParser()
        with mock.patch.object(
            parser.command_pipeline,
            "execute",
            return_value={"success": True, "message": "ok", "verified": True},
        ) as execute:
            parser.execute_command_result("테스트")

        runtime = execute.call_args.args[0]
        self.assertIsInstance(runtime, ParserRuntimeServices)
        self.assertIsNot(runtime, parser)

    def test_composed_manager_constructors_do_not_accept_parent_objects(self):
        for manager_type in COMPOSED_MANAGER_TYPES:
            parameters = inspect.signature(manager_type.__init__).parameters
            forbidden = FORBIDDEN_BACK_REFERENCE_NAMES.intersection(parameters)
            self.assertFalse(
                forbidden,
                f"{manager_type.__name__} constructor accepts parent reference(s): "
                f"{sorted(forbidden)}",
            )

    def test_composition_sources_do_not_assign_forbidden_back_references(self):
        paths = (
            ROOT / "engine" / "confirmation",
            ROOT / "engine" / "pipeline",
            ROOT / "engine" / "ai_actions",
            ROOT / "engine" / "app_actions" / "app_command_router.py",
            ROOT / "engine" / "edit_mode" / "controller.py",
            ROOT / "engine" / "local_commands" / "local_command_analyzer.py",
            ROOT / "engine" / "skills" / "candidate_recording_service.py",
            ROOT / "engine" / "skills" / "learned_replay_service.py",
            ROOT / "engine" / "skills" / "skill_executor.py",
            ROOT / "engine" / "skills" / "skill_learning_service.py",
        )
        source_files = []
        for path in paths:
            source_files.extend(path.rglob("*.py") if path.is_dir() else (path,))

        violations = []
        for source_file in source_files:
            tree = ast.parse(source_file.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                targets = []
                if isinstance(node, (ast.Assign, ast.AnnAssign)):
                    targets = (
                        node.targets if isinstance(node, ast.Assign) else [node.target]
                    )
                for target in targets:
                    if (
                        isinstance(target, ast.Attribute)
                        and isinstance(target.value, ast.Name)
                        and target.value.id == "self"
                        and target.attr in FORBIDDEN_BACK_REFERENCE_NAMES
                    ):
                        violations.append(
                            f"{source_file.relative_to(ROOT)}:{node.lineno}:self.{target.attr}"
                        )
        self.assertEqual([], violations)

    def test_confirmation_registry_is_the_single_confirmation_state_owner(self):
        registry = CommandParser().confirmations

        self.assertIs(registry.pending, registry.responses.manager)
        self.assertIs(registry.pending, registry.factory.manager)
        self.assertIs(registry.responses, registry.factory.response_handler)
        self.assertIs(registry.responses, registry.dispatcher.response_handler)

    def test_edit_state_is_owned_by_edit_mode_managers(self):
        parser = CommandParser()
        controller = parser.edit_mode_controller

        self.assertIs(parser.edit_mode_handler, controller)
        self.assertFalse(hasattr(parser, "edit_session_manager"))
        self.assertFalse(hasattr(parser, "edit_context_manager"))
        self.assertIsNotNone(controller.session_manager)
        self.assertIsNotNone(controller.context_manager)
        self.assertIsNot(controller.session_manager, controller.context_manager)

    def test_learning_state_and_skill_dependencies_have_single_owners(self):
        parser = CommandParser()
        pending = [{"macro": "test"}]
        parser.pending_macros = pending

        self.assertIs(pending, parser.skill_learning_service.pending_macros)
        self.assertIs(pending, parser.pending_macros)
        service_fields = {item.name for item in fields(SkillExecutionServices)}
        self.assertFalse(service_fields.intersection(FORBIDDEN_BACK_REFERENCE_NAMES))
        self.assertIs(
            parser.skill_executor.services.confirmations,
            parser.confirmations,
        )


if __name__ == "__main__":
    unittest.main()
