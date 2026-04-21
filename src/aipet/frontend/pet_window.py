"""Transparent, borderless Live2D pet window with system tray."""

from __future__ import annotations

import asyncio
import contextlib
import json
import sys
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QCursor, QIcon, QMouseEvent, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QGraphicsOpacityEffect,
    QLabel,
    QMenu,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from aipet.frontend.client import GatewayClient
from aipet.frontend.live2d_widget import Live2DWidget
from aipet.frontend.live_canvas import LiveCanvasWidget
from aipet.frontend.theme import MaterialTheme
from aipet.utils.paths import get_project_root

if TYPE_CHECKING:
    from aipet.frontend.chat_window import ChatWindow
    from aipet.frontend.provider_dialog import ProviderDialog


class SpeechBubble(QWidget):
    """Floating speech bubble that appears above the Live2D model."""

    clicked = Signal()
    hidden = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # Make it a top-level frameless window so it renders above the OpenGL widget
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)

        self.label = QLabel(self)
        self.label.setWordWrap(True)
        self.label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        bubble_bg = MaterialTheme.rgba(MaterialTheme.primary, 235)
        bubble_border = MaterialTheme.rgba(MaterialTheme.on_primary, 160)
        self.label.setStyleSheet(
            f"""
            QLabel {{
                background-color: {bubble_bg};
                color: {MaterialTheme.on_primary};
                border-radius: 16px;
                border: 2px solid {bubble_border};
                padding: 10px 14px;
                font-size: 14px;
                qproperty-alignment: AlignLeft AlignVCenter;
            }}
            """
        )
        self.label.setMaximumWidth(280)
        self.label.setMinimumWidth(80)

        self.arrow = QLabel(self)
        self.arrow.setFixedSize(16, 12)
        self.arrow.setStyleSheet(
            f"""
            QLabel {{
                background-color: transparent;
                border-left: 8px solid transparent;
                border-right: 8px solid transparent;
                border-top: 12px solid {MaterialTheme.rgba(MaterialTheme.primary, 235)};
            }}
            """
        )

        self._opacity_effect = QGraphicsOpacityEffect(self)
        self._opacity_effect.setOpacity(1.0)
        self.setGraphicsEffect(self._opacity_effect)

        self._fade_timer = QTimer(self)
        self._fade_timer.timeout.connect(self._fade_step)
        self._fade_value = 1.0

    def set_text(self, text: str) -> None:
        self.label.setText(text)
        # Force a reasonable width so Chinese text wraps horizontally
        self.label.setWordWrap(True)
        self.label.setFixedWidth(240)
        # Explicitly resize to the height needed for wrapped text
        self.label.resize(240, self.label.sizeHint().height())
        lw = self.label.width()
        lh = self.label.height()
        self.label.move(8, 8)
        self.arrow.move(8 + lw // 2 - 8, 8 + lh)
        self.resize(16 + lw, 20 + lh)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)

    def start_auto_hide(self, duration_ms: int = 6000) -> None:
        self._opacity_effect.setOpacity(1.0)
        self.show()
        self.raise_()
        QTimer.singleShot(duration_ms, self._start_fade)

    def _start_fade(self) -> None:
        self._fade_value = 1.0
        self._fade_timer.start(50)

    def _fade_step(self) -> None:
        if not self.isVisible():
            self._fade_timer.stop()
            return
        self._fade_value -= 0.05
        if self._fade_value <= 0:
            self._fade_timer.stop()
            self.hide()
            self.hidden.emit()
        else:
            self._opacity_effect.setOpacity(self._fade_value)


def _create_paw_icon() -> QIcon:
    """Draw a simple paw-print icon in memory."""
    size = 64
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)

    tint = QColor(MaterialTheme.primary)
    painter.setBrush(tint)

    # Main pad
    painter.drawEllipse(22, 24, 28, 26)
    # Toes
    painter.drawEllipse(10, 10, 14, 14)
    painter.drawEllipse(28, 4, 14, 14)
    painter.drawEllipse(46, 10, 14, 14)
    painter.drawEllipse(48, 28, 12, 12)

    painter.end()
    return QIcon(pixmap)


class PetWindow(QWidget):
    """Borderless, transparent Live2D desktop pet window."""

    def __init__(self, client: GatewayClient, parent: Any = None) -> None:
        super().__init__(parent)
        self.client = client
        self.setWindowTitle("AIPet")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAutoFillBackground(False)

        # Cover the entire primary screen so the model never gets clipped
        screen = self.screen()
        if screen is not None:
            geo = screen.availableGeometry()
            self.resize(geo.width(), geo.height())
            self.move(geo.x(), geo.y())
        else:
            self.resize(1920, 1080)
            self.move(0, 0)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.live2d_widget = Live2DWidget(parent=self)
        self.live2d_widget.right_clicked.connect(self._show_context_menu)
        self.live2d_widget.current_scale = 0.5
        layout.addWidget(self.live2d_widget)

        self._setup_tray()
        self._wire_events()

        self._canvases: dict[str, LiveCanvasWidget] = {}
        self._canvas_positions: dict[str, tuple[int, int]] = {}
        self.chat_window: ChatWindow | None = None
        self._provider_dialog: ProviderDialog | None = None
        self._lipsync_timer: QTimer | None = None
        self._lipsync_data: list[Any] = []
        self._lipsync_start_time: float = 0.0

        # Scale hint overlay
        self._scale_hint = QLabel(self)
        self._scale_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._scale_hint.setStyleSheet(
            f"background-color: {MaterialTheme.rgba(MaterialTheme.inverse_surface, 200)}; "
            f"color: {MaterialTheme.inverse_on_surface}; border-radius: 12px; "
            "padding: 6px 14px; font-size: 13px; font-weight: 500;"
        )
        self._scale_hint.hide()
        self._scale_hint_timer = QTimer(self)
        self._scale_hint_timer.setSingleShot(True)
        self._scale_hint_timer.timeout.connect(self._scale_hint.hide)
        self.live2d_widget.scale_changed.connect(self._show_scale_hint)

        # Non-activating on Windows
        if sys.platform == "win32":
            self._apply_win32_style()

        self._setup_mouse_passthrough_timer()
        self._load_position()

        # Live2D state reporter: send snapshot to Gateway every 3s
        self._state_report_timer = QTimer(self)
        self._state_report_timer.timeout.connect(self._send_live2d_state)
        self._state_report_timer.start(3000)

    def _show_scale_hint(self, scale: float) -> None:
        """Show a transient scale percentage overlay."""
        percent = int(scale * 100)
        self._scale_hint.setText(f"{percent}%")
        self._scale_hint.adjustSize()
        # Position at bottom-center of the window
        x = (self.width() - self._scale_hint.width()) // 2
        y = self.height() - self._scale_hint.height() - 40
        self._scale_hint.move(x, y)
        self._scale_hint.show()
        self._scale_hint.raise_()
        self._scale_hint_timer.start(1200)

    def _setup_tray(self) -> None:
        """Create system tray icon and menu."""
        self.tray_menu = QMenu(self)

        show_chat_action = QAction("Open Chat", self)
        show_chat_action.triggered.connect(self._show_chat)
        self.tray_menu.addAction(show_chat_action)

        manage_action = QAction("Manage Providers", self)
        manage_action.triggered.connect(self._show_provider_dialog)
        self.tray_menu.addAction(manage_action)

        self.tray_menu.addSeparator()

        # Model switcher submenu
        self._model_menu = QMenu("Switch Model", self)
        self._refresh_model_menu()
        self.tray_menu.addMenu(self._model_menu)

        self.tray_menu.addSeparator()

        show_hide_action = QAction("Show/Hide Pet", self)
        show_hide_action.triggered.connect(self._toggle_visibility)
        self.tray_menu.addAction(show_hide_action)

        quit_action = QAction("Quit", self)
        quit_action.triggered.connect(self._quit)
        self.tray_menu.addAction(quit_action)

        self.tray_icon = QSystemTrayIcon(self)
        self.tray_icon.setIcon(_create_paw_icon())
        self.tray_icon.setContextMenu(self.tray_menu)
        self.tray_icon.setToolTip("AIPet v2.0")
        self.tray_icon.activated.connect(self._on_tray_activated)
        self.tray_icon.show()

    def _refresh_model_menu(self) -> None:
        """Rebuild the model switcher submenu from scanned models."""
        self._model_menu.clear()
        models = self.live2d_widget.scan_models()
        current_path = Path(self.live2d_widget.model_path).resolve() if self.live2d_widget.model_path else None
        for name, path in models:
            action = QAction(name, self)
            action.setCheckable(True)
            if current_path and Path(path).resolve() == current_path:
                action.setChecked(True)
            action.triggered.connect(lambda checked=False, p=path: self._switch_model(p))
            self._model_menu.addAction(action)
        if not models:
            action = QAction("No models found", self)
            action.setEnabled(False)
            self._model_menu.addAction(action)

    def _switch_model(self, model_path: str) -> None:
        """Hot-switch to another Live2D model."""
        if self.live2d_widget.load_model(model_path):
            self._refresh_model_menu()
            model_name = Path(model_path).parent.name
            asyncio.ensure_future(self._notify_live2d_model(model_name))

    async def _notify_live2d_model(self, model_name: str) -> None:
        """Notify Gateway of the current Live2D model for tag injection."""
        try:
            await self.client.send({
                "type": "request",
                "method": "live2d.set_model",
                "payload": {"model_name": model_name},
            })
        except Exception as exc:
            print(f"[PetWindow] Failed to notify model change: {exc}")

    def _wire_events(self) -> None:
        self.client.on("live2d.expression", self._on_expression)
        self.client.on("live2d.motion", self._on_motion)
        self.client.on("live2d.pose", self._on_pose)
        self.client.on("live2d.emotion", self._on_emotion)
        self.client.on("live2d.prop", self._on_prop)
        self.client.on("tts.start", self._on_tts_start)
        self.client.on("tts.end", self._on_tts_end)
        self.client.on("chat.proactive", self._on_proactive)
        self.client.on("canvas.show", self._on_canvas_show)
        self.client.on("canvas.close", self._on_canvas_close)
        self.client.on("canvas.update", self._on_canvas_update)

    def _on_expression(self, payload: dict[str, Any]) -> None:
        self.live2d_widget.set_expression(payload.get("expression", ""))

    def _on_motion(self, payload: dict[str, Any]) -> None:
        self.live2d_widget.play_motion(
            payload.get("motion", ""), 0, priority=payload.get("priority", 3)
        )

    def _on_pose(self, payload: dict[str, Any]) -> None:
        self.live2d_widget.set_pose(
            payload.get("pose", ""), duration_ms=payload.get("duration_ms", 500)
        )

    def _on_emotion(self, payload: dict[str, Any]) -> None:
        self.live2d_widget.set_emotion(
            payload.get("emotion", ""), duration_ms=payload.get("duration_ms", 500)
        )

    def _on_prop(self, payload: dict[str, Any]) -> None:
        self.live2d_widget.set_prop(
            payload.get("prop", ""), duration_ms=payload.get("duration_ms", 500)
        )

    def get_live2d_state(self) -> dict[str, Any]:
        """Return current Live2D state snapshot for Gateway."""
        return self.live2d_widget.get_state_snapshot()

    def _send_live2d_state(self) -> None:
        """Periodically report current Live2D state to Gateway."""
        if not self.client.connected:
            return
        state = self.live2d_widget.get_state_snapshot()
        asyncio.ensure_future(
            self.client.send({
                "type": "request",
                "method": "live2d.state_report",
                "payload": state,
            })
        )

    def _on_tts_start(self, payload: dict[str, Any]) -> None:
        lipsync_data = payload.get("lipsync_data")
        if lipsync_data and isinstance(lipsync_data, list) and len(lipsync_data) > 0:
            self._start_lipsync_animation(lipsync_data)
        else:
            self.live2d_widget.set_lipsync(0.5)

    def _on_tts_end(self, payload: dict[str, Any]) -> None:
        self._stop_lipsync_animation()
        self.live2d_widget.stop_lipsync()

    def _start_lipsync_animation(self, lipsync_data: list[Any]) -> None:
        """Drive lip-sync using a pre-computed volume envelope."""
        self._stop_lipsync_animation()
        self._lipsync_data = lipsync_data
        self._lipsync_start_time = QTimer.currentTime().msec() if hasattr(QTimer, "currentTime") else 0
        # Fallback: use system time if QTimer.currentTime is not available
        import time
        self._lipsync_start_time = time.time() * 1000

        self._lipsync_timer = QTimer(self)
        self._lipsync_timer.timeout.connect(self._update_lipsync_frame)
        self._lipsync_timer.start(50)  # 20 FPS update
        # Set initial value immediately
        self._update_lipsync_frame()

    def _update_lipsync_frame(self) -> None:
        """Update the Live2D mouth openness based on playback time."""
        import time

        if not hasattr(self, "_lipsync_data") or not self._lipsync_data:
            return
        elapsed_ms = time.time() * 1000 - self._lipsync_start_time
        elapsed_sec = elapsed_ms / 1000.0

        # Find the closest keyframe
        data = self._lipsync_data
        value = 0.0
        for i, frame in enumerate(data):
            t, v = frame
            if elapsed_sec >= t:
                value = v
            else:
                # Interpolate between previous and current frame
                if i > 0:
                    prev_t, prev_v = data[i - 1]
                    if t > prev_t:
                        ratio = (elapsed_sec - prev_t) / (t - prev_t)
                        value = prev_v + (v - prev_v) * ratio
                break

        self.live2d_widget.set_lipsync(value)

    def _stop_lipsync_animation(self) -> None:
        """Stop the lip-sync timer."""
        if hasattr(self, "_lipsync_timer") and self._lipsync_timer is not None:
            self._lipsync_timer.stop()
            self._lipsync_timer.deleteLater()
            self._lipsync_timer = None
        if hasattr(self, "_lipsync_data"):
            self._lipsync_data = []


    def _on_proactive(self, payload: dict[str, Any]) -> None:
        """Handle proactive message by showing a canvas bubble."""
        content = payload.get("content", "")
        expression = payload.get("expression")
        motion = payload.get("motion")
        msg_id = payload.get("message_id", "")
        print(f"[PetWindow] Received chat.proactive: content={content[:30]!r}, expression={expression}, motion={motion}")

        if expression:
            print(f"[PetWindow] Setting expression: {expression}")
            self.live2d_widget.set_expression(expression)
        if motion:
            print(f"[PetWindow] Playing motion: {motion}")
            self.live2d_widget.play_motion(motion, 0, priority=3)

        # Show as a canvas bubble
        canvas_id = msg_id or f"proactive_{uuid.uuid4().hex[:8]}"
        self._show_canvas({
            "canvas_id": canvas_id,
            "canvas_type": "bubble",
            "data": {"text": content},
            "position": "head",
            "duration_ms": 6000,
            "click_action": "open_chat",
            "width": 280,
        })

    def _on_canvas_show(self, payload: dict[str, Any]) -> None:
        self._show_canvas(payload)

    def _on_canvas_close(self, payload: dict[str, Any]) -> None:
        canvas_id = payload.get("canvas_id", "")
        self._destroy_canvas(canvas_id)

    def _on_canvas_update(self, payload: dict[str, Any]) -> None:
        canvas_id = payload.get("canvas_id", "")
        widget = self._canvases.get(canvas_id)
        if widget:
            widget.update_data(payload.get("data", {}))
            # Recalculate position after content changes
            position = payload.get("position", "head")
            x, y = self._calculate_canvas_position(widget, position, payload.get("x"), payload.get("y"))
            widget.move(x, y)
            self._canvas_positions[canvas_id] = (x, y)

    def _show_canvas(self, payload: dict[str, Any]) -> None:
        """Create and position a LiveCanvasWidget."""
        canvas_id = payload.get("canvas_id", "")
        if not canvas_id:
            canvas_id = f"canvas_{uuid.uuid4().hex[:8]}"

        # Close existing canvas with same ID
        if canvas_id in self._canvases:
            self._destroy_canvas(canvas_id)

        canvas_type = payload.get("canvas_type", "bubble")
        data = payload.get("data", {})
        position = payload.get("position", "head")
        duration_ms = payload.get("duration_ms", 0)
        click_action = payload.get("click_action", "dismiss")
        width = payload.get("width", 280)

        widget = LiveCanvasWidget(canvas_id, canvas_type, data)
        widget.setProperty("click_action", click_action)
        widget.set_title(payload.get("title", ""), data.get("icon", ""))
        if canvas_type == "bubble":
            widget.adjustSize()
            widget.setMaximumWidth(280)
        else:
            widget.setFixedWidth(width)
        widget.closed.connect(self._on_canvas_widget_closed)
        widget.clicked.connect(self._on_canvas_widget_clicked)
        widget.size_changed.connect(self._on_canvas_size_changed)

        # Force layout calculation before reading size for positioning
        if widget.layout() is not None:
            widget.layout().activate()

        # Calculate position
        x, y = self._calculate_canvas_position(widget, position, payload.get("x"), payload.get("y"))
        widget.move(x, y)
        # Ensure styles are fully applied before the window becomes visible
        widget.ensurePolished()
        widget.show()
        widget.raise_()

        if duration_ms > 0:
            widget.start_auto_hide(duration_ms)

        self._canvases[canvas_id] = widget
        self._canvas_positions[canvas_id] = (x, y)
        print(f"[PetWindow] Canvas shown: id={canvas_id}, type={canvas_type}, pos=({x},{y})")

    def _calculate_canvas_position(
        self, widget: LiveCanvasWidget, position: str, custom_x: int | None, custom_y: int | None
    ) -> tuple[int, int]:
        """Calculate canvas position based on strategy."""
        screen = self.screen()
        scr = screen.availableGeometry() if screen else None

        if custom_x is not None and custom_y is not None:
            return custom_x, custom_y

        # Get model head position
        head_pos = self.live2d_widget.get_model_head_pos()
        global_head = self.live2d_widget.mapToGlobal(head_pos)

        if position == "head":
            x = global_head.x() - widget.width() // 2
            y = global_head.y() - widget.height() - 10
        elif position == "right":
            x = global_head.x() + 60
            y = global_head.y() - widget.height() // 2
        elif position == "left":
            x = global_head.x() - widget.width() - 60
            y = global_head.y() - widget.height() // 2
        else:
            x = global_head.x() - widget.width() // 2
            y = global_head.y() - widget.height() - 10

        # Clamp to screen bounds
        if scr:
            x = max(scr.left(), min(x, scr.right() - widget.width()))
            y = max(scr.top() - 20, min(y, scr.bottom() - widget.height()))

        # Avoid overlapping with existing canvases (simple vertical stacking)
        existing_rects = []
        for cid, pos in self._canvas_positions.items():
            if cid in self._canvases:
                w = self._canvases[cid]
                existing_rects.append((pos[0], pos[1], w.width(), w.height()))

        # Try shifting down if overlapping
        for _ in range(10):  # max 10 shifts
            overlap = False
            my_rect = (x, y, widget.width(), widget.height())
            for ex, ey, ew, eh in existing_rects:
                if self._rects_overlap(my_rect, (ex, ey, ew, eh)):
                    overlap = True
                    y = ey + eh + 8
                    if scr and y + widget.height() > scr.bottom():
                        y = scr.top() + 20
                    break
            if not overlap:
                break

        return x, y

    @staticmethod
    def _rects_overlap(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> bool:
        """Check if two rectangles overlap."""
        ax, ay, aw, ah = a
        bx, by, bw, bh = b
        return ax < bx + bw and ax + aw > bx and ay < by + bh and ay + ah > by

    def _on_canvas_size_changed(self, canvas_id: str) -> None:
        """Reposition canvas after its content size changes (e.g. deferred text layout)."""
        widget = self._canvases.get(canvas_id)
        if widget is None:
            return
        # Use stored position or default to head position
        pos = self._canvas_positions.get(canvas_id)
        x, y = self._calculate_canvas_position(
            widget, "head", pos[0] if pos else None, pos[1] if pos else None
        )
        widget.move(x, y)
        self._canvas_positions[canvas_id] = (x, y)

    def _on_canvas_widget_closed(self, canvas_id: str) -> None:
        self._destroy_canvas(canvas_id)

    def _on_canvas_widget_clicked(self, canvas_id: str) -> None:
        widget = self._canvases.get(canvas_id)
        if widget is None:
            return
        click_action = widget.property("click_action") or "dismiss"
        if click_action == "none":
            return
        if click_action == "open_chat":
            self._show_chat()
        self._destroy_canvas(canvas_id)

    def _destroy_canvas(self, canvas_id: str) -> None:
        widget = self._canvases.pop(canvas_id, None)
        if widget:
            widget.hide()
            widget.deleteLater()
        self._canvas_positions.pop(canvas_id, None)
        print(f"[PetWindow] Canvas destroyed: {canvas_id}")

    def _destroy_all_canvases(self) -> None:
        for canvas_id in list(self._canvases.keys()):
            self._destroy_canvas(canvas_id)

    def _show_context_menu(self) -> None:
        """Show the tray menu at the cursor position when right-clicking the pet."""
        self.tray_menu.exec(self.cursor().pos())

    def _show_chat(self) -> None:
        if self.chat_window is not None:
            try:
                self.chat_window.show()
                self.chat_window.raise_()
                self.chat_window.activateWindow()
                return
            except RuntimeError:
                self.chat_window = None

        from aipet.frontend.chat_window import ChatWindow

        self.chat_window = ChatWindow(self.client)
        self.chat_window.closed.connect(self._on_chat_window_closed)
        self.chat_window.show()
        self.chat_window.raise_()
        self.chat_window.activateWindow()

    def _on_chat_window_closed(self) -> None:
        # Window is hidden, not destroyed; keep reference for state preservation
        pass

    def _show_provider_dialog(self) -> None:
        if self._provider_dialog is not None:
            try:
                self._provider_dialog.show()
                self._provider_dialog.raise_()
                self._provider_dialog.activateWindow()
                return
            except RuntimeError:
                self._provider_dialog = None

        dialog = ProviderDialog(self.client, self)
        self._provider_dialog = dialog
        dialog.finished.connect(lambda: setattr(self, "_provider_dialog", None))
        if self.chat_window is not None:
            dialog.providers_changed.connect(
                lambda: asyncio.ensure_future(self.chat_window._load_providers())
            )
        asyncio.ensure_future(self._run_provider_dialog(dialog))

    async def _run_provider_dialog(self, dialog: ProviderDialog) -> None:
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
        await dialog._load_providers()

    def _toggle_visibility(self) -> None:
        if self.isVisible():
            self.hide()
            self._passthrough_timer.stop()
        else:
            self.show()
            self._passthrough_timer.start(50)

    def _quit(self) -> None:
        self._save_position()
        self._destroy_all_canvases()
        if hasattr(self, "_state_report_timer") and self._state_report_timer is not None:
            self._state_report_timer.stop()
        if hasattr(self, "_passthrough_timer") and self._passthrough_timer is not None:
            self._passthrough_timer.stop()
        self.live2d_widget.cleanup()
        # Actually close chat window on app quit
        if self.chat_window is not None:
            try:
                self.chat_window.force_close()
            except Exception:
                pass
            self.chat_window = None
        with contextlib.suppress(Exception):
            asyncio.ensure_future(self.client.disconnect())
        QApplication.quit()

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self._toggle_visibility()

    def _apply_win32_style(self) -> None:
        """Prevent window from stealing focus on Windows."""
        try:
            import ctypes

            hwnd = int(self.winId())
            user32 = ctypes.windll.user32
            GWL_EXSTYLE = -20
            WS_EX_NOACTIVATE = 0x08000000
            current = user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
            user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, current | WS_EX_NOACTIVATE)
            SWP_FRAMECHANGED = 0x0020
            SWP_NOMOVE = 0x0002
            SWP_NOSIZE = 0x0001
            SWP_NOZORDER = 0x0004
            user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_FRAMECHANGED)
        except Exception:
            pass

    def _setup_mouse_passthrough_timer(self) -> None:
        """Use a timer to dynamically enable Win32 click-through outside the model."""
        self._passthrough_timer = QTimer(self)
        self._passthrough_timer.timeout.connect(self._update_win32_passthrough)
        self._passthrough_timer.start(50)

    def _update_win32_passthrough(self) -> None:
        """Toggle WS_EX_TRANSPARENT based on whether cursor is over the model."""
        if sys.platform != "win32" or not self.isVisible():
            return
        if self.live2d_widget.is_dragging:
            self._set_window_transparent(False)
            return
        cursor_pos = QCursor.pos()
        local_pos = self.live2d_widget.mapFromGlobal(cursor_pos)
        hit = self.live2d_widget.hit_test(local_pos.x(), local_pos.y())
        self._set_window_transparent(not hit)

    def _set_window_transparent(self, transparent: bool) -> None:
        import ctypes

        hwnd = int(self.winId())
        user32 = ctypes.windll.user32
        GWL_EXSTYLE = -20
        WS_EX_TRANSPARENT = 0x00000020
        current = user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
        has_transparent = bool(current & WS_EX_TRANSPARENT)
        if transparent == has_transparent:
            return
        if transparent:
            user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, current | WS_EX_TRANSPARENT)
        else:
            user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, current & ~WS_EX_TRANSPARENT)
        SWP_FRAMECHANGED = 0x0020
        SWP_NOMOVE = 0x0002
        SWP_NOSIZE = 0x0001
        SWP_NOZORDER = 0x0004
        SWP_SHOWWINDOW = 0x0040
        user32.SetWindowPos(
            hwnd, 0, 0, 0, 0, 0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_FRAMECHANGED | SWP_SHOWWINDOW,
        )

    def _state_file(self) -> Path:
        return get_project_root() / "data" / "window_state.json"

    def _load_position(self) -> None:
        path = self._state_file()
        if not path.exists():
            return
        try:
            with open(path, encoding="utf-8") as f:
                state = json.load(f)
            x = state.get("x")
            y = state.get("y")
            if x is not None and y is not None:
                self.move(int(x), int(y))
            scale = state.get("scale")
            if scale is not None:
                self.live2d_widget.current_scale = float(scale)
        except Exception:
            pass

    def _save_position(self) -> None:
        path = self._state_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump({
                    "x": self.x(),
                    "y": self.y(),
                    "scale": self.live2d_widget.current_scale,
                }, f, indent=2)
        except Exception:
            pass

    def closeEvent(self, event: Any) -> None:
        self._save_position()
        self.live2d_widget.cleanup()
        event.accept()
