"""Fail-closed output path policy for PDF transformations."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

_SAFE_SUFFIX = re.compile(r"^[0-9A-Za-z\uac00-\ud7a3_-]{1,40}$")


class OutputPathPolicyError(ValueError):
    """The requested transformation output path is unsafe."""


@dataclass(frozen=True)
class OutputPathDecision:
    canonical_output_path: str
    overwrite: bool
    backup_required: bool
    requires_confirmation: bool = True


def _local_pdf_path(value, label: str) -> str:
    text = str(value or "").strip()
    if not text or "\x00" in text or len(text) > 32_767:
        raise OutputPathPolicyError(f"{label} is invalid.")
    expanded = os.path.expandvars(os.path.expanduser(text))
    if not os.path.isabs(expanded) or expanded.startswith(("\\\\", "//")):
        raise OutputPathPolicyError(f"{label} must be an absolute local path.")
    canonical = os.path.abspath(expanded)
    if Path(canonical).suffix.casefold() != ".pdf":
        raise OutputPathPolicyError(f"{label} must have a .pdf extension.")
    return canonical


def _path_key(value: str) -> str:
    return os.path.normcase(os.path.normpath(value))


def evaluate_pdf_output_path(
    source_paths: Iterable[str],
    output_path: str,
    *,
    overwrite: bool = False,
    path_exists: Callable[[str], bool] = os.path.exists,
) -> OutputPathDecision:
    """Validate a new local PDF destination without creating any file."""
    if type(overwrite) is not bool:
        raise OutputPathPolicyError("PDF overwrite state must be a boolean.")
    sources = tuple(_local_pdf_path(item, "PDF source path") for item in source_paths)
    if not sources:
        raise OutputPathPolicyError("PDF output policy requires a source path.")
    if any(not os.path.isfile(item) for item in sources):
        raise OutputPathPolicyError("Every PDF source must be an existing local file.")
    if any(os.path.islink(item) for item in sources):
        raise OutputPathPolicyError("PDF source cannot use a symbolic link.")
    output = _local_pdf_path(output_path, "PDF output path")
    if _path_key(output) in {_path_key(item) for item in sources}:
        raise OutputPathPolicyError("PDF output cannot overwrite a source PDF.")
    parent = os.path.dirname(output)
    if not os.path.isdir(parent):
        raise OutputPathPolicyError("PDF output directory does not exist.")
    if os.path.islink(output):
        raise OutputPathPolicyError("PDF output cannot use a symbolic link.")
    output_exists = bool(path_exists(output))
    if output_exists and not overwrite:
        raise OutputPathPolicyError(
            "PDF output already exists. Choose a new name or explicitly allow overwrite."
        )
    return OutputPathDecision(
        canonical_output_path=output,
        overwrite=overwrite,
        backup_required=output_exists and overwrite,
    )


def suggest_pdf_output_path(
    source_path: str,
    suffix: str,
    *,
    path_exists: Callable[[str], bool] = os.path.exists,
) -> str:
    """Suggest a non-existing sibling PDF path without touching the filesystem."""
    source = _local_pdf_path(source_path, "PDF source path")
    normalized_suffix = str(suffix or "").strip()
    if not _SAFE_SUFFIX.fullmatch(normalized_suffix):
        raise OutputPathPolicyError("PDF output name suffix is invalid.")
    parent = Path(source).parent
    stem = Path(source).stem
    for index in range(1, 10_001):
        number = "" if index == 1 else f"_{index}"
        candidate = str(parent / f"{stem}_{normalized_suffix}{number}.pdf")
        if _path_key(candidate) != _path_key(source) and not path_exists(candidate):
            return candidate
    raise OutputPathPolicyError("Could not reserve an available PDF output name.")
