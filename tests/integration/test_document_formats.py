"""Integration tests for document formats supported by Jarvis."""

import os
import tempfile
import unittest
import zipfile

from engine.document_reader import extract_text


def _write_minimal_pdf(path, text):
    content = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("ascii")
    objects = [
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n",
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n",
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>\nendobj\n",
        b"4 0 obj\n<< /Length " + str(len(content)).encode("ascii") + b" >>\nstream\n"
        + content + b"\nendstream\nendobj\n",
        b"5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n",
    ]
    data = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for obj in objects:
        offsets.append(len(data))
        data.extend(obj)
    xref_offset = len(data)
    data.extend(b"xref\n0 6\n0000000000 65535 f \n")
    for offset in offsets[1:]:
        data.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    data.extend(
        b"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n"
        + str(xref_offset).encode("ascii")
        + b"\n%%EOF\n"
    )
    with open(path, "wb") as output:
        output.write(data)


class DocumentFormatIntegrationTests(unittest.TestCase):
    def test_extracts_text_from_actual_pdf_structure(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-doc-test-") as temp_dir:
            path = os.path.join(temp_dir, "sample.pdf")
            _write_minimal_pdf(path, "Jarvis PDF Test")
            self.assertIn("Jarvis PDF Test", extract_text(path))

    def test_extracts_korean_text_from_actual_hwpx_structure(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-doc-test-") as temp_dir:
            path = os.path.join(temp_dir, "sample.hwpx")
            xml = (
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<hp:section xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph">'
                "<hp:p><hp:run><hp:t>자비스 HWPX 테스트</hp:t></hp:run></hp:p>"
                "</hp:section>"
            )
            with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("Contents/section0.xml", xml.encode("utf-8"))
            self.assertIn("자비스 HWPX 테스트", extract_text(path))

    def test_invalid_hwp_returns_a_readable_error_instead_of_raising(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-doc-test-") as temp_dir:
            path = os.path.join(temp_dir, "invalid.hwp")
            with open(path, "wb") as output:
                output.write(b"not-an-ole-document")
            result = extract_text(path)
            self.assertIsInstance(result, str)
            self.assertTrue(result.strip())
            self.assertIn("HWP", result)


if __name__ == "__main__":
    unittest.main()
