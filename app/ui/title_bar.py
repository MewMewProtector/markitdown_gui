"""
Custom frameless-window title bar (similar to MarkText) with drag,
minimize, maximize/restore, and close buttons.
"""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QPoint, QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QWidget,
)

if getattr(sys, "frozen", False):
    # Running inside a PyInstaller bundle: bundled data files live under
    # ``sys._MEIPASS`` (the temp directory the bootloader unpacks to).
    # The spec bundles ``app/assets/`` so the prefix is preserved.
    _BUNDLE_ROOT = Path(sys._MEIPASS) / "app"  # type: ignore[attr-defined]
else:
    # Normal source-tree run: assets sit next to the package.
    _BUNDLE_ROOT = Path(__file__).resolve().parent.parent

_ASSETS = _BUNDLE_ROOT / "assets"


def _render_svg_icon(name: str, color: str, size: int = 32) -> QIcon:
    """
    Load an SVG that uses ``currentColor`` and bake in a concrete color.

    ``QIcon(path)`` does not resolve ``currentColor`` against the parent's
    palette or stylesheet color, so SVG icons loaded that way are always
    drawn in the SVG's literal default (usually black). To keep the icons
    legible against any theme we substitute ``currentColor`` with the
    actual hex value of the requested color and re-render the SVG onto a
    ``QPixmap`` before wrapping it in a ``QIcon``.
    """
    svg_path = _ASSETS / f"{name}.svg"
    svg_text = svg_path.read_text(encoding="utf-8")
    svg_text = svg_text.replace("currentColor", color)
    pixmap = QPixmap(QSize(size, size))
    pixmap.loadFromData(svg_text.encode("utf-8"))
    return QIcon(pixmap)


_CLOSE_HOVER_COLOR = "#FFFFFF"


class TitleBar(QWidget):
    """Frameless title bar. Emits standard window commands via signals."""

    minimize_clicked = Signal()
    maximize_clicked = Signal()
    close_clicked = Signal()

    HEIGHT = 34

    def __init__(
        self,
        title: str = "MarkItDown GUI",
        icon: QIcon | None = None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setObjectName("TitleBar")
        self.setFixedHeight(self.HEIGHT)

        self._drag_pos: QPoint | None = None
        self._pressed = False
        self._is_maximized = False

        # Active icon colors. `apply_icon_palette` replaces them whenever
        # the theme changes so the icons always render in a legible color.
        self._icon_text_color: str = "#1A1F23"
        self._icon_text_muted_color: str = "#5C646B"

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 4, 0)
        layout.setSpacing(6)

        if icon is not None:
            self._icon = QLabel(self)
            self._icon.setPixmap(icon.pixmap(18, 18))
            self._icon.setFixedWidth(22)
            layout.addWidget(self._icon)

        self._title = QLabel(title, self)
        self._title.setObjectName("TitleBarApp")
        layout.addWidget(self._title)
        layout.addStretch(1)

        # Window control buttons (Min / Max / Close) with SVG icons.
        self.btn_min = self._mk_icon_btn(_render_svg_icon("minimize", self._icon_text_color), "Свернуть")
        self.btn_max = self._mk_icon_btn(_render_svg_icon("maximize", self._icon_text_color), "Развернуть")
        self.btn_close = self._mk_icon_btn(_render_svg_icon("close", self._icon_text_color), "Закрыть")

        self.btn_min.clicked.connect(self.minimize_clicked)
        self.btn_max.clicked.connect(self.maximize_clicked)
        self.btn_close.clicked.connect(self.close_clicked)

        layout.addWidget(self.btn_min)
        layout.addWidget(self.btn_max)
        layout.addWidget(self.btn_close)

    def _mk_icon_btn(self, icon: QIcon, tooltip: str) -> QPushButton:
        b = QPushButton(self)
        b.setIcon(icon)
        b.setIconSize(QSize(12, 12))
        b.setFixedSize(40, self.HEIGHT)
        b.setToolTip(tooltip)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        # Visually identical to siblings; danger style applied only on hover.
        b.setProperty("class", "TitleButton")
        b.setObjectName("TitleButton")
        return b

    def set_title(self, text: str) -> None:
        self._title.setText(text)

    def set_maximized(self, maximized: bool) -> None:
        self._is_maximized = maximized
        self.btn_max.setIcon(
            _render_svg_icon("restore", self._icon_text_color)
            if maximized
            else _render_svg_icon("maximize", self._icon_text_color)
        )
        self.btn_max.setToolTip("Восстановить" if maximized else "Развернуть")

    def apply_icon_palette(
        self,
        text_color: str,
        text_muted_color: str | None = None,
    ) -> None:
        """
        Recolor the window-control SVG icons for the active theme.

        ``text_color`` is the color used for the icons in their normal
        state, ``text_muted_color`` is reserved for future use (e.g.
        disabled state). All three buttons (minimize / maximize /
        close) plus the restore icon are re-rendered with the chosen
        color so they remain visible regardless of theme.
        """
        self._icon_text_color = text_color
        self._icon_text_muted_color = text_muted_color or text_color
        self.btn_min.setIcon(_render_svg_icon("minimize", self._icon_text_color))
        if self._is_maximized:
            self.btn_max.setIcon(_render_svg_icon("restore", self._icon_text_color))
            self.btn_max.setToolTip("Восстановить")
        else:
            self.btn_max.setIcon(_render_svg_icon("maximize", self._icon_text_color))
            self.btn_max.setToolTip("Развернуть")
        self.btn_close.setIcon(_render_svg_icon("close", self._icon_text_color))

    # ------------------------------------------------------------------
    # Drag behaviour
    # ------------------------------------------------------------------
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._pressed = True
            self._drag_pos = event.globalPosition().toPoint()

    def mouseMoveEvent(self, event):
        if not self._pressed or self._drag_pos is None:
            return
        window = self.window()
        if window.isMaximized():
            return
        delta = event.globalPosition().toPoint() - self._drag_pos
        self._drag_pos = event.globalPosition().toPoint()
        window.move(window.pos() + delta)

    def mouseReleaseEvent(self, event):
        self._pressed = False
        self._drag_pos = None

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.maximize_clicked.emit()