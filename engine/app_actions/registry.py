"""Registry for native application adapters."""

from __future__ import annotations

from engine.app_actions.base import AppActionBlocked, PreparedAction
from engine.app_actions.contracts import NativeAppAdapter
from engine.app_actions.excel_vba_adapter import ExcelVbaAdapter
from engine.app_actions.hwp_adapter import HwpAdapter
from engine.app_actions.powerpoint_adapter import PowerPointAdapter
from engine.app_actions.word_adapter import WordAdapter


class AppActionRegistry:
    def __init__(self, adapters: dict[str, NativeAppAdapter] | None = None):
        self._adapters = {
            "excel": ExcelVbaAdapter(),
            "hwp": HwpAdapter(),
            "word": WordAdapter(),
            "powerpoint": PowerPointAdapter(),
        }
        if isinstance(adapters, dict):
            self._adapters.update({str(key).casefold(): value for key, value in adapters.items()})

    @staticmethod
    def normalize_target(target):
        normalized = str(target or "").strip().casefold()
        aliases = {
            "엑셀": "excel", "microsoft excel": "excel", "ms excel": "excel",
            "한글": "hwp", "한컴 한글": "hwp", "hanword": "hwp",
            "워드": "word", "microsoft word": "word", "ms word": "word",
            "파워포인트": "powerpoint", "ppt": "powerpoint",
            "microsoft powerpoint": "powerpoint",
        }
        return aliases.get(normalized, normalized)

    def get(self, target):
        normalized = self.normalize_target(target)
        adapter = self._adapters.get(normalized)
        if adapter is None:
            raise AppActionBlocked(f"지원하지 않는 네이티브 앱 대상입니다: {target}")
        return adapter

    def prepare(self, target, operation, params):
        return self.get(target).prepare(str(operation or "").strip(), params)

    def execute(self, prepared):
        if isinstance(prepared, dict):
            prepared = PreparedAction.from_dict(prepared)
        return self.get(prepared.app).execute(prepared)
