"""Set 한글 page margins and paper orientation.

Confirmed against a real 한글 install: ``HSecDef.PageDef`` carries the margins
and the ``Landscape`` flag, ``MiliToHwpUnit`` converts millimetres to the
HWPUNIT values it stores, and writing them back through
``Execute("PageSetup", …)`` round-trips exactly.  The change also survives
lease cycles and document context reads, so it is in the document rather than
an echo of the parameter set.

A one-unit tolerance is allowed on verification because HWPUNIT is 1/7200
inch, far below anything a user could perceive, and an exact comparison would
turn a rounding difference into a failed edit.
"""

from __future__ import annotations

from engine.app_actions.base import (
    AppActionBlocked,
    AppActionError,
    AppActionVerificationError,
    PreparedAction,
)
from engine.app_actions.operations.hwp.base import HwpOperation
from engine.vocabulary.page_setup import (
    LANDSCAPE,
    MARGIN_SIDES,
    normalize_margin_mm,
    normalize_orientation,
    orientation_label,
)

MARGIN_FIELDS = {
    "left": "LeftMargin",
    "right": "RightMargin",
    "top": "TopMargin",
    "bottom": "BottomMargin",
}

# HWPUNIT is 1/7200 inch; one unit is about 0.0035mm.
MARGIN_TOLERANCE = 1


def _page_definition(hwp):
    section = hwp.HParameterSet.HSecDef
    hwp.HAction.GetDefault("PageSetup", section.HSet)
    return section, section.PageDef


def page_setup_state(hwp) -> dict:
    _, definition = _page_definition(hwp)
    state = {
        side: int(getattr(definition, field))
        for side, field in MARGIN_FIELDS.items()
    }
    state["landscape"] = int(getattr(definition, "Landscape", 0))
    return state


class SetPageSetupOperation(HwpOperation):
    name = "set_page_setup"

    def desired_state(self, hwp, params, current):
        desired = dict(current)
        requested = False
        margin = params.get("margin_mm")
        if margin is not None:
            try:
                millimetres = normalize_margin_mm(margin)
            except ValueError as error:
                raise AppActionBlocked(str(error)) from error
            units = int(hwp.MiliToHwpUnit(millimetres))
            for side in params.get("margin_sides") or MARGIN_SIDES:
                if side not in MARGIN_FIELDS:
                    raise AppActionBlocked(
                        "여백은 왼쪽·오른쪽·위·아래만 지정할 수 있습니다."
                    )
                desired[side] = units
            requested = True
        orientation = params.get("orientation")
        if orientation is not None:
            try:
                canonical = normalize_orientation(orientation)
            except ValueError as error:
                raise AppActionBlocked(str(error)) from error
            desired["landscape"] = 1 if canonical == LANDSCAPE else 0
            requested = True
        if not requested:
            raise AppActionBlocked("바꿀 여백이나 용지 방향을 지정해주세요.")
        return desired

    def prepare(self, adapter, hwp, params):
        _, base, _, selection = adapter._context(hwp)
        current = page_setup_state(hwp)
        desired = self.desired_state(hwp, params, current)
        noop = all(
            abs(desired[key] - current[key]) <= MARGIN_TOLERANCE
            if key != "landscape"
            else desired[key] == current[key]
            for key in desired
        )
        labels = []
        if params.get("margin_mm") is not None:
            labels.append(f"여백 {normalize_margin_mm(params['margin_mm'])}mm")
        if params.get("orientation") is not None:
            labels.append(
                orientation_label(normalize_orientation(params["orientation"]))
            )
        label = " · ".join(labels)
        snapshot = {
            **base,
            "operation": self.name,
            "target": "현재 구역",
            "page_setup": current,
            "desired": desired,
        }
        return PreparedAction(
            app=self.app,
            operation=self.name,
            document_id=base["document_id"],
            workbook_name=base["document_name"],
            sheet="현재 문서",
            target="현재 구역",
            params={
                # execute re-prepares from these, so the original request has
                # to survive here: `desired` alone cannot be re-derived without
                # knowing what was asked for.
                "margin_mm": params.get("margin_mm"),
                "margin_sides": list(params.get("margin_sides") or ()),
                "orientation": params.get("orientation"),
                "desired": desired,
                "page_setup_label": label,
            },
            current_state={
                "has_selection": selection["has_selection"],
                "format": current,
                "document_digest": base["text_digest"],
            },
            estimated_changes=0 if noop else 1,
            destructive=False,
            reversible=True,
            verification_method="read_page_setup",
            context_fingerprint=adapter._state_fingerprint(snapshot),
            prepared_at=adapter._created_at(),
            noop=noop,
            metadata={"window_handle": base["window_handle"]},
        )

    def run(self, adapter, hwp, current):
        before = current.current_state["format"]
        if current.noop:
            return adapter._result(current, before, before, False)
        desired = current.params["desired"]
        try:
            section, definition = _page_definition(hwp)
            for side, field in MARGIN_FIELDS.items():
                setattr(definition, field, int(desired[side]))
            definition.Landscape = int(desired["landscape"])
            hwp.HAction.Execute("PageSetup", section.HSet)
            after = page_setup_state(hwp)
            for key, value in desired.items():
                tolerance = 0 if key == "landscape" else MARGIN_TOLERANCE
                if abs(int(after[key]) - int(value)) > tolerance:
                    raise AppActionVerificationError(
                        "한글 쪽 설정 적용 결과가 요청과 다릅니다."
                    )
        except Exception as error:
            try:
                adapter._undo(hwp)
            except Exception:
                pass
            if isinstance(error, AppActionError):
                raise
            raise AppActionVerificationError(
                "한글 쪽 설정 적용 또는 검증에 실패했습니다."
            ) from error
        return adapter._result(current, before, after, True)


__all__ = [
    "MARGIN_FIELDS",
    "MARGIN_TOLERANCE",
    "SetPageSetupOperation",
    "page_setup_state",
]
