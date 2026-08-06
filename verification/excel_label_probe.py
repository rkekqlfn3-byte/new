"""Keep the Korean names Jarvis answers to the ones Excel actually shows.

The names in ``engine/app_actions/operations/excel/ribbon.py`` were written
by hand once, and they were wrong in ways nobody would notice from the code:
기울임 where Excel says 기울임꼴, 자동 줄바꿈 where it says 자동 줄 바꿈,
바깥 테두리 where it says 바깥쪽 테두리.  A reader repeating what the ribbon
shows them would have matched none of those.

Excel will say them itself.  ``CommandBars.GetLabelMso(idMso)`` returns the
localised label and ``GetScreentipMso`` the tooltip, so this asks Excel for
every command in the table and fails when the table no longer agrees.  A
different Office version or language is exactly the drift worth catching.

Excel's captions carry zero-width characters — 굵게 comes back as 굵게​
​ — so both sides are cleaned before comparing.  That is the same
invisible character that let 제한된 액세스 slip past a deny-list.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from engine.app_actions.operations.excel.ribbon import EXCEL_COMMANDS

REPORT_PATH = Path(__file__).with_name("excel_label_report.json")

INVISIBLE = re.compile("[​-‏﻿­]")


def clean(value) -> str:
    return re.sub(r"\s+", " ", INVISIBLE.sub("", str(value or ""))).strip()


def run() -> dict:
    import win32com.client as client

    from engine.app_actions.com_lifecycle import com_apartment

    records: list[dict] = []
    with com_apartment(None):
        application = None
        try:
            application = client.DispatchEx("Excel.Application")
            application.Visible = False
            application.DisplayAlerts = False
            workbook = application.Workbooks.Add()
            bars = application.CommandBars
            for key, entry in EXCEL_COMMANDS.items():
                record = {"key": key, "command": entry.command}
                try:
                    label = clean(bars.GetLabelMso(entry.command))
                except Exception as error:
                    record["outcome"] = f"unavailable:{type(error).__name__}"
                    records.append(record)
                    continue
                record["excel_label"] = label
                record["table_label"] = entry.label
                record["ok"] = label == entry.label and label in entry.words
                record["outcome"] = "matches" if record["ok"] else "drifted"
                records.append(record)
            workbook.Close(SaveChanges=False)
        finally:
            if application is not None:
                try:
                    application.Quit()
                except Exception:
                    pass

    drifted = [record for record in records if not record.get("ok")]
    return {
        "schema_version": 1,
        "total": len(records),
        "matches": len(records) - len(drifted),
        "success": not drifted,
        "records": records,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", default=str(REPORT_PATH))
    args = parser.parse_args(argv)

    report = run()
    Path(args.report).write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    for record in report["records"]:
        mark = "O" if record.get("ok") else "X"
        print(
            f"  {mark} {record['command']:16s} "
            f"표={record.get('table_label', '')!r} "
            f"엑셀={record.get('excel_label', '')!r}"
        )
    print(f"\n{report['matches']}/{report['total']}")
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
