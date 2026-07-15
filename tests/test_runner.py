"""Category-aware unittest runner for Jarvis.

Usage:
    python -m tests.test_runner unit
    python -m tests.test_runner integration
    python -m tests.test_runner windows
    python -m tests.test_runner live
    python -m tests.test_runner all
"""

from __future__ import annotations

import argparse
import sys
import unittest


TEST_GROUPS = {
    "unit": (
        "tests.unit.test_execution_busy",
        "tests.unit.test_gui_markdown_safety",
        "tests.unit.test_learning_quality",
        "tests.unit.test_learning_schema",
        "tests.unit.test_llm_failures",
        "tests.unit.test_native_candidate_workflow",
        "tests.unit.test_ui_persistence_refresh",
    ),
    "integration": (
        "tests.integration.test_ai_action_handler",
        "tests.integration.test_ai_router",
        "tests.integration.test_atomic_storage",
        "tests.integration.test_command_analysis",
        "tests.integration.test_command_context",
        "tests.integration.test_compound_commands",
        "tests.integration.test_confirmation_flow",
        "tests.integration.test_dictionary_locking",
        "tests.integration.test_document_formats",
        "tests.integration.test_dynamic_code_preflight",
        "tests.integration.test_execution_result",
        "tests.integration.test_learning_library",
        "tests.integration.test_learning_loop",
        "tests.integration.test_learning_review",
        "tests.integration.test_llm_validation",
        "tests.integration.test_local_command_analyzer",
        "tests.integration.test_native_action_candidates",
        "tests.integration.test_parser_accuracy",
        "tests.integration.test_performance",
        "tests.integration.test_persistence",
        "tests.integration.test_postconditions",
        "tests.integration.test_run_policy",
        "tests.integration.test_skill_executor",
        "tests.integration.test_skill_fallback",
        "tests.integration.test_skill_native_route",
        "tests.integration.test_skill_services",
        "tests.integration.test_template_matcher",
        "tests.integration.test_tls_security",
    ),
    "windows": (
        "tests.windows.test_action_executor",
        "tests.windows.test_action_registry",
        "tests.windows.test_app_command_router",
        "tests.windows.test_browser_scanner",
        "tests.windows.test_excel_aux_actions",
        "tests.windows.test_excel_core_actions",
        "tests.windows.test_excel_native_write",
        "tests.windows.test_execution_runtime",
        "tests.windows.test_hotkeys",
        "tests.windows.test_hwp_actions",
        "tests.windows.test_preference_manager",
        "tests.windows.test_release_security",
        "tests.windows.test_scanner_quality",
        "tests.windows.test_ui_automation",
        "tests.windows.test_windows_integration",
    ),
    "live": (
        "tests.live.test_live_ai",
    ),
}

TEST_MODULES = tuple(
    module
    for group in ("unit", "integration", "windows", "live")
    for module in TEST_GROUPS[group]
)


def modules_for(group):
    if group == "all":
        return TEST_MODULES
    return TEST_GROUPS[group]


def run_tests(group="unit", verbosity=2):
    modules = modules_for(group)
    suite = unittest.defaultTestLoader.loadTestsFromNames(modules)
    return unittest.TextTestRunner(verbosity=verbosity).run(suite)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "group",
        choices=(*TEST_GROUPS, "all"),
        nargs="?",
        default="unit",
    )
    parser.add_argument("-q", "--quiet", action="store_true")
    args = parser.parse_args(argv)
    result = run_tests(args.group, verbosity=1 if args.quiet else 2)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
