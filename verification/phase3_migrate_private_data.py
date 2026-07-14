"""Archive legacy project data and merge it into the separated user folder."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path


def _load(path: Path, default):
    if not path.is_file():
        return default
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return default
    return value


def _atomic_write(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".phase3.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    os.replace(temporary, path)


def _merge_missing_mapping(current, legacy):
    merged = dict(current) if isinstance(current, dict) else {}
    added = 0
    if isinstance(legacy, dict):
        for key, value in legacy.items():
            if key not in merged:
                merged[key] = value
                added += 1
    return merged, added


def merge_dictionary(legacy_path: Path, target_path: Path):
    legacy = _load(legacy_path, {})
    current = _load(target_path, {})
    if not isinstance(legacy, dict):
        legacy = {}
    if not isinstance(current, dict):
        current = {}
    if not current:
        current = dict(legacy)
        _atomic_write(target_path, current)
        return {"dictionary_created": True, "nouns_added": len(current.get("noun_dictionary", {}))}

    added_counts = {}
    for field in ("noun_dictionary", "macro_dictionary", "search_engines_dict"):
        current[field], added_counts[field] = _merge_missing_mapping(
            current.get(field), legacy.get(field)
        )

    current_learned = current.get("learned_macros")
    if not isinstance(current_learned, dict):
        current_learned = {}
    learned_added = 0
    for app_name, legacy_macros in (
        legacy.get("learned_macros", {}).items()
        if isinstance(legacy.get("learned_macros"), dict) else []
    ):
        app_macros = current_learned.setdefault(app_name, {})
        if not isinstance(app_macros, dict):
            continue
        merged, added = _merge_missing_mapping(app_macros, legacy_macros)
        current_learned[app_name] = merged
        learned_added += added
    current["learned_macros"] = current_learned

    for field in ("favorites", "user_nouns"):
        values = list(current.get(field) or [])
        for value in legacy.get(field) or []:
            if value not in values:
                values.append(value)
        current[field] = values

    current_config = current.get("ai_config")
    if not isinstance(current_config, dict):
        current_config = {}
    legacy_config = legacy.get("ai_config")
    if isinstance(legacy_config, dict):
        for key, value in legacy_config.items():
            if not current_config.get(key) and value:
                current_config[key] = value
    current["ai_config"] = current_config
    current["has_scanned"] = bool(current.get("has_scanned") or legacy.get("has_scanned"))
    try:
        current["schema_version"] = max(
            int(current.get("schema_version", 1)), int(legacy.get("schema_version", 1))
        )
    except (TypeError, ValueError):
        pass
    _atomic_write(target_path, current)
    return {
        "dictionary_created": False,
        "nouns_added": added_counts.get("noun_dictionary", 0),
        "macros_added": added_counts.get("macro_dictionary", 0),
        "search_engines_added": added_counts.get("search_engines_dict", 0),
        "learned_macros_added": learned_added,
        "final_noun_count": len(current.get("noun_dictionary", {})),
    }


def merge_memory(legacy_path: Path, target_path: Path):
    legacy = _load(legacy_path, {})
    current = _load(target_path, {})
    if not isinstance(current, dict):
        current = {}
    changed = False
    if isinstance(legacy, dict):
        for field in ("user_info", "rules", "others"):
            if not str(current.get(field) or "").strip() and str(legacy.get(field) or "").strip():
                current[field] = legacy[field]
                changed = True
    if changed or not target_path.exists():
        _atomic_write(target_path, current or {"user_info": "", "rules": "", "others": ""})
    return changed


def merge_preferences(legacy_path: Path, target_path: Path):
    legacy = _load(legacy_path, {})
    current = _load(target_path, {})
    if not isinstance(current, dict):
        current = {}
    current_prefs, added = _merge_missing_mapping(
        current.get("preferences"),
        legacy.get("preferences") if isinstance(legacy, dict) else {},
    )
    current["schema_version"] = current.get("schema_version", 1)
    current["preferences"] = current_prefs
    if added or not target_path.exists():
        _atomic_write(target_path, current)
    return added


def archive_and_merge(project_root: Path, user_data_dir: Path, archive_root: Path):
    legacy_data = project_root / "data"
    legacy_backups = project_root / "backups"
    timestamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    archive_dir = archive_root / f"phase3_{timestamp}"
    archive_dir.mkdir(parents=True, exist_ok=False)
    if legacy_data.is_dir():
        shutil.copytree(legacy_data, archive_dir / "data")
    if legacy_backups.is_dir():
        shutil.copytree(legacy_backups, archive_dir / "backups")

    user_data_dir.mkdir(parents=True, exist_ok=True)
    dictionary_result = merge_dictionary(
        legacy_data / "dictionaries.json", user_data_dir / "dictionaries.json"
    )
    memory_merged = merge_memory(
        legacy_data / "user_memory.json", user_data_dir / "user_memory.json"
    )
    preferences_added = merge_preferences(
        legacy_data / "user_preferences.json", user_data_dir / "user_preferences.json"
    )

    sessions_added = 0
    source_sessions = legacy_data / "sessions"
    target_sessions = user_data_dir / "sessions"
    target_sessions.mkdir(exist_ok=True)
    if source_sessions.is_dir():
        for source in source_sessions.glob("*.json"):
            target = target_sessions / source.name
            if not target.exists():
                shutil.copy2(source, target)
                sessions_added += 1

    report = {
        "status": "completed",
        "archive_dir": str(archive_dir),
        "legacy_data_archived": legacy_data.is_dir(),
        "legacy_backups_archived": legacy_backups.is_dir(),
        "dictionary": dictionary_result,
        "memory_fields_merged": bool(memory_merged),
        "preferences_added": preferences_added,
        "sessions_added": sessions_added,
        "secret_values_printed": False,
    }
    (archive_dir / "migration_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    local = os.environ.get("LOCALAPPDATA")
    base = Path(local) if local else Path.home() / "AppData" / "Local"
    parser.add_argument("--user-data-dir", type=Path, default=base / "Jarvis" / "data")
    parser.add_argument(
        "--archive-root", type=Path,
        default=base / "Jarvis" / "migration_backups",
    )
    args = parser.parse_args(argv)
    try:
        report = archive_and_merge(
            args.project_root.resolve(), args.user_data_dir.resolve(),
            args.archive_root.resolve(),
        )
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"phase3_migration=failed type={type(error).__name__}")
        return 1
    print("phase3_migration=completed")
    print(f"archive_dir={report['archive_dir']}")
    for key, value in report["dictionary"].items():
        print(f"{key}={value}")
    print(f"preferences_added={report['preferences_added']}")
    print(f"sessions_added={report['sessions_added']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
