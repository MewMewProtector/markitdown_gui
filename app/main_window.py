"""
Main application window: frameless, custom title bar, four tabs, drag-n-drop,
file watcher, and conversion orchestration.

Streaming mode specifics (see plan `.kilo/plans/1784573533201-streaming-md-plan.md`):

* Only files created/copied AFTER the watcher started are auto-processed.
* Each new path is appended to the Files tab (status: «ожидание») and
  pushed onto an internal FIFO queue.
* A readiness timer waits until a file's size and mtime stabilise for
  two ticks (~2 seconds) before launching MarkItDown.
* At most one streaming conversion runs at a time; FIFO ordering is
  preserved across bursts.
* The streaming success path does NOT switch tabs, does NOT load the
  preview, and does NOT write `last_preview`. The manual lifecycle keeps
  the existing behaviour.
* Output filenames are pre-reserved against both disk and in-flight jobs
  so two streaming tasks with the same basename never clobber each
  other (or a manual conversion already running in parallel).
"""
from __future__ import annotations

import os
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from PySide6.QtCore import QPoint, Qt, QThreadPool, QTimer
from PySide6.QtGui import (
    QCloseEvent,
    QColor,
    QDragEnterEvent,
    QDropEvent,
    QIcon,
)
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QSizeGrip,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .core import config
from .core import naming
from .core.conversion_job import ConversionJob
from .core.file_filter import expand_paths, is_supported
from .core.watcher import FolderWatcher
from .ui.log_view import LogView
from .ui.navigation import SideNavigation
from .ui.preview_view import PreviewView
from .ui.scan_view import ScanView
from .ui.settings_view import SettingsView
from .ui.theme import (
    apply_theme,
)
from .ui.title_bar import TitleBar
from .ui.toast_bar import ToastBar


APP_TITLE = "MarkItDown GUI"

# Streaming readiness constants.
READINESS_TICK_MS = 1000        # how often the readiness timer fires.
READINESS_STABLE_TICKS = 2      # 2 consecutive stable checks => ready.
READINESS_TIMEOUT_S = 30.0      # give up on a file after this many seconds.


@dataclass
class _QueueItem:
    """One entry in the streaming FIFO queue."""
    path: str
    settings: dict[str, str] = field(default_factory=dict)
    last_sig: tuple[int, int] | None = None  # (size, mtime_ns)
    stable_count: int = 0
    enqueued_at: float = 0.0
    status: str = "ожидание…"    # UI status text


class MainWindow(QMainWindow):
    """The root application window."""

    def __init__(self):
        super().__init__()

        # Frameless, with custom rounded corners via stylesheet.
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.resize(1180, 760)

        self._job_counter = 0
        self._active_jobs: dict[int, ConversionJob] = {}
        self._settings = config.get_all_settings()
        # Active theme mode is tracked separately from the on-disk settings:
        # the user can change theme via the combo box at any time, and we
        # need the *current* value when handling other events (e.g. accent
        # changes), not the stale startup value of `self._settings["theme"]`.
        self._current_mode: str = self._settings.get("theme", "system")
        self._current_accent: str = self._settings.get("accent_color", "") or ""
        self._current_accent_dark: str = self._settings.get("accent_color_dark", "") or ""

        # Streaming-mode state ------------------------------------------
        # FIFO of pending files (paths plus per-item snapshot of the
        # settings at the moment they were observed).
        self._streaming_queue: deque[_QueueItem] = deque()
        # Set of output paths we have already committed to writing so
        # that concurrent streaming jobs with the same basename don't
        # collide on disk.
        self._output_reservations: set[str] = set()
        # True while a streaming job occupies the thread pool.
        self._streaming_in_flight: bool = False
        # Last snapshot of settings used by a streaming job; kept in
        # sync so that an enqueue-time snapshot can be replayed if the
        # user mutates settings mid-flight.
        self._streaming_settings: dict[str, str] = {}

        # Readiness timer: pumps the queue forward.
        self._readiness_timer = QTimer(self)
        self._readiness_timer.setInterval(READINESS_TICK_MS)
        self._readiness_timer.timeout.connect(self._tick_streaming)

        # Current root that the watcher is actually tracking. Used to
        # detect changes without restarting the watcher spuriously.
        self._active_watch_root: str | None = None
        # Whether streaming mode is currently active. Re-evaluated
        # whenever settings change.
        self._streaming_active: bool = False

        self._build_ui()
        self._wire_signals()
        self._apply_initial_theme()
        self._restore_last_session()
        self._apply_startup_watch()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        # Outer container (QMainWindow central widget).
        central = QWidget(self)
        central.setObjectName("Surface")
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Title bar.
        self.title_bar = TitleBar(APP_TITLE, parent=central)
        outer.addWidget(self.title_bar)

        # Body: persistent side navigation + stacked content.
        body = QWidget(central)
        bl = QHBoxLayout(body)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(0)
        outer.addWidget(body, 1)

        # Keep the historical ``tabs`` attribute because the rest of the app
        # and saved-session logic use its small tab-like API.
        self.tabs = SideNavigation(body)
        self.tabs.addTab("Файлы")
        self.tabs.addTab("Предпросмотр")
        self.tabs.addTab("Настройки")
        self.tabs.addTab("Лог")
        bl.addWidget(self.tabs)

        content = QWidget(body)
        content.setObjectName("ContentArea")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(12, 12, 12, 12)
        content_layout.setSpacing(0)
        self.stack = QStackedWidget(content)
        self.scan_view = ScanView(self.stack)
        self.preview_view = PreviewView(self.stack)
        self.settings_view = SettingsView(self.stack)
        self.log_view = LogView(self.stack)
        for w in (self.scan_view, self.preview_view, self.settings_view, self.log_view):
            self.stack.addWidget(w)
        content_layout.addWidget(self.stack)
        bl.addWidget(content, 1)

        self.setCentralWidget(central)

        # Frameless windows have no native resize border. Keep an unobtrusive
        # bottom-right grip so the window is still resizable in normal mode.
        self._size_grip = QSizeGrip(central)
        self._size_grip.setObjectName("SizeGrip")
        self._size_grip.raise_()

        # Toast.
        self.toast = ToastBar(self)
        self.toast.set_top_offset(self.title_bar.height() + 12)

        # Empty-state label is owned by ScanView but rendered by us when empty.
        self.tabs.setCurrentIndex(0)
        self._update_navigation_status()

    # ------------------------------------------------------------------
    def _wire_signals(self) -> None:
        # Title bar.
        self.title_bar.minimize_clicked.connect(self.showMinimized)
        self.title_bar.maximize_clicked.connect(self._toggle_maximize)
        self.title_bar.close_clicked.connect(self.close)

        # Tabs.
        self.tabs.currentChanged.connect(self.stack.setCurrentIndex)
        self.stack.currentChanged.connect(self._on_stack_changed)

        # Scan view signals.
        self.scan_view.convert_requested.connect(self._start_conversions)
        self.scan_view.preview_requested.connect(self._preview_existing)
        self.scan_view.status_message.connect(self._show_toast)

        # Settings.
        self.settings_view.theme_changed.connect(self._on_theme_changed)
        self.settings_view.accent_changed.connect(self._on_accent_changed)
        self.settings_view.settings_saved.connect(self._on_settings_saved)
        self.settings_view.save_failed.connect(self._on_save_failed)

        # Drag-n-drop.
        self.setAcceptDrops(True)

        # File watcher.
        self.watcher = FolderWatcher(self)
        self.watcher.new_files.connect(self._on_new_files)

    def _apply_initial_theme(self) -> None:
        self._current_palette = apply_theme(
            self.app(),
            self._current_mode,
            accent=self._current_accent or None,
            accent_dark=self._current_accent_dark or None,
        )
        self._update_highlighter_palette()
        self._update_title_bar_palette()

    def _update_title_bar_palette(self) -> None:
        """Use crisp black/white controls instead of muted theme text."""
        icon_color = (
            "#FFFFFF"
            if QColor(self._current_palette.bg).lightnessF() < 0.5
            else "#111418"
        )
        self.title_bar.apply_icon_palette(
            text_color=icon_color,
            text_muted_color=icon_color,
        )

    def _update_highlighter_palette(self) -> None:
        """
        Build a highlighter palette that uses the current theme's accent
        and contrast colors, so the preview syntax highlighting always
        matches the active skin — including custom user accents.
        """
        p = self._current_palette
        # Treat any palette whose base bg is dark as a "dark" scheme.
        bg = QColor(p.bg)
        is_dark = bg.lightnessF() < 0.5
        pal = {
            "header": QColor(p.accent),
            "bold": QColor(p.text),
            # bold_accent is used for the inner text of `**...**` so that
            # bold spans are visually obvious even when the chosen
            # monospace font has a subtle bold variant.
            "bold_accent": QColor(p.accent),
            "italic": QColor(p.text_muted),
            "code": QColor(p.danger),
            "code_bg": QColor(p.code_bg),
            "link": QColor(p.accent),
            "list": QColor(p.accent_hover),
            "quote": QColor(p.text_muted),
            "rule": QColor(p.text_muted),
        }
        # Note: `is_dark` reserved for future dark-specific tweaks.
        _ = is_dark
        self.preview_view.update_highlighter_palette(pal)
        self.preview_view.update_render_palette(p)

    # ------------------------------------------------------------------
    # Theme
    # ------------------------------------------------------------------
    def _on_theme_changed(self, mode: str) -> None:
        # Track the explicitly chosen mode so accent changes don't
        # accidentally fall back to a stale value from settings.
        self._current_mode = mode
        self._apply_theme(mode, persist=True,
                          accent=self._current_accent or None,
                          accent_dark=self._current_accent_dark or None)

    def _on_accent_changed(self, accent_hex: str, accent_dark_hex: str) -> None:
        """
        Apply a new accent while keeping the user's currently selected theme.
        """
        self._current_accent = accent_hex or ""
        self._current_accent_dark = accent_dark_hex or ""
        self._apply_theme(
            self._current_mode,
            persist=False,
            accent=accent_hex or None,
            accent_dark=accent_dark_hex or None,
        )

    def _on_save_failed(self, message: str) -> None:
        """Toast the validation error emitted by SettingsView.save()."""
        self._show_toast(message, "error", 6000)

    def _on_settings_saved(self) -> None:
        """
        Fired by SettingsView AFTER settings were persisted to SQLite.
        """
        # Re-read so the in-memory cache matches disk before we apply.
        self._apply_persisted_settings()

    def _apply_persisted_settings(self) -> None:
        """
        Apply the latest settings to the running app: re-evaluate the
        streaming configuration, swap the watcher root if it changed
        (or stop it if the folder is now empty), and surface startup
        errors to the user. Existing settings (including secrets) are
        preserved verbatim - only the WATCHER reacts.
        """
        self._settings = config.get_all_settings()
        watch = (self._settings.get("watch_folder") or "").strip()
        streaming_on = self._settings.get("streaming_mode") == "1"
        streaming_valid = True
        if streaming_on:
            streaming_valid, msg = config.validate_streaming_paths(
                watch,
                self._settings.get("output_folder") or "",
            )
            if not streaming_valid:
                streaming_on = False
                self._show_toast(
                    "Стриминг отключён: " + msg,
                    "error",
                    6000,
                )

        prev_streaming = self._streaming_active
        self._streaming_active = streaming_on and streaming_valid
        self._streaming_settings = dict(self._settings)

        # If we just turned streaming OFF, cancel pending queue entries.
        if prev_streaming and not self._streaming_active:
            self._cancel_streaming_queue(reason="режим стриминга выключен")
            if self._readiness_timer.isActive():
                self._readiness_timer.stop()

        # If we just turned streaming ON, kick the readiness timer so we
        # don't accidentally miss already-stable entries on restart.
        if self._streaming_active and not self._readiness_timer.isActive():
            self._readiness_timer.start()

        # Apply watch-root changes (or stop the watcher when the path
        # is empty). Empty string must stop the watcher.
        target_root = watch if os.path.isdir(watch) else None
        if target_root != self._active_watch_root:
            if target_root is None:
                self.watcher.set_root(None)
            else:
                self.watcher.set_root(target_root)
            self._active_watch_root = target_root
            # Discard queue items that don't belong to the new root
            # (defensive - shouldn't happen in practice but keeps the
            # FIFO sane).
            self._cancel_streaming_queue(reason="смена папки сканирования")
            if self._readiness_timer.isActive():
                self._readiness_timer.stop()
            if self._streaming_active:
                self._readiness_timer.start()

        self._update_navigation_status()

        # Show the centered "settings saved" toast AFTER the work so we
        # don't momentarily claim success before validation finishes.
        self._show_centered_toast("Настройки сохранены", timeout_ms=4000)

    def _apply_theme(
        self,
        mode: str,
        persist: bool,
        accent: str | None = None,
        accent_dark: str | None = None,
    ) -> None:
        # When no explicit accents are passed, use the values we last
        # observed in the settings UI (which may be unsaved).
        if accent is None:
            accent = self._current_accent or None
        if accent_dark is None:
            accent_dark = self._current_accent_dark or None
        self._current_palette = apply_theme(
            self.app(), mode, accent=accent, accent_dark=accent_dark
        )
        self._update_highlighter_palette()
        # Recolor the window controls whenever theme/accent changes.
        self._update_title_bar_palette()
        if persist:
            # Only the theme flag is persisted here; the user must press
            # the explicit "Сохранить" button in Settings to save accents.
            config.set_setting("theme", mode)
            self._current_mode = mode

    # ------------------------------------------------------------------
    # Watch folder
    # ------------------------------------------------------------------
    def _apply_startup_watch(self) -> None:
        """
        Bring the watcher up at startup. Honours the persisted streaming
        settings but treats an invalid streaming combination as
        "streaming off" rather than refusing to start.
        """
        watch = (self._settings.get("watch_folder") or "").strip()
        if not watch:
            self._update_navigation_status()
            self._show_toast(
                "Выберите папку сканирования во вкладке «Настройки».",
                "info",
                6000,
            )
            self.tabs.setCurrentIndex(2)
            return
        streaming_on = self._settings.get("streaming_mode") == "1"
        if streaming_on:
            ok, msg = config.validate_streaming_paths(
                watch,
                self._settings.get("output_folder") or "",
            )
            if not ok:
                self._show_toast(
                    "Стриминг отключён: " + msg,
                    "error",
                    6000,
                )
                streaming_on = False
                self._streaming_active = False
                self.tabs.setCurrentIndex(2)
            else:
                self._streaming_active = True
        self._streaming_settings = dict(self._settings)
        if os.path.isdir(watch):
            self.watcher.set_root(watch)
            self._active_watch_root = watch
        else:
            self._show_toast(
                f"Папка сканирования не найдена: {watch}",
                "error",
                6000,
            )
            self.tabs.setCurrentIndex(2)
        if self._streaming_active:
            self._readiness_timer.start()
        self._update_navigation_status()

    def _update_navigation_status(self) -> None:
        if not hasattr(self, "tabs") or not hasattr(
            self.tabs, "set_streaming_state"
        ):
            return
        self.tabs.set_streaming_state(
            self._streaming_active,
            queued=len(self._streaming_queue),
            in_flight=self._streaming_in_flight,
            folder=self._active_watch_root or "",
        )

    # ------------------------------------------------------------------
    # Restore last session state
    # ------------------------------------------------------------------
    def _restore_last_session(self) -> None:
        last_preview = config.get_setting("last_preview")
        if last_preview and os.path.isfile(last_preview):
            self.preview_view.load_file(last_preview)
        last_tab = config.get_setting("last_tab")
        try:
            idx = int(last_tab)
        except (TypeError, ValueError):
            idx = 0
        if 0 <= idx < self.tabs.count():
            self.tabs.setCurrentIndex(idx)

    # ------------------------------------------------------------------
    # Tabs
    # ------------------------------------------------------------------
    def _on_stack_changed(self, index: int) -> None:
        self.tabs.blockSignals(True)
        self.tabs.setCurrentIndex(index)
        self.tabs.blockSignals(False)
        if index == 3:
            self.log_view.refresh()
        elif index == 2:
            # Just refresh the form. Touching the watcher here would
            # drop a meaningful amount of in-flight state (and pollute
            # the baseline) every time the user happens to glance at
            # settings. The watcher is re-evaluated only inside
            # `_apply_persisted_settings`.
            self.settings_view.load()
        # Persist active tab so it can be restored on next launch.
        config.set_setting("last_tab", str(index))

    # ------------------------------------------------------------------
    # Conversion - manual launch
    # ------------------------------------------------------------------
    def _start_conversions(
        self,
        paths: list[str],
        from_streaming: bool = False,
        preselected_output_path: str | None = None,
        settings_override: dict[str, str] | None = None,
    ) -> None:
        if not from_streaming:
            if not self.scan_view.confirm_conversion(paths):
                return

            # The confirmation dialog can change ``describe_images``. Read
            # settings only AFTER it closes so this very conversion uses the
            # choice the user just made.
            job_settings = config.get_all_settings()
            self._settings = dict(job_settings)
        else:
            # Streaming items carry an enqueue-time snapshot. Reusing the
            # mutable global settings here makes queued files unexpectedly
            # switch model, prompt, or image-description behaviour midway.
            job_settings = dict(
                settings_override or self._streaming_settings or self._settings
            )

        output_folder = job_settings.get("output_folder", "").strip() or None

        for path in paths:
            output_path = (
                preselected_output_path
                if from_streaming and len(paths) == 1
                else None
            )
            if output_path is None:
                try:
                    output_path = naming.build_output_path(
                        path,
                        output_folder,
                        reserved=self._output_reservations,
                    )
                except OSError as exc:
                    self.scan_view.update_status(path, "ошибка")
                    self._show_toast(
                        f"Не удалось выбрать имя результата: {exc}",
                        "error",
                        5000,
                    )
                    continue

            # Reserve outputs for manual jobs too. Without this, two files
            # named ``report.pdf`` launched in parallel can both choose
            # ``report.md`` before either worker creates it.
            self._output_reservations.add(output_path)
            try:
                self._launch_job(
                    path,
                    output_folder=output_folder,
                    origin=(
                        ConversionJob.ORIGIN_STREAMING
                        if from_streaming
                        else ConversionJob.ORIGIN_MANUAL
                    ),
                    preselected_output_path=output_path,
                    settings=job_settings,
                )
            except Exception:
                self._output_reservations.discard(output_path)
                raise

    # ------------------------------------------------------------------
    # Conversion - core launcher
    # ------------------------------------------------------------------
    def _launch_job(
        self,
        path: str,
        output_folder: str | None,
        origin: str,
        preselected_output_path: str | None = None,
        settings: dict[str, str] | None = None,
    ) -> int:
        """Create a ConversionJob, wire its signals, and dispatch it.
        Returns the new job_id; -1 if launching was refused."""
        self._job_counter += 1
        job_id = self._job_counter
        job = ConversionJob(
            job_id=job_id,
            source_path=path,
            output_folder=output_folder,
            settings=dict(settings or self._settings),
            origin=origin,
            output_path=preselected_output_path,
        )
        job.signals.started.connect(self._on_job_started)
        job.signals.progress.connect(self._on_job_progress)
        job.signals.finished.connect(self._on_job_finished)
        job.signals.failed.connect(self._on_job_failed)
        job.signals.warning.connect(self._on_job_warning)
        self._active_jobs[job_id] = job

        try:
            row_id = config.log_start(path, self._now_iso())
        except Exception:
            row_id = -1
        job._log_row_id = row_id  # type: ignore[attr-defined]
        if preselected_output_path:
            job._reserved_output_path = preselected_output_path  # type: ignore[attr-defined]

        if origin == ConversionJob.ORIGIN_MANUAL:
            self.scan_view.update_status(path, "в работе…")
        QThreadPool.globalInstance().start(job)
        return job_id

    def _on_job_started(self, job_id: int, source_path: str) -> None:
        self.scan_view.update_status(source_path, "в работе…")

    def _on_job_progress(self, job_id: int, source_path: str, percent: int) -> None:
        self.scan_view.update_status(source_path, f"{percent}%")

    def _on_job_finished(self, job_id: int, source_path: str, output_path: str) -> None:
        job = self._active_jobs.pop(job_id, None)
        origin = (job.origin if job is not None else ConversionJob.ORIGIN_MANUAL)
        # Release the pre-reserved output path (if any) so a future
        # job with the same basename can pick a fresh suffix.
        reserved = getattr(job, "_reserved_output_path", None) if job is not None else None
        if reserved:
            self._output_reservations.discard(reserved)

        # Update UI status regardless of origin.
        self.scan_view.update_status(source_path, "готово")
        self._show_toast(f"Готово: {source_path}", "info", 2500)

        if job is not None:
            row_id = getattr(job, "_log_row_id", -1)
            warning_text = getattr(job, "_warning_text", "") or ""
            if row_id and row_id > 0:
                config.log_finish(
                    row_id, output_path, self._now_iso(), "ok", warning_text
                )

        if origin == ConversionJob.ORIGIN_STREAMING:
            # Background-only path: refresh the log table in case the
            # user is already on that tab; do NOT touch the preview or
            # the active tab.
            self.log_view.refresh()
            # Resume the streaming FIFO if anything else is waiting.
            self._streaming_in_flight = False
            self._update_navigation_status()
            self._advance_streaming()
        else:
            # Manual path: load into preview tab.
            self.preview_view.load_file(output_path)
            self.tabs.setCurrentIndex(1)
            config.set_setting("last_preview", output_path)
            self.log_view.refresh()

    def _on_job_warning(self, job_id: int, source_path: str, warning_text: str) -> None:
        job = self._active_jobs.get(job_id)
        if job is not None:
            existing = getattr(job, "_warning_text", "") or ""
            if existing:
                warning_text = f"{existing}; {warning_text}"
            job._warning_text = warning_text  # type: ignore[attr-defined]
        first_line = warning_text.split(";")[0].strip()
        self._show_toast(
            f"Описания картинок: {first_line}", "error", 6000
        )

    def _on_job_failed(self, job_id: int, source_path: str, error: str) -> None:
        job = self._active_jobs.pop(job_id, None)
        origin = (job.origin if job is not None else ConversionJob.ORIGIN_MANUAL)
        reserved = getattr(job, "_reserved_output_path", None) if job is not None else None
        if reserved:
            self._output_reservations.discard(reserved)

        self.scan_view.update_status(source_path, "ошибка")
        self._show_toast(f"Ошибка: {source_path}", "error", 5000)
        if job is not None:
            row_id = getattr(job, "_log_row_id", -1)
            if row_id and row_id > 0:
                short_err = error.splitlines()[0] if error else ""
                config.log_finish(row_id, "", self._now_iso(), "failed", short_err)

        if origin == ConversionJob.ORIGIN_STREAMING:
            self.log_view.refresh()
            self._streaming_in_flight = False
            self._update_navigation_status()
            # Failure must NOT block the rest of the FIFO.
            self._advance_streaming()
        else:
            self.log_view.refresh()

    # ------------------------------------------------------------------
    # Watcher -> UI
    # ------------------------------------------------------------------
    def _on_new_files(self, paths: list[str]) -> None:
        # Streaming mode requires the saved configuration to still be
        # valid at the moment of the event. If it isn't (e.g. the user
        # just blanked the output folder), fall through to the legacy
        # path so the file still appears in the Files tab.
        if self._settings.get("streaming_mode") != "1":
            self._add_legacy(paths)
            return
        if not self._streaming_active:
            # Defensive: settings_saved hasn't been applied yet, or the
            # config was invalid at the last apply. Treat the same as
            # the legacy path; user can fix it from Settings.
            self._add_legacy(paths)
            return
        if self._streaming_in_flight and not self._streaming_queue:
            # Nothing queued and something is already running; just let
            # the new files flow into the queue naturally.
            pass
        self._enqueue_streaming_batch(list(paths))

    def _add_legacy(self, paths: list[str]) -> None:
        added = self.scan_view.add_paths(paths, mark_as_new=True)
        if added:
            self._show_toast(
                f"Новых файлов: {added}. Отмечены галочками.", "info", 3500
            )

    # ------------------------------------------------------------------
    # Streaming queue management
    # ------------------------------------------------------------------
    def _enqueue_streaming_batch(self, paths: list[str]) -> None:
        existing = {item.path for item in self._streaming_queue}
        # Also avoid creating a row for a path that's currently in-flight.
        in_flight = {
            job.source_path for job in self._active_jobs.values()
            if getattr(job, "origin", "") == ConversionJob.ORIGIN_STREAMING
        }
        added_count = 0
        for p in paths:
            if p in existing or p in in_flight:
                continue
            existing.add(p)
            item = _QueueItem(
                path=p,
                settings=dict(self._streaming_settings),
                enqueued_at=time.monotonic(),
            )
            self._streaming_queue.append(item)
            self.scan_view.add_paths([p], mark_as_new=False)
            self.scan_view.update_status(p, item.status)
            added_count += 1
        if added_count:
            self._show_toast(
                f"Стриминг: ожидание {added_count} файл(ов)…",
                "info",
                2500,
            )
            if not self._readiness_timer.isActive():
                self._readiness_timer.start()
        self._update_navigation_status()

    def _cancel_streaming_queue(self, reason: str) -> None:
        if not self._streaming_queue:
            return
        cancelled_paths: list[str] = []
        for item in list(self._streaming_queue):
            self.scan_view.update_status(item.path, "ожидание")
            cancelled_paths.append(item.path)
        self._streaming_queue.clear()
        self._update_navigation_status()
        if cancelled_paths:
            self._show_toast(
                f"Отменено ожидание ({len(cancelled_paths)}): {reason}",
                "info",
                3500,
            )

    def _tick_streaming(self) -> None:
        """Driven by QTimer every READINESS_TICK_MS while streaming is active."""
        if not self._streaming_active:
            self._readiness_timer.stop()
            return
        if self._streaming_in_flight:
            # Only the dispatcher advances the queue when a job finishes.
            return

        now = time.monotonic()
        ready: _QueueItem | None = None
        for item in list(self._streaming_queue):
            if not os.path.isfile(item.path):
                self._fail_queued_item(
                    item, "файл исчез до начала конвертации"
                )
                self._streaming_queue.remove(item)
                continue
            try:
                stat = os.stat(item.path)
                sig: tuple[int, int] = (stat.st_size, stat.st_mtime_ns)
            except OSError as exc:
                self._fail_queued_item(
                    item, f"не удалось прочитать файл: {exc}"
                )
                self._streaming_queue.remove(item)
                continue
            if not os.access(item.path, os.R_OK):
                # File still locked by the writer - reset the counter so
                # we wait for the next tick rather than firing prematurely.
                item.stable_count = 0
                item.last_sig = sig
            elif item.last_sig == sig:
                item.stable_count += 1
            else:
                item.last_sig = sig
                item.stable_count = 1
            # Deadline: give up if the source never stabilises.
            if now - item.enqueued_at > READINESS_TIMEOUT_S:
                self._fail_queued_item(
                    item,
                    "не дождались стабилизации файла "
                    f"(>{int(READINESS_TIMEOUT_S)}s)",
                )
                self._streaming_queue.remove(item)
                continue
            if item.stable_count >= READINESS_STABLE_TICKS:
                ready = item
                break

        if ready is not None:
            self._streaming_queue.remove(ready)
            self._dispatch_ready_item(ready)
            return
        if not self._streaming_queue:
            self._readiness_timer.stop()
        self._update_navigation_status()

    def _dispatch_ready_item(self, item: _QueueItem) -> None:
        """Launch the next ready streaming job."""
        output_folder = (item.settings.get("output_folder") or "").strip() or None
        # Reserve an output path so concurrent jobs (and the manual
        # pipeline) never clobber each other on disk.
        try:
            output_path = naming.build_output_path(
                item.path, output_folder, reserved=self._output_reservations
            )
        except OSError as exc:
            self._fail_streaming_path(item.path, str(exc))
            # Try the next item instead of stalling the timer.
            if self._streaming_queue and not self._streaming_in_flight:
                # Re-arm so the loop continues on the next tick.
                self._readiness_timer.start()
            return
        self._output_reservations.add(output_path)
        self._streaming_in_flight = True
        self._update_navigation_status()
        # A single path, output_path pre-reserved.
        self._start_conversions(
            [item.path],
            from_streaming=True,
            preselected_output_path=output_path,
            settings_override=item.settings,
        )

    def _fail_queued_item(self, item: _QueueItem, reason: str) -> None:
        self.scan_view.update_status(item.path, "ошибка")
        self._show_toast(
            f"Стриминг: {os.path.basename(item.path)} - {reason}",
            "error",
            5000,
        )
        try:
            row_id = config.log_start(item.path, self._now_iso())
            if row_id and row_id > 0:
                config.log_finish(
                    row_id, "", self._now_iso(), "failed", reason
                )
        except Exception:
            pass

    def _fail_streaming_path(self, path: str, reason: str) -> None:
        self.scan_view.update_status(path, "ошибка")
        self._show_toast(
            f"Стриминг: {os.path.basename(path)} - {reason}",
            "error",
            5000,
        )

    def _advance_streaming(self) -> None:
        """Called after a streaming job settles; re-arms the readiness pump."""
        if not self._streaming_active:
            self._streaming_in_flight = False
            self._update_navigation_status()
            return
        if self._streaming_queue and not self._readiness_timer.isActive():
            self._readiness_timer.start()
        self._update_navigation_status()

    # ------------------------------------------------------------------
    # Drag-n-drop
    # ------------------------------------------------------------------
    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dragMoveEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        if not event.mimeData().hasUrls():
            return
        paths = []
        for url in event.mimeData().urls():
            local = url.toLocalFile()
            if local:
                paths.append(local)
            elif url.scheme().lower() in ("http", "https"):
                paths.append(url.toString())
        event.acceptProposedAction()
        if not paths:
            return
        expanded = expand_paths(paths)
        supported = [p for p in expanded if is_supported(p) or p.lower().startswith(("http://", "https://"))]
        rejected = [p for p in expanded if p not in supported]
        added = self.scan_view.add_paths(supported, mark_as_new=False)
        if rejected:
            exts = sorted({("?" + (p.rsplit(".", 1)[-1] if "." in p else "")) for p in rejected})
            preview = ", ".join(exts[:5])
            extra = "" if len(rejected) <= 5 else f" и ещё {len(rejected) - 5}"
            self._show_toast(
                f"Не поддерживаются ({len(rejected)}): {preview}{extra}",
                "error",
                5000,
            )
        if added:
            self.tabs.setCurrentIndex(0)

    # ------------------------------------------------------------------
    # Preview helpers
    # ------------------------------------------------------------------
    def _preview_existing(self, path: str) -> None:
        from pathlib import Path

        # Outputs may live in a configured folder and may have a collision
        # suffix. The conversion log is authoritative; the adjacent path is
        # retained as a fallback for results made by older app versions.
        recorded = config.get_latest_output(path)
        if recorded and os.path.isfile(recorded):
            md_path = Path(recorded)
        elif path.lower().startswith(("http://", "https://")):
            self._show_toast(
                "Для этого URL ещё нет готового результата.", "info"
            )
            return
        else:
            md_path = Path(path).with_suffix(".md")
        if md_path.exists():
            self.preview_view.load_file(str(md_path))
        else:
            self.preview_view.clear()
            self._show_toast(
                f"Файл {md_path.name} ещё не сконвертирован.", "info", 3500
            )
        self.tabs.setCurrentIndex(1)

    # ------------------------------------------------------------------
    # Toast helper
    # ------------------------------------------------------------------
    def _show_toast(self, text: str, kind: str = "info", timeout_ms: int = 3500) -> None:
        self.toast.show_message(text, kind, timeout_ms)

    def _show_centered_toast(self, text: str, kind: str = "info", timeout_ms: int = 4000) -> None:
        from .ui.toast_bar import TOAST_VARIANT_CENTER
        self.toast.show_message(
            text, kind, timeout_ms, variant=TOAST_VARIANT_CENTER
        )

    # ------------------------------------------------------------------
    # Window helpers
    # ------------------------------------------------------------------
    def _toggle_maximize(self) -> None:
        if self.isMaximized():
            self.showNormal()
            self.centralWidget().setContentsMargins(0, 0, 0, 0)
            self.title_bar.set_maximized(False)
            self._size_grip.show()
        else:
            self.showMaximized()
            self.centralWidget().setContentsMargins(6, 6, 6, 6)
            self.title_bar.set_maximized(True)
            self._size_grip.hide()

    def changeEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.type() == event.Type.WindowStateChange:
            self.title_bar.set_maximized(self.isMaximized())
            if self.isMaximized():
                self.centralWidget().setContentsMargins(6, 6, 6, 6)
                self._size_grip.hide()
            else:
                self.centralWidget().setContentsMargins(0, 0, 0, 0)
                self._size_grip.show()
        super().changeEvent(event)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API
        central = self.centralWidget()
        if central is not None and hasattr(self, "_size_grip"):
            grip_size = self._size_grip.sizeHint()
            self._size_grip.resize(grip_size)
            self._size_grip.move(
                max(0, central.width() - grip_size.width()),
                max(0, central.height() - grip_size.height()),
            )
            self._size_grip.raise_()
        if hasattr(self, "toast") and self.toast.isVisible():
            self.toast.reposition()
        super().resizeEvent(event)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt API
        # Stop polling for readiness BEFORE the thread pool is drained
        # so the dispatcher doesn't fire a new job mid-shutdown.
        if self._readiness_timer.isActive():
            self._readiness_timer.stop()
        self._cancel_streaming_queue(reason="приложение закрывается")
        self.watcher.stop()
        QThreadPool.globalInstance().waitForDone(2000)
        self.preview_view.cleanup()
        super().closeEvent(event)

    # ------------------------------------------------------------------
    @staticmethod
    def _now_iso() -> str:
        from datetime import datetime
        return datetime.utcnow().isoformat(timespec="seconds")

    def app(self):  # type: ignore[no-untyped-def]
        from PySide6.QtWidgets import QApplication
        return QApplication.instance()
