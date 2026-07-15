"""
Unit tests for the image-description cache helper.

These tests don't hit the network. We monkeypatch
`image_descriptor._call_llm` and `image_descriptor._make_openai_client`
with `unittest.mock` so the cache lookup / miss / error paths can be
exercised in isolation.

The config DB is redirected to a temp directory via the `APPDATA` env
var so the test never touches the real user settings store.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

# ---------------------------------------------------------------------------
# Redirect APPDATA *before* importing anything that pulls in `config`.
# ---------------------------------------------------------------------------
_TMP_HOME = tempfile.mkdtemp(prefix="markitdown_gui_test_home_")
os.environ["APPDATA"] = _TMP_HOME

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.core import config  # noqa: E402
from app.core import image_descriptor as desc  # noqa: E402


def _fake_image_bytes() -> bytes:
    # We don't need a real JPEG — only the bytes hash matters for caching.
    return b"\xff\xd8\xff\xe0fake-jpeg-bytes-for-tests"


def _write_tmp_image(suffix: str = ".jpg") -> str:
    fd, path = tempfile.mkstemp(suffix=suffix, dir=_TMP_HOME)
    os.close(fd)
    with open(path, "wb") as f:
        f.write(_fake_image_bytes())
    return path


class TestImageDescriptor(unittest.TestCase):
    def setUp(self) -> None:
        # Reset settings between tests so describe_images/api_key defaults
        # are predictable.
        config._conn = None
        try:
            config.get_db().execute("DELETE FROM settings")
            config.get_db().execute("DELETE FROM image_descriptions")
            config.get_db().commit()
        except Exception:
            pass

    def tearDown(self) -> None:
        config.close_db()

    # ------------------------------------------------------------------
    def test_cache_miss_calls_llm_and_stores(self) -> None:
        path = _write_tmp_image(".jpg")
        settings = {
            "llm_api_key": "sk-fake",
            "llm_base_url": "http://example.invalid/v1",
            "llm_model": "gpt-4o-mini",
            "llm_prompt": "Describe this image",
        }

        fake_client = mock.MagicMock()
        fake_response = mock.MagicMock()
        fake_response.choices = [mock.MagicMock()]
        fake_response.choices[0].message.content = "A test description."
        fake_client.chat.completions.create.return_value = fake_response

        with mock.patch.object(
            desc, "_make_openai_client", return_value=fake_client
        ) as make_client:
            result = desc.describe_image_via_cache(path, settings)

        self.assertEqual(result, "A test description.")
        make_client.assert_called_once()
        fake_client.chat.completions.create.assert_called_once()
        # Cache should now contain the description.
        hash_key = desc.compute_hash(path)
        cached = config.get_cached_description(
            hash_key, "gpt-4o-mini", "Describe this image"
        )
        self.assertEqual(cached, "A test description.")

    def test_cache_hit_skips_llm(self) -> None:
        path = _write_tmp_image(".png")
        settings = {
            "llm_api_key": "sk-fake",
            "llm_base_url": "",
            "llm_model": "gpt-4o-mini",
            "llm_prompt": "",
        }

        # Pre-populate the cache so the next call should NOT hit the network.
        hash_key = desc.compute_hash(path)
        config.store_description(
            hash_key, "gpt-4o-mini", desc.DEFAULT_PROMPT, "cached value", "now"
        )

        with mock.patch.object(
            desc, "_make_openai_client"
        ) as make_client:
            result = desc.describe_image_via_cache(path, settings)

        self.assertEqual(result, "cached value")
        make_client.assert_not_called()

    def test_llm_error_returns_none_and_does_not_raise(self) -> None:
        path = _write_tmp_image(".jpeg")
        settings = {
            "llm_api_key": "sk-fake",
            "llm_base_url": "",
            "llm_model": "gpt-4o-mini",
            "llm_prompt": "",
        }

        fake_client = mock.MagicMock()
        fake_client.chat.completions.create.side_effect = RuntimeError("boom")

        with mock.patch.object(
            desc, "_make_openai_client", return_value=fake_client
        ):
            result = desc.describe_image_via_cache(path, settings)

        self.assertIsNone(result)
        # Nothing should be stored in the cache after a failed call.
        hash_key = desc.compute_hash(path)
        cached = config.get_cached_description(
            hash_key, "gpt-4o-mini", desc.DEFAULT_PROMPT
        )
        self.assertIsNone(cached)

    def test_no_api_key_returns_none(self) -> None:
        path = _write_tmp_image(".jpg")
        settings = {
            "llm_api_key": "",
            "llm_base_url": "",
            "llm_model": "gpt-4o-mini",
            "llm_prompt": "",
        }
        result = desc.describe_image_via_cache(path, settings)
        self.assertIsNone(result)

    def test_is_image_file(self) -> None:
        self.assertTrue(desc.is_image_file("foo.jpg"))
        self.assertTrue(desc.is_image_file("FOO.JPEG"))
        self.assertTrue(desc.is_image_file("bar.png"))
        self.assertFalse(desc.is_image_file("bar.gif"))
        self.assertFalse(desc.is_image_file("doc.pdf"))
        self.assertFalse(desc.is_image_file("https://example.com/x.jpg"))


class TestConverterBuild(unittest.TestCase):
    """Verify that Converter._build adds llm_model / llm_prompt as required."""

    def setUp(self) -> None:
        # Make sure the user-level settings DB isn't accidentally touched.
        config._conn = None

    def tearDown(self) -> None:
        config.close_db()

    def _make_converter(self, **overrides) -> "Converter":
        from app.core.converter import Converter

        settings = {
            "describe_images": "0",
            "llm_api_key": "",
            "llm_base_url": "",
            "llm_model": "",
            "llm_prompt": "",
        }
        settings.update(overrides)
        return Converter(settings)

    def test_describe_off_omits_llm_model(self) -> None:
        # Force `_build` to instantiate a real MarkItDown if available,
        # otherwise just intercept the constructor.
        captured: dict = {}

        def fake_mid(**kwargs):
            captured.update(kwargs)
            return mock.MagicMock()

        with mock.patch("app.core.converter.MarkItDown", side_effect=fake_mid):
            self._make_converter(
                describe_images="0",
                llm_api_key="sk-fake",
                llm_base_url="http://x/v1",
                llm_model="gpt-4o-mini",
            ).client()
        self.assertNotIn("llm_model", captured)
        self.assertNotIn("llm_prompt", captured)

    def test_describe_on_includes_llm_model_and_default(self) -> None:
        captured: dict = {}

        def fake_mid(**kwargs):
            captured.update(kwargs)
            return mock.MagicMock()

        with mock.patch("app.core.converter.MarkItDown", side_effect=fake_mid):
            self._make_converter(
                describe_images="1",
                llm_api_key="sk-fake",
                llm_base_url="http://x/v1",
                llm_model="custom-model",
                llm_prompt="my prompt",
            ).client()
        self.assertEqual(captured.get("llm_model"), "custom-model")
        self.assertEqual(captured.get("llm_prompt"), "my prompt")

    def test_describe_on_default_model_when_empty(self) -> None:
        captured: dict = {}

        def fake_mid(**kwargs):
            captured.update(kwargs)
            return mock.MagicMock()

        with mock.patch("app.core.converter.MarkItDown", side_effect=fake_mid):
            self._make_converter(
                describe_images="1",
                llm_api_key="sk-fake",
                llm_base_url="",
                llm_model="",
                llm_prompt="",
            ).client()
        self.assertEqual(captured.get("llm_model"), desc.DEFAULT_MODEL)


if __name__ == "__main__":
    unittest.main(verbosity=2)
