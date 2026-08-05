"""Ratchet budgets for legacy functions that still need decomposition.

Every engine function is limited to 99 lines.  Existing exceptions are frozen
at their current size, have a responsible subsystem, and expire so that the
allow-list cannot become a permanent blind spot.
"""

from __future__ import annotations

from dataclasses import dataclass

MAX_FUNCTION_LINES = 99


@dataclass(frozen=True, slots=True)
class LongFunctionException:
    max_lines: int
    owner: str
    reason: str
    expires: str


def _exception(max_lines: int, owner: str, reason: str) -> LongFunctionException:
    return LongFunctionException(
        max_lines=max_lines,
        owner=owner,
        reason=reason,
        expires="2026-11-30",
    )


LONG_FUNCTION_EXCEPTIONS = {
    "engine/action_executor.py::ActionExecutor._verify_step": _exception(
        115, "execution", "Native postcondition dispatch still needs per-action verifiers."
    ),
    "engine/action_executor.py::ActionExecutor.execute_plan": _exception(
        162, "execution", "Execution, rollback, and result assembly need separate services."
    ),
    "engine/action_executor.py::ActionExecutor.validate_plan": _exception(
        128, "execution", "Plan validation rules need per-action validators."
    ),
    "engine/ai_actions/batch_preflight.py::BatchPreflight.run": _exception(
        144, "ai-actions", "Batch dependency and safety checks need staged validators."
    ),
    "engine/ai_actions/result_validator.py::ResultValidator.validate": _exception(
        171, "ai-actions", "AI result validation needs domain-specific validators."
    ),
    "engine/api/command_api.py::parse_command": _exception(
        104, "api", "API request lifecycle needs a command session service."
    ),
    "engine/app_actions/app_command_router.py::AppCommandRouter.build_success": _exception(
        131, "app-routing", "Native result normalization needs adapter-specific builders."
    ),
    "engine/app_actions/app_command_router.py::AppCommandRouter.execute": _exception(
        101, "app-routing", "App dispatch and confirmation continuation need separation."
    ),
    "engine/builtins.py::BuiltinMacros.handle_close": _exception(
        108, "builtins", "Close targeting and confirmation policy need separate helpers."
    ),
    "engine/confirmation/confirmation_factory.py::ConfirmationFactory.queue_app_method": _exception(
        123, "confirmation", "App-method preparation needs a dedicated confirmation builder."
    ),
    "engine/confirmation/confirmation_response_handler.py::ConfirmationResponseHandler.resolve_selection": _exception(
        225, "confirmation", "Selection, missing-info, and resume branches need handlers."
    ),
    "engine/confirmation/handlers/skill_policy.py::resolve": _exception(
        151, "confirmation", "Skill policy approval branches need typed continuation handlers."
    ),
    "engine/confirmation/handlers/uia_target.py::resolve": _exception(
        134, "confirmation", "UIA target revalidation and execution need separation."
    ),
    "engine/decision/decision_engine.py::DecisionEngine.evaluate": _exception(
        140, "decision", "Decision evidence scoring needs rule objects."
    ),
    "engine/diagnostics/failure_triage.py::DeterministicFailureClassifier.classify": _exception(
        343, "diagnostics", "Failure responsibility rules need independent classifiers."
    ),
    "engine/diagnostics/self_diagnosis.py::DiagnosticIncidentManager.record_execution_failure": _exception(
        143, "diagnostics", "Incident redaction and persistence need separate builders."
    ),
    "engine/edit_mode/context.py::EditContextManager.capture": _exception(
        150, "edit-context", "Application context capture needs per-app providers."
    ),
    "engine/edit_mode/context.py::NativeDocumentContextReader._capture_powerpoint": _exception(
        113, "edit-context", "PowerPoint context extraction needs focused readers."
    ),
    "engine/edit_mode/native_bridge.py::NativeDocumentBridge._office_metadata": _exception(
        123, "edit-bridge", "Office metadata extraction needs per-application adapters."
    ),
    "engine/edit_mode/stage11.py::Stage11NativeEditAdapter.prepare": _exception(
        136, "edit-stage11", "Preference preparation needs per-application strategies."
    ),
    "engine/edit_mode/stage11.py::StructuredPreferenceIntentAnalyzer.analyze": _exception(
        156, "edit-stage11", "Preference intent rules need domain analyzers."
    ),
    "engine/edit_mode/stage5.py::StructuredEditIntentAnalyzer._analyze_excel": _exception(
        207, "edit-stage5", "Excel edit intent rules need operation analyzers."
    ),
    "engine/edit_mode/stage5.py::edit_success_message": _exception(
        248, "edit-stage5", "Success copy needs result-type formatters."
    ),
    "engine/edit_mode/stage6.py::StructuredStage6IntentAnalyzer._analyze_powerpoint": _exception(
        115, "edit-stage6", "PowerPoint intent rules need operation analyzers."
    ),
    "engine/edit_mode/stage7.py::build_commit_records": _exception(
        179, "edit-stage7", "Commit evidence assembly needs per-app record builders."
    ),
    "engine/learning/business_workflow_skill.py::_validate_template": _exception(
        112, "learning", "Workflow validation needs step-specific validators."
    ),
    "engine/llm/gemini_provider.py::GeminiProvider.call": _exception(
        239, "llm", "Transport, streaming, and response mapping need separation."
    ),
    "engine/llm/openai_provider.py::OpenAIProvider.call": _exception(
        222, "llm", "Transport, streaming, and response mapping need separation."
    ),
    "engine/macro_runner.py::MacroRunner.run": _exception(
        112, "macro", "Macro lifecycle and timeout cleanup need separation."
    ),
    "engine/managers/dict_manager.py::DictionaryManager.load": _exception(
        118, "storage", "Schema loading, credential migration, and repair need phases."
    ),
    "engine/managers/native_action_candidate_manager.py::_migrate_candidate_record": _exception(
        114, "learning", "Candidate schema migration needs versioned migration steps."
    ),
    "engine/parser.py::CommandParser.__init__": _exception(
        100, "composition", "Facade construction awaits a dedicated composition root."
    ),
    "engine/pipeline/command_pipeline.py::CommandPipeline.execute": _exception(
        156, "pipeline", "Top-level route ordering needs declarative route stages."
    ),
    "engine/pipeline/command_pipeline.py::_recover_missing_app_target": _exception(
        104, "pipeline", "Missing-target recovery needs a dedicated resolver."
    ),
    "engine/pipeline/conversation_route.py::execute_conversation_route": _exception(
        117, "pipeline", "Conversation routing and AI fallback need separation."
    ),
    "engine/pipeline/edit_route.py::execute_edit_route": _exception(
        101, "pipeline", "Edit routing and response normalization need separation."
    ),
    "engine/pipeline/learned_route.py::execute_learned_route": _exception(
        134, "pipeline", "Learned route matching and execution need separation."
    ),
    "engine/security/dynamic_code_preflight.py::DynamicCodePreflight.analyze": _exception(
        154, "security", "AST scan, policy aggregation, and result creation need phases."
    ),
    "engine/security/dynamic_code_preflight.py::_RiskVisitor.visit_Call": _exception(
        252, "security", "Dangerous-call rules need independent policy visitors."
    ),
    "engine/skills/skill_executor.py::SkillExecutor.execute": _exception(
        160, "skills", "Skill policy, dispatch, verification, and learning need separation."
    ),
    "engine/skills/skill_learning_service.py::SkillLearningService.approve_locked": _exception(
        129, "skills", "Skill approval validation and persistence need separation."
    ),
    "engine/ui_automation.py::WindowsUIAutomation._locate_control": _exception(
        180, "uia", "Control lookup strategies need individual locator objects."
    ),
}
