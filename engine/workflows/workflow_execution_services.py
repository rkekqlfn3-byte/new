"""Preparation and step-run services for persistent business workflows."""

from __future__ import annotations

import copy
import uuid
from datetime import datetime
from pathlib import Path
from collections.abc import Mapping


class WorkflowPlanPreparationService:
    """Validate options and construct a side-effect-free approval plan."""

    def __init__(self, executor, dependencies):
        self.executor = executor
        self.dep = dependencies

    def prepare(
        self,
        source_path,
        *,
        title=None,
        output_dir=None,
        preferences=None,
        slide_count=5,
        explicit_slide_count=False,
        report_format="word",
        include_presentation=True,
        include_report=True,
        join_plan=None,
        source_scope=None,
    ):
        source = self._source(source_path)
        options = self._options(
            preferences,
            slide_count,
            report_format,
            include_presentation,
            include_report,
            join_plan,
            source_scope,
        )
        self.executor._preflight_report_environment(
            options["report_format"], options["include_report"]
        )
        destination = Path(self.dep["absolute_path"](output_dir or source.parent))
        if not destination.is_dir():
            raise self.dep["error"]("산출물 폴더를 찾을 수 없습니다.")
        workflow_id = uuid.uuid4().hex
        token = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + workflow_id
        output_paths = self._output_paths(source, destination, token, options)
        title = self._title(source, title, options["preferences"])
        return self._state(
            source,
            destination,
            workflow_id,
            title,
            output_paths,
            options,
            explicit_slide_count,
        )

    def _source(self, value):
        source = Path(self.dep["absolute_path"](value))
        if not source.is_file() or source.suffix.casefold() not in self.dep["excel_suffixes"]:
            raise self.dep["error"]("저장된 Excel 파일(.xlsx/.xlsm/.xlsb/.xls)이 필요합니다.")
        return source

    def _options(
        self,
        preferences,
        slide_count,
        report_format,
        include_presentation,
        include_report,
        join_plan,
        source_scope,
    ):
        learned = self.dep["validate_preferences"](preferences)
        try:
            slide_count = int(slide_count)
        except (TypeError, ValueError) as error:
            raise self.dep["error"]("PPT 장수는 숫자여야 합니다.") from error
        if not 3 <= slide_count <= 20:
            raise self.dep["error"]("PPT 장수는 3~20장 범위여야 합니다.")
        report_format = self.dep["report_format"](report_format)
        include_presentation = self.dep["include_presentation"](include_presentation)
        include_report = self.dep["include_report"](include_report)
        if not include_report and not include_presentation:
            raise self.dep["error"]("보고서와 발표자료를 모두 제외할 수 없습니다.")
        if not include_report:
            report_format = "word"
        join_plan = self.dep["validate_join"](join_plan)
        source_scope = self.dep["validate_scope"](source_scope)
        if join_plan is not None and source_scope is not None:
            raise self.dep["scope_error"](
                "시트 조인과 선택 범위만 분석은 한 요청에서 함께 사용할 수 없습니다."
            )
        return {
            "preferences": learned,
            "slide_count": slide_count,
            "report_format": report_format,
            "include_presentation": include_presentation,
            "include_report": include_report,
            "join_plan": join_plan,
            "source_scope": source_scope,
        }

    def _output_paths(self, source, destination, token, options):
        paths = {}
        report_format = options["report_format"]
        if options["include_report"]:
            for kind in self.dep["report_kinds"](report_format):
                key = self.dep["report_output_key"](report_format, kind)
                label = "Word" if kind == "word" else "한글"
                stem = (
                    f"{self.dep['safe_stem'](source.stem)}_JARVIS_보고서"
                    if report_format != "both"
                    else f"{self.dep['safe_stem'](source.stem)}_JARVIS_{label}_보고서"
                )
                paths[key] = str(self.dep["reserve_output"](
                    destination,
                    stem,
                    self.dep["report_formats"][kind]["suffix"],
                    token,
                ))
        if options["include_presentation"]:
            path = self.dep["reserve_output"](
                destination,
                f"{self.dep['safe_stem'](source.stem)}_JARVIS_{options['slide_count']}장_요약",
                ".pptx",
                token,
            )
            paths["presentation"] = str(path)
        return paths

    @staticmethod
    def _title(source, title, preferences):
        if title is not None:
            return str(title)
        style = str(preferences.get("title_style") or "default")
        return {
            "short": source.stem,
            "noun": f"{source.stem} 분석 보고서",
            "sentence": f"{source.stem} 분석 결과를 보고합니다",
        }.get(style, f"{source.stem} 분석")

    def _state(
        self,
        source,
        destination,
        workflow_id,
        title,
        output_paths,
        options,
        explicit_slide_count,
    ):
        source_fingerprint = self.dep["fingerprint"](source)
        step_order = self.dep["step_order"](
            options["report_format"],
            options["include_presentation"],
            options["include_report"],
        )
        now = self.dep["timestamp"]()
        state = {
            "schema_version": self.dep["schema_version"],
            "workflow_id": workflow_id,
            "status": "approval_required",
            "title": title,
            "source_path": str(source),
            "source_fingerprint": source_fingerprint,
            "output_dir": str(destination),
            "output_paths": output_paths,
            "report_format": options["report_format"],
            "include_presentation": options["include_presentation"],
            "include_report": options["include_report"],
            "join_plan": options["join_plan"],
            "source_scope": options["source_scope"],
            "step_order": list(step_order),
            "step_contract_schema_version": self.dep["contract_schema_version"],
            "step_contracts": self.dep["step_contracts"](
                workflow_id,
                options["report_format"],
                source_fingerprint,
                options["include_presentation"],
                options["include_report"],
            ),
            "slide_count": options["slide_count"],
            "explicit_slide_count": bool(explicit_slide_count),
            "applied_preferences": options["preferences"],
            "current_step": None,
            "successful_steps": [],
            "failed_step": None,
            "created_files": [],
            "verification_results": {},
            "work_product": None,
            "steps": {
                name: {
                    "status": "pending",
                    "attempts": 0,
                    "error": None,
                    "artifact": None,
                    "started_at": None,
                    "completed_at": None,
                }
                for name in step_order
            },
            "created_at": now,
            "updated_at": now,
        }
        return copy.deepcopy(state)


class WorkflowStepRunService:
    """Advance a persisted workflow while preserving verified step state."""

    def __init__(self, executor, dependencies):
        self.executor = executor
        self.dep = dependencies

    def run(self, workflow_id):
        state = self._load_runnable(workflow_id)
        if state.get("status") == "completed":
            self.executor._reconcile(state)
            if len(state["successful_steps"]) == len(self.executor._state_step_order(state)):
                return self.executor._result(state, changed=False)
        self.executor._reconcile(state)
        order = self.executor._state_step_order(state)
        state.update({"status": "running", "failed_step": None, "failure": None})
        self.executor._save(state)
        for name in order:
            if name in state["successful_steps"]:
                continue
            self._begin_step(state, name)
            try:
                artifact = self.executor._run_step(state, name)
                self._complete_step(state, name, artifact)
            except Exception as error:
                self._fail_step(state, name, error)
        state.update({"status": "completed", "current_step": None, "failed_step": None})
        self.executor._save(state)
        return self.executor._result(state, changed=True)

    def _load_runnable(self, workflow_id):
        state = self.executor.load(workflow_id)
        if state.get("status") in {"approval_required", "cancelled"}:
            raise self.dep["error"]("승인되지 않은 워크플로 상태는 실행할 수 없습니다.")
        failure = state.get("failure")
        retryable = bool(failure.get("retryable")) if isinstance(failure, Mapping) else False
        if state.get("status") == "blocked" and not retryable:
            error_type = self.dep["scope_error"] if state.get("source_scope") else self.dep["join_error"]
            raise error_type(
                "재시도할 수 없는 검증 오류로 차단된 워크플로입니다. 분석 범위나 "
                "조인 조건을 고쳐 새 요청으로 다시 미리보기·승인해주세요."
            )
        return state

    def _begin_step(self, state, name):
        step = state["steps"][name]
        state["current_step"] = name
        step["status"] = "running"
        step["attempts"] = int(step.get("attempts") or 0) + 1
        step["started_at"] = self.dep["timestamp"]()
        step["error"] = None
        self.executor._save(state)

    def _complete_step(self, state, name, artifact):
        verification, stored = self._verified_artifact(state, name, artifact)
        step = state["steps"][name]
        step["artifact"] = stored
        step["status"] = "succeeded"
        step["completed_at"] = self.dep["timestamp"]()
        state["verification_results"][name] = verification
        if name not in state["successful_steps"]:
            state["successful_steps"].append(name)
        self.executor._save(state)

    def _verified_artifact(self, state, name, artifact):
        if name == "analyze_excel":
            expected_scope = self.dep["validate_scope"](state.get("source_scope"))
            verification = self._analysis_verification(artifact, expected_scope)
            if expected_scope is not None and not verification["source_scope_verified"]:
                raise self.dep["scope_error"](
                    "분석 결과가 승인한 Excel 선택 범위와 일치하지 않습니다."
                )
            state["work_product"] = artifact
            return verification, None
        stored = dict(artifact)
        if not self.executor._artifact_valid(stored):
            raise self.dep["error"]("생성된 산출물의 파일 지문 검증에 실패했습니다.")
        created_path = str(stored["path"])
        if created_path not in state["created_files"]:
            state["created_files"].append(created_path)
        return dict(stored.get("verification") or {}), stored

    @staticmethod
    def _analysis_verification(artifact, expected_scope):
        tables = artifact["tables"]
        return {
            "valid_common_model": True,
            "metric_count": len(artifact["metrics"]),
            "table_count": len(tables),
            "sheet_count": sum(not bool(table.get("derived")) for table in tables),
            "derived_table_count": sum(bool(table.get("derived")) for table in tables),
            "chart_count": len(artifact["charts"]),
            "relationship_count": sum(len(table.get("relationships") or []) for table in tables),
            "pivot_summary_count": sum(len(table.get("pivot_summaries") or []) for table in tables),
            "join_summary_count": sum(bool(table.get("join")) for table in tables),
            "source_scope_verified": bool(
                expected_scope is not None
                and len(tables) == 1
                and dict(tables[0].get("source_scope") or {}) == expected_scope
            ),
        }

    def _fail_step(self, state, name, error):
        step = state["steps"][name]
        step["status"] = "failed"
        step["error"] = str(error)[:2_000]
        status = str(getattr(error, "status", "") or "failed").strip()
        state["status"] = status if status in {"failed", "blocked"} else "failed"
        state["failed_step"] = name
        state["current_step"] = name
        state["failure"] = {
            "error_type": str(getattr(error, "error_type", "") or "execution_error"),
            "status": state["status"],
            "retryable": bool(getattr(error, "retryable", True)),
        }
        self.executor._save(state)
        raise self.dep["execution_error"](
            state["workflow_id"], name, str(error), cause=error
        ) from error
