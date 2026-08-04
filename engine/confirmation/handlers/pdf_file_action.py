"""Resume an explicitly approved, local PDF file transformation."""

from engine.execution_result import failure_result
from engine.pdf import PdfReadError, PdfReferenceError


def resolve(runtime, context):
    try:
        return runtime.pdf_task_service.execute_prepared(context.payload)
    except (PdfReadError, PdfReferenceError, OSError, RuntimeError, TypeError, ValueError) as error:
        code = getattr(error, "code", "pdf_file_action_failed")
        code = getattr(code, "value", code)
        return failure_result(
            str(error),
            action="pdf_file_action",
            error_type=getattr(error, "error_type", "validation_error"),
            status=getattr(error, "status", "failed"),
            data={"pdf_error": {"code": str(code)}},
        )
