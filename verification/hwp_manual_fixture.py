"""Build the 한글 document used for the manual checklist.

Every check in ``docs/HWP_MANUAL_TEST_2026-08-05.md`` needs something to act
on: paragraphs to space and align, text to select and delete, a table to write
cells into, and enough content that a page break is visible.  Making that by
hand each time is the slow part of manual testing, so it is generated.

The document is created in a 한글 instance this script owns and closes, and is
written to a path the caller chooses — it never touches an existing file.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from engine.app_actions.com_lifecycle import com_apartment
from engine.app_actions.hwp_adapter import create_owned_hwp_application
from engine.workflows.business_workflow import _registered_hwp_security_module

DEFAULT_NAME = "jarvis_manual_test.hwp"

PARAGRAPHS = (
    "JARVIS 한글 편집 시험 문서",
    "",
    "1. 줄간격 시험용 문단입니다. 이 문단에 커서를 두고 줄간격을 바꿔 보세요. "
    "문장이 여러 줄에 걸쳐야 줄간격 변화가 눈에 보이므로 일부러 길게 씁니다. "
    "160%, 1.5배, 넓게, 좁게를 차례로 시험해 보세요.",
    "",
    "2. 정렬 시험용 문단입니다. 좌측 정렬, 우측으로 정렬, 배분 정렬, 중앙 정렬을 "
    "차례로 말해 보세요. 좌측과 우측과 배분은 이번에 새로 인식하게 된 표현입니다.",
    "",
    "3. 글머리표 시험용 문단입니다. 글머리표 넣어줘, 번호 매기기로, "
    "글머리표 없애줘를 차례로 시험해 보세요.",
    "",
    "4. 삭제 시험용 문단입니다. 이 문장 중 일부를 마우스로 선택한 뒤 지워줘라고 "
    "말하고, 실행 취소로 되돌아오는지 확인해 보세요.",
    "",
    "5. 아래 표의 칸에 값을 넣어 보세요. 표 2행 3열에 \"매출\" 넣어줘처럼 "
    "따옴표로 감싸 말합니다. 표 밖에 커서를 두고 쪽 나눔도 시험해 보세요.",
    "",
)

TABLE_ROWS = 3
TABLE_COLUMNS = 4


def _insert_text(hwp, text):
    action = hwp.CreateAction("InsertText")
    parameter_set = action.CreateSet()
    parameter_set.SetItem("Text", text)
    action.Execute(parameter_set)


def build(target: Path) -> dict:
    if target.exists():
        raise SystemExit(f"이미 있는 파일은 덮어쓰지 않습니다: {target.name}")
    target.parent.mkdir(parents=True, exist_ok=True)

    with com_apartment(None):
        lease = None
        try:
            lease = create_owned_hwp_application()
            hwp = lease.application
            module_name = _registered_hwp_security_module()
            if not module_name or not bool(
                hwp.RegisterModule("FilePathCheckDLL", module_name)
            ):
                raise SystemExit(
                    "한글 Automation 파일 보안 모듈이 등록되어 있지 않아 "
                    "시험 문서를 만들지 못했습니다."
                )
            hwp.XHwpWindows.Item(0).Visible = True

            for paragraph in PARAGRAPHS:
                _insert_text(hwp, paragraph + "\r\n")

            creation = hwp.HParameterSet.HTableCreation
            hwp.HAction.GetDefault("TableCreate", creation.HSet)
            creation.Rows = TABLE_ROWS
            creation.Cols = TABLE_COLUMNS
            creation.WidthType = 0
            creation.HeightType = 0
            hwp.HAction.Execute("TableCreate", creation.HSet)

            hwp.HAction.Run("MoveDocEnd")
            _insert_text(hwp, "\r\n6. 여기부터는 표 바깥입니다.\r\n")

            hwp.SaveAs(str(target), "HWP", "")
            saved = target.exists()
        finally:
            if lease is not None:
                try:
                    lease.application.XHwpDocuments.Item(0).Clear(option=1)
                except Exception:
                    pass
                try:
                    lease.cleanup()
                except Exception:
                    pass
    return {
        "path": str(target),
        "saved": bool(saved),
        "size": target.stat().st_size if target.exists() else 0,
        "paragraphs": len([item for item in PARAGRAPHS if item]),
        "table": f"{TABLE_ROWS}x{TABLE_COLUMNS}",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        default=str(Path("outputs") / DEFAULT_NAME),
        help="만들 한글 파일 경로 (기본: outputs/jarvis_manual_test.hwp)",
    )
    args = parser.parse_args(argv)
    report = build(Path(os.path.abspath(args.output)))
    for key, value in report.items():
        print(f"{key}: {value}")
    return 0 if report["saved"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
