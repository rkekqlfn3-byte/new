"""Bounded COM apartments and explicit Office application ownership."""

from __future__ import annotations

import logging
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@contextmanager
def com_apartment(com_runtime=None):
    """Initialize COM for exactly one operation and always balance it."""
    if com_runtime is False:
        # The caller owns one surrounding COM apartment for the full lifetime
        # of a persistent pywin32 proxy (for example, an isolated live probe).
        yield
        return
    runtime = com_runtime
    if runtime is None:
        try:
            import pythoncom as runtime
        except ImportError:
            runtime = None

    initialized = False
    if runtime is not None:
        runtime.CoInitialize()
        initialized = True
    try:
        yield
    finally:
        if initialized:
            runtime.CoUninitialize()


@dataclass
class OfficeApplicationLease:
    """One COM application reference with explicit Jarvis ownership."""

    application: Any
    owns_application: bool = False
    application_kind: str = "office"
    owned_documents: list[Any] = field(default_factory=list)

    @property
    def created_by_jarvis(self) -> bool:
        return self.owns_application

    def register_owned_document(self, document):
        if not self.owns_application:
            raise ValueError(
                "사용자가 실행한 Office 인스턴스의 문서는 Jarvis 소유로 등록할 수 없습니다."
            )
        if document is not None:
            self.owned_documents.append(document)
        return document

    def _close_owned_document(self, document):
        close = getattr(document, "Close", None)
        if not callable(close):
            return
        if self.application_kind == "excel":
            close(SaveChanges=False)
            return
        if self.application_kind == "powerpoint":
            close()
            return
        try:
            close(False)
        except TypeError:
            close()

    def cleanup(self, log=None):
        """Release references; close and quit only when Jarvis owns the app."""
        active_logger = log or logger
        application = self.application
        documents = list(self.owned_documents)
        try:
            if self.owns_application:
                for document in reversed(documents):
                    try:
                        self._close_owned_document(document)
                    except Exception as error:
                        active_logger.warning(
                            "Owned Office document cleanup failed kind=%s error=%s",
                            self.application_kind,
                            type(error).__name__,
                        )
                quit_application = getattr(application, "Quit", None)
                if callable(quit_application):
                    try:
                        quit_application()
                    except Exception as error:
                        active_logger.warning(
                            "Owned Office application quit failed kind=%s error=%s",
                            self.application_kind,
                            type(error).__name__,
                        )
        finally:
            documents.clear()
            self.owned_documents.clear()
            self.application = None


def application_lease(value, application_kind):
    """Treat raw COM objects as user-owned, attached applications."""
    if isinstance(value, OfficeApplicationLease):
        if not value.application_kind or value.application_kind == "office":
            value.application_kind = str(application_kind)
        return value
    return OfficeApplicationLease(
        application=value,
        owns_application=False,
        application_kind=str(application_kind),
    )
