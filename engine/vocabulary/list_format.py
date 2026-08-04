"""The one place bullet and numbering wording is defined.

Word will want the same words, so this lives here rather than inside the 한글
operation — the same reasoning as line spacing.
"""

from __future__ import annotations

NONE = "none"
BULLET = "bullet"
NUMBER = "number"

CANONICAL_LIST_FORMATS = (NONE, BULLET, NUMBER)

LIST_FORMAT_ALIASES = {
    "글머리표": BULLET, "글머리 기호": BULLET, "불릿": BULLET,
    "점": BULLET, "기호": BULLET, "bullet": BULLET,
    "번호": NUMBER, "번호 매기기": NUMBER, "번호매기기": NUMBER,
    "넘버링": NUMBER, "순번": NUMBER, "number": NUMBER,
    "없음": NONE, "해제": NONE, "취소": NONE, "일반": NONE, "none": NONE,
}

_KOREAN_LABELS = {
    NONE: "글머리표 없음",
    BULLET: "글머리표",
    NUMBER: "번호 매기기",
}

LIST_FORMAT_COMMAND_PATTERN = (
    r"(글머리\s*(?:기호|표)?|불릿|번호\s*매기기|넘버링|bullet|number)"
)


def normalize_list_format(value):
    """Resolve any accepted wording to one canonical list format."""
    text = str(value or "").strip().casefold()
    if not text:
        raise ValueError("글머리표 종류를 지정해주세요.")
    canonical = LIST_FORMAT_ALIASES.get(text, text)
    if canonical not in CANONICAL_LIST_FORMATS:
        raise ValueError("글머리표는 글머리표·번호 매기기·없음을 지원합니다.")
    return canonical


def list_format_label(canonical) -> str:
    return _KOREAN_LABELS[canonical]


__all__ = [
    "BULLET",
    "CANONICAL_LIST_FORMATS",
    "LIST_FORMAT_ALIASES",
    "LIST_FORMAT_COMMAND_PATTERN",
    "NONE",
    "NUMBER",
    "list_format_label",
    "normalize_list_format",
]
