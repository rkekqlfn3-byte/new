"""Central version and runtime identity for diagnostics.

Stamped onto execution diagnostics so a later log reader can tell which code
and data-schema version produced a record. A frozen EXE has no git available,
so ``APP_VERSION`` is the manual source of truth — bump it when cutting a
release.
"""

from __future__ import annotations

import platform
import sys

# JARVIS application version. V1 was the first stable release; this hardening
# batch (HTML sanitization, concurrent-execution guard, native-route contract,
# diagnostics) is 1.1.0. Bump on each release cut.
APP_VERSION = "1.1.0"

# Persisted user-data schema (see default_data/dictionaries.json schema_version).
DATA_SCHEMA_VERSION = 4


def runtime_info() -> dict:
    """Return a small, JSON-serializable identity block for diagnostics."""
    return {
        "app_version": APP_VERSION,
        "data_schema_version": DATA_SCHEMA_VERSION,
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "frozen": bool(getattr(sys, "frozen", False)),
    }
