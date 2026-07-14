"""Read-only resource and writable user-data paths for Jarvis."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


APP_NAME = "Jarvis"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
BUNDLE_ROOT = Path(getattr(sys, "_MEIPASS", PROJECT_ROOT))


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def resource_path(*parts: str) -> str:
    """Return a read-only resource path in source or PyInstaller builds."""
    return str(BUNDLE_ROOT.joinpath(*parts))


def _default_user_data_dir() -> Path:
    override = os.environ.get("JARVIS_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()

    local_app_data = os.environ.get("LOCALAPPDATA")
    base = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
    return base / APP_NAME / "data"


USER_DATA_DIR = _default_user_data_dir()


def seed_user_data(target_dir: os.PathLike | str, source_dirs) -> Path:
    """Copy missing persistent files without ever overwriting user changes."""
    target = Path(target_dir)
    target.mkdir(parents=True, exist_ok=True)

    sources = [Path(source) for source in source_dirs if Path(source).is_dir()]
    for filename in (
        "dictionaries.json", "user_memory.json", "user_preferences.json"
    ):
        destination = target / filename
        if destination.exists():
            continue
        for source in sources:
            candidate = source / filename
            if candidate.is_file():
                shutil.copy2(candidate, destination)
                break

    destination_sessions = target / "sessions"
    destination_sessions.mkdir(exist_ok=True)
    for source in sources:
        source_sessions = source / "sessions"
        if not source_sessions.is_dir():
            continue
        for session in source_sessions.glob("*.json"):
            destination = destination_sessions / session.name
            if not destination.exists():
                shutil.copy2(session, destination)

    return target


def initialize_user_data() -> Path:
    """Create writable data and migrate old layouts without overwriting it."""
    sources = []
    if is_frozen():
        # Old onedir releases wrote beside Jarvis.exe. Prefer that data during migration.
        sources.append(Path(sys.executable).resolve().parent / "data")
        # Releases before the private-data split bundled their seed under data/.
        sources.append(BUNDLE_ROOT / "data")
    else:
        # Source versions before phase 3 kept writable private data in the repository.
        sources.append(PROJECT_ROOT / "data")
    # This directory is always read-only and contains no API key or personal data.
    sources.append(BUNDLE_ROOT / "default_data")
    return seed_user_data(USER_DATA_DIR, sources)


def user_data_path(*parts: str) -> str:
    initialize_user_data()
    return str(USER_DATA_DIR.joinpath(*parts))
