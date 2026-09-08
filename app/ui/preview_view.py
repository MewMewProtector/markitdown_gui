"""Rendered and source Markdown preview with offline LaTeX support."""
from __future__ import annotations

import os
import platform
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QUrl, Qt
from PySide6.QtGui import QDesktopServices, QFont, QGuiApplication
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QButtonGroup,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..core.markdown_renderer import render_markdown_document
from .md_highlighter import MarkdownHighlighter


class _PreviewPage(QWebEnginePage):
    """Open clicked links externally instead of replacing the preview."""

    def acceptNavigationRequest(self, url, nav_type, is_main_frame):  # noqa: N802
        if (
            is_main_frame
            and nav_type == QWebEnginePage.NavigationType.NavigationTypeLinkClicked
        ):
            QDesktopServices.openUrl(url)
            return False
        return super().acceptNavigationRequest(url, nav_type, is_main_frame)


class PreviewView(QWidget):
    """Shows either rendered Markdown or the syntax-highlighted source."""

    VIEW_RENDERED = 0
    VIEW_SOURCE = 1

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)

        self._current_path: str | None = None
        self._current_md: str = ""
        self._render_palette = None
        self._preview_temp_path: str | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 12)
        root.setSpacing(12)

        self.page_title = QLabel("Предпросмотр", self)
        self.page_title.setObjectName("PageTitle")
        root.addWidget(self.page_title)
        subtitle = QLabel(
            "Переключайтесь между готовым документом и исходным Markdown.", self
        )
        subtitle.setObjectName("PageSubtitle")
        root.addWidget(subtitle)

        toolbar = QWidget(self)
        tlay = QHBoxLayout(toolbar)
        tlay.setContentsMargins(0, 0, 0, 0)
        tlay.setSpacing(8)

        self.lbl_name = QLabel("Файл не выбран", toolbar)
        self.lbl_name.setStyleSheet("font-weight: 650;")
        tlay.addWidget(self.lbl_name)

        self.lbl_meta = QLabel("", toolbar)
        self.lbl_meta.setObjectName("Muted")
        tlay.addWidget(self.lbl_meta)
        tlay.addStretch(1)

        self.view_group = QButtonGroup(self)
        self.view_group.setExclusive(True)
        self.btn_rendered = QPushButton("Документ", toolbar)
        self.btn_source = QPushButton("Исходник", toolbar)
        for index, button in enumerate((self.btn_rendered, self.btn_source)):
            button.setObjectName("SegmentButton")
            button.setCheckable(True)
            self.view_group.addButton(button, index)
            tlay.addWidget(button)
        self.btn_rendered.setChecked(True)
        self.view_group.idClicked.connect(self._set_view_mode)

        self.btn_copy = QPushButton("Копировать Markdown", toolbar)
        self.btn_copy.setObjectName("Primary")
        self.btn_copy.clicked.connect(self.copy_to_clipboard)
        self.btn_copy.setEnabled(False)

        self.btn_reveal = QPushButton("Показать файл", toolbar)
        self.btn_reveal.clicked.connect(self.reveal_in_explorer)
        self.btn_reveal.setEnabled(False)

        tlay.addWidget(self.btn_copy)
        tlay.addWidget(self.btn_reveal)
        root.addWidget(toolbar)

        self.content_stack = QStackedWidget(self)

        self.web_view = QWebEngineView(self.content_stack)
        self.web_view.setObjectName("RenderedPreview")
        self.web_view.setPage(_PreviewPage(self.web_view))
        settings = self.web_view.settings()
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True
        )
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, False
        )
        settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, False)
        self.content_stack.addWidget(self.web_view)

        self.editor = QPlainTextEdit(self.content_stack)
        self.editor.setReadOnly(True)
        font = QFont("Cascadia Code")
        if not font.exactMatch():
            font = QFont("Consolas")
        font.setStyleHint(QFont.StyleHint.Monospace)
        self.editor.setFont(font)
        self.highlighter = MarkdownHighlighter(self.editor.document())
        self.content_stack.addWidget(self.editor)

        root.addWidget(self.content_stack, 1)
        self._show_empty_document()
        self.destroyed.connect(lambda *_args: self._cleanup_temp())

    def load_file(self, path: str, content: str | None = None) -> None:
        self._current_path = path
        if content is None:
            try:
                with open(path, "r", encoding="utf-8") as file:
                    content = file.read()
            except OSError as exc:
                content = f"_Не удалось прочитать файл: {exc}_"
        self._current_md = content
        self.editor.setPlainText(content)
        self._refresh_meta()
        self._render_current()

    def clear(self) -> None:
        self._current_path = None
        self._current_md = ""
        self.editor.clear()
        self.lbl_name.setText("Файл не выбран")
        self.lbl_meta.setText("")
        self.btn_copy.setEnabled(False)
        self.btn_reveal.setEnabled(False)
        self._show_empty_document()

    def cleanup(self) -> None:
        """Remove the temporary rendered-preview document immediately."""
        self._cleanup_temp()

    def update_highlighter_palette(self, palette: dict) -> None:
        self.highlighter.set_palette(palette)

    def update_render_palette(self, palette) -> None:
        self._render_palette = palette
        if self._current_md:
            self._render_current()

    def _set_view_mode(self, index: int) -> None:
        self.content_stack.setCurrentIndex(index)

    def _render_current(self) -> None:
        base_url = ""
        if self._current_path:
            try:
                base_url = Path(self._current_path).resolve().parent.as_uri() + "/"
            except (OSError, ValueError):
                pass
        document = render_markdown_document(
            self._current_md,
            self._render_palette,
            base_url=base_url,
        )
        self._load_html(document)

    def _show_empty_document(self) -> None:
        document = render_markdown_document(
            "# Здесь появится результат\n\n"
            "После успешной конвертации документ откроется автоматически. "
            "LaTeX-формулы вроде `$x_i^2$` будут отрисованы с индексами и символами.",
            self._render_palette,
        )
        self._load_html(document)

    def _load_html(self, document: str) -> None:
        old_path = self._preview_temp_path
        handle = tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            suffix=".html",
            prefix="markitdown_gui_preview_",
            delete=False,
        )
        try:
            handle.write(document)
        finally:
            handle.close()
        self._preview_temp_path = handle.name
        self.web_view.load(QUrl.fromLocalFile(handle.name))
        if old_path and old_path != handle.name:
            try:
                os.unlink(old_path)
            except OSError:
                pass

    def _cleanup_temp(self) -> None:
        path = self._preview_temp_path
        self._preview_temp_path = None
        if path:
            try:
                os.unlink(path)
            except OSError:
                pass

    def _refresh_meta(self) -> None:
        if not self._current_path:
            return
        path = Path(self._current_path)
        try:
            stat = path.stat()
            size_str = self._human_size(stat.st_size)
            mtime = datetime.fromtimestamp(stat.st_mtime).strftime("%d.%m.%Y %H:%M")
            exists = True
        except OSError:
            size_str = "—"
            mtime = "—"
            exists = False

        self.lbl_name.setText(path.name)
        self.lbl_meta.setText(f"{size_str} · {mtime}")
        self.btn_copy.setEnabled(True)
        self.btn_reveal.setEnabled(exists)

    @staticmethod
    def _human_size(num: int) -> str:
        for unit in ("Б", "КБ", "МБ", "ГБ"):
            if num < 1024.0:
                return f"{num:3.1f} {unit}"
            num /= 1024.0
        return f"{num:.1f} ТБ"

    def copy_to_clipboard(self) -> None:
        QGuiApplication.clipboard().setText(self._current_md)

    def reveal_in_explorer(self) -> None:
        if not self._current_path:
            return
        path = os.path.abspath(self._current_path)
        if platform.system() == "Windows":
            subprocess.Popen(["explorer", "/select,", path])
        elif platform.system() == "Darwin":
            subprocess.Popen(["open", "-R", path])
        else:
            subprocess.Popen(["xdg-open", os.path.dirname(path)])


__all__ = ["PreviewView"]
