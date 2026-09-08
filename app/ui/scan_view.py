"""
Files tab: list of pending files with checkboxes, conversion triggers,
and `add files`/`add folder` buttons.

Each row is a `QStandardItem` with `Qt.CheckState` and a custom user role
holding the absolute file path. Signals are exposed so that the main window
can react to row activations (double-click → preview).
"""
from __future__ import annotations

import os
from typing import Iterable

from PySide6.QtCore import QModelIndex, Qt, Signal
from PySide6.QtGui import QColor, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QStyle,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from ..core.file_filter import (
    expand_paths,
    extension_of,
    filter_supported,
    is_supported,
)
from ..core.file_filter import is_http_url  # re-exported below
from ..core import config


class ScanView(QWidget):
    """The "Files" tab."""

    convert_requested = Signal(list)      # list[str] of selected paths
    preview_requested = Signal(str)       # abs path
    status_message = Signal(str, str)     # text, kind ("info" / "error")

    COL_NAME = 0
    COL_SIZE = 1
    COL_STATUS = 2

    PATH_ROLE = Qt.ItemDataRole.UserRole + 1
    IS_NEW_ROLE = Qt.ItemDataRole.UserRole + 2

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 12)
        root.setSpacing(14)

        # Page header ------------------------------------------------
        self.page_title = QLabel("Файлы", self)
        self.page_title.setObjectName("PageTitle")
        root.addWidget(self.page_title)
        subtitle = QLabel(
            "Добавляйте документы вручную или оставьте стриминг работать в фоне.",
            self,
        )
        subtitle.setObjectName("PageSubtitle")
        root.addWidget(subtitle)

        # Drop zone --------------------------------------------------
        drop_zone = QFrame(self)
        drop_zone.setObjectName("DropZone")
        drop_layout = QVBoxLayout(drop_zone)
        drop_layout.setContentsMargins(20, 14, 20, 14)
        drop_layout.setSpacing(6)

        drop_title = QLabel("＋  Перетащите файлы или папку", drop_zone)
        drop_title.setObjectName("DropTitle")
        drop_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        drop_layout.addWidget(drop_title)
        drop_hint = QLabel(
            "PDF, Office, изображения, текст, архивы, аудио и веб-ссылки",
            drop_zone,
        )
        drop_hint.setObjectName("Muted")
        drop_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        drop_layout.addWidget(drop_hint)

        add_actions = QHBoxLayout()
        add_actions.addStretch(1)

        self.btn_add_files = QPushButton("Добавить файлы…", self)
        self.btn_add_files.setObjectName("Primary")
        self.btn_add_files.clicked.connect(self._on_add_files)
        add_actions.addWidget(self.btn_add_files)

        self.btn_add_folder = QPushButton("Добавить папку…", self)
        self.btn_add_folder.clicked.connect(self._on_add_folder)
        add_actions.addWidget(self.btn_add_folder)
        add_actions.addStretch(1)
        drop_layout.addLayout(add_actions)
        root.addWidget(drop_zone)

        # Command bar -----------------------------------------------
        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)

        self.lbl_count = QLabel("Файлов: 0", self)
        self.lbl_count.setObjectName("StatusBadge")
        toolbar.addWidget(self.lbl_count)
        self.lbl_progress = QLabel("Очередь пуста", self)
        self.lbl_progress.setObjectName("Muted")
        toolbar.addWidget(self.lbl_progress)
        toolbar.addStretch(1)

        self.btn_select_all = QPushButton("Выбрать все", self)
        self.btn_select_all.clicked.connect(self.set_all_checked)
        toolbar.addWidget(self.btn_select_all)

        self.btn_clear = QPushButton("Очистить", self)
        self.btn_clear.setObjectName("Danger")
        self.btn_clear.clicked.connect(self.clear)
        toolbar.addWidget(self.btn_clear)

        root.addLayout(toolbar)

        # Tree view -------------------------------------------------
        self.model = QStandardItemModel(0, 3, self)
        self.model.setHorizontalHeaderLabels(["Файл", "Размер", "Статус"])
        self.tree = QTreeView(self)
        self.tree.setModel(self.model)
        self.tree.setAlternatingRowColors(True)
        self.tree.setSelectionBehavior(QTreeView.SelectionBehavior.SelectRows)
        self.tree.setEditTriggers(QTreeView.EditTrigger.NoEditTriggers)
        self.tree.doubleClicked.connect(self._on_double_clicked)
        header = self.tree.header()
        header.setSectionResizeMode(self.COL_NAME, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(self.COL_SIZE, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(self.COL_STATUS, QHeaderView.ResizeMode.ResizeToContents)
        root.addWidget(self.tree, 1)

        # Action row ------------------------------------------------
        action_row = QHBoxLayout()
        self.btn_convert = QPushButton("Преобразовать выбранные", self)
        self.btn_convert.setObjectName("Primary")
        self.btn_convert.clicked.connect(self._on_convert_clicked)
        action_row.addWidget(self.btn_convert)
        action_row.addStretch(1)
        root.addLayout(action_row)

        # Empty-state hint ------------------------------------------
        self._empty_hint = QLabel(
            "Очередь пока пуста.\nДобавленные документы появятся в этой таблице.",
            self.tree,
        )
        self._empty_hint.setObjectName("EmptyHint")
        self._empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_hint.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True
        )
        self._empty_hint.hide()

        self.model.rowsInserted.connect(self._update_empty_hint)
        self.model.rowsRemoved.connect(self._update_empty_hint)
        self.model.modelReset.connect(self._update_empty_hint)
        self.model.rowsInserted.connect(self._refresh_summary)
        self.model.rowsRemoved.connect(self._refresh_summary)
        self.model.modelReset.connect(self._refresh_summary)
        self.tree.installEventFilter(self)
        self._update_empty_hint()
        self._refresh_summary()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def add_paths(self, paths: Iterable[str], mark_as_new: bool = False) -> int:
        """Add unique paths. Returns count of newly added files."""
        added = 0
        for p in paths:
            if not p:
                continue
            if not (is_supported(p) or is_http_url(p)):
                continue
            if self._contains_path(p):
                continue
            self._append_row(p, status="новый" if mark_as_new else "ожидание")
            added += 1
        return added

    def add_paths_reporting_unsupported(self, paths: Iterable[str]) -> int:
        """
        Like `add_paths` but emits a status message listing the extensions
        of files that were rejected. Returns count of accepted files.
        """
        expanded = expand_paths(list(paths))
        accepted, rejected = filter_supported(expanded)
        added = self.add_paths(accepted, mark_as_new=False)
        if rejected:
            preview = ", ".join(sorted({extension_of(p) or "?" for p in rejected[:5]}))
            extra = "" if len(rejected) <= 5 else f" и ещё {len(rejected) - 5}"
            self.status_message.emit(
                f"Не поддерживаются: {preview}{extra}",
                "error",
            )
        return added

    def clear(self) -> None:
        self.model.removeRows(0, self.model.rowCount())

    def selected_paths(self) -> list[str]:
        result: list[str] = []
        for r in range(self.model.rowCount()):
            item = self.model.item(r, self.COL_NAME)
            if item and item.checkState() == Qt.CheckState.Checked:
                result.append(item.data(self.PATH_ROLE) or "")
        return [p for p in result if p]

    def set_all_checked(self, checked: bool = True) -> None:
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for r in range(self.model.rowCount()):
            item = self.model.item(r, self.COL_NAME)
            if item:
                item.setCheckState(state)

    def row_count(self) -> int:
        return self.model.rowCount()

    def update_status(self, path: str, status: str) -> None:
        for r in range(self.model.rowCount()):
            item = self.model.item(r, self.COL_NAME)
            if item and item.data(self.PATH_ROLE) == path:
                status_item = self.model.item(r, self.COL_STATUS)
                status_item.setText(status)
                status_item.setForeground(self._status_color(status))
                if status == "готово":
                    f = self.model.item(r, self.COL_NAME).font()
                    f.setBold(False)
                    self.model.item(r, self.COL_NAME).setFont(f)
                self._refresh_summary()
                return

    def remove_row(self, path: str) -> None:
        for r in range(self.model.rowCount()):
            item = self.model.item(r, self.COL_NAME)
            if item and item.data(self.PATH_ROLE) == path:
                self.model.removeRow(r)
                return

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _append_row(self, path: str, status: str = "ожидание") -> None:
        name_item = QStandardItem(os.path.basename(path))
        name_item.setCheckable(True)
        name_item.setCheckState(Qt.CheckState.Checked)
        name_item.setData(path, self.PATH_ROLE)
        name_item.setToolTip(path)

        if is_http_url(path):
            size_text = "URL"
        else:
            try:
                size_text = self._human_size(os.path.getsize(path))
            except OSError:
                size_text = "—"

        size_item = QStandardItem(size_text)
        size_item.setEditable(False)
        size_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        status_item = QStandardItem(status)
        status_item.setEditable(False)
        status_item.setForeground(self._status_color(status))

        self.model.appendRow([name_item, size_item, status_item])

    @staticmethod
    def _status_color(status: str) -> QColor:
        value = status.lower()
        if value == "готово" or value == "100%":
            return QColor("#239B63")
        if "ошиб" in value:
            return QColor("#D9534F")
        if "%" in value or "работ" in value:
            return QColor("#2D7FF9")
        if "нов" in value:
            return QColor("#A66DD4")
        return QColor("#727B84")

    def _refresh_summary(self, *_args) -> None:
        total = self.model.rowCount()
        active = 0
        failed = 0
        ready = 0
        for row in range(total):
            item = self.model.item(row, self.COL_STATUS)
            status = item.text().lower() if item else ""
            if status == "готово":
                ready += 1
            elif "ошиб" in status:
                failed += 1
            elif "%" in status or "работ" in status or "ожидан" in status:
                active += 1
        self.lbl_count.setText(f"Файлов: {total}")
        if not total:
            summary = "Очередь пуста"
        else:
            parts = []
            if active:
                parts.append(f"в процессе: {active}")
            if ready:
                parts.append(f"готово: {ready}")
            if failed:
                parts.append(f"ошибки: {failed}")
            summary = " · ".join(parts) if parts else "Готово к запуску"
        self.lbl_progress.setText(summary)

    @staticmethod
    def _human_size(num: int) -> str:
        for unit in ("Б", "КБ", "МБ", "ГБ"):
            if num < 1024.0:
                return f"{num:3.1f} {unit}"
            num /= 1024.0
        return f"{num:.1f} ТБ"

    def _contains_path(self, path: str) -> bool:
        for r in range(self.model.rowCount()):
            item = self.model.item(r, self.COL_NAME)
            if item and item.data(self.PATH_ROLE) == path:
                return True
        return False

    def _on_convert_clicked(self) -> None:
        paths = self.selected_paths()
        if not paths:
            self.status_message.emit("Ничего не выбрано для конвертации.", "info")
            return
        self.convert_requested.emit(paths)

    # ------------------------------------------------------------------
    # Pre-conversion confirmation
    # ------------------------------------------------------------------
    def confirm_conversion(self, paths: list[str]) -> bool:
        """
        If any path is an image, open a small dialog asking whether the LLM
        should be invoked to describe it. The checkbox in the dialog is
        synced with the global `describe_images` setting: toggling here
        writes back to the same row the Settings tab reads from.

        Returns True if the caller should proceed, False if the user picked
        «Отмена». When no image is present in the batch, the dialog is
        skipped entirely and True is returned.
        """
        if not any(_is_l1_image(p) for p in paths):
            return True

        dlg = QDialog(self)
        dlg.setWindowTitle("Описание картинок")
        dlg.setModal(True)

        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        label = QLabel(
            "Найдены изображения. Описать их через LLM?", dlg
        )
        label.setWordWrap(True)
        layout.addWidget(label)

        cb = QCheckBox("Описывать картинки (LLM)", dlg)
        cb.setChecked(config.get_setting("describe_images") == "1")
        layout.addWidget(cb)

        # Defensive hint: no API key set → warn the user that descriptions
        # won't be generated even if the box is checked.
        if cb.isChecked() and not config.get_setting("llm_api_key"):
            hint = QLabel(
                "Внимание: API-ключ LLM не задан — описания не будут добавлены.",
                dlg,
            )
            hint.setObjectName("Muted")
            hint.setWordWrap(True)
            layout.addWidget(hint)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel,
            parent=dlg,
        )
        # Russian labels on the action buttons.
        ok_btn = buttons.button(QDialogButtonBox.StandardButton.Ok)
        if ok_btn is not None:
            ok_btn.setText("Конвертировать")
        cancel_btn = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        if cancel_btn is not None:
            cancel_btn.setText("Отмена")
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        layout.addWidget(buttons)

        accepted = dlg.exec() == QDialog.DialogCode.Accepted
        if accepted:
            # Treat Cancel as a real cancellation. Previously merely toggling
            # the checkbox wrote to SQLite immediately, even if the user then
            # pressed «Отмена».
            config.set_setting(
                "describe_images", "1" if cb.isChecked() else "0"
            )
        return accepted

    # ------------------------------------------------------------------
    # Empty-state hint overlay
    # ------------------------------------------------------------------
    def _update_empty_hint(self, *_args) -> None:
        empty = self.model.rowCount() == 0
        self._empty_hint.setVisible(empty)
        if empty:
            self._reposition_hint()

    def _reposition_hint(self) -> None:
        if not self._empty_hint.isVisible():
            return
        # Center within the tree's viewport.
        vp = self.tree.viewport()
        hint = self._empty_hint.sizeHint()
        x = max(0, (vp.width() - hint.width()) // 2)
        y = max(0, (vp.height() - hint.height()) // 2)
        self._empty_hint.setGeometry(x, y, hint.width(), hint.height())

    def eventFilter(self, obj, event):  # noqa: N802 - Qt API
        if obj is self.tree and event.type() in (
            event.Type.Resize,
            event.Type.Show,
        ):
            self._reposition_hint()
        return super().eventFilter(obj, event)

    def _on_double_clicked(self, index: QModelIndex) -> None:
        if not index.isValid():
            return
        item = self.model.item(index.row(), self.COL_NAME)
        if item:
            path = item.data(self.PATH_ROLE)
            if path:
                self.preview_requested.emit(path)

    def _on_add_files(self) -> None:
        exts = " ".join(
            f"*.{ext}" for ext in sorted(_supported_ext_set())
        )
        path, _ = QFileDialog.getOpenFileNames(
            self,
            "Выберите файлы",
            "",
            f"Поддерживаемые ({exts});;Все файлы (*.*)",
        )
        if path:
            self.add_paths_reporting_unsupported(path)

    def _on_add_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Выберите папку")
        if folder:
            self.add_paths_reporting_unsupported([folder])


def _supported_ext_set() -> set[str]:
    # Local import to avoid a hard dependency on file_filter for callers.
    from ..core.file_filter import SUPPORTED_EXTS
    return SUPPORTED_EXTS


def _is_l1_image(path: str) -> bool:
    """True for the L1 image extensions covered by this iteration."""
    if not path or path.lower().startswith(("http://", "https://")):
        return False
    return extension_of(path) in {"jpg", "jpeg", "png"}
