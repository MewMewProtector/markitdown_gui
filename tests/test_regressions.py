"""Regression tests for bugs found during the project audit."""
from __future__ import annotations

import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock


class TestDatabaseThreadSafety(unittest.TestCase):
    def test_worker_can_store_description_on_main_connection(self) -> None:
        from app.core import config

        with tempfile.TemporaryDirectory() as tmp:
            db_dir = Path(tmp) / "state"
            db_path = db_dir / "state.db"
            config.close_db()
            with mock.patch.object(config, "_DB_DIR", db_dir), mock.patch.object(
                config, "_DB_PATH", db_path
            ):
                # Initialize the singleton on the main thread first. This was
                # the exact sequence that used to trigger sqlite's
                # check_same_thread failure in conversion workers.
                config.get_db()
                worker = threading.Thread(
                    target=config.store_description,
                    args=("hash", "model", "prompt", "description", "now"),
                )
                worker.start()
                worker.join(timeout=5)
                self.assertFalse(worker.is_alive())
                self.assertEqual(
                    config.get_cached_description("hash", "model", "prompt"),
                    "description",
                )
                config.close_db()


class TestConverterConfiguration(unittest.TestCase):
    def test_content_understanding_uses_markitdown_argument_names(self) -> None:
        from app.core.converter import Converter

        with mock.patch("app.core.converter.MarkItDown") as markitdown:
            Converter(
                {
                    "enable_cu": "1",
                    "cu_endpoint": "https://example.test/cu",
                    "cu_analyzer_id": "analyzer-1",
                }
            ).client()

        kwargs = markitdown.call_args.kwargs
        self.assertEqual(kwargs["cu_endpoint"], "https://example.test/cu")
        self.assertEqual(kwargs["cu_analyzer_id"], "analyzer-1")
        self.assertNotIn("az_content_understanding_endpoint", kwargs)

    def test_direct_image_client_gets_openrouter_headers(self) -> None:
        from app.core.converter import Converter

        fake_client = mock.MagicMock()
        fake_client.default_headers = {}
        with mock.patch("openai.OpenAI", return_value=fake_client, create=True), \
             mock.patch("app.core.converter.MarkItDown") as markitdown:
            Converter(
                {
                    "llm_api_key": "sk-fake",
                    "llm_base_url": "https://openrouter.ai/api/v1",
                }
            ).client()

        client = markitdown.call_args.kwargs["llm_client"]
        self.assertEqual(client.default_headers["X-Title"], "markitdown_gui")
        self.assertIn("HTTP-Referer", client.default_headers)

    def test_inline_description_has_no_deleted_temp_image_link(self) -> None:
        from app.core.converter import Converter

        converter = Converter({})
        temp_image = os.path.join(tempfile.gettempdir(), "temporary-image.png")
        with mock.patch(
            "app.core.converter.describe_image_with_error",
            return_value=("useful description", None),
        ):
            markdown, errors = converter._build_inline_markdown(
                [(1, temp_image)], group_by_page=True, total=1
            )

        self.assertFalse(errors)
        self.assertIn("useful description", markdown)
        self.assertIn("Изображение: temporary-image.png", markdown)
        self.assertNotIn(f"]({temp_image})", markdown)

    def test_audio_and_youtube_toggles_are_enforced(self) -> None:
        from app.core.converter import Converter

        converter = Converter({"enable_audio": "0", "enable_youtube": "0"})
        converter._client = mock.MagicMock()
        with self.assertRaisesRegex(RuntimeError, "Аудио"):
            converter.convert("recording.mp3")
        with self.assertRaisesRegex(RuntimeError, "YouTube"):
            converter.convert("https://www.youtube.com/watch?v=abc")
        converter._client.convert.assert_not_called()


class TestUrlHandling(unittest.TestCase):
    def test_url_is_supported_and_gets_safe_output_name(self) -> None:
        from app.core.file_filter import filter_supported
        from app.core.naming import build_output_path

        url = "https://example.com/articles/hello%20world.html?ref=home"
        accepted, rejected = filter_supported([url])
        self.assertEqual(accepted, [url])
        self.assertFalse(rejected)
        with tempfile.TemporaryDirectory() as out:
            output = build_output_path(url, out)
        self.assertEqual(os.path.basename(output), "hello world.md")


class TestConversionLaunch(unittest.TestCase):
    @staticmethod
    def _window():
        from app.main_window import MainWindow

        win = MainWindow.__new__(MainWindow)
        win._settings = {"describe_images": "0", "output_folder": ""}
        win._streaming_settings = dict(win._settings)
        win._output_reservations = set()
        win.scan_view = mock.MagicMock()
        win._launch_job = mock.MagicMock()
        win._show_toast = mock.MagicMock()
        return win

    def test_confirmation_choice_applies_to_current_conversion(self) -> None:
        from app.core import config

        win = self._window()
        state = {"describe_images": "0"}

        def confirm(_paths):
            state["describe_images"] = "1"
            return True

        win.scan_view.confirm_conversion.side_effect = confirm
        with tempfile.TemporaryDirectory() as out:
            settings = {"describe_images": state["describe_images"], "output_folder": out}

            def current_settings():
                settings["describe_images"] = state["describe_images"]
                return dict(settings)

            source = os.path.join(out, "source.pdf")
            with mock.patch.object(config, "get_all_settings", side_effect=current_settings):
                win._start_conversions([source])

        launched_settings = win._launch_job.call_args.kwargs["settings"]
        self.assertEqual(launched_settings["describe_images"], "1")

    def test_manual_jobs_with_same_basename_get_unique_outputs(self) -> None:
        from app.core import config

        win = self._window()
        win.scan_view.confirm_conversion.return_value = True
        with tempfile.TemporaryDirectory() as out:
            first = os.path.join(out, "a", "report.pdf")
            second = os.path.join(out, "b", "report.pdf")
            settings = {"describe_images": "0", "output_folder": out}
            with mock.patch.object(config, "get_all_settings", return_value=settings):
                win._start_conversions([first, second])

        outputs = [
            call.kwargs["preselected_output_path"]
            for call in win._launch_job.call_args_list
        ]
        self.assertEqual(len(outputs), 2)
        self.assertNotEqual(outputs[0], outputs[1])
        self.assertEqual(os.path.basename(outputs[0]), "report.md")
        self.assertEqual(os.path.basename(outputs[1]), "report_1.md")

    def test_streaming_uses_enqueue_time_settings(self) -> None:
        win = self._window()
        snapshot = {
            "describe_images": "1",
            "llm_model": "queued-model",
            "output_folder": "C:/output",
        }
        win._settings = {**snapshot, "llm_model": "new-model"}
        win._streaming_settings = dict(win._settings)
        win._start_conversions(
            ["C:/input/report.pdf"],
            from_streaming=True,
            preselected_output_path="C:/output/report.md",
            settings_override=snapshot,
        )
        self.assertEqual(
            win._launch_job.call_args.kwargs["settings"]["llm_model"],
            "queued-model",
        )

    def test_preview_uses_logged_output_folder(self) -> None:
        from app.core import config

        win = self._window()
        win.preview_view = mock.MagicMock()
        win.tabs = mock.MagicMock()
        with tempfile.TemporaryDirectory() as out:
            result = os.path.join(out, "report_2.md")
            Path(result).write_text("done", encoding="utf-8")
            with mock.patch.object(config, "get_latest_output", return_value=result):
                win._preview_existing("C:/input/report.pdf")

        win.preview_view.load_file.assert_called_once_with(result)
        win.tabs.setCurrentIndex.assert_called_once_with(1)

    def test_preview_uses_logged_output_for_url(self) -> None:
        from app.core import config

        win = self._window()
        win.preview_view = mock.MagicMock()
        win.tabs = mock.MagicMock()
        with tempfile.TemporaryDirectory() as out:
            result = os.path.join(out, "video.md")
            Path(result).write_text("done", encoding="utf-8")
            with mock.patch.object(config, "get_latest_output", return_value=result):
                win._preview_existing("https://youtu.be/example")

        win.preview_view.load_file.assert_called_once_with(result)


class TestConfirmationDialog(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def test_cancel_does_not_persist_checkbox_change(self) -> None:
        from PySide6.QtWidgets import QDialog
        from app.core import config
        from app.ui.scan_view import ScanView

        view = ScanView()
        with mock.patch.object(config, "get_setting", return_value="0"), \
             mock.patch.object(config, "set_setting") as save, \
             mock.patch.object(
                 QDialog, "exec", return_value=QDialog.DialogCode.Rejected
             ):
            accepted = view.confirm_conversion(["picture.png"])

        self.assertFalse(accepted)
        save.assert_not_called()
        view.deleteLater()


class TestWatcherLifecycle(unittest.TestCase):
    def test_refresh_known_populates_snapshot(self) -> None:
        from app.core.watcher import FolderWatcher

        watcher = FolderWatcher.__new__(FolderWatcher)
        watcher._root = "root"
        watcher._known = set()
        watcher._walk = mock.MagicMock(return_value=({"root/a.pdf"}, []))
        watcher.refresh_known()
        self.assertEqual(watcher._known, {"root/a.pdf"})

    def test_initial_scan_reports_file_created_after_watch_started(self) -> None:
        from app.core.watcher import _ScanTask

        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "new.pdf")
            Path(path).write_text("new", encoding="utf-8")
            watcher = mock.MagicMock()
            watcher._root = root
            watcher._known = set()
            watcher._watch_started_ns = time.time_ns() - 1_000_000_000
            watcher._walk.return_value = ({path}, [])
            task = _ScanTask(watcher, generation=3, initial=True)
            task.run()

        args = watcher._scan_result.emit.call_args.args
        self.assertEqual(args[0], 3)
        self.assertEqual(args[1], [path])
        self.assertEqual(args[3], [path])


@unittest.skipUnless(os.name == "nt", "Windows paths are case-insensitive")
class TestWindowsPathHandling(unittest.TestCase):
    def test_streaming_rejects_same_folder_with_different_case(self) -> None:
        from app.core import config

        with tempfile.TemporaryDirectory() as folder:
            ok, _message = config.validate_streaming_paths(folder, folder.upper())
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()
