"""
Animated toast/notification bar that slides in from the top of a parent
widget and auto-hides after a timeout.

The "center" variant is used for prominent confirmations like
"Настройки сохранены" — it shows a green check icon on the left, uses
the theme's accent color for its border, and stays on screen long
enough to read.
"""
from __future__ import annotations

from PySide6.QtCore import (
    QEasingCurve,
    QParallelAnimationGroup,
    QPropertyAnimation,
    QRectF,
    QSize,
    QTimer,
    Qt,
)
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QWidget,
)


# Visual variants — `center` is used for prominent confirmations
# (e.g. "Настройки сохранены"); `left` is the default for transient
# info/error toasts.
TOAST_VARIANT_LEFT = "left"
TOAST_VARIANT_CENTER = "center"


class _CheckIcon(QWidget):
    """
    Small widget that paints a green check inside a filled circle.
    Used as the leading icon for the centered "settings saved" toast.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(QSize(22, 22))

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(1, 1, self.width() - 2, self.height() - 2)
        # Filled green circle.
        painter.setBrush(QColor(34, 170, 88))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(rect)
        # White check stroke inside.
        pen = QPen(QColor(255, 255, 255))
        pen.setWidth(2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        # Two segments forming a check mark, inset slightly from the circle.
        r = rect.adjusted(4, 4, -4, -4)
        x0, y0 = r.left(), r.top()
        x1, y1 = r.right(), r.bottom()
        # Short leg of the check (~ 35% from left).
        short_x = x0 + (x1 - x0) * 0.32
        short_y = y0 + (y1 - y0) * 0.55
        # Tip (bottom).
        tip_x = x0 + (x1 - x0) * 0.48
        tip_y = y0 + (y1 - y0) * 0.78
        # Top-right end (~ 80% from left).
        long_x = x0 + (x1 - x0) * 0.82
        long_y = y0 + (y1 - y0) * 0.32
        painter.drawLine(int(short_x), int(short_y), int(tip_x), int(tip_y))
        painter.drawLine(int(tip_x), int(tip_y), int(long_x), int(long_y))
        painter.end()


class ToastBar(QFrame):
    """Notification banner shown below the title bar and faded out gently."""

    DEFAULT_TIMEOUT_MS = 3500
    # Center variant ("settings saved") gets a longer, comfortable read
    # time so the user can actually see the confirmation.
    CENTER_TIMEOUT_MS = 4000
    DEFAULT_TOP_OFFSET = 50
    SHOW_DURATION_MS = 220
    HIDE_DURATION_MS = 460

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Toast")
        self.setFixedHeight(44)
        self.hide()
        self._top_offset = self.DEFAULT_TOP_OFFSET
        self._animation_phase = "idle"

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 6, 16, 6)
        layout.setSpacing(10)
        # Leading icon (only shown for the center variant).
        self.icon = _CheckIcon(self)
        self.icon.hide()
        layout.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignVCenter)
        self.label = QLabel(self)
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.label, 1)

        # Position and opacity are animated together. A dedicated effect is
        # used instead of changing the window opacity, which would affect the
        # entire application rather than this notification only.
        self._opacity_effect = QGraphicsOpacityEffect(self)
        self._opacity_effect.setOpacity(1.0)
        self.setGraphicsEffect(self._opacity_effect)

        self._position_animation = QPropertyAnimation(self, b"pos", self)
        self._opacity_animation = QPropertyAnimation(
            self._opacity_effect, b"opacity", self
        )
        self._animation_group = QParallelAnimationGroup(self)
        self._animation_group.addAnimation(self._position_animation)
        self._animation_group.addAnimation(self._opacity_animation)
        self._animation_group.finished.connect(self._on_animation_finished)

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.hide_animated)

        # The toast can appear at the top-left or top-center depending on
        # `show_message(..., variant=...)`. The default stays top-left for
        # backward compatibility with existing callers.
        self._variant = TOAST_VARIANT_LEFT

    def set_top_offset(self, offset: int) -> None:
        """Place the toast this many pixels below the parent widget's top."""
        self._top_offset = max(0, int(offset))
        if self.isVisible():
            self.reposition()

    # ------------------------------------------------------------------
    def show_message(
        self,
        text: str,
        kind: str = "info",
        timeout_ms: int = DEFAULT_TIMEOUT_MS,
        variant: str = TOAST_VARIANT_LEFT,
    ) -> None:
        # A new message replaces the previous timeout and any in-progress
        # fade, so a stale timer cannot hide the new notification early.
        self._hide_timer.stop()
        self._animation_group.stop()
        self._variant = variant
        if kind == "error":
            self.setObjectName("ToastError")
            self.icon.hide()
        elif variant == TOAST_VARIANT_CENTER:
            # Distinct object-name so the stylesheet can give it a
            # more prominent look (rounded pill, slightly larger).
            self.setObjectName("ToastCenter")
            # Green check icon on the left.
            self.icon.show()
            # Center variant gets its own comfortable default if the
            # caller didn't pass an explicit timeout.
            if timeout_ms == self.DEFAULT_TIMEOUT_MS:
                timeout_ms = self.CENTER_TIMEOUT_MS
        else:
            self.setObjectName("Toast")
            self.icon.hide()
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
            self._opacity_effect.setOpacity(1.0)
            self.show()
            return
        x, end_y, width = self._target_geometry()
        self.resize(width, self.height())
        start_pos = self.pos()
        start_pos.setX(x)
        start_pos.setY(end_y - 12)
        end_pos = self.pos()
        end_pos.setX(x)
        end_pos.setY(end_y)
        self.move(start_pos)
        self._opacity_effect.setOpacity(0.0)
        self.show()
        self.raise_()
        self._animation_phase = "show"
        self._configure_animation(
            start_pos,
            end_pos,
            0.0,
            1.0,
            self.SHOW_DURATION_MS,
            QEasingCurve.Type.OutCubic,
        )
        self._animation_group.start()

    def hide_animated(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            self.hide()
            return
        # ``isVisible()`` is also false when an ancestor is temporarily
        # hidden (for example while the main window is being restored).
        # Only skip when this toast itself was explicitly hidden.
        if self.isHidden():
            return
        self._hide_timer.stop()
        self._animation_group.stop()
        start = self.pos()
        end = self.pos()
        end.setY(end.y() - 8)
        self._animation_phase = "hide"
        self._configure_animation(
            start,
            end,
            self._opacity_effect.opacity(),
            0.0,
            self.HIDE_DURATION_MS,
            QEasingCurve.Type.InOutCubic,
        )
        self._animation_group.start()

    def reposition(self) -> None:
        """Keep a visible toast anchored correctly after a window resize."""
        if self.parentWidget() is None:
            return
        x, y, width = self._target_geometry()
        self.resize(width, self.height())
        self.move(x, y)
        self.raise_()

    def _target_geometry(self) -> tuple[int, int, int]:
        parent = self.parentWidget()
        if parent is None:
            return self.x(), self.y(), self.width()
        rect = parent.contentsRect()
        end_y = rect.top() + self._top_offset
        if self._variant == TOAST_VARIANT_CENTER:
            width = max(360, min(560, rect.width() - 64))
            x = rect.left() + (rect.width() - width) // 2
        else:
            width = max(280, min(640, rect.width() - 32))
            x = rect.left() + 16
        return x, end_y, width

    def _configure_animation(
        self,
        start_pos,
        end_pos,
        start_opacity: float,
        end_opacity: float,
        duration: int,
        easing: QEasingCurve.Type,
    ) -> None:
        self._position_animation.setDuration(duration)
        self._position_animation.setEasingCurve(easing)
        self._position_animation.setStartValue(start_pos)
        self._position_animation.setEndValue(end_pos)
        self._opacity_animation.setDuration(duration)
        self._opacity_animation.setEasingCurve(easing)
        self._opacity_animation.setStartValue(start_opacity)
        self._opacity_animation.setEndValue(end_opacity)

    def _on_animation_finished(self) -> None:
        if self._animation_phase == "hide":
            self.hide()
            # Reset for the next show; the widget is already hidden, so this
            # cannot cause a flash.
            self._opacity_effect.setOpacity(1.0)
        self._animation_phase = "idle"

    def paintEvent(self, event):  # noqa: N802 - Qt API
        # Default Qt styling is fine; this only exists to avoid a QPainter
        # warning on some Windows platforms when widgets are animated.
        painter = QPainter(self)
        painter.setPen(QColor(0, 0, 0, 0))
        painter.end()
        super().paintEvent(event)
