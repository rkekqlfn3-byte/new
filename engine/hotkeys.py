"""Small Windows hotkey helper without heavyweight GUI automation packages."""

import ctypes
import re

KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002

_KEYS = {
    "backspace": (0x08, False),
    "tab": (0x09, False),
    "enter": (0x0D, False),
    "shift": (0x10, False),
    "ctrl": (0x11, False),
    "alt": (0x12, False),
    "pause": (0x13, False),
    "capslock": (0x14, False),
    "esc": (0x1B, False),
    "space": (0x20, False),
    "pageup": (0x21, True),
    "pagedown": (0x22, True),
    "end": (0x23, True),
    "home": (0x24, True),
    "left": (0x25, True),
    "up": (0x26, True),
    "right": (0x27, True),
    "down": (0x28, True),
    "printscreen": (0x2C, True),
    "insert": (0x2D, True),
    "delete": (0x2E, True),
    "win": (0x5B, True),
    "apps": (0x5D, True),
    ";": (0xBA, False),
    "=": (0xBB, False),
    ",": (0xBC, False),
    "-": (0xBD, False),
    ".": (0xBE, False),
    "/": (0xBF, False),
    "`": (0xC0, False),
    "[": (0xDB, False),
    "\\": (0xDC, False),
    "]": (0xDD, False),
    "'": (0xDE, False),
}

_ALIASES = {
    "control": "ctrl",
    "ctl": "ctrl",
    "windows": "win",
    "windowskey": "win",
    "super": "win",
    "option": "alt",
    "return": "enter",
    "escape": "esc",
    "del": "delete",
    "ins": "insert",
    "pgup": "pageup",
    "pgdn": "pagedown",
    "prtsc": "printscreen",
    "컨트롤": "ctrl",
    "윈도우": "win",
    "쉬프트": "shift",
    "시프트": "shift",
    "알트": "alt",
    "엔터": "enter",
    "스페이스": "space",
}


def resolve_key(key):
    normalized = str(key).strip().lower().replace(" ", "")
    normalized = _ALIASES.get(normalized, normalized)

    if len(normalized) == 1 and normalized.isascii() and normalized.isalnum():
        return ord(normalized.upper()), False
    function_match = re.fullmatch(r"f([1-9]|1[0-9]|2[0-4])", normalized)
    if function_match:
        return 0x6F + int(function_match.group(1)), False
    if normalized in _KEYS:
        return _KEYS[normalized]
    raise ValueError(f"지원하지 않는 단축키: {key}")


def press_hotkey(keys, keybd_event=None):
    if isinstance(keys, str):
        keys = [part.strip() for part in keys.split("+") if part.strip()]
    if not keys:
        raise ValueError("단축키가 비어 있습니다.")

    sender = keybd_event or ctypes.windll.user32.keybd_event
    resolved = [resolve_key(key) for key in keys]
    pressed = []
    try:
        for virtual_key, extended in resolved:
            flags = KEYEVENTF_EXTENDEDKEY if extended else 0
            sender(virtual_key, 0, flags, 0)
            pressed.append((virtual_key, extended))
    finally:
        for virtual_key, extended in reversed(pressed):
            flags = KEYEVENTF_KEYUP | (KEYEVENTF_EXTENDEDKEY if extended else 0)
            sender(virtual_key, 0, flags, 0)

    return True
