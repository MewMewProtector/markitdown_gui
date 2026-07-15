"""
Animated toast/notification bar that slides in from the top of a parent
widget and auto-hides after a timeout.
"""
from __future__ import annotations

from PySide6.QtCore import (
    QEasingCurve,
    QPropertyAnimation,
    QTimer,
    Qt,
)
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel


class ToastBar(QFrame):
    """Slide-down notification banner shown at the top of the main window."""

    DEFAULT_TIMEOUT_MS = 3500

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Toast")
        self.setFixedHeight(40)
        self.hide()

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 6, 14, 6)
        self.label = QLabel(self)
        layout.addWidget(self.label)

        self._anim = QPropertyAnimation(self, b"pos", self)
        self._anim.setDuration(220)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.hide_animated)

    # ------------------------------------------------------------------
    def show_message(
        self,
        text: str,
        kind: str = "info",
        timeout_ms: int = DEFAULT_TIMEOUT_MS,
    ) -> None:
        if kind == "error":
            self.setObjectName("Toast ToastError")
        else:
            self.setObjectName("Toast")
        # Re-polish to apply object-name style change.
        self.style().unpolish(self)
        self.style().polish(self)
        self.label.setText(text)
        self.show_animated()
        if timeout_ms > 0:
            self._hide_timer.start(timeout_ms)

    def show_animated(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            self.show()
            return
        end_y = parent.contentsRect().top() + 6
        x = parent.contentsRect().left() + 16
        width = max(280, min(640, parent.contentsRect().width() - 32))
        self.resize(width, self.height())
        start_pos = self.pos()
        end_pos = self.pos()
        end_pos.setX(x)
        end_pos.setY(end_y)
        self.move(start_pos.x(), end_pos.y() - self.height())
        self.show()
        self.raise_()
        self._anim.stop()
        self._anim.setStartValue(self.pos())
        self._anim.setEndValue(end_pos)
        self._anim.start()

    def hide_animated(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            self.hide()
            return
        end = self.pos()
        end.setY(end.y() - self.height())
        self._anim.stop()
        self._anim.setStartValue(self.pos())
        self._anim.setEndValue(end)
        self._anim.finished.connect(self.hide)
        self._anim.start()

    def paintEvent(self, event):  # noqa: N802 - Qt API
        # Default Qt styling is fine; this only exists to avoid a QPainter
        # warning on some Windows platforms when widgets are animated.
        painter = QPainter(self)
        painter.setPen(QColor(0, 0, 0, 0))
        painter.end()
        super().paintEvent(event)