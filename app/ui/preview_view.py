"""
Preview tab: shows the raw Markdown of the last successfully converted file
with syntax highlighting, plus a small toolbar with metadata and quick
actions (Copy / Reveal in Explorer).
"""
from __future__ import annotations

import os
import platform
import subprocess
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QGuiApplication
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .md_highlighter import MarkdownHighlighter


class PreviewView(QWidget):
    """Displays a Markdown file in a read-only editor."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)

        self._current_path: str | None = None
        self._current_md: str = ""

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        # --- Toolbar ---------------------------------------------------
        toolbar = QWidget(self)
        tlay = QHBoxLayout(toolbar)
        tlay.setContentsMargins(0, 0, 0, 0)
        tlay.setSpacing(8)

        self.lbl_name = QLabel("Файл не выбран", toolbar)
        self.lbl_name.setStyleSheet("font-weight: 600;")
        tlay.addWidget(self.lbl_name)

        self.lbl_meta = QLabel("", toolbar)
        self.lbl_meta.setObjectName("Muted")
        tlay.addWidget(self.lbl_meta)
        tlay.addStretch(1)

        self.btn_copy = QPushButton("Копировать", toolbar)
        self.btn_copy.setObjectName("Primary")
        self.btn_copy.clicked.connect(self.copy_to_clipboard)
        self.btn_copy.setEnabled(False)

        self.btn_reveal = QPushButton("Открыть в проводнике", toolbar)
        self.btn_reveal.clicked.connect(self.reveal_in_explorer)
        self.btn_reveal.setEnabled(False)

        tlay.addWidget(self.btn_copy)
        tlay.addWidget(self.btn_reveal)
        root.addWidget(toolbar)

        # --- Editor ---------------------------------------------------
        self.editor = QPlainTextEdit(self)
        self.editor.setReadOnly(True)
        font = QFont("JetBrains Mono")
        if not font.exactMatch():
            font = QFont("Consolas")
        font.setStyleHint(QFont.StyleHint.Monospace)
        self.editor.setFont(font)
        self.highlighter = MarkdownHighlighter(self.editor.document())
        root.addWidget(self.editor, 1)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def load_file(self, path: str, content: str | None = None) -> None:
        self._current_path = path
        if content is None:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    content = f.read()
            except OSError as exc:
                content = f"_Не удалось прочитать файл: {exc}_"
        self._current_md = content
        self.editor.setPlainText(content)
        self._refresh_meta()

    def clear(self) -> None:
        self._current_path = None
        self._current_md = ""
        self.editor.clear()
        self.lbl_name.setText("Файл не выбран")
        self.lbl_meta.setText("")
        self.btn_copy.setEnabled(False)
        self.btn_reveal.setEnabled(False)

    def update_highlighter_palette(self, palette: dict) -> None:
        self.highlighter.set_palette(palette)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _refresh_meta(self) -> None:
        if not self._current_path:
            return
        path = Path(self._current_path)
        try:
            stat = path.stat()
            size = stat.st_size
            size_str = self._human_size(size)
            mtime = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
        except OSError:
            size_str = "—"
            mtime = "—"

        self.lbl_name.setText(path.name)
        self.lbl_meta.setText(f"{size_str} · {mtime}")
        self.btn_copy.setEnabled(True)
        self.btn_reveal.setEnabled(True)

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