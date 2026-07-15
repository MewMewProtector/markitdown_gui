"""
Smoke tests for the smart PDF converter.
Run with: `python -m tests.test_pdf_converter`
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.core import pdf_converter  # noqa: E402


@unittest.skipUnless(pdf_converter.is_available(), "pymupdf not installed")
class TestPdfConverter(unittest.TestCase):
    def test_extract_returns_string(self):
        # Use a non-existent file — we just want to ensure the function
        # raises FileNotFoundError, not ImportError, when given an invalid path.
        with self.assertRaises(FileNotFoundError):
            pdf_converter.extract_markdown("nonexistent_path_zzz.pdf")

    def test_whitespace_normalization(self):
        # Camel-case boundary.
        self.assertEqual(pdf_converter._normalize_whitespace("multiObject"), "multi Object")
        # Letter/digit boundary.
        self.assertEqual(pdf_converter._normalize_whitespace("MOT17"), "MOT 17")
        # Punctuation sticky.
        self.assertEqual(pdf_converter._normalize_whitespace("words.Next"), "words. Next")
        # Already spaced.
        self.assertEqual(pdf_converter._normalize_whitespace("hello world"), "hello world")

    def test_abstract_postprocess(self):
        md = "Foo bar\nAbstract—This paper proposes.\nBaz\nIndex Terms—MOT, Tracking."
        out = pdf_converter._fix_abstract_keywords(md)
        self.assertIn("## Abstract", out)
        self.assertIn("## Index Terms", out)
        self.assertIn("This paper proposes.", out)
        self.assertIn("MOT, Tracking.", out)
        # Original Abstract— prefix should not be left in the body.
        self.assertNotIn("Abstract—This", out)

    def test_table_renders_as_markdown_table(self):
        """
        Build a synthetic table object that pymupdf's `find_tables` would
        return and verify the rendering function turns it into a clean
        markdown table (instead of dumping the four cells as one garbled
        paragraph, which was the StrongSORT bug).
        """
        from app.core.pdf_converter import _render_tables

        # Fake a page whose find_tables() yields a 2x2 grid.
        fake_page = mock.MagicMock()
        fake_table = mock.MagicMock()
        fake_table.extract.return_value = [
            ["Method", "HOTA"],
            ["SORT",   "36.1"],
            ["ByteTrack", "60.9"],
        ]
        fake_page.find_tables.return_value.tables = [fake_table]
        out = _render_tables(fake_page, [(0, 0, 400, 300)])
        self.assertIn("### Таблица 1", out)
        self.assertIn("| Method | HOTA |", out)
        self.assertIn("|---|", out)
        self.assertIn("| SORT | 36.1 |", out)
        self.assertIn("| ByteTrack | 60.9 |", out)

    def test_table_pipe_escaped(self):
        """Pipes inside cell text must be escaped in the rendered table."""
        from app.core.pdf_converter import _render_tables
        fake_page = mock.MagicMock()
        fake_table = mock.MagicMock()
        fake_table.extract.return_value = [
            ["Header|with|pipes", "B"],
            ["row | cell", "C"],
        ]
        fake_page.find_tables.return_value.tables = [fake_table]
        out = _render_tables(fake_page, [(0, 0, 400, 300)])
        self.assertIn("Header\\|with\\|pipes", out)
        self.assertIn("row \\| cell", out)

    def test_table_bbox_inside_detection(self):
        """A block fully inside a table bbox must be skipped from text."""
        from app.core.pdf_converter import _bbox_inside_any
        containers = [(100.0, 100.0, 300.0, 300.0)]
        # Fully inside.
        self.assertTrue(
            _bbox_inside_any((150, 150, 250, 250), containers)
        )
        # Partially overlapping — NOT inside.
        self.assertFalse(
            _bbox_inside_any((50, 150, 150, 250), containers)
        )
        # Outside entirely.
        self.assertFalse(
            _bbox_inside_any((0, 0, 50, 50), containers)
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)