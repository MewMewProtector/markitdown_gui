"""Modern left-side navigation for the main application window."""
from __future__ import annotations

import os

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)


class SideNavigation(QFrame):
    """A compact navigation rail with the small QTabBar API we rely on."""

    currentChanged = Signal(int)

    _ICONS = ("▤", "◫", "⚙", "↺")

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("Navigation")
        self.setFixedWidth(224)
        self._buttons: list[QPushButton] = []
        self._current_index = -1

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 18, 14, 16)
        root.setSpacing(8)

        brand = QHBoxLayout()
        brand.setSpacing(10)
        logo = QLabel("M↓", self)
        logo.setObjectName("NavLogo")
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo.setFixedSize(38, 38)
        brand.addWidget(logo)

        brand_text = QVBoxLayout()
        brand_text.setSpacing(0)
        title = QLabel("MarkItDown", self)
        title.setObjectName("NavBrand")
        subtitle = QLabel("Workspace", self)
        subtitle.setObjectName("Muted")
        brand_text.addWidget(title)
        brand_text.addWidget(subtitle)
        brand.addLayout(brand_text, 1)
        root.addLayout(brand)
        root.addSpacing(18)

        section = QLabel("РАБОЧАЯ ОБЛАСТЬ", self)
        section.setObjectName("NavSection")
        root.addWidget(section)

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        root.addSpacing(2)
        self._button_layout = QVBoxLayout()
        self._button_layout.setSpacing(4)
        root.addLayout(self._button_layout)
        root.addStretch(1)

        self.status_card = QFrame(self)
        self.status_card.setObjectName("NavStatus")
        status_layout = QVBoxLayout(self.status_card)
        status_layout.setContentsMargins(12, 10, 12, 10)
        status_layout.setSpacing(3)
        self.status_title = QLabel("○ Стриминг выключен", self.status_card)
        self.status_title.setObjectName("NavStatusTitle")
        self.status_detail = QLabel(
            "Автоматическая обработка неактивна", self.status_card
        )
        self.status_detail.setObjectName("Muted")
        self.status_detail.setWordWrap(True)
        status_layout.addWidget(self.status_title)
        status_layout.addWidget(self.status_detail)
        root.addWidget(self.status_card)

    def addTab(self, text: str) -> int:  # noqa: N802 - QTabBar compatibility
        index = len(self._buttons)
        icon = self._ICONS[index] if index < len(self._ICONS) else "•"
        button = QPushButton(f"{icon}   {text}", self)
        button.setObjectName("NavButton")
        button.setCheckable(True)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setProperty("navIndex", index)
        button.clicked.connect(lambda _checked=False, i=index: self.setCurrentIndex(i))
        self._group.addButton(button, index)
        self._button_layout.addWidget(button)
        self._buttons.append(button)
        if self._current_index < 0:
            self.setCurrentIndex(0)
        return index

    def count(self) -> int:
        return len(self._buttons)

    def currentIndex(self) -> int:  # noqa: N802 - QTabBar compatibility
        return self._current_index

    def setCurrentIndex(self, index: int) -> None:  # noqa: N802
        if not 0 <= index < len(self._buttons):
            return
        changed = index != self._current_index
        self._current_index = index
        self._buttons[index].setChecked(True)
        if changed:
            self.currentChanged.emit(index)

    def set_streaming_state(
        self,
        active: bool,
        *,
        queued: int = 0,
        in_flight: bool = False,
        folder: str = "",
    ) -> None:
        self.status_card.setProperty("active", active)
        self.status_card.style().unpolish(self.status_card)
        self.status_card.style().polish(self.status_card)
        if not active:
            self.status_title.setText("○ Стриминг выключен")
            self.status_detail.setText("Автоматическая обработка неактивна")
            return

        self.status_title.setText("● Стриминг активен")
        location = os.path.basename(os.path.normpath(folder)) if folder else "папка не выбрана"
        if in_flight:
            activity = "Идёт конвертация"
        elif queued:
            activity = "Ожидание готовности файла"
        else:
            activity = "Ожидание новых файлов"
        queue_text = f" · в очереди: {queued}" if queued else ""
        self.status_detail.setText(f"{activity}{queue_text}\n{location}")


__all__ = ["SideNavigation"]
