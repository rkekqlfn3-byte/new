import json
import logging
import os
import re
from pathlib import Path

logger = logging.getLogger(__name__)


PROFILE_DIR_RE = re.compile(r"^Profile (\d+)$", re.IGNORECASE)
BOOKMARK_NAME_RE = re.compile(r"[^a-z0-9가-힣\s]")


def _default_chrome_user_data_dir():
    return Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "User Data"


def discover_chrome_bookmark_files(user_data_dir=None):
    """Return existing Chrome profile bookmark files in a stable order."""
    root = Path(user_data_dir) if user_data_dir else _default_chrome_user_data_dir()
    if not root.is_dir():
        return []

    profiles = []
    for directory in root.iterdir():
        if not directory.is_dir():
            continue
        if directory.name == "Default":
            order = (0, 0, directory.name.lower())
        else:
            match = PROFILE_DIR_RE.fullmatch(directory.name)
            if not match:
                continue
            order = (1, int(match.group(1)), directory.name.lower())

        bookmark_path = directory / "Bookmarks"
        if bookmark_path.is_file():
            profiles.append((order, directory.name, bookmark_path))

    profiles.sort(key=lambda item: item[0])
    return [(profile_name, str(bookmark_path)) for _, profile_name, bookmark_path in profiles]


def clean_bookmark_name(name):
    cleaned = BOOKMARK_NAME_RE.sub("", str(name).lower())
    return re.sub(r"\s+", " ", cleaned).strip()


def _iter_bookmark_urls(node):
    if isinstance(node, dict):
        if node.get("type") == "url":
            yield node.get("name", ""), node.get("url", "")
        for child in node.get("children", []):
            yield from _iter_bookmark_urls(child)
    elif isinstance(node, list):
        for child in node:
            yield from _iter_bookmark_urls(child)


def scan_chrome_bookmarks(noun_dict, user_data_dir=None):
    """Merge bookmarks from every available Chrome Default/Profile N profile."""
    bookmark_files = discover_chrome_bookmark_files(user_data_dir)
    if not bookmark_files:
        return -1

    apps_found = 0
    for profile_name, bookmark_path in bookmark_files:
        try:
            with open(bookmark_path, "r", encoding="utf-8") as file:
                data = json.load(file)
        except (OSError, json.JSONDecodeError) as error:
            logger.warning("Chrome 프로필 북마크 검색 실패 profile=%s: %s", profile_name, error)
            continue

        roots = data.get("roots", {})
        root_nodes = roots.values() if isinstance(roots, dict) else []
        for root_node in root_nodes:
            for raw_name, url in _iter_bookmark_urls(root_node):
                clean_name = clean_bookmark_name(raw_name)
                if clean_name and isinstance(url, str) and url and clean_name not in noun_dict:
                    noun_dict[clean_name] = url
                    apps_found += 1

    return apps_found
