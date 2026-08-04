"""Operation-focused services for verified Office undo flows."""

from __future__ import annotations

from engine.app_actions.base import (
    AppActionBlocked,
    AppActionContextChanged,
    AppActionError,
    AppActionVerificationError,
    PreparedAction,
)
from engine.app_actions.office_edit_helpers import exact_office_document
from engine.app_actions.operations.hwp.insert_table import table_control_count


class HwpUndoService:
    """Validate, execute, and verify one HWP native undo operation."""

    SUPPORTED = frozenset({
        "insert_text",
        "delete_text",
        "insert_table",
        "set_text_format",
        "set_paragraph_format",
        "set_line_spacing",
        "find_replace",
    })

    def __init__(self, adapter, paragraph_alignments):
        self.adapter = adapter
        self.paragraph_alignments = paragraph_alignments

    def execute(self, prepared, record=None):
        prepared = self._validated_prepared(prepared)
        observations = dict((record or {}).get("after_observations") or {})
        expected_after = dict(observations.get("after") or {})
        with self.adapter._hwp() as hwp:
            _, current_base, _, _ = self.adapter._context(hwp)
            self._validate_current_state(hwp, prepared, current_base, expected_after)
            restored = self._undo_and_verify(hwp, prepared)
        return {
            "success": True,
            "verified": True,
            "status": "success",
            "app": "hwp",
            "operation": "undo_last_edit",
            "document_id": prepared.document_id,
            "workbook_name": prepared.workbook_name,
            "sheet": prepared.sheet,
            "target": prepared.target,
            "changed": True,
            "verification_method": "native_undo_and_snapshot_readback",
            "before": expected_after,
            "after": restored,
        }

    def _validated_prepared(self, prepared):
        if not isinstance(prepared, PreparedAction):
            prepared = PreparedAction.from_dict(prepared or {})
        if prepared.app != "hwp" or not prepared.reversible:
            raise AppActionBlocked("복원할 수 있는 한글 편집 작업이 아닙니다.")
        if prepared.operation not in self.SUPPORTED:
            raise AppActionBlocked("이 한글 작업은 자동 복원을 지원하지 않습니다.")
        return prepared

    def _validate_current_state(self, hwp, prepared, current_base, expected_after):
        if str(current_base["document_id"]).casefold() != str(
            prepared.document_id
        ).casefold():
            raise AppActionContextChanged(
                "편집했던 한글 문서가 현재 활성 문서가 아니어서 복원하지 않았습니다."
            )
        if prepared.operation in {"insert_text", "delete_text", "find_replace"}:
            expected_digest = str(
                expected_after.get("document_digest")
                or expected_after.get("text_digest")
                or ""
            ).upper()
            if not expected_digest or current_base["text_digest"] != expected_digest:
                raise AppActionContextChanged(
                    "직전 편집 뒤 문서 내용이 달라져 안전하게 복원하지 않았습니다."
                )
            return
        if prepared.operation == "set_text_format":
            current_format = self.adapter._char_state(hwp)
            desired = dict(prepared.params.get("desired") or {})
            if not all(current_format.get(key) == value for key, value in desired.items()):
                raise AppActionContextChanged(
                    "직전 편집 뒤 선택 영역 서식이 달라져 복원하지 않았습니다."
                )
            return
        current_format = self.adapter._paragraph_state(hwp)
        if prepared.operation == "insert_table":
            expected = int(prepared.current_state.get("table_count", -1)) + 1
            if table_control_count(hwp) != expected:
                raise AppActionContextChanged(
                    "직전에 넣은 표가 그대로 있지 않아 복원하지 않았습니다."
                )
            return
        if prepared.operation == "set_line_spacing":
            if current_format.get("line_spacing") != int(
                prepared.params.get("line_spacing", -1)
            ):
                raise AppActionContextChanged(
                    "직전 편집 뒤 줄간격이 달라져 복원하지 않았습니다."
                )
            return
        alignment = str(prepared.params.get("alignment") or "")
        expected_alignment = self.paragraph_alignments.get(
            alignment, (None, None)
        )[1]
        if current_format.get("alignment") != expected_alignment:
            raise AppActionContextChanged(
                "직전 편집 뒤 문단 정렬이 달라져 복원하지 않았습니다."
            )

    def _undo_and_verify(self, hwp, prepared):
        try:
            self.adapter._undo(hwp)
            _, restored_base, _, _ = self.adapter._context(hwp)
            restored, verified = self._restored_snapshot(
                hwp, prepared, restored_base
            )
            if not verified:
                raise AppActionVerificationError(
                    "한글 실행 취소 뒤 원래 상태가 복원되었는지 확인하지 못했습니다."
                )
            return restored
        except AppActionError:
            raise
        except Exception as error:
            raise AppActionVerificationError(
                "한글 실행 취소 또는 복원 검증에 실패했습니다."
            ) from error

    def _restored_snapshot(self, hwp, prepared, restored_base):
        if prepared.operation == "insert_table":
            expected = int(prepared.current_state.get("table_count", -1))
            restored = {"table_count": table_control_count(hwp)}
            return restored, restored["table_count"] == expected
        if prepared.operation in {"insert_text", "delete_text", "find_replace"}:
            expected = str(prepared.current_state.get("document_digest") or "").upper()
            restored = {"document_digest": restored_base["text_digest"]}
            return restored, bool(expected) and restored_base["text_digest"] == expected
        if prepared.operation == "set_text_format":
            restored = self.adapter._char_state(hwp)
        else:
            restored = self.adapter._paragraph_state(hwp)
        original = dict(prepared.current_state.get("format") or {})
        return restored, all(
            restored.get(key) == value for key, value in original.items()
        )


class PowerPointUndoService:
    """Dispatch PowerPoint undo verification by edit operation."""

    def __init__(self, adapter, selection_shapes):
        self.adapter = adapter
        self.selection_shapes = selection_shapes

    def execute(self, prepared):
        if isinstance(prepared, dict):
            prepared = PreparedAction.from_dict(prepared)
        if prepared.app != "powerpoint" or not prepared.reversible:
            raise AppActionBlocked("되돌릴 수 있는 PowerPoint 작업이 아닙니다.")
        if prepared.operation not in self.adapter.supported_operations:
            raise AppActionBlocked("직전 PowerPoint 작업을 복원할 수 없습니다.")
        path = prepared.params.get("document_path")
        with exact_office_document(
            "powerpoint",
            path,
            application_getter=self.adapter._application_getter,
            require_visible=self.adapter._require_visible,
            com_runtime=self.adapter._com_runtime,
        ) as (application, presentation):
            slide, shape, state = self.adapter._context(application, presentation)
            self._validate_target(prepared, state)
            self._restore_operation(slide, shape, state, prepared)
        return {
            "success": True,
            "verified": True,
            "changed": True,
            "operation": "undo_last_edit",
            "restored_operation": prepared.operation,
            "document_id": prepared.document_id,
            "target": prepared.target,
        }

    @staticmethod
    def _validate_target(prepared, state):
        if (
            int(state["slide_id"]) != int(prepared.current_state["slide_id"])
            or int(state["shape_id"]) != int(prepared.current_state["shape_id"])
        ):
            raise AppActionContextChanged(
                "PowerPoint 직전 슬라이드 또는 Shape가 달라져 복원하지 않았습니다."
            )

    def _restore_operation(self, slide, shape, state, prepared):
        handlers = {
            "replace_shape_text": self._restore_text,
            "set_text_format": self._restore_font,
            "set_text_alignment": self._restore_alignment,
            "move_shape": self._restore_position,
            "resize_shape": self._restore_size,
        }
        handler = handlers.get(prepared.operation, self._restore_style)
        handler(slide, shape, state, prepared)

    def _restore_text(self, _slide, shape, _state, prepared):
        target = self.adapter._range_for_prepared(shape, prepared)
        if str(target.Text or "") != str(prepared.params["text"]):
            raise AppActionContextChanged(
                "PowerPoint 직전 텍스트가 달라져 복원하지 않았습니다."
            )
        original = str(prepared.params["original_text"])
        target.Text = original
        full = shape.TextFrame.TextRange
        restored = (
            full
            if prepared.params.get("selection_type") == self.selection_shapes
            else full.Characters(prepared.params["text_start"], len(original))
        )
        if str(restored.Text or "") != original:
            raise AppActionVerificationError(
                "PowerPoint 원본 텍스트 복원을 확인하지 못했습니다."
            )

    def _restore_font(self, _slide, shape, _state, prepared):
        target = self.adapter._range_for_prepared(shape, prepared)
        desired = dict(prepared.params["desired"])
        current = self.adapter._style_state(target)
        if any(current.get(key) != value for key, value in desired.items()):
            raise AppActionContextChanged(
                "PowerPoint 글자 서식이 직전 편집 결과와 다릅니다."
            )
        original = dict(prepared.params["original"])
        self.adapter._apply_font(target, original)
        restored = self.adapter._style_state(target)
        if any(restored.get(key) != value for key, value in original.items()):
            raise AppActionVerificationError(
                "PowerPoint 글자 서식 복원을 확인하지 못했습니다."
            )

    def _restore_alignment(self, _slide, shape, _state, prepared):
        target = self.adapter._range_for_prepared(shape, prepared)
        if int(target.ParagraphFormat.Alignment) != int(prepared.params["alignment"]):
            raise AppActionContextChanged(
                "PowerPoint 텍스트 정렬이 직전 편집 결과와 다릅니다."
            )
        original = int(prepared.params["original_alignment"])
        target.ParagraphFormat.Alignment = original
        if int(target.ParagraphFormat.Alignment) != original:
            raise AppActionVerificationError(
                "PowerPoint 텍스트 정렬 복원을 확인하지 못했습니다."
            )

    def _restore_position(self, slide, _shape, state, prepared):
        shape_id = state["shape_id"]
        if not self.adapter._position_matches(
            self.adapter._geometry_state(slide, shape_id),
            prepared.params["left"],
            prepared.params["top"],
        ):
            raise AppActionContextChanged(
                "PowerPoint Shape 위치가 직전 편집 결과와 다릅니다."
            )
        self.adapter._set_shape_position(
            slide,
            shape_id,
            prepared.params["original_left"],
            prepared.params["original_top"],
        )
        if not self.adapter._position_matches(
            self.adapter._geometry_state(slide, shape_id),
            prepared.params["original_left"],
            prepared.params["original_top"],
        ):
            raise AppActionVerificationError(
                "PowerPoint Shape 원래 위치 복원을 확인하지 못했습니다."
            )

    def _restore_size(self, slide, _shape, state, prepared):
        shape_id = state["shape_id"]
        lock = int(prepared.params["lock_aspect_ratio"])
        if not self.adapter._size_matches(
            self.adapter._geometry_state(slide, shape_id),
            prepared.params["width"],
            prepared.params["height"],
            lock,
        ):
            raise AppActionContextChanged(
                "PowerPoint Shape 크기가 직전 편집 결과와 다릅니다."
            )
        self.adapter._set_shape_size(
            slide,
            shape_id,
            prepared.params["original_width"],
            prepared.params["original_height"],
            lock,
        )
        if not self.adapter._size_matches(
            self.adapter._geometry_state(slide, shape_id),
            prepared.params["original_width"],
            prepared.params["original_height"],
            lock,
        ):
            raise AppActionVerificationError(
                "PowerPoint Shape 원래 크기 복원을 확인하지 못했습니다."
            )

    def _restore_style(self, _slide, shape, _state, prepared):
        target = self.adapter._range_for_prepared(shape, prepared)
        desired = dict(prepared.params["desired"])
        if self.adapter._style_state(target) != desired:
            raise AppActionContextChanged(
                "PowerPoint 스타일이 직전 편집 결과와 다릅니다."
            )
        self.adapter._apply_style(target, prepared.params["original"])
        if self.adapter._style_state(target) != prepared.params["original"]:
            raise AppActionVerificationError(
                "PowerPoint 원본 스타일 복원을 확인하지 못했습니다."
            )
