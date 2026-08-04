"""The one place line-spacing wording is defined.

Korean office documents specify 줄간격 constantly — public-sector templates
prescribe it — and no application supported it before.  Word will want the same
wording, so the table lives here from the start rather than being copied later.

Spacing is expressed as a percentage of the line height, which is how both
한글 and Word model it: 160 means 160%.
"""

from __future__ import annotations

import re

MIN_PERCENT = 50
MAX_PERCENT = 500

# Named steps, so `줄간격 넓게` works without the user knowing a number.
NAMED_SPACINGS = {
    "좁게": 130,
    "좁은": 130,
    "보통": 160,
    "기본": 160,
    "넓게": 200,
    "넓은": 200,
    "매우 넓게": 250,
}

_MULTIPLE_RE = re.compile(r"([0-9]+(?:\.[0-9]+)?)\s*배")
_PERCENT_RE = re.compile(r"([0-9]+(?:\.[0-9]+)?)\s*%")
_BARE_NUMBER_RE = re.compile(r"([0-9]+(?:\.[0-9]+)?)")

LINE_SPACING_COMMAND_PATTERN = r"(?:줄\s*간격|행\s*간격|줄\s*간|line\s*spacing)"


def normalize_line_spacing(value):
    """Resolve any accepted wording to a whole percentage.

    Accepts ``160``, ``"160%"``, ``"1.5배"`` and ``"넓게"``.  Raises
    ``ValueError`` for anything else; adapters translate that into their own
    blocked error so the message stays in their voice.
    """
    if value is None:
        raise ValueError("줄간격을 지정해주세요.")
    if isinstance(value, bool):
        raise ValueError("줄간격은 숫자나 배수로 지정해주세요.")
    if isinstance(value, (int, float)):
        percent = float(value)
    else:
        text = str(value).strip().casefold()
        if not text:
            raise ValueError("줄간격을 지정해주세요.")
        if text in NAMED_SPACINGS:
            percent = float(NAMED_SPACINGS[text])
        else:
            multiple = _MULTIPLE_RE.search(text)
            percent_match = _PERCENT_RE.search(text)
            bare = _BARE_NUMBER_RE.search(text)
            if multiple:
                percent = float(multiple.group(1)) * 100
            elif percent_match:
                percent = float(percent_match.group(1))
            elif bare:
                # A bare number below 10 reads as a multiple: `줄간격 2` means
                # double spacing, not 2%.
                number = float(bare.group(1))
                percent = number * 100 if number < 10 else number
            else:
                raise ValueError(
                    "줄간격은 160, 160%, 1.5배 또는 좁게·보통·넓게로 지정해주세요."
                )
    if percent != int(percent):
        percent = round(percent)
    percent = int(percent)
    if not MIN_PERCENT <= percent <= MAX_PERCENT:
        raise ValueError(
            f"줄간격은 {MIN_PERCENT}%부터 {MAX_PERCENT}% 사이여야 합니다."
        )
    return percent


def line_spacing_label(percent) -> str:
    return f"줄간격 {int(percent)}%"


__all__ = [
    "LINE_SPACING_COMMAND_PATTERN",
    "MAX_PERCENT",
    "MIN_PERCENT",
    "NAMED_SPACINGS",
    "line_spacing_label",
    "normalize_line_spacing",
]
