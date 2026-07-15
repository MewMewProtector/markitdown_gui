"""
Smoke tests for the Office Open XML image extractor.

DOCX/PPTX/XLSX are zip containers — we synthesize tiny archives that
look like the real thing and verify the extractor pulls out only the
files in the right `*/media/` location.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.core.office_image_extractor import (  # noqa: E402
    extract_office_images,
    is_office_ext,
)


def _make_zip(out_path: str, entries: dict[str, bytes]) -> None:
    """Build a zip with the given entries (name -> bytes)."""
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in entries.items():
            z.writestr(name, data)


# A trivial 1x1 PNG (89 bytes) — well above our 256-byte cutoff is not
# met, so we use a larger fake payload. Since the extractor only checks
# the file extension and minimum byte size, the content doesn't matter
# for unit tests.
_FAKE_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 1500
_FAKE_JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 1500 + b"\xff\xd9"
_FAKE_TXT = b"hello" * 200


class TestOfficeImageExtractor(unittest.TestCase):
    def test_is_office_ext(self) -> None:
        self.assertTrue(is_office_ext(".docx"))
        self.assertTrue(is_office_ext(".PPTX"))
        self.assertTrue(is_office_ext(".xlsx"))
        self.assertFalse(is_office_ext(".pdf"))
        self.assertFalse(is_office_ext(".doc"))

    def test_docx_extracts_word_media(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            docx_path = os.path.join(tmp, "test.docx")
            _make_zip(
                docx_path,
                {
                    "[Content_Types].xml": _FAKE_TXT,
                    "word/document.xml": _FAKE_TXT,
                    "word/media/image1.png": _FAKE_PNG,
                    "word/media/photo.jpg": _FAKE_JPG,
                    "word/media/notes.xml": _FAKE_TXT,  # not media
                },
            )
            out_dir = os.path.join(tmp, "out")
            os.makedirs(out_dir, exist_ok=True)
            images = extract_office_images(docx_path, out_dir)
            names = sorted(os.path.basename(i.file_path) for i in images)
            # Both PNG and JPG should be picked up, the .xml file should
            # NOT.
            self.assertEqual(names, ["docx_image1.png", "docx_photo.jpg"])
            # Every extracted file should be inside `out_dir`.
            for img in images:
                self.assertTrue(img.file_path.startswith(out_dir))

    def test_pptx_extracts_ppt_media(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pptx_path = os.path.join(tmp, "deck.pptx")
            _make_zip(
                pptx_path,
                {
                    "ppt/presentation.xml": _FAKE_TXT,
                    "ppt/media/slide1.png": _FAKE_PNG,
                    "ppt/media/slide2.jpeg": _FAKE_JPG,
                    # Outside media — should be ignored.
                    "ppt/theme/theme1.xml": _FAKE_TXT,
                },
            )
            out_dir = os.path.join(tmp, "out")
            os.makedirs(out_dir, exist_ok=True)
            images = extract_office_images(pptx_path, out_dir)
            names = sorted(os.path.basename(i.file_path) for i in images)
            self.assertEqual(names, ["pptx_slide1.png", "pptx_slide2.jpeg"])

    def test_xlsx_extracts_xl_media(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            xlsx_path = os.path.join(tmp, "sheet.xlsx")
            _make_zip(
                xlsx_path,
                {
                    "xl/worksheets/sheet1.xml": _FAKE_TXT,
                    "xl/media/logo.png": _FAKE_PNG,
                    "_rels/.rels": _FAKE_TXT,  # not media
                },
            )
            out_dir = os.path.join(tmp, "out")
            os.makedirs(out_dir, exist_ok=True)
            images = extract_office_images(xlsx_path, out_dir)
            names = sorted(os.path.basename(i.file_path) for i in images)
            self.assertEqual(names, ["xlsx_logo.png"])

    def test_no_media_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            docx_path = os.path.join(tmp, "empty.docx")
            _make_zip(docx_path, {"word/document.xml": _FAKE_TXT})
            out_dir = os.path.join(tmp, "out")
            os.makedirs(out_dir, exist_ok=True)
            self.assertEqual(extract_office_images(docx_path, out_dir), [])

    def test_unknown_ext_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = os.path.join(tmp, "out")
            os.makedirs(out_dir, exist_ok=True)
            self.assertEqual(
                extract_office_images(os.path.join(tmp, "x.pdf"), out_dir),
                [],
            )

    def test_tiny_file_below_threshold_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            docx_path = os.path.join(tmp, "tiny.docx")
            _make_zip(
                docx_path,
                {
                    "word/media/tiny.png": b"abc",  # 3 bytes — below _MIN_BYTES
                },
            )
            out_dir = os.path.join(tmp, "out")
            os.makedirs(out_dir, exist_ok=True)
            self.assertEqual(extract_office_images(docx_path, out_dir), [])

    def test_does_not_touch_other_dirs(self) -> None:
        """
        The extractor must write ONLY inside the caller-provided `out_dir`.
        It must never create files anywhere else (e.g. next to the
        source archive or in CWD).
        """
        with tempfile.TemporaryDirectory() as tmp:
            docx_path = os.path.join(tmp, "src.docx")
            _make_zip(docx_path, {"word/media/img.png": _FAKE_PNG})
            out_dir = os.path.join(tmp, "out")
            os.makedirs(out_dir, exist_ok=True)
            before = set(os.listdir(tmp))
            extract_office_images(docx_path, out_dir)
            after = set(os.listdir(tmp))
            # `src.docx` and `out/` should be the only top-level entries —
            # the extractor must not have created stray files anywhere
            # else in `tmp`.
            self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main(verbosity=2)
