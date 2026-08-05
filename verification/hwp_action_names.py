"""Read 한글's own action names instead of guessing them.

The first catalogue run invented its candidates from what the ribbon looks
like, and 95 of 223 came back False.  That number measured the guessing,
not 한글: ``HyperlinkInsert`` does not exist, while ``InsertHyperlink``
does, so the feature was there all along behind a name nobody had asked it
for.

한글 ships the real list.  ``HwpAppModule.dll`` carries a table of
null-terminated action names — every one of eleven names already proven
against a live 한글 is in it — so the candidates can be read out of the
installation rather than imagined.

Nothing here runs 한글.  It only reads a file that is already on disk and
returns strings; deciding which of them are usable is still the catalogue's
job, because a name existing is not the same as it doing something.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

# Where the table sits inside the module. The bounds are deliberately wider
# than the names found so far, so a differently built 한글 still lands
# inside them; anything outside is other tables' strings.
DEFAULT_INSTALL_ROOTS = (
    r"C:\Program Files (x86)\Hnc",
    r"C:\Program Files\Hnc",
)
MODULE_NAME = "HwpAppModule.dll"

# Every action name proven against a live 한글 so far. If a build does not
# contain all of them, the extraction is reading the wrong thing and should
# say so rather than return a plausible-looking list.
PROVEN_NAMES = (
    "CharShapeBold", "CharShapeItalic", "CharShapeUnderline",
    "InsertFootnote", "InsertEndnote", "BreakPage", "BreakColumn",
    "TableCreate", "ParagraphShapeAlignCenter",
    "ParagraphShapeIndentPositive", "MoveDocBegin",
)

_NAME = re.compile(rb"([A-Z][A-Za-z0-9_]{2,50})\x00")

# Names that describe opening a window rather than making an edit. The
# catalogue would classify them as `dialog` anyway, at a second or two each.
_DIALOG_SUFFIXES = ("Dialog", "Dlg", "Setting", "Settings", "Option", "Options")

# Families that must never be run to find out what they do: printing puts
# paper through a real printer, the file and password families write to disk
# or lock the document, macro playback runs whatever is recorded.
_NEVER_PREFIXES = (
    "Print", "File", "Macro", "Password", "Mail", "Send", "Exit", "Quit",
    "Script", "Recover", "Security", "Sign", "Distribute", "Publish",
)

# Moving the caret is not an edit. These filled the first catalogue with
# `changed` verdicts that meant only that the caret had moved.
_NAVIGATION_PREFIXES = ("Move", "Select", "Find", "Goto", "Scroll", "View")


def module_path(roots=DEFAULT_INSTALL_ROOTS) -> Path | None:
    """Locate the installed module, or nothing when 한글 is not present."""
    for root in roots:
        base = Path(root)
        if not base.is_dir():
            continue
        for candidate in base.rglob(MODULE_NAME):
            if candidate.is_file():
                return candidate
    return None


def extract_names(path: Path) -> tuple[str, ...]:
    """Every action name in the module's string table."""
    blob = path.read_bytes()
    found = {match.group(1).decode("ascii") for match in _NAME.finditer(blob)}
    return tuple(sorted(found))


def is_probeable(name: str) -> bool:
    """Whether running this name to see what it does is safe and useful."""
    if name.startswith(_NEVER_PREFIXES) or name.startswith(_NAVIGATION_PREFIXES):
        return False
    if name.endswith(_DIALOG_SUFFIXES):
        return False
    # 한글 lists most actions twice, once bare and once with an A prefix.
    # The bare name is what HAction.Run accepts; every proven name is bare.
    return not (name.startswith("A") and name[1:2].isupper())


def collect(path: Path | None = None) -> dict:
    path = path or module_path()
    if path is None:
        return {"found": False, "reason": "hwp_not_installed"}
    names = extract_names(path)
    missing = [name for name in PROVEN_NAMES if name not in names]
    probeable = tuple(name for name in names if is_probeable(name))
    return {
        "found": True,
        "module": str(path),
        "total": len(names),
        "probeable": len(probeable),
        "missing_proven": missing,
        "trustworthy": not missing,
        "names": probeable,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out")
    parser.add_argument("--limit", type=int, default=40)
    args = parser.parse_args(argv)

    report = collect()
    if not report["found"]:
        print("한글 설치를 찾지 못했습니다.")
        return 1
    print("module    :", report["module"])
    print("total     :", report["total"])
    print("probeable :", report["probeable"])
    print("trustworthy:", report["trustworthy"], report["missing_proven"] or "")
    if args.out:
        Path(args.out).write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    else:
        for name in report["names"][: args.limit]:
            print("   ", name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
