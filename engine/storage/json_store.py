"""Crash-resistant JSON persistence with per-file locking and recovery."""

from __future__ import annotations

import copy
import json
import logging
import os
import shutil
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


_LOCKS = {}
_LOCKS_GUARD = threading.Lock()
_UNSET = object()
_RECOVERY_EVENTS = []
_RECOVERY_EVENTS_LOCK = threading.Lock()


def _temporary_prefix(path):
    path = Path(path)
    return f".{path.name}.{os.getpid()}."


def _cleanup_abandoned_temporaries(path):
    """Remove temp files owned by processes that no longer exist."""
    path = Path(path)
    prefix = f".{path.name}."
    try:
        import psutil

        for candidate in path.parent.glob(f"{prefix}*.tmp"):
            remainder = candidate.name[len(prefix):]
            pid_text = remainder.split(".", 1)[0]
            if not pid_text.isdigit() or psutil.pid_exists(int(pid_text)):
                continue
            try:
                candidate.unlink()
            except (FileNotFoundError, PermissionError, OSError):
                pass
    except (ImportError, OSError):
        pass


def _path_lock(path):
    key = os.path.normcase(os.path.abspath(os.fspath(path)))
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.RLock())


def _record_recovery(path, source, error):
    event = {
        "path": os.path.abspath(os.fspath(path)),
        "source": source,
        "error": str(error),
        "timestamp": datetime.now().isoformat(timespec="seconds"),
    }
    with _RECOVERY_EVENTS_LOCK:
        _RECOVERY_EVENTS.append(event)


def get_recovery_events(clear=False):
    with _RECOVERY_EVENTS_LOCK:
        events = [dict(item) for item in _RECOVERY_EVENTS]
        if clear:
            _RECOVERY_EVENTS.clear()
    return events


def _replace_with_retry(source, destination, retries=3, base_delay=0.05):
    attempts = max(1, int(retries))
    for attempt in range(attempts):
        try:
            os.replace(source, destination)
            return
        except (PermissionError, OSError):
            if attempt + 1 >= attempts:
                raise
            time.sleep(base_delay * (2 ** attempt))


def _is_valid_json(path):
    try:
        with open(path, "r", encoding="utf-8") as source:
            json.load(source)
        return True
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return False


def _atomic_copy(source, destination, retries=3):
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    _cleanup_abandoned_temporaries(destination)
    descriptor, temporary = tempfile.mkstemp(
        prefix=_temporary_prefix(destination), suffix=".tmp", dir=destination.parent
    )
    os.close(descriptor)
    try:
        shutil.copy2(source, temporary)
        # The primary file remains valid until this backup replace completes.
        # The primary write itself is fsynced; repeating it for the secondary
        # copy only doubles latency without improving primary-file safety.
        _replace_with_retry(temporary, destination, retries=retries)
    finally:
        try:
            os.remove(temporary)
        except FileNotFoundError:
            pass


def backup_json(
    path,
    max_versions=0,
    backup_dir=None,
    retries=3,
    version_interval_seconds=0,
):
    """Preserve a valid JSON file as .bak and optional timestamped versions."""
    path = Path(path)
    if not path.is_file() or not _is_valid_json(path):
        return []

    created = []
    backup_path = Path(f"{path}.bak")
    _atomic_copy(path, backup_path, retries=retries)
    created.append(str(backup_path))

    if max_versions:
        version_dir = Path(backup_dir) if backup_dir else path.parent / "backups"
        version_dir.mkdir(parents=True, exist_ok=True)
        versions = sorted(
            version_dir.glob(f"{path.stem}_*{path.suffix}"),
            key=lambda item: item.stat().st_mtime_ns,
            reverse=True,
        )
        newest_age = (
            time.time() - versions[0].stat().st_mtime if versions else None
        )
        if newest_age is None or newest_age >= max(0, version_interval_seconds):
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            version_path = version_dir / f"{path.stem}_{stamp}{path.suffix}"
            _atomic_copy(path, version_path, retries=retries)
            created.append(str(version_path))
            versions.insert(0, version_path)
        for old_version in versions[int(max_versions):]:
            try:
                old_version.unlink()
            except FileNotFoundError:
                pass
    return created


def atomic_write_json(
    path,
    data,
    *,
    retries=3,
    max_versions=0,
    backup_dir=None,
    version_interval_seconds=0,
):
    """Write JSON without exposing a partially written destination file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _path_lock(path):
        _cleanup_abandoned_temporaries(path)
        descriptor, temporary = tempfile.mkstemp(
            prefix=_temporary_prefix(path), suffix=".tmp", dir=path.parent
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as output:
                json.dump(data, output, ensure_ascii=False, indent=4)
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())
            backup_json(
                path,
                max_versions=max_versions,
                backup_dir=backup_dir,
                retries=retries,
                version_interval_seconds=version_interval_seconds,
            )
            _replace_with_retry(temporary, path, retries=retries)
        finally:
            try:
                os.remove(temporary)
            except FileNotFoundError:
                pass
    return True


def safe_read_json(path, default=_UNSET, *, recover=True, retries=3):
    """Read JSON and restore a valid .bak when the primary file is damaged."""
    path = Path(path)
    fallback = {} if default is _UNSET else default
    with _path_lock(path):
        _cleanup_abandoned_temporaries(path)
        _cleanup_abandoned_temporaries(Path(f"{path}.bak"))
        try:
            with path.open("r", encoding="utf-8") as source:
                return json.load(source)
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
            backup_path = Path(f"{path}.bak")
            if recover and backup_path.is_file():
                try:
                    with backup_path.open("r", encoding="utf-8") as source:
                        recovered = json.load(source)
                except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as backup_error:
                    logger.debug(
                        "JSON 백업 복구 실패 path=%s error=%s", path, backup_error
                    )
                else:
                    # Reading a valid backup and repairing the primary are
                    # separate outcomes. A temporary Windows file lock must
                    # not turn valid user data into an empty default value.
                    try:
                        atomic_write_json(path, recovered, retries=retries)
                    except OSError as restore_error:
                        logger.warning(
                            "JSON primary restore deferred path=%s error=%s",
                            path,
                            type(restore_error).__name__,
                        )
                    _record_recovery(path, str(backup_path), error)
                    logger.warning(
                        "JSON 백업에서 복구했습니다 path=%s error=%s", path, error
                    )
                    return recovered
    return copy.deepcopy(fallback)


def recover_json(path, default=_UNSET, *, retries=3):
    return safe_read_json(path, default, recover=True, retries=retries)
