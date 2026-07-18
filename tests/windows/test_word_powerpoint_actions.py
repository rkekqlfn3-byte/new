import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from engine.app_actions.base import AppActionContextChanged
from engine.app_actions.powerpoint_adapter import PowerPointAdapter
from engine.app_actions.word_adapter import WordAdapter


class Collection:
    def __init__(self, *items):
        self.items = list(items)

    @property
    def Count(self):
        return len(self.items)

    def Item(self, index):
        return self.items[index - 1]


class FakeWordRange:
    def __init__(self, document, start, end):
        self.document = document
        self.Start = int(start)
        self.End = int(end)
        self.Font = document.font
        self.ParagraphFormat = document.paragraph

    @property
    def Text(self):
        return self.document.text[self.Start:self.End]

    @Text.setter
    def Text(self, value):
        self.document.text = (
            self.document.text[:self.Start]
            + str(value)
            + self.document.text[self.End:]
        )
        self.End = self.Start + len(str(value))

    def Select(self):
        application = getattr(self.document, "application", None)
        if application is not None:
            application.Selection = FakeWordSelection(
                self.document,
                self.Start,
                self.End,
            )


class FakeWordSelection(FakeWordRange):
    def __init__(self, document, start, end):
        super().__init__(document, start, end)
        self.Document = document
        self.Style = SimpleNamespace(NameLocal="본문")
        self.Cells = Collection()

    @staticmethod
    def Information(code):
        return False


class FakeWordDocument:
    ProtectionType = -1
    ReadOnly = False
    Saved = True

    def __init__(self, path, text):
        self.FullName = str(path)
        self.Name = Path(path).name
        self.text = text
        self.font = SimpleNamespace(Bold=0, Size=10.0)
        self.paragraph = SimpleNamespace(Alignment=0)

    @property
    def Content(self):
        return FakeWordRange(self, 0, len(self.text))

    def Range(self, start, end):
        return FakeWordRange(self, start, end)

    def Save(self):
        self.Saved = True


class FakeWordApplication:
    Visible = True

    def __init__(self, document, start, end):
        self.ActiveDocument = document
        self.Documents = Collection(document)
        document.application = self
        self.Selection = FakeWordSelection(document, start, end)


class Color:
    def __init__(self, rgb):
        self.RGB = rgb


class PptFont:
    def __init__(self, size=20.0, bold=0, name="Arial", color=0):
        self.Size = size
        self.Bold = bold
        self.Name = name
        self.Color = Color(color)


class FakePptTextRange:
    def __init__(self, shape, start=1, length=None):
        self.shape = shape
        self.Start = int(start)
        self._length = length

    @property
    def Length(self):
        return len(self.Text) if self._length is None else int(self._length)

    @property
    def Text(self):
        start = self.Start - 1
        end = len(self.shape.text) if self._length is None else start + self._length
        return self.shape.text[start:end]

    @Text.setter
    def Text(self, value):
        start = self.Start - 1
        end = len(self.shape.text) if self._length is None else start + self._length
        self.shape.text = self.shape.text[:start] + str(value) + self.shape.text[end:]
        self._length = len(str(value))

    @property
    def Font(self):
        return self.shape.font

    @property
    def ParagraphFormat(self):
        return self.shape.paragraph

    def Characters(self, start, length):
        return FakePptTextRange(self.shape, start, length)


class FakeTextFrame:
    HasText = True

    def __init__(self, shape):
        self.shape = shape

    @property
    def TextRange(self):
        return FakePptTextRange(self.shape)


class FakeShape:
    HasTextFrame = -1
    Type = 14

    def __init__(
        self,
        shape_id,
        name,
        text,
        *,
        placeholder_type=1,
        font_size=20.0,
        bold=0,
        color=0,
    ):
        self.Id = shape_id
        self.Name = name
        self.text = text
        self.font = PptFont(font_size, bold, color=color)
        self.paragraph = SimpleNamespace(Alignment=1)
        self.PlaceholderFormat = SimpleNamespace(Type=placeholder_type)
        self.Left = 50.0
        self.Top = 50.0
        self.Width = 300.0
        self.Height = 50.0
        self.Rotation = 0.0
        self.LockAspectRatio = -1

    @property
    def TextFrame(self):
        return FakeTextFrame(self)

    def Select(self):
        return True


class FakeSlide:
    def __init__(self, index, slide_id, *shapes):
        self.SlideIndex = index
        self.SlideID = slide_id
        self.Shapes = Collection(*shapes)


class FakePresentation:
    ReadOnly = False
    Saved = True

    def __init__(self, path, slides):
        self.FullName = str(path)
        self.Name = Path(path).name
        self.Slides = Collection(*slides)
        self.PageSetup = SimpleNamespace(SlideWidth=960.0, SlideHeight=540.0)


class FakePptApplication:
    Visible = True

    def __init__(self, presentation, slide, shape):
        self.ActivePresentation = presentation
        self.Presentations = Collection(presentation)
        selection = SimpleNamespace(
            Type=2,
            ShapeRange=Collection(shape),
        )
        self.ActiveWindow = SimpleNamespace(
            View=SimpleNamespace(Slide=slide),
            Selection=selection,
        )


class WordAdapterTests(unittest.TestCase):
    def test_replace_format_align_and_save_are_read_back_verified(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "stage6.docx"
            path.write_bytes(b"fixture")
            document = FakeWordDocument(path, "앞 현재 문장 뒤")
            application = FakeWordApplication(document, 2, 7)
            adapter = WordAdapter(application_getter=lambda: application)

            replacement = adapter.prepare(
                "replace_selection",
                {"document_path": str(path), "text": "새 문장"},
            )
            result = adapter.execute(replacement)
            self.assertTrue(result["verified"])
            self.assertEqual("앞 새 문장 뒤", document.text)
            self.assertEqual(
                {"bold": 0, "font_size": 10.0, "alignment": 0},
                result["format"],
            )

            application.Selection = FakeWordSelection(document, 2, 7)
            formatted = adapter.prepare(
                "set_text_format",
                {"document_path": str(path), "bold": True, "font_size_delta": 2},
            )
            self.assertTrue(adapter.execute(formatted)["verified"])
            self.assertEqual(-1, document.font.Bold)
            self.assertEqual(12.0, document.font.Size)

            aligned = adapter.prepare(
                "set_paragraph_format",
                {"document_path": str(path), "alignment": "가운데"},
            )
            self.assertTrue(adapter.execute(aligned)["verified"])
            self.assertEqual(1, document.paragraph.Alignment)

            document.Saved = False
            saved = adapter.prepare("save_document", {"document_path": str(path)})
            self.assertFalse(saved.reversible)
            self.assertTrue(adapter.execute(saved)["verified"])

    def test_changed_word_range_is_blocked_before_write(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "stage6.docx"
            path.write_bytes(b"fixture")
            document = FakeWordDocument(path, "선택 문장")
            application = FakeWordApplication(document, 0, 5)
            adapter = WordAdapter(application_getter=lambda: application)
            prepared = adapter.prepare(
                "replace_selection",
                {"document_path": str(path), "text": "교체"},
            )
            application.Selection = FakeWordSelection(document, 1, 5)
            with self.assertRaises(AppActionContextChanged):
                adapter.execute(prepared)
            self.assertEqual("선택 문장", document.text)

    def test_verified_word_replacement_can_restore_its_snapshot(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "stage7.docx"
            path.write_bytes(b"fixture")
            document = FakeWordDocument(path, "원래 문장")
            application = FakeWordApplication(document, 0, len(document.text))
            adapter = WordAdapter(application_getter=lambda: application)
            prepared = adapter.prepare(
                "replace_selection",
                {"document_path": str(path), "text": "수정 문장"},
            )
            result = adapter.execute(prepared)

            restored = adapter.undo(
                prepared,
                {"after_observations": result},
            )

            self.assertTrue(restored["verified"])
            self.assertEqual("원래 문장", document.text)
            self.assertEqual("원래 문장", application.Selection.Text)


class PowerPointAdapterTests(unittest.TestCase):
    def _fixture(self, path):
        previous = FakeShape(
            3,
            "제목 1",
            "앞 장 제목",
            font_size=32.0,
            bold=-1,
            color=255,
        )
        current = FakeShape(7, "제목 2", "현재 제목", font_size=24.0)
        slides = [FakeSlide(1, 100, previous), FakeSlide(2, 200, current)]
        presentation = FakePresentation(path, slides)
        application = FakePptApplication(presentation, slides[1], current)
        return application, current

    def test_text_format_geometry_and_previous_style_are_verified(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "stage6.pptx"
            path.write_bytes(b"fixture")
            application, shape = self._fixture(path)
            adapter = PowerPointAdapter(application_getter=lambda: application)

            replacement = adapter.prepare(
                "replace_shape_text",
                {"document_path": str(path), "text": "새 제목"},
            )
            self.assertTrue(adapter.execute(replacement)["verified"])
            self.assertEqual("새 제목", shape.text)

            formatted = adapter.prepare(
                "set_text_format",
                {"document_path": str(path), "font_size_delta": 2},
            )
            self.assertTrue(adapter.execute(formatted)["verified"])
            self.assertEqual(26.0, shape.font.Size)

            moved = adapter.prepare(
                "move_shape",
                {"document_path": str(path), "dx": 10, "dy": 0},
            )
            self.assertTrue(adapter.execute(moved)["verified"])
            self.assertEqual(60.0, shape.Left)

            resized = adapter.prepare(
                "resize_shape",
                {"document_path": str(path), "scale": 1.1},
            )
            self.assertTrue(adapter.execute(resized)["verified"])
            self.assertAlmostEqual(330.0, shape.Width)

            matched = adapter.prepare(
                "match_previous_style",
                {"document_path": str(path)},
            )
            self.assertTrue(adapter.execute(matched)["verified"])
            self.assertEqual(32.0, shape.font.Size)
            self.assertEqual(-1, shape.font.Bold)
            self.assertEqual(255, shape.font.Color.RGB)

    def test_changed_shape_context_is_blocked_before_write(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "stage6.pptx"
            path.write_bytes(b"fixture")
            application, shape = self._fixture(path)
            adapter = PowerPointAdapter(application_getter=lambda: application)
            prepared = adapter.prepare(
                "move_shape",
                {"document_path": str(path), "dx": 10, "dy": 0},
            )
            shape.Top += 1
            with self.assertRaises(AppActionContextChanged):
                adapter.execute(prepared)
            self.assertEqual(50.0, shape.Left)

    def test_verified_powerpoint_move_can_restore_its_snapshot(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "stage7.pptx"
            path.write_bytes(b"fixture")
            application, shape = self._fixture(path)
            adapter = PowerPointAdapter(application_getter=lambda: application)
            prepared = adapter.prepare(
                "move_shape",
                {"document_path": str(path), "dx": 25, "dy": 15},
            )
            adapter.execute(prepared)

            restored = adapter.undo(prepared)

            self.assertTrue(restored["verified"])
            self.assertEqual(50.0, shape.Left)
            self.assertEqual(50.0, shape.Top)


if __name__ == "__main__":
    unittest.main()
