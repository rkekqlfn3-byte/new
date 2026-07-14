"""Safe integrated test runner for Jarvis.

All command classification tests use analyze_command() or mocked execution
boundaries. Running this module never opens apps, kills processes, changes
volume, schedules shutdown, or writes to the user's real Jarvis data files.
"""

import sys
import unittest


TEST_MODULES = [
    "engine.test_command_analysis",
    "engine.test_parser_accuracy",
    "engine.test_compound_commands",
    "engine.test_llm_validation",
    "engine.test_dynamic_code_preflight",
    "engine.test_llm_failures",
    "engine.test_tls_security",
    "engine.test_release_security",
    "engine.test_gui_markdown_safety",
    "engine.test_ui_persistence_refresh",
    "engine.test_atomic_storage",
    "engine.test_persistence",
    "engine.test_dictionary_locking",
    "engine.test_scanner_quality",
    "engine.test_browser_scanner",
    "engine.test_hotkeys",
    "engine.test_windows_integration",
    "engine.test_document_formats",
    "engine.test_live_ai",
    "engine.test_learning_loop",
    "engine.test_learning_review",
    "engine.test_learning_library",
    "engine.test_learning_quality",
    "engine.test_learning_schema",
    "engine.test_skill_executor",
    "engine.test_template_matcher",
    "engine.test_action_executor",
    "engine.test_action_registry",
    "engine.test_ai_action_handler",
    "engine.test_app_command_router",
    "engine.test_ui_automation",
    "engine.test_execution_runtime",
    "engine.test_execution_busy",
    "engine.test_execution_result",
    "engine.test_confirmation_flow",
    "engine.test_excel_native_write",
    "engine.test_excel_core_actions",
    "engine.test_excel_aux_actions",
    "engine.test_hwp_actions",
    "engine.test_native_action_candidates",
    "engine.test_native_candidate_workflow",
    "engine.test_preference_manager",
    "engine.test_run_policy",
    "engine.test_skill_fallback",
    "engine.test_postconditions",
    "engine.test_ai_router",
    "engine.test_command_context",
    "engine.test_local_command_analyzer",
    "engine.test_performance",
    "engine.test_skill_services",
]


def run_tests(verbosity=2):
    loader = unittest.defaultTestLoader
    suite = unittest.TestSuite(loader.loadTestsFromName(name) for name in TEST_MODULES)
    return unittest.TextTestRunner(verbosity=verbosity).run(suite)


if __name__ == "__main__":
    result = run_tests()
    sys.exit(0 if result.wasSuccessful() else 1)
