"""Registry for native application adapters."""

from __future__ import annotations

from engine.app_actions.base import AppActionBlocked, PreparedAction
from engine.app_actions.excel_adapter import ExcelAdapter
from engine.app_actions.hwp_adapter import HwpAdapter


class AppActionRegistry:
    def __init__(self, adapters=None):
        self._adapters = {"excel": ExcelAdapter(), "hwp": HwpAdapter()}
        if isinstance(adapters, dict):
            self._adapters.update({str(key).casefold(): value for key, value in adapters.items()})

    @staticmethod
    def normalize_target(target):
        normalized = str(target or "").strip().casefold()
        aliases = {
            "엑셀": "excel", "microsoft excel": "excel", "ms excel": "excel",
            "한글": "hwp", "한컴 한글": "hwp", "hanword": "hwp",
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
