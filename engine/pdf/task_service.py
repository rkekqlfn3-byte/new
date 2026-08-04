"""Execute page-grounded PDF-3 tasks without persisting document contents."""

from __future__ import annotations

from typing import Any

from engine.execution_result import success_result
from engine.pdf.analysis import (
    chunk_pdf_pages,
    citation_label,
    find_pdf_headings,
    find_pdf_tables,
)
from engine.pdf.grounded_answer import PdfGroundedAnswerService, build_transfer_plan
from engine.pdf.intent import PdfCommandRequest, PdfIntentKind
from engine.pdf.office_workflow import PdfOfficeWorkflow
from engine.pdf.reference import PdfReferenceError
from engine.pdf.transformation_service import PdfTransformationService

MAX_PDF_DISPLAY_MATCHES = 20
MAX_PDF_DISPLAY_HEADINGS = 80


class PdfTaskError(PdfReferenceError):
    """A recoverable PDF task failure with a content-free error code."""


def _page_ranges(page_numbers) -> str:
    pages = tuple(sorted({int(page) for page in page_numbers}))
    ranges: list[str] = []
    start = previous = pages[0]
    for page in pages[1:]:
        if page == previous + 1:
            previous = page
            continue
        ranges.append(str(start) if start == previous else f"{start}-{previous}")
        start = previous = page
    ranges.append(str(start) if start == previous else f"{start}-{previous}")
    return ", ".join(ranges)


class PdfTaskService:
    """Own local PDF analysis and the explicitly approved external-AI boundary."""

    def __init__(
        self,
        intake_manager,
        llm_engine,
        dict_manager,
        office_workflow=None,
        transformation_service=None,
    ):
        self._intake = intake_manager
        self._llm = llm_engine
        self._dict = dict_manager
        self._office = office_workflow or PdfOfficeWorkflow()
        self._transformations = transformation_service or PdfTransformationService(
            intake_manager
        )

    def _read_request(self, request: PdfCommandRequest):
        if not isinstance(request, PdfCommandRequest):
            raise TypeError("PDF task service requires a structured request.")
        connection = self._intake.current(revalidate=True)
        reference = request.reference
        if connection.connection_id != reference.connection_id:
            raise PdfTaskError(
                "pdf_connection_replaced",
                "PDF를 확인하는 동안 연결 대상이 바뀌었습니다. 다시 요청해 주세요.",
            )
        if connection.document.document_fingerprint != reference.document_fingerprint:
            raise PdfTaskError(
                "pdf_source_changed",
                "연결한 PDF가 변경되었습니다. 다시 연결해 주세요.",
            )
        extraction = self._intake.read_current(pages=reference.page_numbers)
        return connection, extraction

    def execute_local(self, request: PdfCommandRequest):
        kind = request.intent.kind
        if kind is PdfIntentKind.SEARCH:
            return self._search(request)
        if kind is PdfIntentKind.TABLE_OF_CONTENTS:
            return self._table_of_contents(request)
        if kind is PdfIntentKind.TABLE_EXTRACT:
            return self._table_preview(request)
        raise PdfTaskError(
            "pdf_task_not_local",
            "이 PDF 작업은 로컬 전용 작업이 아닙니다.",
        )

    def _search(self, request: PdfCommandRequest):
        result = self._intake.search_current(
            request.intent.query or "",
            pages=request.reference.page_numbers,
        )
        matches = result.matches[:MAX_PDF_DISPLAY_MATCHES]
        if matches:
            lines = [
                f"- p.{match.page_number}: {match.excerpt}"
                for match in matches
            ]
            message = (
                f"PDF에서 {len(result.matches)}개를 찾았습니다.\n"
                + "\n".join(lines)
                + f"\n\n{citation_label(match.page_number for match in matches)}"
            )
        else:
            message = "선택한 PDF 페이지에서 해당 내용을 찾지 못했습니다."
        evidence = result.to_evidence_dict()
        evidence["displayed_match_count"] = len(matches)
        evidence["chat_persistence"] = "session_only"
        return success_result(
            message,
            action="pdf_search",
            target=request.reference.connection_id,
            verified=True,
            data=evidence,
        )

    def _table_of_contents(self, request: PdfCommandRequest):
        _connection, extraction = self._read_request(request)
        headings = find_pdf_headings(extraction)[:MAX_PDF_DISPLAY_HEADINGS]
        if not headings:
            raise PdfTaskError(
                "pdf_toc_not_detected",
                "선택한 페이지에서 신뢰할 수 있는 제목 구조를 찾지 못했습니다.",
            )
        message = "PDF 제목 구조를 찾았습니다.\n" + "\n".join(
            f"- p.{heading.page_number}: {heading.text}" for heading in headings
        )
        return success_result(
            message,
            action="pdf_table_of_contents",
            target=request.reference.connection_id,
            verified=True,
            data={
                "pdf_request": request.to_evidence_dict(),
                "heading_count": len(headings),
                "headings": [heading.to_evidence_dict() for heading in headings],
                "citation_pages": sorted({item.page_number for item in headings}),
                "chat_persistence": "session_only",
            },
        )

    def _table_preview(self, request: PdfCommandRequest):
        _connection, extraction = self._read_request(request)
        tables = find_pdf_tables(extraction)
        if not tables:
            raise PdfTaskError(
                "pdf_table_low_confidence",
                "표 구조를 확실하게 판별하지 못했습니다. 페이지를 지정하거나 표 범위를 더 설명해 주세요.",
            )
        if len(tables) > 1:
            raise PdfTaskError(
                "pdf_table_ambiguous",
                "신뢰 가능한 표가 여러 개입니다. 사용할 페이지를 하나 지정해 주세요.",
            )
        table = tables[0]
        lines = [" | ".join(table.headers)]
        lines.extend(" | ".join(row) for row in table.rows[:20])
        message = (
            f"p.{table.page_number}에서 표 1개를 확인했습니다.\n"
            + "\n".join(lines)
            + f"\n\n{citation_label((table.page_number,))}"
        )
        return success_result(
            message,
            action="pdf_table_extract",
            target=request.reference.connection_id,
            verified=True,
            data={
                "pdf_request": request.to_evidence_dict(),
                "table": table.to_evidence_dict(),
                "citation_pages": [table.page_number],
                "chat_persistence": "session_only",
            },
        )

    def prepare_external(self, request: PdfCommandRequest, request_text: str) -> dict[str, Any]:
        if request.intent.kind not in {
            PdfIntentKind.SUMMARY,
            PdfIntentKind.EXPLAIN,
            PdfIntentKind.REPORT,
        }:
            raise PdfTaskError(
                "pdf_external_task_unsupported",
                "이 PDF 작업은 아직 외부 AI 실행 대상으로 지원되지 않습니다.",
            )
        connection, extraction = self._read_request(request)
        chunks = chunk_pdf_pages(extraction)
        config = self._dict.get_ai_config()
        provider = str(config.get("provider", "openai")).casefold()
        if not str(config.get("api_key", "")).strip():
            raise PdfTaskError(
                "pdf_ai_key_missing",
                "PDF 내용을 분석하려면 AI 설정에서 API 키를 먼저 연결해 주세요.",
            )
        plan = build_transfer_plan(
            chunks,
            provider=provider,
            purpose=request.intent.kind.value,
        )
        prepared = {
            "kind": "prepared_pdf_external_action",
            "connection_id": request.reference.connection_id,
            "document_fingerprint": request.reference.document_fingerprint,
            "page_numbers": list(request.reference.page_numbers),
            "intent": request.intent.kind.value,
            "request_text": str(request_text or "")[:1000],
            "transfer": plan.to_dict(),
        }
        if request.intent.kind is PdfIntentKind.REPORT:
            prepared["office"] = self._office.prepare(
                connection,
                request.intent.output_kinds,
            )
        return prepared

    def prepare_table_excel(self, request: PdfCommandRequest) -> dict[str, Any]:
        if (
            request.intent.kind is not PdfIntentKind.TABLE_EXTRACT
            or request.intent.output_kinds != ("excel",)
        ):
            raise PdfTaskError(
                "pdf_table_output_invalid",
                "Excel 생성 요청의 PDF 표 대상이 올바르지 않습니다.",
            )
        connection, extraction = self._read_request(request)
        tables = find_pdf_tables(extraction)
        if len(tables) != 1:
            code = "pdf_table_low_confidence" if not tables else "pdf_table_ambiguous"
            raise PdfTaskError(
                code,
                "Excel로 만들 표를 하나로 확실하게 판별하지 못했습니다. 페이지를 하나 지정해 주세요.",
            )
        table = tables[0]
        return {
            "kind": "prepared_pdf_office_action",
            "connection_id": request.reference.connection_id,
            "document_fingerprint": request.reference.document_fingerprint,
            "page_numbers": list(request.reference.page_numbers),
            "intent": request.intent.kind.value,
            "table": table.to_evidence_dict(),
            "office": self._office.prepare(connection, ("excel",)),
        }

    def prepare_file_action(self, request: PdfCommandRequest) -> dict[str, Any]:
        return self._transformations.prepare(request)

    def execute_prepared(self, payload: dict[str, Any]):
        if not isinstance(payload, dict):
            raise PdfTaskError("pdf_confirmation_invalid", "PDF 승인 정보가 올바르지 않습니다.")
        if payload.get("kind") in {
            "prepared_pdf_file_action",
            "prepared_pdf_file_undo",
        }:
            return self._execute_prepared_file_action(payload)
        try:
            kind = PdfIntentKind(payload.get("intent"))
            page_numbers = tuple(int(page) for page in payload.get("page_numbers", ()))
        except (TypeError, ValueError) as error:
            raise PdfTaskError(
                "pdf_confirmation_invalid",
                "PDF 승인 정보가 올바르지 않습니다.",
            ) from error
        if kind not in {
            PdfIntentKind.SUMMARY,
            PdfIntentKind.EXPLAIN,
            PdfIntentKind.REPORT,
            PdfIntentKind.TABLE_EXTRACT,
        }:
            raise PdfTaskError("pdf_confirmation_invalid", "지원하지 않는 PDF 승인 작업입니다.")
        connection, extraction = self._validated_prepared_context(payload, page_numbers)
        if kind is PdfIntentKind.TABLE_EXTRACT:
            return self._execute_prepared_table(payload, connection, extraction)
        return self._execute_prepared_external(payload, kind, connection, extraction)

    def _execute_prepared_file_action(self, payload):
        result = self._transformations.execute(payload)
        undone = payload.get("kind") == "prepared_pdf_file_undo"
        output_name = str(result.get("output_name") or "PDF 결과물")
        if undone:
            message = f"방금 만든 PDF 결과물을 안전하게 삭제했습니다: {output_name}"
            action = "pdf_undo"
        else:
            operation = str((result.get("file_action") or {}).get("operation") or "transform")
            labels = {
                "extract_pages": "분할",
                "merge_documents": "병합",
                "rotate_pages": "회전",
            }
            message = (
                f"PDF {labels.get(operation, '파일 작업')} 결과를 만들고 다시 열어 "
                f"검증했습니다: {output_name}"
            )
            action = f"pdf_{operation}"
        return success_result(
            message,
            action=action,
            verified=True,
            data={"pdf_file_action": result},
        )

    def _validated_prepared_context(self, payload, page_numbers):
        connection = self._intake.current(revalidate=True)
        if (
            connection.connection_id != payload.get("connection_id")
            or connection.document.document_fingerprint != payload.get("document_fingerprint")
        ):
            raise PdfTaskError(
                "pdf_context_changed",
                "승인 대기 중 PDF 연결 또는 파일 내용이 바뀌었습니다. 다시 요청해 주세요.",
            )
        extraction = self._intake.read_current(pages=page_numbers)
        return connection, extraction

    def _execute_prepared_table(self, payload, connection, extraction):
        tables = find_pdf_tables(extraction)
        if len(tables) != 1 or tables[0].to_evidence_dict() != payload.get("table"):
            raise PdfTaskError(
                "pdf_table_changed",
                "승인 후 PDF 표 구조가 달라졌습니다. 새 표를 다시 확인해 주세요.",
            )
        table = tables[0]
        artifacts = self._office.execute(
            connection,
            payload.get("office") or {},
            citation_pages=(table.page_number,),
            table=table,
        )
        names = ", ".join(artifacts["output_names"])
        return success_result(
            f"PDF 표를 Excel 파일로 만들고 다시 읽어 검증했습니다: {names}\n\n"
            f"{citation_label((table.page_number,))}",
            action="pdf_table_to_excel",
            target=connection.connection_id,
            verified=True,
            data={
                "pdf_table": table.to_evidence_dict(),
                "artifacts": artifacts,
            },
        )

    def _execute_prepared_external(self, payload, kind, connection, extraction):
        chunks = chunk_pdf_pages(extraction)
        config = self._dict.get_ai_config()
        provider = str(config.get("provider", "openai")).casefold()
        api_key = str(config.get("api_key", "")).strip()
        if not api_key:
            raise PdfTaskError(
                "pdf_ai_key_missing",
                "AI 설정의 API 키가 없어 PDF 분석을 실행하지 못했습니다.",
            )
        current_plan = build_transfer_plan(chunks, provider=provider, purpose=kind.value)
        if current_plan.to_dict() != payload.get("transfer"):
            raise PdfTaskError(
                "pdf_transfer_plan_changed",
                "승인 후 PDF 전송 범위가 달라졌습니다. 새 범위를 확인해 주세요.",
            )

        def provider_call(system: str, prompt: str):
            return self._llm._invoke_provider(
                provider,
                api_key,
                system,
                prompt,
                None,
                "question",
                None,
            )

        answer = PdfGroundedAnswerService(provider_call).generate(
            chunks,
            provider=provider,
            purpose=kind.value,
            request_text=str(payload.get("request_text", "")),
        )
        artifacts = None
        if kind is PdfIntentKind.REPORT:
            artifacts = self._office.execute(
                connection,
                payload.get("office") or {},
                answer=answer.answer,
                citation_pages=answer.citation_pages,
            )
        message = answer.display_text
        if artifacts:
            message += "\n\n생성·검증한 파일: " + ", ".join(artifacts["output_names"])
        data = {
            "pdf_grounding": answer.to_evidence_dict(),
            "chat_persistence": "session_only",
        }
        if artifacts:
            data["artifacts"] = artifacts
        return success_result(
            message,
            action=f"pdf_{kind.value}",
            target=connection.connection_id,
            verified=True,
            data=data,
        )


__all__ = ["PdfTaskError", "PdfTaskService", "_page_ranges"]
