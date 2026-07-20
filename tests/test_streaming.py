"""
Smoke tests for the streaming mode flag.

Streaming mode toggles auto-conversion of newly observed files in the
watched folder. The actual UI plumbing lives in `main_window` and is
exercised via the persisted `streaming_mode` flag.
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


class TestStreamingSetting(unittest.TestCase):
    """`streaming_mode` must be a recognised persisted setting."""

    @classmethod
    def setUpClass(cls) -> None:
        # Redirect APPDATA so we don't touch the user's real DB.
        cls._tmp_home = tempfile.mkdtemp(prefix="markitdown_gui_streaming_")
        os.environ["APPDATA"] = cls._tmp_home
        from app.core import config as _config
        _config.close_db()
        import importlib
        importlib.reload(_config)
        cls._config = _config

    @classmethod
    def tearDownClass(cls) -> None:
        cls._config.close_db()

    def setUp(self) -> None:
        # Reset settings table to a known-empty state.
        try:
            self._config.get_db().execute("DELETE FROM settings")
            self._config.get_db().commit()
        except Exception:
            pass

    def test_default_is_off(self) -> None:
        self.assertEqual(self._config.get_setting("streaming_mode"), "0")

    def test_set_and_get_round_trips(self) -> None:
        self._config.set_setting("streaming_mode", "1")
        self.assertEqual(self._config.get_setting("streaming_mode"), "1")

    def test_in_get_all_settings(self) -> None:
        self._config.set_setting("streaming_mode", "1")
        all_settings = self._config.get_all_settings()
        self.assertEqual(all_settings.get("streaming_mode"), "1")


class TestStreamingTriggersAutoConversion(unittest.TestCase):
    """
    When the watcher emits `new_files` AND `streaming_mode == 1`,
    MainWindow must kick off conversion jobs instead of just queueing
    files in the Files tab. When the mode is off, the legacy behaviour
    (just add to scan_view) is preserved.
    """

    def _build_main_window(self):
        # We don't actually instantiate a full QMainWindow — instead we
        # stub out the heavy bits so we can drive `_on_new_files` in
        # isolation.
        from app.main_window import MainWindow

        win = MainWindow.__new__(MainWindow)
        win._settings = {
            "describe_images": "0",
            "streaming_mode": "0",
            "llm_api_key": "",
            "output_folder": "",
        }
        win._job_counter = 0
        win._active_jobs = {}
        win._show_toast = mock.MagicMock()
        win.scan_view = mock.MagicMock()
        # `_start_conversions` is the integration point we want to test.
        win._start_conversions = mock.MagicMock()
        return win

    def test_streaming_on_calls_start_conversions(self) -> None:
        from app.core import config

        with mock.patch.object(config, "get_setting", return_value="1"):
            win = self._build_main_window()
            win._on_new_files(["/watched/a.pdf", "/watched/b.docx"])
            win._start_conversions.assert_called_once()
            args, kwargs = win._start_conversions.call_args
            self.assertEqual(args[0], ["/watched/a.pdf", "/watched/b.docx"])
            self.assertTrue(kwargs.get("from_streaming"))
            win._show_toast.assert_called_once()
            # scan_view must NOT be touched in streaming mode.
            win.scan_view.add_paths.assert_not_called()

    def test_streaming_off_uses_legacy_path(self) -> None:
        from app.core import config

        with mock.patch.object(config, "get_setting", return_value="0"):
            win = self._build_main_window()
            win.scan_view.add_paths.return_value = 2
            win._on_new_files(["/watched/a.pdf", "/watched/b.docx"])
            win._start_conversions.assert_not_called()
            win.scan_view.add_paths.assert_called_once()
            win._show_toast.assert_called_once()


class TestSettingsViewStreamingUI(unittest.TestCase):
    """
    The streaming checkbox on the Settings tab must be wired to the
    `streaming_mode` setting in load() and save().
    """

    @classmethod
    def setUpClass(cls) -> None:
        # Need a QApplication for the SettingsView widget.
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        cls._app = QApplication.instance() or QApplication([])

        cls._tmp_home = tempfile.mkdtemp(prefix="markitdown_gui_streaming_ui_")
        os.environ["APPDATA"] = cls._tmp_home
        from app.core import config as _config
        _config.close_db()
        import importlib
        importlib.reload(_config)
        cls._config = _config

    @classmethod
    def tearDownClass(cls) -> None:
        cls._config.close_db()

    def setUp(self) -> None:
        try:
            self._config.get_db().execute("DELETE FROM settings")
            self._config.get_db().commit()
        except Exception:
            pass
        from app.ui.settings_view import SettingsView
        self.view = SettingsView()
        # `setVisible()` only takes effect once the widget has a top-level
        # window. In the real app SettingsView is shown inside
        # QStackedWidget, so this is the production scenario.
        self.view.show()

    def tearDown(self) -> None:
        self.view.deleteLater()

    def test_default_state_is_unchecked(self) -> None:
        self.assertFalse(self.view.cb_streaming_mode.isChecked())

    def test_load_persisted_state(self) -> None:
        self._config.set_setting("streaming_mode", "1")
        self.view.load()
        self.assertTrue(self.view.cb_streaming_mode.isChecked())

    def test_save_persists_state(self) -> None:
        self.view.cb_streaming_mode.setChecked(True)
        self.view.save()
        self.assertEqual(self._config.get_setting("streaming_mode"), "1")

    def test_save_off_persists_zero(self) -> None:
        self.view.cb_streaming_mode.setChecked(False)
        self.view.save()
        self.assertEqual(self._config.get_setting("streaming_mode"), "0")

    def test_hint_visible_when_no_output_folder(self) -> None:
        self.view.edit_output.setText("")
        self.view.cb_streaming_mode.setChecked(True)
        self.view._update_streaming_hint()
        self.assertTrue(self.view._streaming_hint.isVisible())

    def test_hint_hidden_when_output_folder_set(self) -> None:
        self.view.edit_output.setText("/some/output")
        self.view.cb_streaming_mode.setChecked(True)
        self.view._update_streaming_hint()
        self.assertFalse(self.view._streaming_hint.isVisible())

    def test_hint_hidden_when_streaming_off(self) -> None:
        self.view.edit_output.setText("")
        self.view.cb_streaming_mode.setChecked(False)
        self.view._update_streaming_hint()
        self.assertFalse(self.view._streaming_hint.isVisible())


if __name__ == "__main__":
    unittest.main(verbosity=2)
