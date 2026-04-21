"""A slim edge-docked trigger button to toggle the chat window."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Qt, QTimer, QPropertyAnimation, QEasingCurve, QRect
from PySide6.QtGui import QPainter, QColor, QFont, QMouseEvent, QPaintEvent
from PySide6.QtWidgets import QApplication, QWidget

from aipet.frontend.theme import MaterialTheme

if TYPE_CHECKING:
    from aipet.frontend.pet_window import PetWindow


class ChatTriggerButton(QWidget):
    """A vertical strip docked to the right screen edge.

    * Collapsed: a 6 px coloured strip (always visible).
    * Expanded (hover): a 40 px pill with a chat icon.
    * Click: toggles the ChatWindow via the parent PetWindow.
    """

    _COLLAPSED_W = 6
    _EXPANDED_W = 40
    _HEIGHT = 120
    _OFFSET_FROM_EDGE = 0  # flush against the right edge

    def __init__(self, pet_window: PetWindow) -> None:
        super().__init__(None)
        self._pet = pet_window
        self._is_expanded = False
        self._hover_animation: QPropertyAnimation | None = None

        # Independent top-level tool window – stays on top.
        # WA_ShowWithoutActivating is used instead of WindowDoesNotAcceptFocus
        # because the latter causes Windows to ignore the window after other
        # top-level windows in the same process are activated/closed.
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setMouseTracking(True)

        self._reposition()

        # Re-position when the screen geometry changes (task-bar moves, DPI changes, etc.)
        app = QApplication.instance()
        if app is not None:
            app.primaryScreen().availableGeometryChanged.connect(self._reposition)

    # ------------------------------------------------------------------
    # Geometry / positioning
    # ------------------------------------------------------------------

    def _reposition(self) -> None:
        """Place the strip at the right edge of the primary screen."""
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        geo = screen.availableGeometry()
        x = geo.right() - self._COLLAPSED_W + self._OFFSET_FROM_EDGE
        y = geo.center().y() - self._HEIGHT // 2
        self.setGeometry(x, y, self._EXPANDED_W, self._HEIGHT)
        self.setFixedSize(self._EXPANDED_W, self._HEIGHT)

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
