"""
Log tab: table view of past conversions backed by SQLite.
Supports column filters, refresh, CSV export, and "clear log".
"""
from __future__ import annotations

import csv
from pathlib import Path

from PySide6.QtCore import QSortFilterProxyModel, Qt
from PySide6.QtGui import QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
    QFileDialog,
)

from ..core import config


COLUMNS = [
    ("ID", "id"),
    ("Источник", "source_path"),
    ("Результат", "output_path"),
    ("Статус", "status"),
    ("Начало", "started_at"),
    ("Конец", "finished_at"),
    ("Ошибка", "error"),
]


class LogView(QWidget):
    """Shows recent conversion entries."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        # Toolbar ---------------------------------------------------
        toolbar = QHBoxLayout()

        self.filter_edit = QLineEdit(self)
        self.filter_edit.setPlaceholderText("Фильтр…")
        self.filter_edit.textChanged.connect(self._on_filter_changed)
        toolbar.addWidget(self.filter_edit, 1)

        self.btn_refresh = QPushButton("Обновить", self)
        self.btn_refresh.clicked.connect(self.refresh)
        toolbar.addWidget(self.btn_refresh)

        self.btn_export = QPushButton("Экспорт CSV", self)
        self.btn_export.clicked.connect(self.export_csv)
        toolbar.addWidget(self.btn_export)

        self.btn_clear = QPushButton("Очистить", self)
        self.btn_clear.setObjectName("Danger")
        self.btn_clear.clicked.connect(self._on_clear)
        toolbar.addWidget(self.btn_clear)

        root.addLayout(toolbar)

        # Table -----------------------------------------------------
        self.model = QStandardItemModel(0, len(COLUMNS), self)
        self.model.setHorizontalHeaderLabels([c[0] for c in COLUMNS])

        self.proxy = QSortFilterProxyModel(self)
        self.proxy.setSourceModel(self.model)
        self.proxy.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.proxy.setFilterKeyColumn(-1)

        self.table = QTableView(self)
        self.table.setModel(self.proxy)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableView.EditTrigger.NoEditTriggers)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(6, QHeaderView.ResizeMode.Stretch)
        root.addWidget(self.table, 1)

        self.lbl_summary = QLabel("", self)
        self.lbl_summary.setObjectName("Muted")
        root.addWidget(self.lbl_summary)

        self.refresh()

    # ------------------------------------------------------------------
    def refresh(self) -> None:
        rows = config.get_log_rows(limit=1000)
        self.model.removeRows(0, self.model.rowCount())
        for row in rows:
            items = []
            for label, key in COLUMNS:
                value = str(row.get(key, "") or "")
                item = QStandardItem(value)
                item.setEditable(False)
                if key == "status":
                    if value == "ok":
                        item.setForeground(Qt.GlobalColor.darkGreen)
                    elif value in ("failed", "error"):
                        item.setForeground(Qt.GlobalColor.red)
                    else:
                        item.setForeground(Qt.GlobalColor.darkYellow)
                items.append(item)
            self.model.appendRow(items)
        self.lbl_summary.setText(f"Записей: {len(rows)}")

    def _on_filter_changed(self, text: str) -> None:
        self.proxy.setFilterFixedString(text)

    def _on_clear(self) -> None:
        config.clear_log()
        self.refresh()

    def export_csv(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Экспорт лога",
            "conversion_log.csv",
            "CSV (*.csv)",
        )
        if not path:
            return
        rows = config.get_log_rows(limit=10000)
        with open(path, "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([c[0] for c in COLUMNS])
            for r in rows:
                writer.writerow([r.get(key, "") for _label, key in COLUMNS])