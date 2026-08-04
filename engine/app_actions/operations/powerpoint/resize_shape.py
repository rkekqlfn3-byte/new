"""Resize the selected PowerPoint shape, keeping it on the slide."""

from __future__ import annotations

from engine.app_actions.base import (
    AppActionBlocked,
    AppActionError,
    AppActionVerificationError,
    PreparedAction,
)
from engine.app_actions.office_helpers import (
    prepared_at_timestamp,
    stable_state_fingerprint,
)
from engine.app_actions.operations.powerpoint.base import PowerPointOperation


class ResizeShapeOperation(PowerPointOperation):
    name = "resize_shape"

    def prepare(self, adapter, session, params):
        presentation = session.presentation
        _, _, state = adapter._context(session.application, presentation)
        if params.get("width") is not None and params.get("height") is not None:
            width = float(params["width"])
            height = float(params["height"])
            width_scale = width / state["width"] if state["width"] else 0
            height_scale = height / state["height"] if state["height"] else 0
            if not 0.5 <= width_scale <= 2 or not 0.5 <= height_scale <= 2:
                raise AppActionBlocked(
                    "PowerPoint Shape 크기 배율은 0.5~2 사이여야 합니다."
                )
        else:
            scale = float(params.get("scale", 1))
            if not 0.5 <= scale <= 2 or abs(scale - 1) < 0.0001:
                raise AppActionBlocked(
                    "PowerPoint Shape 크기 배율은 0.5~2 사이여야 합니다."
                )
            width = state["width"] * scale
            height = state["height"] * scale
        slide_width = float(presentation.PageSetup.SlideWidth)
        slide_height = float(presentation.PageSetup.SlideHeight)
        if state["left"] + width > slide_width or state["top"] + height > slide_height:
            raise AppActionBlocked("크기를 바꾸면 Shape가 슬라이드 밖으로 나갑니다.")
        snapshot = {
            **adapter._base_snapshot(state, self.name),
            "desired_width": width,
            "desired_height": height,
        }
        return PreparedAction(
            app=self.app,
            operation=self.name,
            document_id=state["document_id"],
            workbook_name=state["document_name"],
            sheet=f"슬라이드 {state['slide_index']}",
            target=adapter._target(state),
            params={
                "document_path": state["document_id"],
                "width": width,
                "height": height,
                "original_width": state["width"],
                "original_height": state["height"],
                "lock_aspect_ratio": state["lock_aspect_ratio"],
            },
            current_state=adapter._common_current_state(state),
            estimated_changes=1,
            destructive=False,
            reversible=True,
            verification_method="read_powerpoint_shape_size",
            context_fingerprint=stable_state_fingerprint(snapshot),
            prepared_at=prepared_at_timestamp(),
        )

    def run(self, adapter, target, current):
        slide = target.slide
        shape_id = target.shape_id
        original_lock = int(current.params["lock_aspect_ratio"])
        try:
            adapter._set_shape_size(
                slide,
                shape_id,
                current.params["width"],
                current.params["height"],
                original_lock,
            )
            actual = adapter._geometry_state(slide, shape_id)
            if not adapter._size_matches(
                actual,
                current.params["width"],
                current.params["height"],
                original_lock,
            ):
                raise AppActionVerificationError("PowerPoint Shape 크기 결과가 다릅니다.")
            adapter._shape_by_id(slide, shape_id).Select()
        except Exception as error:
            restored = False
            try:
                adapter._set_shape_size(
                    slide,
                    shape_id,
                    current.params["original_width"],
                    current.params["original_height"],
                    original_lock,
                )
                restored = adapter._size_matches(
                    adapter._geometry_state(slide, shape_id),
                    current.params["original_width"],
                    current.params["original_height"],
                    original_lock,
                )
            except Exception:
                restored = False
            if not restored:
                raise AppActionVerificationError(
                    "PowerPoint Shape 크기 변경 실패 뒤 원래 크기 복원을 확인하지 못했습니다."
                ) from None
            if isinstance(error, AppActionError):
                raise
            raise AppActionVerificationError(
                "PowerPoint Shape 크기 변경 또는 검증에 실패했습니다."
            ) from error
        return adapter._result(
            current,
            True,
            {"width": actual["width"], "height": actual["height"]},
        )


__all__ = ["ResizeShapeOperation"]
