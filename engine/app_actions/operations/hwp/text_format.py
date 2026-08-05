"""Apply bold, font size and text colour to the Hanword selection."""

from __future__ import annotations

from engine.app_actions.base import (
    AppActionBlocked,
    AppActionError,
    AppActionVerificationError,
    PreparedAction,
)
from engine.app_actions.operations.hwp.base import (
    MAX_FORMAT_SELECTION_CHARS,
    HwpOperation,
)
from engine.app_actions.operations.hwp.state import (
    COLOR_RGB,
    char_state,
    normalize_color_name,
)


class SetTextFormatOperation(HwpOperation):
    name = "set_text_format"

    def desired_format(self, hwp, params, current=None):
        supplied = params.get("desired")
        desired = dict(supplied) if isinstance(supplied, dict) else {}
        labels = dict(params.get("format_labels") or {})
        if not isinstance(supplied, dict):
            if params.get("bold") is not None:
                desired["bold"] = 1 if bool(params.get("bold")) else 0
            if params.get("font_size") is not None:
                try:
                    size = float(params.get("font_size"))
                except (TypeError, ValueError) as error:
                    raise AppActionBlocked("한글 글자 크기는 숫자로 지정해주세요.") from error
                if not 1 <= size <= 409:
                    raise AppActionBlocked("한글 글자 크기는 1부터 409포인트 사이여야 합니다.")
                desired["font_size_hu"] = int(hwp.PointToHwpUnit(size))
                labels["font_size"] = int(size) if size.is_integer() else size
            elif params.get("font_size_delta") is not None:
                try:
                    delta = float(params.get("font_size_delta"))
                except (TypeError, ValueError) as error:
                    raise AppActionBlocked(
                        "한글 글자 크기 변화량은 숫자로 지정해주세요."
                    ) from error
                if delta == 0 or abs(delta) > 72:
                    raise AppActionBlocked(
                        "한글 글자 크기 변화량은 0이 아닌 72포인트 이하여야 합니다."
                    )
                current = dict(current or char_state(hwp))
                minimum = int(hwp.PointToHwpUnit(1))
                maximum = int(hwp.PointToHwpUnit(409))
                current_height = int(current["font_size_hu"])
                if not minimum <= current_height <= maximum:
                    raise AppActionBlocked(
                        "선택 영역의 글자 크기가 섞여 있어 상대 크기를 안전하게 계산할 수 없습니다."
                    )
                target = current_height + int(hwp.PointToHwpUnit(delta))
                if not minimum <= target <= maximum:
                    raise AppActionBlocked(
                        "변경 후 한글 글자 크기는 1부터 409포인트 사이여야 합니다."
                    )
                desired["font_size_hu"] = target
                labels["font_size_delta"] = delta
            if params.get("text_color") is not None:
                color = normalize_color_name(params.get("text_color"))
                desired["text_color"] = int(hwp.RGBColor(*COLOR_RGB[color]))
                labels["text_color"] = color
        allowed = {"bold", "font_size_hu", "text_color"}
        if not desired or set(desired) - allowed:
            raise AppActionBlocked("지원하는 한글 글자 서식을 하나 이상 지정해주세요.")
        desired = {key: int(value) for key, value in desired.items()}
        return desired, labels

    def prepare(self, adapter, hwp, params):
        _, base, _, selection = adapter._context(hwp)
        if not selection["has_selection"]:
            raise AppActionBlocked("글자 서식을 적용할 텍스트를 한글에서 먼저 선택해주세요.")
        if selection["text_length"] > MAX_FORMAT_SELECTION_CHARS:
            raise AppActionBlocked(
                f"글자 서식은 한 번에 최대 {MAX_FORMAT_SELECTION_CHARS:,}자까지 지원합니다."
            )
        current = char_state(hwp)
        target = adapter._target(base, selection)
        desired, labels = self.desired_format(hwp, params, current)
        noop = all(current.get(key) == value for key, value in desired.items())
        snapshot = {
            **base,
            "operation": self.name,
            "target": target,
            "char_state": current,
            "desired": desired,
        }
        return PreparedAction(
            app=self.app,
            operation=self.name,
            document_id=base["document_id"],
            workbook_name=base["document_name"],
            sheet="현재 문서",
            target=target,
            params={"desired": desired, "format_labels": labels},
            current_state={
                "has_selection": True,
                "selected_length": selection["text_length"],
                "selected_digest": selection["text_digest"],
                "selected_preview": selection["text"][:120],
                "format": current,
                "document_digest": base["text_digest"],
            },
            estimated_changes=0 if noop else selection["text_length"],
            destructive=selection["text_length"] > 1000 and not noop,
            reversible=True,
            verification_method="read_selected_char_shape",
            context_fingerprint=adapter._state_fingerprint(snapshot),
            prepared_at=adapter._created_at(),
            noop=noop,
            metadata={"window_handle": base["window_handle"]},
        )

    def run(self, adapter, hwp, current):
        before = current.current_state["format"]
        if current.noop:
            return adapter._result(current, before, before, False)
        try:
            shape = hwp.HParameterSet.HCharShape
            hwp.HAction.GetDefault("CharShape", shape.HSet)
            for key, attribute in (
                ("bold", "Bold"),
                ("font_size_hu", "Height"),
                ("text_color", "TextColor"),
            ):
                if key in current.params["desired"]:
                    setattr(shape, attribute, current.params["desired"][key])
            hwp.HAction.Execute("CharShape", shape.HSet)
            after = char_state(hwp)
            if not all(
                after.get(key) == value
                for key, value in current.params["desired"].items()
            ):
                raise AppActionVerificationError("한글 선택 영역의 글자 서식이 요청과 다릅니다.")
        except Exception as error:
            try:
                adapter._undo(hwp)
            except Exception:
                pass
            if isinstance(error, AppActionError):
                raise
            raise AppActionVerificationError(
                "한글 글자 서식 적용 또는 검증에 실패했습니다."
            ) from error
        return adapter._result(current, before, after, True)


__all__ = ["SetTextFormatOperation"]
