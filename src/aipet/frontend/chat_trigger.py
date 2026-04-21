"""A slim edge-docked trigger button to toggle the chat window.

This widget is a child of PetWindow (not a top-level window) so that it is
immune to multi-window Z-order issues on Windows.  PetWindow's mouse-
passthrough logic is extended to disable passthrough when the cursor is over
this button, letting it receive hover and click events normally.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Qt, QPropertyAnimation, QEasingCurve, QRect
from PySide6.QtGui import QPainter, QColor, QFont, QMouseEvent, QPaintEvent
from PySide6.QtWidgets import QWidget

from aipet.frontend.theme import MaterialTheme

if TYPE_CHECKING:
    from aipet.frontend.pet_window import PetWindow


class ChatTriggerButton(QWidget):
    """A vertical strip docked to the right edge of the PetWindow.

    * Collapsed: a 6 px coloured strip (always visible).
    * Expanded (hover): a 40 px pill with a chat icon.
    * Click: toggles the ChatWindow via the parent PetWindow.
    """

    _COLLAPSED_W = 6
    _EXPANDED_W = 40
    _HEIGHT = 120

    def __init__(self, pet_window: PetWindow) -> None:
        super().__init__(pet_window)
        self._pet = pet_window
        self._is_expanded = False
        self._hover_animation: QPropertyAnimation | None = None

        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMouseTracking(True)

        # Position at the right edge of the parent (PetWindow covers the screen)
        parent_w = pet_window.width()
        parent_h = pet_window.height()
        self.setGeometry(
            parent_w - self._COLLAPSED_W,
            parent_h // 2 - self._HEIGHT // 2,
            self._EXPANDED_W,
            self._HEIGHT,
        )

    # ------------------------------------------------------------------
    # Geometry helpers used by PetWindow
    # ------------------------------------------------------------------

    def hit_test_global(self, global_pos: Any) -> bool:
        """Return whether *global_pos* (QPoint) is inside this button."""
        return self.rect().contains(self.mapFromGlobal(global_pos))

    # ------------------------------------------------------------------
    # Mouse interaction
    # ------------------------------------------------------------------

    def enterEvent(self, event: Any) -> None:
        self._set_expanded(True)

    def leaveEvent(self, event: Any) -> None:
        self._set_expanded(False)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._toggle_chat()
        event.accept()

    def _toggle_chat(self) -> None:
        """Show or hide the ChatWindow."""
        chat = getattr(self._pet, "chat_window", None)
        if chat is not None and chat.isVisible():
            chat.hide()
        else:
            self._pet._show_chat()

    # ------------------------------------------------------------------
    # Expand / collapse animation
    # ------------------------------------------------------------------

    def _set_expanded(self, expanded: bool) -> None:
        if self._is_expanded == expanded:
            return
        self._is_expanded = expanded

        if self._hover_animation is not None:
            self._hover_animation.stop()

        target = self._EXPANDED_W if expanded else self._COLLAPSED_W
        self._hover_animation = QPropertyAnimation(self, b"geometry")
        self._hover_animation.setDuration(180)
        self._hover_animation.setEasingCurve(QEasingCurve.Type.OutCubic)

        geo = self.geometry()
        start_rect = QRect(geo.x(), geo.y(), geo.width(), geo.height())
        # Keep the right edge pinned; grow / shrink to the left
        end_x = geo.right() - target
        end_rect = QRect(end_x, geo.y(), target, geo.height())

        self._hover_animation.setStartValue(start_rect)
        self._hover_animation.setEndValue(end_rect)
        self._hover_animation.start()

    # ------------------------------------------------------------------
    # Drawing
    # ------------------------------------------------------------------

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = self.width()
        h = self.height()
        radius = w / 2.0

        # Background colour – use the primary colour at partial opacity
        bg = QColor(MaterialTheme.primary)
        bg.setAlpha(180 if self._is_expanded else 140)

        painter.setBrush(bg)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(0, 0, w, h, radius, radius)

        # Draw icon when expanded
        if self._is_expanded and w > 20:
            painter.setPen(QColor(MaterialTheme.on_primary))
            painter.setFont(QFont(MaterialTheme.font_family, 18))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "💬")

        painter.end()
