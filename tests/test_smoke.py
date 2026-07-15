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
from unittest import mock

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


class TestConverterDescribeImages(unittest.TestCase):
    """
    Smoke check that `Converter._build` passes (or omits) llm_model /
    llm_prompt according to the `describe_images` setting.

    We stub out `MarkItDown` so the test never depends on the optional
    markitdown install in CI.
    """

    def _make(self, **overrides):
        from app.core.converter import Converter

        s = {
            "describe_images": "0",
            "llm_api_key": "sk-test",
            "llm_base_url": "http://localhost/v1",
            "llm_model": "gpt-4o-mini",
            "llm_prompt": "",
        }
        s.update(overrides)
        return Converter(s)

    def test_describe_off_omits_llm_kwargs(self):
        captured: dict = {}

        def fake_mid(**kwargs):
            captured.update(kwargs)
            return mock.MagicMock()

        with mock.patch(
            "app.core.converter.MarkItDown", side_effect=fake_mid
        ):
            self._make(describe_images="0").client()
        self.assertNotIn("llm_model", captured)
        self.assertNotIn("llm_prompt", captured)

    def test_describe_on_adds_llm_model(self):
        captured: dict = {}

        def fake_mid(**kwargs):
            captured.update(kwargs)
            return mock.MagicMock()

        with mock.patch(
            "app.core.converter.MarkItDown", side_effect=fake_mid
        ):
            self._make(describe_images="1", llm_prompt="Describe it").client()
        self.assertEqual(captured.get("llm_model"), "gpt-4o-mini")
        self.assertEqual(captured.get("llm_prompt"), "Describe it")


class TestInlineImageCleanup(unittest.TestCase):
    """
    End-to-end check that the inline-image description path creates and
    removes its temp directory exactly once, and never touches anything
    outside `tempfile.gettempdir()`.
    """

    def setUp(self) -> None:
        # Isolate the config DB so we don't clobber the real one.
        os.environ["APPDATA"] = tempfile.mkdtemp(
            prefix="markitdown_gui_inline_test_home_"
        )
        # Force-reload the config module's DB connection.
        import importlib
        from app.core import config as _config
        _config.close_db()
        importlib.reload(_config)
        self._config = _config

    def tearDown(self) -> None:
        self._config.close_db()

    def _build_docx_with_image(self, out_path: str) -> None:
        import zipfile
        fake_png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 1500
        fake_txt = b"hello" * 200
        with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("[Content_Types].xml", fake_txt)
            z.writestr("word/document.xml", fake_txt)
            z.writestr("word/media/pic.png", fake_png)

    def test_inline_describe_cleans_temp_dir(self) -> None:
        from app.core.converter import Converter

        with tempfile.TemporaryDirectory() as tmp:
            docx_path = os.path.join(tmp, "demo.docx")
            self._build_docx_with_image(docx_path)

            converter = Converter({
                "describe_images": "1",
                "llm_api_key": "sk-fake",
                "llm_base_url": "",
                "llm_model": "gpt-4o-mini",
                "llm_prompt": "Describe it",
                "use_plugins": "0",
            })

            # Stub markitdown so we don't need the real dep for text.
            fake_mid = mock.MagicMock()
            fake_result = mock.MagicMock()
            fake_result.text_content = "# Demo\n\nBody text.\n"
            fake_mid.convert.return_value = fake_result
            with mock.patch.object(converter, "_client", fake_mid), \
                 mock.patch(
                     "app.core.converter.describe_image_via_cache",
                     return_value="a yellow square",
                 ) as describe:
                out = converter.convert(docx_path)

            # Description block was appended.
            self.assertIn("## Описания изображений", out)
            self.assertIn("a yellow square", out)
            describe.assert_called_once()

            # No temp directory matching our prefix should remain.
            tmp_root = tempfile.gettempdir()
            leftovers = [
                d for d in os.listdir(tmp_root)
                if d.startswith("markitdown_gui_office_imgs_")
            ]
            self.assertEqual(
                leftovers, [],
                f"Temp dirs left behind: {leftovers}",
            )

    def test_inline_describe_off_when_setting_disabled(self) -> None:
        from app.core.converter import Converter

        with tempfile.TemporaryDirectory() as tmp:
            docx_path = os.path.join(tmp, "demo.docx")
            self._build_docx_with_image(docx_path)

            converter = Converter({
                "describe_images": "0",
                "llm_api_key": "sk-fake",
                "llm_model": "gpt-4o-mini",
            })
            fake_mid = mock.MagicMock()
            fake_result = mock.MagicMock()
            fake_result.text_content = "# Demo\n"
            fake_mid.convert.return_value = fake_result
            with mock.patch.object(converter, "_client", fake_mid), \
                 mock.patch(
                     "app.core.converter.describe_image_via_cache",
                 ) as describe:
                out = converter.convert(docx_path)

            self.assertNotIn("## Описания изображений", out)
            describe.assert_not_called()

    def test_inline_describe_no_api_key_no_block(self) -> None:
        from app.core.converter import Converter

        with tempfile.TemporaryDirectory() as tmp:
            docx_path = os.path.join(tmp, "demo.docx")
            self._build_docx_with_image(docx_path)

            converter = Converter({
                "describe_images": "1",
                "llm_api_key": "",
                "llm_model": "gpt-4o-mini",
            })
            fake_mid = mock.MagicMock()
            fake_result = mock.MagicMock()
            fake_result.text_content = "# Demo\n"
            fake_mid.convert.return_value = fake_result
            with mock.patch.object(converter, "_client", fake_mid), \
                 mock.patch(
                     "app.core.converter.describe_image_via_cache",
                 ) as describe:
                out = converter.convert(docx_path)

            # Without an API key we don't even try.
            self.assertNotIn("## Описания изображений", out)
            describe.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)