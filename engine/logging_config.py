"""Central, rotating application logs with basic secret redaction."""

from __future__ import annotations

import logging
import re
import sys
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path

from engine.runtime_paths import USER_DATA_DIR
from engine.version import APP_VERSION


LOG_DIR = Path(USER_DATA_DIR).parent / "logs"
LOG_PATH = LOG_DIR / "jarvis.log"
ERROR_LOG_PATH = LOG_DIR / "errors.log"
_MAX_LOG_BYTES = 5 * 1024 * 1024
_BACKUP_COUNT = 5
_REDACTION_PATTERNS = (
    (re.compile(r"(?i)(authorization\s*:\s*bearer\s+)[^\s,;]+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}"), "Bearer [REDACTED]"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{8,}"), "sk-[REDACTED]"),
    (re.compile(r"\bAIza[A-Za-z0-9_-]{16,}"), "AIza[REDACTED]"),
    (
        re.compile(r"(?i)([?&](?:api_?key|key)=)[^&\s]+"),
        r"\1[REDACTED]",
    ),
    (
        re.compile(r"(?i)(['\"]api_?key['\"]\s*:\s*['\"])[^'\"]+"),
        r"\1[REDACTED]",
    ),
)


def redact_text(value) -> str:
    text = str(value or "")
    for pattern, replacement in _REDACTION_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


class RedactingFormatter(logging.Formatter):
    def format(self, record):
        return redact_text(super().format(record))


class RuntimeContextFilter(logging.Filter):
    def filter(self, record):
        record.app_version = getattr(record, "app_version", APP_VERSION)
        record.execution_id = getattr(record, "execution_id", "-")
        record.session_id = getattr(record, "session_id", "-")
        record.route = getattr(record, "route", "-")
        record.error_type = getattr(record, "error_type", "-")
        return True


def _install_exception_hooks():
    logger = logging.getLogger("jarvis.unhandled")

    def log_unhandled(exc_type, exc_value, exc_traceback):
        if issubclass(exc_type, KeyboardInterrupt):
            return sys.__excepthook__(exc_type, exc_value, exc_traceback)
        logger.critical(
            "Unhandled application exception",
            exc_info=(exc_type, exc_value, exc_traceback),
        )

    sys.excepthook = log_unhandled
    if hasattr(threading, "excepthook"):
        def log_thread_exception(args):
            logger.critical(
                "Unhandled thread exception: %s",
                getattr(args.thread, "name", "unknown"),
                exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
            )

        threading.excepthook = log_thread_exception


def configure_logging(level=logging.INFO):
    """Configure rotating general/error logs and return the general log path."""
    root_logger = logging.getLogger()
    for handler in root_logger.handlers:
        if getattr(handler, "_jarvis_rotating_handler", False):
            return LOG_PATH

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    formatter = RedactingFormatter(
        "%(asctime)s %(levelname)s %(name)s "
        "app=%(app_version)s execution=%(execution_id)s "
        "session=%(session_id)s route=%(route)s error=%(error_type)s: "
        "%(message)s"
    )
    context_filter = RuntimeContextFilter()
    handler = RotatingFileHandler(
        LOG_PATH,
        maxBytes=_MAX_LOG_BYTES,
        backupCount=_BACKUP_COUNT,
        encoding="utf-8",
    )
    handler._jarvis_rotating_handler = True
    handler.addFilter(context_filter)
    handler.setFormatter(formatter)
    root_logger.addHandler(handler)

    error_handler = RotatingFileHandler(
        ERROR_LOG_PATH,
        maxBytes=_MAX_LOG_BYTES,
        backupCount=_BACKUP_COUNT,
        encoding="utf-8",
    )
    error_handler._jarvis_rotating_handler = True
    error_handler.setLevel(logging.ERROR)
    error_handler.addFilter(context_filter)
    error_handler.setFormatter(formatter)
    root_logger.addHandler(error_handler)
    root_logger.setLevel(level)
    logging.captureWarnings(True)
    _install_exception_hooks()
    return LOG_PATH
