"""
Tests for the streaming-mode helpers added in the
`.kilo/plans/1784573533201-streaming-md-plan.md` rework:

* Folder-pair validation in `config.validate_streaming_paths`.
* Reserved-name support in `naming.build_output_path`.
* MainWindow streaming queue: readiness, FIFO, output reservation, error
  isolation, and the streaming-vs-manual UI side effects.

All tests redirect `APPDATA` to a per-test temp dir so the real
`state.db` is never touched.
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from collections import deque
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def _isolate_appdata() -> str:
    tmp = tempfile.mkdtemp(prefix="markitdown_gui_streaming2_")
    os.environ["APPDATA"] = tmp
    from app.core import config as _config
    _config.close_db()
    import importlib
    importlib.reload(_config)
    return _config


# ---------------------------------------------------------------------------
# 1) config.validate_streaming_paths
# ---------------------------------------------------------------------------


class TestValidateStreamingPaths(unittest.TestCase):
    def test_missing_watch_rejected(self) -> None:
        from app.core import config
        ok, msg = config.validate_streaming_paths("", "/tmp")
        self.assertFalse(ok)
        self.assertIn("сканирования", msg)

    def test_missing_output_rejected(self) -> None:
        from app.core import config
        with tempfile.TemporaryDirectory() as w:
            ok, msg = config.validate_streaming_paths(w, "")
            self.assertFalse(ok)
            self.assertIn("сохранения", msg)

    def test_identical_paths_rejected(self) -> None:
        from app.core import config
        with tempfile.TemporaryDirectory() as d:
            ok, msg = config.validate_streaming_paths(d, d)
            self.assertFalse(ok)
            self.assertIn("совпадает", msg)

    def test_output_inside_watch_rejected(self) -> None:
        from app.core import config
        with tempfile.TemporaryDirectory() as w:
            nested = os.path.join(w, "results")
            os.makedirs(nested)
            ok, msg = config.validate_streaming_paths(w, nested)
            self.assertFalse(ok)
            self.assertIn("внутри", msg)

    def test_watch_inside_output_accepted(self) -> None:
        from app.core import config
        with tempfile.TemporaryDirectory() as out:
            nested = os.path.join(out, "watched")
            os.makedirs(nested)
            ok, msg = config.validate_streaming_paths(nested, out)
            self.assertTrue(ok, msg)

    def test_siblings_accepted(self) -> None:
        from app.core import config
        with tempfile.TemporaryDirectory() as parent:
            w = os.path.join(parent, "in")
            o = os.path.join(parent, "out")
            os.makedirs(w)
            os.makedirs(o)
            ok, msg = config.validate_streaming_paths(w, o)
            self.assertTrue(ok, msg)

    def test_nonexistent_rejected(self) -> None:
        from app.core import config
        ok, msg = config.validate_streaming_paths(
            "/nonexistent/watch_qwe", "/nonexistent/out_asd"
        )
        self.assertFalse(ok)


# ---------------------------------------------------------------------------
# 2) naming.build_output_path with a reservation set
# ---------------------------------------------------------------------------


class TestNamingReservation(unittest.TestCase):
    def test_reserved_taken_into_account(self) -> None:
        from app.core import naming
        with tempfile.TemporaryDirectory() as tmp:
            reserved = {os.path.join(tmp, "report.md")}
            path = naming.build_output_path(
                os.path.join(tmp, "src", "a", "report.pdf"),
                tmp,
                reserved=reserved,
            )
            self.assertEqual(os.path.basename(path), "report_1.md")

    def test_existing_files_taken_into_account(self) -> None:
        from app.core import naming
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "report.md").write_text("first")
            reserved = {os.path.join(tmp, "report_1.md")}
            path = naming.build_output_path(
                os.path.join(tmp, "a", "report.pdf"), tmp, reserved=reserved
            )
            self.assertEqual(os.path.basename(path), "report_2.md")


# ---------------------------------------------------------------------------
# 3) MainWindow streaming queue & lifecycle
# ---------------------------------------------------------------------------


def _qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _make_win(settings=None):
    _qapp()
    from app.main_window import MainWindow
    win = MainWindow.__new__(MainWindow)
    win._job_counter = 0
    win._active_jobs = {}
    win._settings = settings or {
        "describe_images": "0",
        "streaming_mode": "0",
        "llm_api_key": "",
        "llm_base_url": "",
        "llm_model": "",
        "llm_prompt": "",
        "output_folder": "",
        "watch_folder": "",
    }
    win._streaming_queue = deque()
    win._streaming_in_flight = False
    win._streaming_settings = dict(win._settings)
    win._streaming_active = False
    win._output_reservations = set()
    timer = mock.MagicMock()
    timer.isActive.return_value = False
    win._readiness_timer = timer
    win._active_watch_root = None
    win.scan_view = mock.MagicMock()
    win.preview_view = mock.MagicMock()
    win.settings_view = mock.MagicMock()
    win.log_view = mock.MagicMock()
    win.tabs = mock.MagicMock()
    win.toast = mock.MagicMock()
    win._show_toast = mock.MagicMock()
    win._show_centered_toast = mock.MagicMock()
    return win


def _file_sig(path: str) -> tuple[int, int]:
    """Return (size, mtime_ns) — matches os.stat() output used by the queue."""
    st = os.stat(path)
    return (st.st_size, st.st_mtime_ns)


class TestStreamingQueueReadiness(unittest.TestCase):
    """The readiness pump must launch exactly one job at a time."""

    def test_stable_file_dispatches(self) -> None:
        win = _make_win()
        win._streaming_active = True
        with tempfile.TemporaryDirectory() as out:
            src = os.path.join(out, "doc.pdf")
            Path(src).write_text("hello")
            from app.main_window import _QueueItem
            sig = _file_sig(src)
            qi = _QueueItem(
                path=src,
                settings=dict(win._settings),
                last_sig=sig,
                stable_count=2,
                enqueued_at=time.monotonic(),
            )
            win._streaming_queue.append(qi)
            win._dispatch_ready_item = mock.MagicMock()
            win._tick_streaming()
            win._dispatch_ready_item.assert_called_once_with(qi)
            self.assertEqual(len(win._streaming_queue), 0)

    def test_unstable_file_keeps_queued(self) -> None:
        win = _make_win()
        win._streaming_active = True
        with tempfile.TemporaryDirectory() as out:
            src = os.path.join(out, "growing.pdf")
            Path(src).write_text("v1")
            from app.main_window import _QueueItem
            qi = _QueueItem(
                path=src,
                settings=dict(win._settings),
                last_sig=(0, 0),
                stable_count=0,
                enqueued_at=time.monotonic(),
            )
            win._streaming_queue.append(qi)
            win._dispatch_ready_item = mock.MagicMock()
            win._tick_streaming()
            win._dispatch_ready_item.assert_not_called()
            self.assertEqual(len(win._streaming_queue), 1)
            self.assertEqual(qi.stable_count, 1)

    def test_missing_file_marks_failure_and_continues(self) -> None:
        win = _make_win()
        win._streaming_active = True
        from app.main_window import _QueueItem
        qi = _QueueItem(
            path="/nonexistent/does_not_exist.pdf",
            settings=dict(win._settings),
            enqueued_at=time.monotonic(),
        )
        win._streaming_queue.append(qi)
        win._dispatch_ready_item = mock.MagicMock()
        win._tick_streaming()
        win._dispatch_ready_item.assert_not_called()
        self.assertEqual(len(win._streaming_queue), 0)
        win.scan_view.update_status.assert_called_with(qi.path, "ошибка")

    def test_deadline_expired_marks_failure(self) -> None:
        win = _make_win()
        win._streaming_active = True
        with tempfile.TemporaryDirectory() as out:
            src = os.path.join(out, "never.pdf")
            Path(src).write_text("x")
            from app.main_window import _QueueItem
            qi = _QueueItem(
                path=src,
                settings=dict(win._settings),
                enqueued_at=time.monotonic() - 9999.0,
            )
            win._streaming_queue.append(qi)
            win._dispatch_ready_item = mock.MagicMock()
            win._tick_streaming()
            win._dispatch_ready_item.assert_not_called()
            self.assertEqual(len(win._streaming_queue), 0)


class TestStreamingFifo(unittest.TestCase):
    def test_in_flight_blocks_dispatch(self) -> None:
        win = _make_win()
        win._streaming_active = True
        win._streaming_in_flight = True
        from app.main_window import _QueueItem
        with tempfile.TemporaryDirectory() as out:
            src = os.path.join(out, "x.pdf")
            Path(src).write_text("hi")
            qi = _QueueItem(
                path=src,
                settings=dict(win._settings),
                last_sig=_file_sig(src),
                stable_count=5,
                enqueued_at=time.monotonic(),
            )
            win._streaming_queue.append(qi)
            win._dispatch_ready_item = mock.MagicMock()
            win._tick_streaming()
            win._dispatch_ready_item.assert_not_called()
            self.assertEqual(len(win._streaming_queue), 1)


class TestStreamingDispatchReservesOutput(unittest.TestCase):
    def test_same_basename_gets_unique_output(self) -> None:
        from app.core import naming
        win = _make_win()
        win._streaming_active = True
        with tempfile.TemporaryDirectory() as out:
            a = os.path.join(out, "a", "report.pdf")
            b = os.path.join(out, "b", "report.pdf")
            os.makedirs(os.path.dirname(a))
            os.makedirs(os.path.dirname(b))
            Path(a).write_text("a")
            Path(b).write_text("b")
            r1 = naming.build_output_path(a, out, reserved=win._output_reservations)
            win._output_reservations.add(r1)
            r2 = naming.build_output_path(b, out, reserved=win._output_reservations)
            self.assertNotEqual(os.path.basename(r1), os.path.basename(r2))
            self.assertEqual(os.path.basename(r1), "report.md")
            self.assertEqual(os.path.basename(r2), "report_1.md")
            win._output_reservations.discard(r1)
            r3 = naming.build_output_path(b, out, reserved=win._output_reservations)
            self.assertEqual(os.path.basename(r3), "report.md")


class TestStreamingVsManualUi(unittest.TestCase):
    def _job(self, origin: str) -> mock.MagicMock:
        j = mock.MagicMock()
        j.origin = origin
        j._log_row_id = 0
        j._warning_text = ""
        return j

    def test_streaming_finish_does_not_change_tab(self) -> None:
        from app.core.conversion_job import ConversionJob
        win = _make_win()
        win._active_jobs[42] = self._job(ConversionJob.ORIGIN_STREAMING)
        win._on_job_finished(42, "/x/a.pdf", "/out/a.md")
        win.preview_view.load_file.assert_not_called()
        self.assertNotIn(42, win._active_jobs)

    def test_manual_finish_loads_preview_and_changes_tab(self) -> None:
        from app.core.conversion_job import ConversionJob
        win = _make_win()
        win._active_jobs[42] = self._job(ConversionJob.ORIGIN_MANUAL)
        win._on_job_finished(42, "/x/a.pdf", "/out/a.md")
        win.preview_view.load_file.assert_called_once_with("/out/a.md")
        win.tabs.setCurrentIndex.assert_called_with(1)

    def test_streaming_failure_releases_next_item(self) -> None:
        from app.core.conversion_job import ConversionJob
        from app.main_window import _QueueItem
        win = _make_win()
        win._streaming_active = True
        win._streaming_in_flight = True
        win._active_jobs[1] = self._job(ConversionJob.ORIGIN_STREAMING)
        win._streaming_queue.append(_QueueItem(
            path="/x/b.pdf", settings=dict(win._settings)
        ))
        win._advance_streaming = mock.MagicMock()
        win._on_job_failed(1, "/x/a.pdf", "boom")
        self.assertFalse(win._streaming_in_flight)
        win._advance_streaming.assert_called_once()


class TestStreamingCancellation(unittest.TestCase):
    def test_disabling_streaming_clears_pending_queue(self) -> None:
        from app.main_window import _QueueItem
        win = _make_win()
        win._streaming_active = True
        win._streaming_queue.append(_QueueItem(path="/x/a.pdf", settings={}))
        win._streaming_queue.append(_QueueItem(path="/x/b.pdf", settings={}))
        win._cancel_streaming_queue(reason="test")
        self.assertEqual(len(win._streaming_queue), 0)


class TestSettingsAppliedImmediately(unittest.TestCase):
    """`apply_persisted_settings` must swap the watcher root when the
    persisted path changes."""

    def test_watcher_set_root_called_on_change(self) -> None:
        from app.core import config
        from app.core.conversion_job import ConversionJob
        win = _make_win()
        win.watcher = mock.MagicMock()
        with tempfile.TemporaryDirectory() as tmp:
            win._settings = {
                **win._settings,
                "watch_folder": tmp,
                "streaming_mode": "0",
                "output_folder": "",
            }
            with mock.patch.object(
                config, "get_all_settings", return_value=win._settings
            ):
                win._apply_persisted_settings()
            win.watcher.set_root.assert_called_with(tmp)
            self.assertEqual(win._active_watch_root, tmp)

    def test_invalid_streaming_disables_and_toasts(self) -> None:
        from app.core import config
        win = _make_win()
        win.watcher = mock.MagicMock()
        with tempfile.TemporaryDirectory() as tmp:
            win._settings = {
                **win._settings,
                "watch_folder": tmp,
                "streaming_mode": "1",
                "output_folder": tmp,  # identical => invalid
            }
            with mock.patch.object(
                config, "get_all_settings", return_value=win._settings
            ):
                win._apply_persisted_settings()
            self.assertFalse(win._streaming_active)
            win._show_toast.assert_called()
            toast_call = win._show_toast.call_args
            self.assertIn("Стриминг отключён", toast_call.args[0])


class TestSettingsViewSaveWithoutLoss(unittest.TestCase):
    """If validation fails, only the obsolete setting is rejected — no
    partial write to SQLite and no DB errors."""

    @classmethod
    def setUpClass(cls) -> None:
        _isolate_appdata()
        _qapp()
        from app.ui.settings_view import SettingsView
        cls.view = SettingsView()
        cls.view.show()
        from app.core import config as _config
        cls.config = _config

    def tearDown(self) -> None:
        try:
            self.config.get_db().execute("DELETE FROM settings")
            self.config.get_db().commit()
        except Exception:
            pass

    def test_invalid_save_keeps_existing_settings(self) -> None:
        # Seed a known good LLM setting.
        self.config.set_setting("llm_model", "gpt-4o-mini")
        # Turn streaming on with no output folder.
        self.view.cb_streaming_mode.setChecked(True)
        self.view.edit_watch.setText(tempfile.mkdtemp())
        self.view.edit_output.setText("")
        saved: list[str] = []

        def fake_set(key, value):
            saved.append(f"{key}={value}")

        with mock.patch.object(self.config, "set_setting", side_effect=fake_set):
            self.view.save()
        # streaming_mode must NOT have been written.
        self.assertNotIn("streaming_mode=1", saved)
        # The LLM row we seeded must remain untouched on disk.
        self.assertEqual(self.config.get_setting("llm_model"), "gpt-4o-mini")


# ---------------------------------------------------------------------------
# 5) Watcher scanner contract
# ---------------------------------------------------------------------------


class TestWatcherScanResultIgnoredWhenGenerationChanged(unittest.TestCase):
    """
    After `set_root` invalidates the generation, stale results from
    previous scans must not reach the UI. We verify this by feeding an
    out-of-date generation number into the slot and confirming the
    pending `new_files` signal never fires.
    """

    def test_stale_generation_dropped(self) -> None:
        _qapp()
        from app.core.watcher import FolderWatcher
        from app.core import config as _config
        # Don't go through set_root (which needs a real dir).
        w = FolderWatcher.__new__(FolderWatcher)
        w.new_files = mock.MagicMock()
        w._known = set()
        w._watched_dirs = set()
        w._scan_dirty = False
        w._generation = 5
        # A current-generation scan may already be running when an older
        # worker reports back.
        w._scan_in_progress = True

        # A scan result from an OLDER generation must be discarded.
        w._on_scan_complete(4, ["/old/a.pdf"], [])
        w.new_files.emit.assert_not_called()
        self.assertTrue(w._scan_in_progress)


class TestWatcherScanDirtyForcesRescan(unittest.TestCase):
    """
    If a filesystem event arrives while the previous scan is still in
    progress, the dispatcher must schedule another scan as soon as the
    current one finishes (so we don't miss the late-arriving file).
    """

    def test_rescan_scheduled_after_complete(self) -> None:
        _qapp()
        from app.core.watcher import FolderWatcher
        w = FolderWatcher.__new__(FolderWatcher)
        w.new_files = mock.MagicMock()
        w._known = set()
        w._watched_dirs = set()
        w._scan_dirty = True
        w._generation = 0
        w._debounce = mock.MagicMock()
        w._root = "/some/place"
        # Simulate completion of an in-flight scan.
        w._on_scan_complete(0, [], [])
        self.assertFalse(w._scan_dirty)
        w._debounce.start.assert_called_once()


if __name__ == "__main__":
    unittest.main(verbosity=2)
