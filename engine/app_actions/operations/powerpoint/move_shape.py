"""Move the selected PowerPoint shape, keeping it on the slide."""

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


class MoveShapeOperation(PowerPointOperation):
    name = "move_shape"

    def prepare(self, adapter, session, params):
        presentation = session.presentation
        _, _, state = adapter._context(session.application, presentation)
        if params.get("left") is not None and params.get("top") is not None:
            left = float(params["left"])
            top = float(params["top"])
            dx = left - state["left"]
            dy = top - state["top"]
        else:
            dx = float(params.get("dx", 0))
            dy = float(params.get("dy", 0))
            left = state["left"] + dx
            top = state["top"] + dy
        if (dx == 0 and dy == 0) or abs(dx) > 500 or abs(dy) > 500:
            raise AppActionBlocked("PowerPoint Shape 이동량은 -500~500pt 범위여야 합니다.")
        slide_width = float(presentation.PageSetup.SlideWidth)
        slide_height = float(presentation.PageSetup.SlideHeight)
        if (
            left < 0
            or top < 0
            or left + state["width"] > slide_width
            or top + state["height"] > slide_height
        ):
            raise AppActionBlocked("Shape가 슬라이드 밖으로 나가므로 이동하지 않았습니다.")
        snapshot = {
            **adapter._base_snapshot(state, self.name),
            "desired_left": left,
            "desired_top": top,
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
                "left": left,
                "top": top,
                "original_left": state["left"],
                "original_top": state["top"],
            },
            current_state=adapter._common_current_state(state),
            estimated_changes=1,
            destructive=False,
            reversible=True,
            verification_method="read_powerpoint_shape_position",
            context_fingerprint=stable_state_fingerprint(snapshot),
            prepared_at=prepared_at_timestamp(),
        )

    def run(self, adapter, target, current):
        slide = target.slide
        shape_id = target.shape_id
        try:
            adapter._set_shape_position(
                slide,
                shape_id,
                current.params["left"],
                current.params["top"],
            )
            actual = adapter._geometry_state(slide, shape_id)
            if not adapter._position_matches(
                actual, current.params["left"], current.params["top"]
            ):
                raise AppActionVerificationError("PowerPoint Shape 이동 결과가 다릅니다.")
            adapter._shape_by_id(slide, shape_id).Select()
        except Exception as error:
            restored = False
            try:
                adapter._set_shape_position(
                    slide,
                    shape_id,
                    current.params["original_left"],
                    current.params["original_top"],
                )
                restored = adapter._position_matches(
                    adapter._geometry_state(slide, shape_id),
                    current.params["original_left"],
                    current.params["original_top"],
                )
            except Exception:
                restored = False
            if not restored:
                raise AppActionVerificationError(
                    "PowerPoint Shape 이동 실패 뒤 원래 위치 복원을 확인하지 못했습니다."
                ) from None
            if isinstance(error, AppActionError):
                raise
            raise AppActionVerificationError(
                "PowerPoint Shape 이동 또는 검증에 실패했습니다."
            ) from error
        return adapter._result(
            current,
            True,
            {"left": actual["left"], "top": actual["top"]},
        )


__all__ = ["MoveShapeOperation"]
