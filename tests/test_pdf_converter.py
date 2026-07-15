"""
Smoke tests for the smart PDF converter.
Run with: `python -m tests.test_pdf_converter`
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main(verbosity=2)