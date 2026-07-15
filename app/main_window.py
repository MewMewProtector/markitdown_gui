"""
Main application window: frameless, custom title bar, four tabs, drag-n-drop,
file watcher, and conversion orchestration.
"""
from __future__ import annotations

import os

from PySide6.QtCore import QPoint, Qt, QThreadPool
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
    QStackedWidget,
    QTabBar,
    QVBoxLayout,
    QWidget,
)

from .core import config
from .core.conversion_job import ConversionJob
from .core.file_filter import expand_paths, is_supported
from .core.watcher import FolderWatcher
from .ui.log_view import LogView
from .ui.preview_view import PreviewView
from .ui.scan_view import ScanView
from .ui.settings_view import SettingsView
from .ui.theme import (
    apply_theme,
)
from .ui.title_bar import TitleBar
from .ui.toast_bar import ToastBar


APP_TITLE = "MarkItDown GUI"


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

        self._build_ui()
        self._wire_signals()
        self._apply_initial_theme()
        self._maybe_prompt_watch_folder()
        self._restore_last_session()

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

        # Body: tab bar + stacked content.
        body = QWidget(central)
        bl = QVBoxLayout(body)
        bl.setContentsMargins(12, 12, 12, 12)
        bl.setSpacing(8)
        outer.addWidget(body, 1)

        self.tabs = QTabBar(body)
        self.tabs.setExpanding(False)
        self.tabs.setDrawBase(False)
        self.tabs.addTab("Файлы")
        self.tabs.addTab("Предпросмотр")
        self.tabs.addTab("Настройки")
        self.tabs.addTab("Лог")
        bl.addWidget(self.tabs)

        self.stack = QStackedWidget(body)
        self.scan_view = ScanView(self.stack)
        self.preview_view = PreviewView(self.stack)
        self.settings_view = SettingsView(self.stack)
        self.log_view = LogView(self.stack)
        for w in (self.scan_view, self.preview_view, self.settings_view, self.log_view):
            self.stack.addWidget(w)
        bl.addWidget(self.stack, 1)

        self.setCentralWidget(central)

        # Toast.
        self.toast = ToastBar(self)

        # Empty-state label is owned by ScanView but rendered by us when empty.
        self.tabs.setCurrentIndex(0)

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
        Without this distinction, changing the accent would re-resolve
        "system" against the *current* OS color scheme and could visibly
        switch the theme even though the user only wanted a colour tweak.
        """
        self._current_accent = accent_hex or ""
        self._current_accent_dark = accent_dark_hex or ""
        self._apply_theme(
            self._current_mode,
            persist=False,
            accent=accent_hex or None,
            accent_dark=accent_dark_hex or None,
        )

    def _on_settings_saved(self) -> None:
        """
        Fired by SettingsView AFTER settings were persisted to SQLite.
        Shows a prominent centered toast so the user knows the click took
        effect (not when the button was pressed, but when the writes
        actually completed).
        """
        self._show_centered_toast("Настройки сохранены", timeout_ms=2200)
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
        # Recolor the window-control icons so they remain legible against
        # the active title-bar background.
        self.title_bar.apply_icon_palette(
            text_color=self._current_palette.text,
            text_muted_color=self._current_palette.text_muted,
        )
        if persist:
            # Only the theme flag is persisted here; the user must press
            # the explicit "Сохранить" button in Settings to save accents.
            config.set_setting("theme", mode)
            self._current_mode = mode

    # ------------------------------------------------------------------
    # Watch folder
    # ------------------------------------------------------------------
    def _maybe_prompt_watch_folder(self) -> None:
        watch = config.get_setting("watch_folder")
        if not watch:
            self._show_toast(
                "Выберите папку сканирования во вкладке «Настройки».", "info", 6000
            )
            self.tabs.setCurrentIndex(2)
            return
        self.watcher.set_root(watch)

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

    def reload_watch_folder(self) -> None:
        watch = config.get_setting("watch_folder")
        if watch:
            self.watcher.set_root(watch)
            self._show_toast(f"Слежение за папкой: {watch}", "info", 4000)

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
            self.settings_view.load()
            # If user just saved, watcher may need refreshing.
            self.reload_watch_folder()
        # Persist active tab so it can be restored on next launch.
        config.set_setting("last_tab", str(index))

    # ------------------------------------------------------------------
    # Conversion
    # ------------------------------------------------------------------
    def _start_conversions(self, paths: list[str]) -> None:
        # Refresh settings (the pre-conversion dialog may have just toggled
        # `describe_images`, and we also want to pick up the latest LLM
        # endpoint fields).
        self._settings = config.get_all_settings()

        # If the batch contains at least one image, ask the user whether
        # the LLM should describe it. The dialog updates the global flag
        # directly, so reading settings again here would be a no-op — but
        # we still refresh above for the other LLM fields.
        if not self.scan_view.confirm_conversion(paths):
            return

        output_folder = self._settings.get("output_folder", "").strip() or None

        for path in paths:
            self._job_counter += 1
            job_id = self._job_counter
            job = ConversionJob(
                job_id=job_id,
                source_path=path,
                output_folder=output_folder,
                settings=self._settings,
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

            self.scan_view.update_status(path, "в работе…")
            QThreadPool.globalInstance().start(job)

    def _on_job_started(self, job_id: int, source_path: str) -> None:
        self.scan_view.update_status(source_path, "в работе…")

    def _on_job_progress(self, job_id: int, source_path: str, percent: int) -> None:
        self.scan_view.update_status(source_path, f"{percent}%")

    def _on_job_finished(self, job_id: int, source_path: str, output_path: str) -> None:
        self.scan_view.update_status(source_path, "готово")
        self._show_toast(f"Готово: {source_path}", "info", 2500)
        job = self._active_jobs.pop(job_id, None)
        if job is not None:
            row_id = getattr(job, "_log_row_id", -1)
            warning_text = getattr(job, "_warning_text", "") or ""
            if row_id and row_id > 0:
                config.log_finish(
                    row_id, output_path, self._now_iso(), "ok", warning_text
                )
        # Load into preview tab.
        self.preview_view.load_file(output_path)
        self.tabs.setCurrentIndex(1)
        # Mark in the settings file as a one-time update for last previewed path.
        config.set_setting("last_preview", output_path)
        # Refresh log in case it is already opened.
        self.log_view.refresh()

    def _on_job_warning(self, job_id: int, source_path: str, warning_text: str) -> None:
        """
        Non-fatal warning from a successful conversion — currently used to
        surface image-description failures. We stash the warning on the
        job so the final `_on_job_finished` can prepend it to the log
        row's error field, then toast the first line so the user notices.
        """
        job = self._active_jobs.get(job_id)
        if job is not None:
            # Stash for `_on_job_finished` to pick up.
            existing = getattr(job, "_warning_text", "") or ""
            if existing:
                warning_text = f"{existing}; {warning_text}"
            job._warning_text = warning_text  # type: ignore[attr-defined]
        first_line = warning_text.split(";")[0].strip()
        self._show_toast(
            f"Описания картинок: {first_line}", "error", 6000
        )

    def _on_job_failed(self, job_id: int, source_path: str, error: str) -> None:
        self.scan_view.update_status(source_path, "ошибка")
        self._show_toast(f"Ошибка: {source_path}", "error", 5000)
        job = self._active_jobs.pop(job_id, None)
        if job is not None:
            row_id = getattr(job, "_log_row_id", -1)
            if row_id and row_id > 0:
                # First line only in the DB; full trace available in console.
                short_err = error.splitlines()[0] if error else ""
                config.log_finish(row_id, "", self._now_iso(), "failed", short_err)
        self.log_view.refresh()

    # ------------------------------------------------------------------
    # Watcher → UI
    # ------------------------------------------------------------------
    def _on_new_files(self, paths: list[str]) -> None:
        added = self.scan_view.add_paths(paths, mark_as_new=True)
        if added:
            self._show_toast(
                f"Новых файлов: {added}. Отмечены галочками.", "info", 3500
            )

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
        event.acceptProposedAction()
        if not paths:
            return
        expanded = expand_paths(paths)
        # Supported ones go into the list, the rest get a toast.
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
        if path.lower().startswith(("http://", "https://")):
            self._show_toast("URL нельзя открыть в предпросмотре.", "info")
            return
        from pathlib import Path
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

    def _show_centered_toast(self, text: str, kind: str = "info", timeout_ms: int = 2200) -> None:
        """Prominent top-centered toast used for confirmations."""
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
            # Restore small margins so rounded corners aren't clipped.
            self.centralWidget().setContentsMargins(0, 0, 0, 0)
            self.title_bar.set_maximized(False)
        else:
            self.showMaximized()
            # Hide 6 px so Windows doesn't crop rounded corners.
            self.centralWidget().setContentsMargins(6, 6, 6, 6)
            self.title_bar.set_maximized(True)

    def changeEvent(self, event) -> None:  # noqa: N802 - Qt API
        # When maximized, restore the 6px inset; when restored, clear it.
        if event.type() == event.Type.WindowStateChange:
            self.title_bar.set_maximized(self.isMaximized())
            if self.isMaximized():
                self.centralWidget().setContentsMargins(6, 6, 6, 6)
            else:
                self.centralWidget().setContentsMargins(0, 0, 0, 0)
        super().changeEvent(event)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt API
        self.watcher.stop()
        QThreadPool.globalInstance().waitForDone(2000)
        super().closeEvent(event)

    # ------------------------------------------------------------------
    @staticmethod
    def _now_iso() -> str:
        from datetime import datetime
        return datetime.utcnow().isoformat(timespec="seconds")

    def app(self):  # type: ignore[no-untyped-def]
        from PySide6.QtWidgets import QApplication
        return QApplication.instance()