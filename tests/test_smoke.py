"""
Smoke tests for pure-Python helpers (no PySide6 / markitdown required).
Run with: `python -m tests.test_smoke` from the project root.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.core import file_filter, naming  # noqa: E402


class TestFileFilter(unittest.TestCase):
    def test_supported_extensions(self):
        self.assertTrue(file_filter.is_supported("report.pdf"))
        self.assertTrue(file_filter.is_supported("PHOTO.PNG"))
        self.assertTrue(file_filter.is_supported("data.xlsx"))
        self.assertFalse(file_filter.is_supported("setup.exe"))
        self.assertTrue(file_filter.is_supported("archive.zip"))
        self.assertFalse(file_filter.is_supported("setup.exe"))
        self.assertFalse(file_filter.is_supported("notes.zzz"))

    def test_url_detection(self):
        self.assertTrue(file_filter.is_url("https://example.com"))
        self.assertTrue(file_filter.is_url("http://localhost:8000/"))
        self.assertFalse(file_filter.is_url("C:/path/to/file.pdf"))
        self.assertFalse(file_filter.is_url("not a url"))

    def test_expand_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "a.txt").write_text("a")
            sub = Path(tmp) / "sub"
            sub.mkdir()
            (sub / "b.pdf").write_text("b")
            (sub / "c.exe").write_text("c")
            expanded = file_filter.expand_paths([tmp])
            names = sorted(os.path.basename(p) for p in expanded)
            self.assertEqual(names, ["a.txt", "b.pdf", "c.exe"])

    def test_filter_supported(self):
        supported, rejected = file_filter.filter_supported(
            ["a.pdf", "b.zzz", "c.png", "d.exe"]
        )
        self.assertEqual(set(supported), {"a.pdf", "c.png"})
        self.assertEqual(set(rejected), {"b.zzz", "d.exe"})


class TestNaming(unittest.TestCase):
    def test_md_basename(self):
        self.assertEqual(naming.md_basename("foo/bar.pdf"), "bar")
        self.assertEqual(naming.md_basename("noext"), "noext")
        self.assertEqual(naming.md_basename("/abs/path/Spec.PDF"), "Spec")

    def test_build_output_path_uses_output_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = naming.build_output_path("/some/source/report.pdf", tmp)
            self.assertEqual(out, os.path.join(tmp, "report.md"))

    def test_build_output_path_next_to_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "report.docx")
            out = naming.build_output_path(src, None)
            self.assertEqual(out, os.path.join(tmp, "report.md"))

    def test_build_output_path_autoincrement(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "report.md").write_text("first")
            out1 = naming.build_output_path(os.path.join(tmp, "report.pdf"), tmp)
            (Path(tmp) / os.path.basename(out1)).write_text("second")
            out2 = naming.build_output_path(os.path.join(tmp, "report.pdf"), tmp)
            (Path(tmp) / os.path.basename(out2)).write_text("third")
            self.assertEqual(os.path.basename(out1), "report_1.md")
            self.assertEqual(os.path.basename(out2), "report_2.md")


if __name__ == "__main__":
    unittest.main(verbosity=2)