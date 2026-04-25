"""Live Canvas floating panel system for the Live2D pet window.

Supports multiple canvas types: bubble, card, image, list, code, rich.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, Qt, QTimer, Signal
from PySide6.QtGui import QFont, QPixmap, QTextDocument
from PySide6.QtWidgets import (
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from aipet.frontend.theme import MaterialTheme


class LiveCanvasWidget(QWidget):
    """A single floating canvas panel that renders various content types."""

    clicked = Signal(str)  # canvas_id
    closed = Signal(str)  # canvas_id
    size_changed = Signal(str)  # canvas_id

    def __init__(
        self,
        canvas_id: str,
        canvas_type: str,
        data: dict[str, Any],
        parent: QWidget | None = None,
        max_content_height: int = 400,
    ) -> None:
        super().__init__(parent)
        self.canvas_id = canvas_id
        self.canvas_type = canvas_type
        self._data = data
        self._max_content_height = max_content_height
        self._fade_timer: QTimer | None = None
        self._fade_value = 1.0

        # Opacity effect for unified fade-in / fade-out animations
        self._opacity_effect = QGraphicsOpacityEffect(self)
        self._opacity_effect.setOpacity(1.0)
        self.setGraphicsEffect(self._opacity_effect)
        self._show_animation: QPropertyAnimation | None = None

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)

        self._setup_ui()
        self._apply_style()
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Container with border and background
        self.container = QWidget()
        container_layout = QVBoxLayout(self.container)
        container_layout.setContentsMargins(14, 12, 14, 12)
        container_layout.setSpacing(8)

        # Header: icon + title + close button
        header = QHBoxLayout()
        header.setSpacing(8)
        header.setContentsMargins(0, 0, 0, 0)

        self.icon_label = QLabel()
        self.icon_label.setStyleSheet(
            f"color: {MaterialTheme.primary}; font-size: 13px; font-weight: 800; "
            "border: none; background: transparent;"
        )

        self.title_label = QLabel()
        self.title_label.setFont(QFont(MaterialTheme.font_family, 13, QFont.Weight.Bold))
        self.title_label.setStyleSheet(
            f"color: {MaterialTheme.on_surface}; border: none; background: transparent;"
        )
        self.title_label.setWordWrap(True)

        self.close_btn = QPushButton("✕")
        self.close_btn.setFixedSize(22, 22)
        self.close_btn.setStyleSheet(MaterialTheme.icon_button(size=22, danger=True))
        self.close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_btn.clicked.connect(self._on_close_clicked)

        header.addWidget(self.icon_label)
        header.addWidget(self.title_label, stretch=1)
        header.addWidget(self.close_btn)
        container_layout.addLayout(header)

        # Content area based on type
        self.content_widget = self._build_content()
        container_layout.addWidget(self.content_widget, stretch=1)

        layout.addWidget(self.container)

    def _build_content(self) -> QWidget:
        """Build the content widget based on canvas_type."""
        data = self._data
        ctype = self.canvas_type

        if ctype == "bubble":
            return self._build_bubble(data)
        elif ctype == "card":
            return self._build_card(data)
        elif ctype == "image":
            return self._build_image(data)
        elif ctype == "list":
            return self._build_list(data)
        elif ctype == "code":
            return self._build_code(data)
        elif ctype == "rich":
            return self._build_rich(data)
        else:
            return self._build_fallback(data)

    def _build_bubble(self, data: dict[str, Any]) -> QWidget:
        text = data.get("text", "")
        label = QLabel(text)
        label.setWordWrap(True)
        label.setFont(QFont(MaterialTheme.font_family, 13))
        label.setStyleSheet(
            f"color: {MaterialTheme.on_surface}; border: none; background: transparent;"
        )
        label.setMaximumWidth(260)
        label.setMinimumWidth(40)
        # Use QTextDocument for reliable wrapped height calculation
        if text:
            doc = QTextDocument()
            doc.setDefaultFont(label.font())
            doc.setPlainText(text)
            doc.setTextWidth(260)
            height = int(doc.size().height()) + 16
            label.setFixedHeight(min(height, self._max_content_height))
        else:
            label.setFixedHeight(30)
        return label

    def _build_card(self, data: dict[str, Any]) -> QWidget:
        content = data.get("content", "")
        browser = QTextBrowser()
        browser.setOpenExternalLinks(True)
        browser.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        browser.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        browser.setStyleSheet(
            "QTextBrowser { background: transparent; border: none; padding: 0px; }"
        )
        browser.setFrameStyle(0)
        browser.setHtml(self._text_to_html(content))
        browser.setMinimumHeight(60)
        browser.setMaximumWidth(280)

        def _set_height() -> None:
            try:
                vw = browser.viewport().width()
                if vw > 0:
                    browser.document().setTextWidth(vw)
                h = int(browser.document().size().height()) + 8
                browser.setFixedHeight(max(min(h, self._max_content_height), 60))
                self.size_changed.emit(self.canvas_id)
            except RuntimeError:
                pass

        QTimer.singleShot(50, _set_height)
        return browser

    def _build_image(self, data: dict[str, Any]) -> QWidget:
        src = data.get("src", "")
        caption = data.get("caption", "")

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        img_label = QLabel()
        img_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        img_label.setStyleSheet("border: none; background: transparent;")
        img_label.setMaximumWidth(280)
        img_label.setMinimumHeight(80)

        pixmap = QPixmap()
        if src.startswith("data:image"):
            # base64 data URI
            import base64

            try:
                header, encoded = src.split(",", 1)
                pixmap.loadFromData(base64.b64decode(encoded))
            except Exception:
                pass
        elif src.startswith("http"):
            # URL - try to load (likely won't work without network thread, show placeholder)
            pixmap.loadFromData(b"")  # placeholder
        else:
            pixmap.load(src)

        if not pixmap.isNull():
            scaled = pixmap.scaledToWidth(260, Qt.TransformationMode.SmoothTransformation)
            if scaled.height() > 200:
                scaled = scaled.scaledToHeight(200, Qt.TransformationMode.SmoothTransformation)
            img_label.setPixmap(scaled)
        else:
            img_label.setText("图片暂不可预览")
            img_label.setStyleSheet(
                f"QLabel {{ color: {MaterialTheme.on_surface_variant}; font-size: 14px; "
                f"background: {MaterialTheme.surface_container_high}; border: 1px solid {MaterialTheme.outline_variant}; "
                "border-radius: 8px; padding: 20px; }}"
            )

        layout.addWidget(img_label)

        if caption:
            cap_label = QLabel(caption)
            cap_label.setWordWrap(True)
            cap_label.setFont(QFont(MaterialTheme.font_family, 11))
            cap_label.setStyleSheet(
                f"color: {MaterialTheme.on_surface_variant}; border: none; background: transparent;"
            )
            cap_label.setMaximumWidth(280)
            layout.addWidget(cap_label)

        return container

    def _build_list(self, data: dict[str, Any]) -> QWidget:
        items = data.get("items", [])
        checkable = data.get("checkable", False)

        list_widget = QListWidget()
        list_widget.setStyleSheet(
            f"""
            QListWidget {{
                background: transparent;
                border: none;
                color: {MaterialTheme.on_surface};
                outline: none;
            }}
            QListWidget::item {{
                padding: 4px 2px;
                border: none;
            }}
            """
        )
        list_widget.setMaximumWidth(280)
        calculated_height = len(items) * 28 + 4
        list_widget.setMaximumHeight(min(calculated_height, self._max_content_height))
        list_widget.setMinimumHeight(min(max(calculated_height, 40), self._max_content_height))
        for item_text in items:
            item = QListWidgetItem(item_text)
            if checkable:
                item.setCheckState(Qt.CheckState.Unchecked)
            list_widget.addItem(item)
        return list_widget

    def _build_code(self, data: dict[str, Any]) -> QWidget:
        code = data.get("code", "")

        browser = QTextBrowser()
        browser.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        browser.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        browser.setStyleSheet(
            f"""
            QTextBrowser {{
                background-color: {MaterialTheme.inverse_surface};
                color: {MaterialTheme.inverse_on_surface};
                border: none;
                border-radius: 8px;
                padding: 10px;
                font-family: Consolas, "JetBrains Mono", monospace;
                font-size: 12px;
            }}
            """
        )
        browser.setFrameStyle(0)
        escaped = code.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        html = f"<pre style='margin:0;white-space:pre-wrap;word-wrap:break-word;'>{escaped}</pre>"
        browser.setHtml(html)
        browser.setMinimumHeight(60)
        browser.setMaximumWidth(280)

        def _set_height() -> None:
            try:
                vw = browser.viewport().width()
                if vw > 0:
                    browser.document().setTextWidth(vw)
                h = int(browser.document().size().height()) + 20
                browser.setFixedHeight(max(min(h, self._max_content_height), 60))
                self.size_changed.emit(self.canvas_id)
            except RuntimeError:
                pass

        QTimer.singleShot(50, _set_height)
        return browser

    def _build_rich(self, data: dict[str, Any]) -> QWidget:
        html = data.get("html", "")
        browser = QTextBrowser()
        browser.setOpenExternalLinks(True)
        browser.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        browser.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        browser.setStyleSheet(
            "QTextBrowser { background: transparent; border: none; padding: 0px; }"
        )
        browser.setFrameStyle(0)
        browser.setHtml(html)
        browser.setMinimumHeight(60)
        browser.setMaximumWidth(280)

        def _set_height() -> None:
            try:
                vw = browser.viewport().width()
                if vw > 0:
                    browser.document().setTextWidth(vw)
                h = int(browser.document().size().height()) + 8
                browser.setFixedHeight(max(min(h, self._max_content_height), 60))
                self.size_changed.emit(self.canvas_id)
            except RuntimeError:
                pass

        QTimer.singleShot(50, _set_height)
        return browser

    def _build_fallback(self, data: dict[str, Any]) -> QWidget:
        text = str(data) if data else ""
        label = QLabel(text)
        label.setWordWrap(True)
        label.setFont(QFont(MaterialTheme.font_family, 12))
        label.setStyleSheet(
            f"color: {MaterialTheme.on_surface}; border: none; background: transparent;"
        )
        label.setMaximumWidth(280)
        return label

    def _text_to_html(self, text: str) -> str:
        """Convert plain text with markdown-like formatting to simple HTML."""
        import re

        html = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        html = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", html)
        html = re.sub(
            r"`(.+?)`",
            r"<code style='background:#F4F4F5;padding:1px 4px;border-radius:4px;'>\1</code>",
            html,
        )
        html = html.replace("\n", "<br>")
        return (
            f"<html><head><style>"
            f"body {{ font-family: {MaterialTheme.font_family}, sans-serif; font-size: 13px; color: {MaterialTheme.on_surface}; line-height: 1.5; }}"
            f"p {{ margin: 4px 0; }}"
            f"</style></head><body>{html}</body></html>"
        )

    def _apply_style(self) -> None:
        theme_colors = {
            "purple": (MaterialTheme.primary_container, MaterialTheme.on_primary_container),
            "blue": ("#E8F0FF", "#17335F"),
            "green": (MaterialTheme.secondary_container, MaterialTheme.on_secondary_container),
            "orange": ("#FFF1DE", "#5C3510"),
            "red": (MaterialTheme.error_container, MaterialTheme.on_error_container),
            "default": (MaterialTheme.surface_container, MaterialTheme.on_surface),
        }
        theme = self._data.get("theme", "default")
        bg, fg = theme_colors.get(theme, theme_colors["default"])

        if self.canvas_type == "bubble":
            self.icon_label.hide()
            self.title_label.hide()
            self.close_btn.hide()
            self.container.setStyleSheet(
                f"""
                QWidget {{
                    background-color: {MaterialTheme.rgba(MaterialTheme.surface_container, 246)};
                    border-radius: 14px;
                    border: 1px solid {MaterialTheme.rgba(MaterialTheme.primary, 80)};
                }}
                """
            )
            # Bubble auto-sizes to content
            self.setMinimumWidth(80)
            self.setMaximumWidth(280)
        else:
            self.container.setStyleSheet(
                f"""
                QWidget {{
                    background-color: {bg};
                    color: {fg};
                    border-radius: 12px;
                    border: 1px solid {MaterialTheme.outline_variant};
                }}
                QLabel {{ color: {fg}; }}
                """
            )
            # Update title color
            self.title_label.setStyleSheet(f"color: {fg}; border: none; background: transparent;")

    def set_title(self, title: str, icon: str = "") -> None:
        self.title_label.setText(title)
        self.icon_label.setText(icon)
        self.icon_label.setVisible(bool(icon))
        self.title_label.setVisible(bool(title))

    def start_auto_hide(self, duration_ms: int) -> None:
        """Start auto-hide timer. 0 = persist."""
        if duration_ms > 0:
            QTimer.singleShot(duration_ms, self._start_fade)

    def _start_fade(self) -> None:
        self._fade_value = 1.0
        self._fade_timer = QTimer(self)
        self._fade_timer.timeout.connect(self._fade_step)
        self._fade_timer.start(50)

    def _fade_step(self) -> None:
        if not self.isVisible():
            if self._fade_timer:
                self._fade_timer.stop()
            return
        self._fade_value -= 0.05
        if self._fade_value <= 0:
            if self._fade_timer:
                self._fade_timer.stop()
            self._on_close_clicked()
        else:
            self._opacity_effect.setOpacity(self._fade_value)

    def showEvent(self, event: Any) -> None:
        super().showEvent(event)
        # Play fade-in animation on each show
        self._opacity_effect.setOpacity(0.0)
        if self._show_animation is not None:
            self._show_animation.stop()
        self._show_animation = QPropertyAnimation(self._opacity_effect, b"opacity")
        self._show_animation.setDuration(200)
        self._show_animation.setStartValue(0.0)
        self._show_animation.setEndValue(1.0)
        self._show_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._show_animation.start()

    def _on_close_clicked(self) -> None:
        self.closed.emit(self.canvas_id)
        self.hide()
        self.deleteLater()

    def enterEvent(self, event: Any) -> None:
        super().enterEvent(event)
        # Subtle hover feedback: brighten opacity slightly
        if self._opacity_effect:
            self._opacity_effect.setOpacity(1.0)

    def leaveEvent(self, event: Any) -> None:
        super().leaveEvent(event)
        if self._opacity_effect:
            self._opacity_effect.setOpacity(1.0)

    def mousePressEvent(self, event: Any) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.canvas_id)
        super().mousePressEvent(event)

    def update_data(self, data: dict[str, Any]) -> None:
        """Update content data and rebuild the widget."""
        self._data.update(data)
        # Remove old content and rebuild
        old = self.content_widget
        if old:
            layout = self.container.layout()
            if isinstance(layout, QVBoxLayout):
                layout.removeWidget(old)
                old.deleteLater()
        self.content_widget = self._build_content()
        layout = self.container.layout()
        if isinstance(layout, QVBoxLayout):
            layout.insertWidget(1, self.content_widget, stretch=1)
        self._apply_style()
        self.adjustSize()
