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


# Visual variants — `center` is used for prominent confirmations
# (e.g. "Настройки сохранены"); `left` is the default for transient
# info/error toasts.
TOAST_VARIANT_LEFT = "left"
TOAST_VARIANT_CENTER = "center"


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
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.label)

        self._anim = QPropertyAnimation(self, b"pos", self)
        self._anim.setDuration(220)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.hide_animated)

        # The toast can appear at the top-left or top-center depending on
        # `show_message(..., variant=...)`. The default stays top-left for
        # backward compatibility with existing callers.
        self._variant = TOAST_VARIANT_LEFT

    # ------------------------------------------------------------------
    def show_message(
        self,
        text: str,
        kind: str = "info",
        timeout_ms: int = DEFAULT_TIMEOUT_MS,
        variant: str = TOAST_VARIANT_LEFT,
    ) -> None:
        self._variant = variant
        if kind == "error":
            self.setObjectName("Toast ToastError")
        elif variant == TOAST_VARIANT_CENTER:
            # Distinct object-name so the stylesheet can give it a
            # more prominent look (rounded pill, slightly larger).
            self.setObjectName("Toast ToastCenter")
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

        if self._variant == TOAST_VARIANT_CENTER:
            # A wider, horizontally centered pill near the top edge.
            width = max(360, min(560, parent.contentsRect().width() - 64))
            x = parent.contentsRect().left() + (
                parent.contentsRect().width() - width
            ) // 2
        else:
            width = max(280, min(640, parent.contentsRect().width() - 32))
            x = parent.contentsRect().left() + 16

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