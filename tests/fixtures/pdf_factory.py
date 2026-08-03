"""Generate owned PDF fixtures without reading user documents."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from pypdf import PdfReader, PdfWriter


def _write_pdf_objects(path: Path, objects: list[bytes]) -> Path:
    data = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for number, payload in enumerate(objects, start=1):
        offsets.append(len(data))
        data.extend(f"{number} 0 obj\n".encode("ascii"))
        data.extend(payload)
        if not payload.endswith(b"\n"):
            data.extend(b"\n")
        data.extend(b"endobj\n")
    xref_offset = len(data)
    data.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    data.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        data.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    data.extend(
        b"trailer\n<< /Size "
        + str(len(objects) + 1).encode("ascii")
        + b" /Root 1 0 R >>\nstartxref\n"
        + str(xref_offset).encode("ascii")
        + b"\n%%EOF\n"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _escape_pdf_text(value: str) -> str:
    text = str(value or "")
    if any(ord(character) > 127 for character in text):
        raise ValueError("The minimal owned PDF fixture accepts ASCII text only.")
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _text_stream(lines: Iterable[str]) -> bytes:
    commands = ["BT", "/F1 12 Tf", "72 720 Td"]
    for index, line in enumerate(lines):
        if index:
            commands.append("0 -18 Td")
        commands.append(f"({_escape_pdf_text(line)}) Tj")
    commands.append("ET")
    return " ".join(commands).encode("ascii")


def write_text_pdf(
    path: str | Path,
    pages: Iterable[Iterable[str] | str],
    *,
    rotations: Iterable[int] | None = None,
) -> Path:
    """Write a small, valid text PDF with optional page rotations."""
    destination = Path(path)
    normalized_pages = [
        [item] if isinstance(item, str) else [str(line) for line in item] for item in pages
    ]
    if not normalized_pages:
        raise ValueError("At least one owned PDF page is required.")
    normalized_rotations = list(rotations or [0] * len(normalized_pages))
    if len(normalized_rotations) != len(normalized_pages):
        raise ValueError("Rotation count must match the owned PDF page count.")
    if any(rotation not in {0, 90, 180, 270} for rotation in normalized_rotations):
        raise ValueError("Owned PDF rotations must be 0, 90, 180, or 270 degrees.")

    font_id = 3 + (2 * len(normalized_pages))
    page_ids = [3 + (2 * index) for index in range(len(normalized_pages))]
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        (
            b"<< /Type /Pages /Kids ["
            + b" ".join(f"{page_id} 0 R".encode("ascii") for page_id in page_ids)
            + b"] /Count "
            + str(len(page_ids)).encode("ascii")
            + b" >>"
        ),
    ]
    for page_id, lines, rotation in zip(
        page_ids, normalized_pages, normalized_rotations, strict=True
    ):
        content_id = page_id + 1
        rotation_entry = f" /Rotate {rotation}".encode("ascii") if rotation else b""
        objects.append(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792]"
            + rotation_entry
            + b" /Resources << /Font << /F1 "
            + str(font_id).encode("ascii")
            + b" 0 R >> >> /Contents "
            + str(content_id).encode("ascii")
            + b" 0 R >>"
        )
        stream = _text_stream(lines)
        objects.append(
            b"<< /Length "
            + str(len(stream)).encode("ascii")
            + b" >>\nstream\n"
            + stream
            + b"\nendstream"
        )
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    return _write_pdf_objects(destination, objects)


def write_scanned_pdf(path: str | Path) -> Path:
    """Write an image-only PDF that deliberately has no text layer."""
    image = b"\x7f"
    content = b"q 200 0 0 100 72 600 cm /Im0 Do Q"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /XObject << /Im0 5 0 R >> >> /Contents 4 0 R >>"
        ),
        b"<< /Length "
        + str(len(content)).encode("ascii")
        + b" >>\nstream\n"
        + content
        + b"\nendstream",
        (
            b"<< /Type /XObject /Subtype /Image /Width 1 /Height 1 "
            b"/ColorSpace /DeviceGray /BitsPerComponent 8 /Length 1 >>\nstream\n"
            + image
            + b"\nendstream"
        ),
    ]
    return _write_pdf_objects(Path(path), objects)


def write_encrypted_pdf(
    path: str | Path,
    *,
    password: str = "jarvis-test-password",
) -> Path:
    """Write an encrypted owned PDF and remove its temporary plain source."""
    destination = Path(path)
    plain = destination.with_name(f".{destination.stem}.plain.pdf")
    write_text_pdf(plain, ["Encrypted Jarvis Fixture"])
    try:
        reader = PdfReader(plain)
        writer = PdfWriter()
        for page in reader.pages:
            writer.add_page(page)
        writer.encrypt(user_password=password)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("wb") as output:
            writer.write(output)
    finally:
        plain.unlink(missing_ok=True)
    return destination


def write_corrupt_pdf(path: str | Path) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(b"%PDF-not-a-valid-owned-fixture")
    return destination


@dataclass(frozen=True)
class OwnedPdfFixtures:
    text: Path
    scanned: Path
    encrypted: Path
    corrupt: Path
    rotated: Path
    table: Path

    def as_dict(self) -> dict[str, Path]:
        return {
            "text": self.text,
            "scanned": self.scanned,
            "encrypted": self.encrypted,
            "corrupt": self.corrupt,
            "rotated": self.rotated,
            "table": self.table,
        }


def create_owned_pdf_fixtures(root: str | Path) -> OwnedPdfFixtures:
    """Create every PDF-0 fixture beneath one caller-owned directory."""
    directory = Path(root)
    directory.mkdir(parents=True, exist_ok=True)
    return OwnedPdfFixtures(
        text=write_text_pdf(
            directory / "text.pdf",
            [["Jarvis PDF Page One"], ["Jarvis PDF Page Two"]],
        ),
        scanned=write_scanned_pdf(directory / "scanned.pdf"),
        encrypted=write_encrypted_pdf(directory / "encrypted.pdf"),
        corrupt=write_corrupt_pdf(directory / "corrupt.pdf"),
        rotated=write_text_pdf(
            directory / "rotated.pdf",
            ["Rotated Jarvis Page"],
            rotations=[90],
        ),
        table=write_text_pdf(
            directory / "table.pdf",
            [["Item Amount", "Alpha 10", "Beta 20"]],
        ),
    )
