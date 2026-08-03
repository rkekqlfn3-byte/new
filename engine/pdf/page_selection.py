"""Normalize one-based PDF page selections without reading a document."""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from engine.pdf.errors import PdfReadError, PdfReadErrorCode

MAX_PAGE_SELECTION_CHARS = 2_000
MAX_PAGE_SELECTION_ITEMS = 100_000
_RANGE = re.compile(r"^(\d+)\s*[-~]\s*(\d+)$")
_NUMBER = re.compile(r"^\d+$")


def _selection_error(message: str) -> PdfReadError:
    return PdfReadError(PdfReadErrorCode.PAGE_SELECTION_INVALID, message)


def _bounded_page(value: Any, page_count: int) -> int:
    if type(value) is int:
        page = value
    elif isinstance(value, str) and value.isdigit():
        page = int(value)
    else:
        raise _selection_error("PDF page number must be an integer.")
    if page < 1 or page > page_count:
        raise _selection_error("PDF page number is outside the document range.")
    return page


def _parse_text_selection(value: str, page_count: int) -> tuple[int, ...]:
    text = value.strip().casefold()
    if text in {"all", "*"}:
        return tuple(range(1, page_count + 1))
    if not text or len(text) > MAX_PAGE_SELECTION_CHARS:
        raise _selection_error("PDF page selection text is invalid.")
    pages: list[int] = []
    for token in (item.strip() for item in text.replace("–", "-").split(",")):
        match = _RANGE.fullmatch(token)
        if match:
            start = _bounded_page(match.group(1), page_count)
            end = _bounded_page(match.group(2), page_count)
            if start > end:
                raise _selection_error("PDF page ranges must be ascending.")
            pages.extend(range(start, end + 1))
        elif _NUMBER.fullmatch(token):
            pages.append(_bounded_page(token, page_count))
        else:
            raise _selection_error("PDF page selection syntax is invalid.")
        if len(pages) > MAX_PAGE_SELECTION_ITEMS:
            raise _selection_error("PDF page selection exceeds the safe limit.")
    return tuple(sorted(set(pages)))


def normalize_page_selection(selection: Any, page_count: int) -> tuple[int, ...]:
    """Return sorted, unique, one-based pages within ``page_count``."""
    if isinstance(page_count, bool) or not isinstance(page_count, int) or page_count < 0:
        raise _selection_error("PDF page count is invalid.")
    if selection is None:
        return tuple(range(1, page_count + 1))
    if page_count == 0:
        raise _selection_error("An empty PDF has no selectable pages.")
    if isinstance(selection, str):
        return _parse_text_selection(selection, page_count)
    if isinstance(selection, int) and not isinstance(selection, bool):
        return (_bounded_page(selection, page_count),)
    if isinstance(selection, (bytes, bytearray)) or not isinstance(selection, Iterable):
        raise _selection_error("PDF page selection must be pages or a page range.")
    pages = tuple(_bounded_page(item, page_count) for item in selection)
    if not pages or len(pages) > MAX_PAGE_SELECTION_ITEMS:
        raise _selection_error("PDF page selection is empty or too large.")
    return tuple(sorted(set(pages)))
