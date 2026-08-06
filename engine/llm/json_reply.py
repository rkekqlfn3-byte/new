"""Read a JSON object out of whatever shape a provider wrapped it in.

The command providers answer as ``{"response": "<json string>"}``, so a
reply has to be unwrapped twice; a model that adds prose around the object,
or fences it, has to be tolerated too.  This lived in three identical copies
— edit-mode translation, macro composition and PDF intent — which made the
envelope, a provider contract detail, something three files had to agree on.
"""

from __future__ import annotations

import json
import re
from typing import Any, Mapping

_JSON_OBJECT = re.compile(r"\{.*\}", re.DOTALL)


def json_reply(raw) -> dict:
    """The object a provider meant to send, or an empty mapping."""
    if isinstance(raw, Mapping):
        candidate: Any = raw
    else:
        match = _JSON_OBJECT.search(str(raw or ""))
        if not match:
            return {}
        try:
            candidate = json.loads(match.group(0))
        except (TypeError, ValueError):
            return {}
    inner = candidate.get("response") if isinstance(candidate, Mapping) else None
    if isinstance(inner, str):
        match = _JSON_OBJECT.search(inner)
        if match:
            try:
                candidate = json.loads(match.group(0))
            except (TypeError, ValueError):
                return {}
    return candidate if isinstance(candidate, Mapping) else {}


__all__ = ["json_reply"]
