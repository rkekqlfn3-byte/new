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
        "tests.unit.test_browser_launcher",
        "tests.unit.test_business_workflow_skill",
        "tests.unit.test_workflow_step_registry",
        "tests.unit.test_edit_context",
        "tests.unit.test_edit_mode_contracts",
        "tests.unit.test_edit_target_ui",
        "tests.unit.test_excel_a_group_contract",
        "tests.unit.test_edit_target_identity",
        "tests.unit.test_edit_intent_memory",
        "tests.unit.test_edit_session",
        "tests.unit.test_learning_quality",
        "tests.unit.test_learning_schema",
        "tests.unit.test_logging_privacy",
        "tests.unit.test_app_operations",
        "tests.unit.test_hwp_caret_overlay",
        "tests.unit.test_vocabulary",
        "tests.unit.test_maintenance_tools",
        "tests.unit.test_manual_acceptance_recorder",
        "tests.unit.test_maintenance_performance",
        "tests.unit.test_maintainability_budgets",
        "tests.unit.test_llm_edit_intent",
        "tests.unit.test_llm_pdf_intent",
        "tests.unit.test_llm_failures",
        "tests.unit.test_native_candidate_workflow",
        "tests.unit.test_office_helpers",
        "tests.unit.test_file_action_contracts",
        "tests.unit.test_parser_composition",
        "tests.unit.test_pdf_contracts",
        "tests.unit.test_pdf_analysis",
        "tests.unit.test_pdf_acceptance_battery",
        "tests.unit.test_pdf_page_selection",
        "tests.unit.test_pdf_connection_ui",
        "tests.unit.test_pdf_grounded_answer",
        "tests.unit.test_pdf_office_workflow",
        "tests.unit.test_pdf_intent",
        "tests.unit.test_pdf_reference",
        "tests.unit.test_pdf_search",
        "tests.unit.test_pdf_transformation",
        "tests.unit.test_prototype1_stage1_probe",
        "tests.unit.test_prototype1_stage3_probe",
        "tests.unit.test_prototype1_stage6_probe",
        "tests.unit.test_prototype1_stage7_probe",
        "tests.unit.test_prototype1_stage8_probe",
        "tests.unit.test_prototype11_stage9_probe",
        "tests.unit.test_prototype11_stage10_probe",
        "tests.unit.test_prototype11_stage11_probe",
        "tests.unit.test_prototype11_stage12_probe",
        "tests.unit.test_prototype11_hwp_watchdog_probe",
        "tests.unit.test_product_goal_acceptance",
        "tests.unit.test_stage5_editing",
        "tests.unit.test_stage6_editing",
        "tests.unit.test_stage7_editing",
        "tests.unit.test_stage9_vba",
        "tests.unit.test_stage10_workflow",
        "tests.unit.test_stage11_user_learning",
        "tests.unit.test_stage12_self_diagnosis",
        "tests.unit.test_text_tone",
        "tests.unit.test_user_feedback",
        "tests.unit.test_utterance_acceptance_battery",
        "tests.unit.test_failure_triage",
        "tests.unit.test_restart",
        "tests.unit.test_recovery_contract",
        "tests.unit.test_selection_overlay",
        "tests.unit.test_source_identity",
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
        "tests.integration.test_edit_mode_routing",
        "tests.integration.test_edit_session_api",
        "tests.integration.test_execution_result",
        "tests.integration.test_learning_library",
        "tests.integration.test_learning_loop",
        "tests.integration.test_learning_review",
        "tests.integration.test_llm_validation",
        "tests.integration.test_local_command_analyzer",
        "tests.integration.test_maintenance_failure_injection",
        "tests.integration.test_native_action_candidates",
        "tests.integration.test_noun_removal",
        "tests.integration.test_parser_accuracy",
        "tests.integration.test_performance",
        "tests.integration.test_persistence",
        "tests.integration.test_pdf_characterization",
        "tests.integration.test_pdf_api",
        "tests.integration.test_pdf_command_route",
        "tests.integration.test_pdf3_tasks",
        "tests.integration.test_pdf4_transformations",
        "tests.integration.test_pdf_intake",
        "tests.integration.test_pdf_structured_reader",
        "tests.integration.test_postconditions",
        "tests.integration.test_run_policy",
        "tests.integration.test_skill_executor",
        "tests.integration.test_skill_fallback",
        "tests.integration.test_skill_native_route",
        "tests.integration.test_skill_services",
        "tests.integration.test_stage5_edit_flow",
        "tests.integration.test_stage6_edit_flow",
        "tests.integration.test_stage7_edit_flow",
        "tests.integration.test_stage8_prototype_flow",
        "tests.integration.test_stage9_vba_flow",
        "tests.integration.test_stage10_workflow_flow",
        "tests.integration.test_stage11_user_learning_flow",
        "tests.integration.test_stage12_diagnostic_flow",
        "tests.integration.test_template_matcher",
        "tests.integration.test_tls_security",
    ),
    "windows": (
        "tests.windows.test_action_executor",
        "tests.windows.test_action_registry",
        "tests.windows.test_app_command_router",
        "tests.windows.test_browser_scanner",
        "tests.windows.test_com_lifecycle",
        "tests.windows.test_edit_context_regressions",
        "tests.windows.test_excel_aux_actions",
        "tests.windows.test_excel_core_actions",
        "tests.windows.test_excel_native_write",
        "tests.windows.test_execution_runtime",
        "tests.windows.test_hotkeys",
        "tests.windows.test_hwp_actions",
        "tests.windows.test_preference_manager",
        "tests.windows.test_release_security",
        "tests.windows.test_scanner_quality",
        "tests.windows.test_stage8_stability",
        "tests.windows.test_excel_vba_actions",
        "tests.windows.test_ui_automation",
        "tests.windows.test_word_powerpoint_actions",
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
