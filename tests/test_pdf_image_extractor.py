"""
Smoke tests for the PDF image extractor.

The tests use pymupdf directly to build a tiny in-memory PDF containing
a single raster image, then verify our extractor pulls it out and that
the temp directory contract holds.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.core.pdf_image_extractor import (  # noqa: E402
    extract_pdf_images,
    is_available,
)


def _build_pdf_with_image(out_path: str) -> None:
    """
    Create a minimal PDF with one embedded image. We use pymupdf itself
    so the test exercises the same library the extractor depends on.
    """
    import fitz  # type: ignore

    doc = fitz.open()
    page = doc.new_page(width=400, height=300)

    # Build a tiny 64x64 RGB pixmap (well above the 24-px cutoff).
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 63, 63), False)
    pix.clear_with(0xFFFF00)  # yellow
    page.insert_image(fitz.Rect(50, 50, 200, 200), pixmap=pix)
    doc.save(out_path)
    doc.close()


def _build_pdf_without_images(out_path: str) -> None:
    import fitz  # type: ignore

    doc = fitz.open()
    doc.new_page(width=400, height=300).insert_text(
        (50, 150), "just text, no images"
    )
    doc.save(out_path)
    doc.close()


@unittest.skipUnless(is_available(), "pymupdf not installed")
class TestPdfImageExtractor(unittest.TestCase):
    def test_extract_returns_list_with_page_info(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pdf_path = os.path.join(tmp, "with_img.pdf")
            out_dir = os.path.join(tmp, "out")
            os.makedirs(out_dir, exist_ok=True)
            _build_pdf_with_image(pdf_path)
            images = extract_pdf_images(pdf_path, out_dir)
            self.assertGreaterEqual(len(images), 1)
            img = images[0]
            self.assertEqual(img.page_number, 1)
            self.assertTrue(os.path.isfile(img.file_path))
            self.assertTrue(img.file_path.startswith(out_dir))
            self.assertGreater(img.width, 0)
            self.assertGreater(img.height, 0)

    def test_pdf_without_images_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pdf_path = os.path.join(tmp, "no_img.pdf")
            out_dir = os.path.join(tmp, "out")
            os.makedirs(out_dir, exist_ok=True)
            _build_pdf_without_images(pdf_path)
            self.assertEqual(extract_pdf_images(pdf_path, out_dir), [])

    def test_does_not_touch_other_dirs(self) -> None:
        """Extraction must only write inside `out_dir`."""
        with tempfile.TemporaryDirectory() as tmp:
            pdf_path = os.path.join(tmp, "src.pdf")
            out_dir = os.path.join(tmp, "out")
            os.makedirs(out_dir, exist_ok=True)
            _build_pdf_with_image(pdf_path)
            before = set(os.listdir(tmp))
            extract_pdf_images(pdf_path, out_dir)
            after = set(os.listdir(tmp))
            self.assertEqual(before, after)

    def test_missing_file_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = os.path.join(tmp, "out")
            os.makedirs(out_dir, exist_ok=True)
            with self.assertRaises(FileNotFoundError):
                extract_pdf_images(os.path.join(tmp, "nope.pdf"), out_dir)


if __name__ == "__main__":
    unittest.main(verbosity=2)
