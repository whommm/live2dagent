"""Chat window with bubbles, streaming, model selector and session sidebar."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from markdown_it import MarkdownIt
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import get_lexer_by_name, guess_lexer
from PySide6.QtCore import QEasingCurve, QPropertyAnimation, Qt, QTimer, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QFont, QMouseEvent, QTextDocument
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QTextBrowser,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from aipet.frontend.client import GatewayClient, fire_and_forget
from aipet.frontend.theme import MaterialTheme

if TYPE_CHECKING:
    from aipet.frontend.provider_dialog import ProviderDialog


class MessageBubble(QWidget):
    """A single chat message bubble with avatar, markdown support, and actions."""

    delete_requested = Signal(str)
    copy_requested = Signal()
    regenerate_requested = Signal(str)
    edit_requested = Signal(str)

    _md = MarkdownIt("commonmark", {"html": False})

    def __init__(
        self,
        role: str,
        content: str,
        timestamp: str | None = None,
        message_id: str = "",
        parent: QWidget | None = None,
        is_error: bool = False,
        animate: bool = True,
    ) -> None:
        super().__init__(parent)
        self.role = role
        self.message_id = message_id
        self._is_error = is_error
        self._is_streaming = False
        self._stream_buffer = ""
        self._tool_status_label: QLabel | None = None
        self._setup_ui(content, timestamp)
        self._apply_style()
        # NOTE: QGraphicsOpacityEffect is disabled on MessageBubble because
        # ChatWindow already has a QGraphicsOpacityEffect. Qt 6 does not
        # support nested graphics effects and it causes "Painter not active"
        # errors when the paint engine tries to render both simultaneously.
        self._animate = False
        self._opacity_effect = None
        self._move_animation: QPropertyAnimation | None = None
        self._fade_animation: QPropertyAnimation | None = None

    def _setup_ui(self, content: str, timestamp: str | None) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(18, 10, 18, 10)
        main_layout.setSpacing(6)

        self.time_label = QLabel(timestamp or "")
        self.time_label.setFont(QFont(MaterialTheme.font_family, 8))
        self.time_label.setStyleSheet(f"color: {MaterialTheme.outline};")
        self.time_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        main_layout.addWidget(self.time_label)

        row = QHBoxLayout()
        row.setSpacing(10)
        row.setContentsMargins(0, 0, 0, 0)

        self.avatar = QLabel()
        self.avatar.setFixedSize(34, 34)
        self.avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.avatar.setStyleSheet(
            f"background-color: {MaterialTheme.surface_container_high}; color: {MaterialTheme.on_surface_variant}; "
            "border: 1px solid rgba(126, 135, 148, 80); border-radius: 17px; font-size: 12px; font-weight: 700;"
        )

        # Content column: bubble container + toolbar
        content_col = QVBoxLayout()
        content_col.setSpacing(2)
        content_col.setContentsMargins(0, 0, 0, 0)
        content_col.setAlignment(Qt.AlignmentFlag.AlignTop)

        # Bubble container handles background, radius, padding
        self.bubble_container = QWidget()
        bubble_layout = QVBoxLayout(self.bubble_container)
        bubble_layout.setContentsMargins(14, 11, 14, 11)
        bubble_layout.setSpacing(0)

        # Text display: QTextBrowser for assistant/markdown, QLabel for system/plain text
        if self.role == "system":
            self.text_display: Any = QLabel(content)
            self.text_display.setWordWrap(True)
            self.text_display.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self.text_display.setFont(QFont(MaterialTheme.font_family, 11))
            bubble_layout.addWidget(self.text_display)
        elif self.role == "assistant" or self._has_markdown(content):
            browser = QTextBrowser()
            browser.setOpenExternalLinks(True)
            browser.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            browser.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            # Ensure viewport is transparent so bubble_container background shows through
            browser.setStyleSheet(
                f"QTextBrowser {{ background-color: transparent; border: none; padding: 0px; color: {MaterialTheme.on_secondary_container}; }}"
                f"QTextBrowser QAbstractScrollArea::viewport {{ background-color: transparent; }}"
            )
            color = (
                MaterialTheme.on_secondary_container
                if self.role == "assistant"
                else MaterialTheme.on_surface
            )
            browser.setHtml(
                self._markdown_to_html(
                    self._clean_html_tags(self._strip_live2d_tags(content)), text_color=color
                )
            )
            self.text_display = browser
            bubble_layout.addWidget(self.text_display)
        else:
            label = QLabel(self._clean_html_tags(self._strip_live2d_tags(content)))
            label.setWordWrap(True)
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            label.setFont(QFont(MaterialTheme.font_family, 11))
            self.text_display = label
            bubble_layout.addWidget(self.text_display)

        self.text_display.setMaximumWidth(520)
        content_col.addWidget(self.bubble_container)

        # Toolbar with copy button (uses opacity effect to avoid layout resize)
        self.toolbar = QHBoxLayout()
        self.toolbar.setSpacing(4)
        self.toolbar.setContentsMargins(0, 0, 0, 0)
        self.toolbar.addStretch()

        self.copy_btn = QPushButton("复")
        self.copy_btn.setFixedSize(26, 26)
        self.copy_btn.setStyleSheet(MaterialTheme.icon_button(size=26))
        self.copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.copy_btn.setToolTip("复制消息")
        self.copy_btn.clicked.connect(self._on_copy_clicked)
        self.toolbar.addWidget(self.copy_btn)

        self.delete_btn = QPushButton("删")
        self.delete_btn.setFixedSize(26, 26)
        self.delete_btn.setStyleSheet(MaterialTheme.icon_button(size=26, danger=True))
        self.delete_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.delete_btn.setToolTip("删除消息")
        self.delete_btn.clicked.connect(self._on_delete_clicked)
        self.toolbar.addWidget(self.delete_btn)

        # Regenerate for assistant, Edit for user
        if self.role == "assistant":
            self.regenerate_btn = QPushButton("重")
            self.regenerate_btn.setFixedSize(26, 26)
            self.regenerate_btn.setStyleSheet(MaterialTheme.icon_button(size=26))
            self.regenerate_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self.regenerate_btn.setToolTip("重新生成")
            self.regenerate_btn.clicked.connect(self._on_regenerate_clicked)
            self.toolbar.addWidget(self.regenerate_btn)
        elif self.role == "user":
            self.edit_btn = QPushButton("改")
            self.edit_btn.setFixedSize(26, 26)
            self.edit_btn.setStyleSheet(MaterialTheme.icon_button(size=26))
            self.edit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self.edit_btn.setToolTip("编辑并重发")
            self.edit_btn.clicked.connect(self._on_edit_clicked)
            self.toolbar.addWidget(self.edit_btn)

        toolbar_widget = QWidget()
        toolbar_widget.setLayout(self.toolbar)
        toolbar_widget.setFixedHeight(28)
        # System messages don't need action buttons
        if self.role == "system":
            toolbar_widget.hide()
        self._toolbar_widget = toolbar_widget
        content_col.addWidget(toolbar_widget)

        # Tool execution status label (hidden by default)
        self._tool_status_label = QLabel()
        self._tool_status_label.setFont(QFont(MaterialTheme.font_family, 9))
        self._tool_status_label.setStyleSheet(
            f"color: {MaterialTheme.primary}; border: none; background: transparent; padding: 2px 0px;"
        )
        self._tool_status_label.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self._tool_status_label.hide()
        content_col.addWidget(self._tool_status_label)

        if self.role == "user":
            self.avatar.setText("你")
            row.addStretch()
            row.addLayout(content_col)
            row.addWidget(self.avatar)
        elif self.role == "system":
            self.avatar.hide()
            row.addStretch()
            row.addLayout(content_col)
            row.addStretch()
        else:
            self.avatar.setText("AI")
            row.addWidget(self.avatar)
            row.addLayout(content_col)
            row.addStretch()

        main_layout.addLayout(row)

    def _apply_style(self) -> None:
        if self.role == "user":
            # Use plain property syntax (no QWidget selector) to avoid cascading to children
            self.bubble_container.setStyleSheet(
                f"background-color: {MaterialTheme.primary_container}; "
                "border-radius: 16px; border: 1px solid rgba(79, 103, 165, 36);"
            )
            if isinstance(self.text_display, QLabel):
                self.text_display.setStyleSheet(
                    f"QLabel {{ color: {MaterialTheme.on_primary_container}; background-color: transparent; border: none; padding: 0px; }}"
                )
            else:
                self.text_display.setStyleSheet(
                    f"QTextBrowser {{ color: {MaterialTheme.on_primary_container}; background-color: transparent; border: none; padding: 0px; }}"
                )
        elif self.role == "system":
            if getattr(self, "_is_error", False):
                self.text_display.setStyleSheet(
                    f"QLabel {{ background-color: {MaterialTheme.error_container}; color: {MaterialTheme.on_error_container}; "
                    f"font-weight: 500; padding: 6px 12px; font-size: 12px; border-radius: 8px; }}"
                )
            else:
                self.text_display.setStyleSheet(
                    f"QLabel {{ background-color: transparent; color: {MaterialTheme.outline}; font-style: italic; padding: 4px 8px; font-size: 12px; }}"
                )
            return
        else:
            self.bubble_container.setStyleSheet(
                f"background-color: {MaterialTheme.surface_container}; "
                f"border: 1px solid {MaterialTheme.outline_variant}; border-radius: 16px;"
            )
            if isinstance(self.text_display, QLabel):
                self.text_display.setStyleSheet(
                    f"QLabel {{ color: {MaterialTheme.on_secondary_container}; background-color: transparent; border: none; padding: 0px; }}"
                )
            else:
                self.text_display.setStyleSheet(
                    f"QTextBrowser {{ color: {MaterialTheme.on_secondary_container}; background-color: transparent; border: none; padding: 0px; }}"
                )

    def showEvent(self, event: Any) -> None:
        """Recalculate text height once the widget has real layout geometry."""
        super().showEvent(event)
        # Defer height calculation to the next event-loop iteration so the
        # viewport has valid geometry.
        QTimer.singleShot(0, self._update_text_height)

    def _update_text_height(self) -> None:
        """Resize text display to fit its content exactly."""
        if isinstance(self.text_display, QTextBrowser):
            # viewport width is only accurate after layout / showEvent
            width = self.text_display.viewport().width()
            if width > 0:
                self.text_display.document().setTextWidth(width)
            doc_height = self.text_display.document().size().height()
            new_height = max(int(doc_height) + 8, 24)
            self.text_display.setFixedHeight(new_height)
            self.text_display.updateGeometry()
        elif isinstance(self.text_display, QLabel) and self.role != "system":
            # QLabel's sizeHint is unreliable before show; use QTextDocument
            doc = QTextDocument()
            doc.setDefaultFont(self.text_display.font())
            doc.setPlainText(self.text_display.text())
            max_w = self.text_display.maximumWidth()
            doc.setTextWidth(max_w if max_w > 0 else 520)
            new_height = max(int(doc.size().height()) + 8, 24)
            self.text_display.setFixedHeight(new_height)
            self.text_display.updateGeometry()

    def _on_copy_clicked(self) -> None:
        text = self.get_text()
        QApplication.clipboard().setText(text)
        self.copy_btn.setText("✓")
        QTimer.singleShot(1500, self._restore_copy_btn_text)

    def _restore_copy_btn_text(self) -> None:
        """Restore copy button text after a delay."""
        with contextlib.suppress(RuntimeError):
            self.copy_btn.setText("复")

    def _on_delete_clicked(self) -> None:
        self.delete_requested.emit(self.message_id)

    def _on_regenerate_clicked(self) -> None:
        self.regenerate_requested.emit(self.message_id)

    def _on_edit_clicked(self) -> None:
        self.edit_requested.emit(self.message_id)

    @staticmethod
    def _has_markdown(text: str) -> bool:
        """Quick heuristic to detect markdown formatting."""
        patterns = [
            r"```",
            r"\*\*",
            r"__",
            r"`[^`]+`",
            r"^#{1,6} ",
            r"^\s*[-*+] ",
            r"^\s*\d+\. ",
            r"\[.*?\]\(.*?\)",
            r"^\s*> ",
            r"\n\s*---\s*\n",
        ]
        return any(re.search(p, text, re.MULTILINE) for p in patterns)

    @classmethod
    def _highlight_code_blocks(cls, html: str) -> str:
        """Replace markdown code blocks with Pygments-highlighted HTML."""
        import re

        def _replace_block(match: re.Match) -> str:
            lang = match.group(1) or ""
            code = match.group(2)
            # Unescape HTML entities that markdown-it encoded
            code = code.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
            try:
                lexer = get_lexer_by_name(lang, stripall=True) if lang else guess_lexer(code)
                formatter = HtmlFormatter(noclasses=True, nowrap=True, style="default")
                highlighted = highlight(code, lexer, formatter)
            except Exception:
                highlighted = code
            return (
                f'<pre style="background-color:{MaterialTheme.surface_variant};border-radius:8px;padding:10px;'
                f'margin:6px 0;overflow-x:auto;font-family:Consolas,\\"JetBrains Mono\\",monospace;'
                f'font-size:12px;"><code>{highlighted}</code></pre>'
            )

        # markdown-it renders fenced code blocks as <pre><code class="language-xxx">...</code></pre>
        html = re.sub(
            r'<pre><code class="language-([^"]*)">(.*?)</code></pre>',
            _replace_block,
            html,
            flags=re.DOTALL,
        )
        # Handle code blocks without language
        html = re.sub(
            r"<pre><code>(.*?)</code></pre>",
            lambda m: _replace_block(
                re.match(
                    r'<pre><code class="language-([^"]*)">(.*?)</code></pre>',
                    '<pre><code class="language-">' + m.group(1) + "</code></pre>",
                    re.DOTALL,
                )
            ),
            html,
            flags=re.DOTALL,
        )
        return html

    @classmethod
    def _markdown_to_html(cls, text: str, text_color: str | None = None) -> str:
        html = cls._md.render(text)
        html = cls._highlight_code_blocks(html)
        fg = text_color or MaterialTheme.on_surface
        styles = f"""
        <html><head><style>
        body {{
            font-family: {MaterialTheme.font_family}, sans-serif;
            font-size: 13px;
            color: {fg};
            line-height: 1.5;
        }}
        pre {{
            background-color: {MaterialTheme.surface_variant};
            border-radius: 8px;
            padding: 10px;
            margin: 6px 0;
            font-family: Consolas, "JetBrains Mono", "Cascadia Code", monospace;
            font-size: 12px;
        }}
        code {{
            background-color: {MaterialTheme.surface_variant};
            border-radius: 4px;
            padding: 1px 4px;
            font-family: Consolas, "JetBrains Mono", "Cascadia Code", monospace;
            font-size: 12px;
        }}
        pre code {{
            background-color: transparent;
            border-radius: 0;
            padding: 0;
        }}
        p {{ margin: 4px 0; }}
        blockquote {{
            border-left: 3px solid {MaterialTheme.outline_variant};
            margin: 4px 0;
            padding-left: 10px;
            color: {MaterialTheme.on_surface_variant};
        }}
        ul, ol {{ margin: 4px 0; padding-left: 20px; }}
        li {{ margin: 2px 0; }}
        a {{ color: {MaterialTheme.primary}; }}
        h1, h2, h3, h4 {{ margin: 8px 0 4px; font-weight: 600; }}
        hr {{ border: none; border-top: 1px solid {MaterialTheme.outline_variant}; margin: 8px 0; }}
        table {{ border-collapse: collapse; margin: 6px 0; }}
        th, td {{ border: 1px solid {MaterialTheme.outline_variant}; padding: 4px 8px; }}
        th {{ background-color: {MaterialTheme.surface_variant}; }}
        </style></head><body>{html}</body></html>
        """
        return styles

    @staticmethod
    def _strip_live2d_tags(text: str) -> str:
        """Remove [expression:xxx], [motion:xxx], [pose:xxx], [emotion:xxx] and [prop:xxx] tags from displayed text."""
        return re.sub(
            r"\[\s*(expression|motion|pose|emotion|prop)\s*:\s*[^\[\]]+?\s*\]", "", text
        ).strip()

    @staticmethod
    def _clean_html_tags(text: str) -> str:
        """Convert common HTML tags (like <br>) to plain text equivalents."""
        # <br> variants → newline
        text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
        # <p> → newline (strip surrounding)
        text = re.sub(r"</?p\s*/?>", "\n", text, flags=re.IGNORECASE)
        # <div> → newline
        text = re.sub(r"</?div\s*/?>", "\n", text, flags=re.IGNORECASE)
        # Collapse multiple newlines
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    def append_text(self, text: str) -> None:
        cleaned = self._clean_html_tags(self._strip_live2d_tags(text))
        if isinstance(self.text_display, QTextBrowser):
            if self._is_streaming:
                # Incremental append during streaming to avoid full re-render
                cursor = self.text_display.textCursor()
                cursor.movePosition(cursor.MoveOperation.End)
                cursor.insertText(cleaned)
                self._stream_buffer += cleaned
                # Update height every ~50 chars to balance smoothness vs layout cost
                if len(self._stream_buffer) >= 50:
                    self._update_text_height()
                    self._stream_buffer = ""
            else:
                current = self.text_display.toPlainText()
                new_text = self._clean_html_tags(self._strip_live2d_tags(current + text))
                color = (
                    MaterialTheme.on_secondary_container
                    if self.role == "assistant"
                    else MaterialTheme.on_surface
                )
                self.text_display.setHtml(self._markdown_to_html(new_text, text_color=color))
                self._update_text_height()
        else:
            self.text_display.setText(
                self._clean_html_tags(self._strip_live2d_tags(self.text_display.text() + text))
            )

    def set_streaming(self, streaming: bool) -> None:
        """Mark this bubble as being in a streaming state."""
        self._is_streaming = streaming
        self._stream_buffer = ""

    def finish_streaming(self) -> None:
        """Finalize streaming by re-rendering the full content as Markdown."""
        if not self._is_streaming:
            return
        self._is_streaming = False
        self._stream_buffer = ""
        if isinstance(self.text_display, QTextBrowser):
            final_text = self.text_display.toPlainText()
            color = (
                MaterialTheme.on_secondary_container
                if self.role == "assistant"
                else MaterialTheme.on_surface
            )
            self.text_display.setHtml(self._markdown_to_html(final_text, text_color=color))
            # Defer height calculation until the document layout is ready.
            QTimer.singleShot(0, self._update_text_height)

    def show_tool_status(self, text: str) -> None:
        """Show a small status label below the bubble."""
        if self._tool_status_label is not None:
            self._tool_status_label.setText(text)
            self._tool_status_label.show()

    def hide_tool_status(self) -> None:
        """Hide the tool execution status label."""
        if self._tool_status_label is not None:
            self._tool_status_label.hide()

    def set_text(self, text: str) -> None:
        cleaned = self._clean_html_tags(self._strip_live2d_tags(text))
        if isinstance(self.text_display, QTextBrowser):
            color = (
                MaterialTheme.on_secondary_container
                if self.role == "assistant"
                else MaterialTheme.on_surface
            )
            self.text_display.setHtml(self._markdown_to_html(cleaned, text_color=color))
            self._update_text_height()
        else:
            self.text_display.setText(cleaned)

    def get_text(self) -> str:
        if isinstance(self.text_display, QTextBrowser):
            return str(self.text_display.toPlainText()).strip()
        return str(self.text_display.text()).strip()


class ToolCard(QWidget):
    """A visual card showing tool call status with expandable details."""

    def __init__(
        self,
        tool_call_id: str,
        name: str,
        arguments: dict[str, Any],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.tool_call_id = tool_call_id
        self.name = name
        self.arguments = arguments
        self._is_expanded = False
        self._setup_ui()

    def _setup_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Left colored status strip
        self.strip = QLabel()
        self.strip.setFixedWidth(4)
        self.strip.setStyleSheet(f"background-color: {MaterialTheme.primary}; border-radius: 2px;")
        root.addWidget(self.strip)

        # Main card container
        self.container = QWidget()
        container_layout = QVBoxLayout(self.container)
        container_layout.setContentsMargins(12, 10, 12, 10)
        container_layout.setSpacing(6)

        # Header row: icon + name + args preview + status + expand btn
        header = QHBoxLayout()
        header.setSpacing(8)
        header.setContentsMargins(0, 0, 0, 0)

        self.icon_label = QLabel("工具")
        self.icon_label.setStyleSheet(
            f"font-size: 11px; font-weight: 700; color: {MaterialTheme.primary}; "
            "border: none; background: transparent;"
        )

        self.name_label = QLabel(self.name)
        self.name_label.setFont(QFont("JetBrains Mono", 11, QFont.Weight.Medium))
        self.name_label.setStyleSheet(
            f"color: {MaterialTheme.on_surface_variant}; border: none; background: transparent;"
        )

        # Argument summary (one-line)
        args_summary = self._fmt_args_summary(self.arguments)
        self.args_preview = QLabel(args_summary)
        self.args_preview.setFont(QFont("JetBrains Mono", 9))
        self.args_preview.setStyleSheet(
            f"color: {MaterialTheme.outline}; border: none; background: transparent;"
        )
        self.args_preview.setMaximumWidth(180)

        self.status_label = QLabel("● 运行中")
        self.status_label.setFont(QFont(MaterialTheme.font_family, 10))
        self.status_label.setStyleSheet(
            f"color: {MaterialTheme.primary}; border: none; background: transparent; "
            f"padding: 1px 6px; border-radius: 4px;"
        )

        self.expand_btn = QPushButton("▸")
        self.expand_btn.setFixedSize(24, 24)
        self.expand_btn.setStyleSheet(
            f"QPushButton {{ background-color: transparent; color: {MaterialTheme.outline}; "
            f"border: none; border-radius: 12px; font-size: 12px; padding: 0px; }}"
            f"QPushButton:hover {{ color: {MaterialTheme.on_surface}; background-color: {MaterialTheme._alpha(MaterialTheme.on_surface, 6)}; }}"
        )
        self.expand_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.expand_btn.setToolTip("展开详情")
        self.expand_btn.clicked.connect(self._toggle_expand)

        header.addWidget(self.icon_label)
        header.addWidget(self.name_label)
        header.addWidget(self.args_preview, stretch=1)
        header.addWidget(self.status_label)
        header.addWidget(self.expand_btn)
        container_layout.addLayout(header)

        # Detail area (arguments JSON + result), hidden by default
        self.detail_widget = QWidget()
        self.detail_widget.hide()
        detail_layout = QVBoxLayout(self.detail_widget)
        detail_layout.setContentsMargins(4, 4, 4, 4)
        detail_layout.setSpacing(8)

        # Arguments block
        args_block = QWidget()
        args_block.setStyleSheet(
            f"background-color: {MaterialTheme._alpha(MaterialTheme.on_surface, 3)}; border-radius: 6px;"
        )
        args_block_layout = QVBoxLayout(args_block)
        args_block_layout.setContentsMargins(8, 6, 8, 6)
        args_block_layout.setSpacing(2)

        args_title = QLabel("参数")
        args_title.setFont(QFont(MaterialTheme.font_family, 9, QFont.Weight.Bold))
        args_title.setStyleSheet(
            f"color: {MaterialTheme.outline}; border: none; background: transparent;"
        )
        args_block_layout.addWidget(args_title)

        self.args_detail = QLabel(self._fmt_dict_pretty(self.arguments))
        self.args_detail.setFont(QFont("JetBrains Mono", 10))
        self.args_detail.setStyleSheet(
            f"color: {MaterialTheme.on_surface_variant}; border: none; background: transparent;"
        )
        self.args_detail.setWordWrap(True)
        self.args_detail.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        args_block_layout.addWidget(self.args_detail)
        detail_layout.addWidget(args_block)

        # Result block
        self.result_block = QWidget()
        self.result_block.setStyleSheet(
            f"background-color: {MaterialTheme._alpha(MaterialTheme.on_surface, 3)}; border-radius: 6px;"
        )
        result_block_layout = QVBoxLayout(self.result_block)
        result_block_layout.setContentsMargins(8, 6, 8, 6)
        result_block_layout.setSpacing(2)

        result_title = QLabel("结果")
        result_title.setFont(QFont(MaterialTheme.font_family, 9, QFont.Weight.Bold))
        result_title.setStyleSheet(
            f"color: {MaterialTheme.outline}; border: none; background: transparent;"
        )
        result_block_layout.addWidget(result_title)

        self.result_text = QTextBrowser()
        self.result_text.setOpenExternalLinks(False)
        self.result_text.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.result_text.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.result_text.setMaximumHeight(120)
        self.result_text.setStyleSheet(
            f"QTextBrowser {{ background-color: transparent; border: none; padding: 0px; "
            f"color: {MaterialTheme.on_surface_variant}; font-family: 'JetBrains Mono', 'Consolas', monospace; font-size: 11px; }}"
        )
        result_block_layout.addWidget(self.result_text)
        self.result_block.hide()
        detail_layout.addWidget(self.result_block)

        container_layout.addWidget(self.detail_widget)

        self.container.setStyleSheet(
            f"QWidget {{ background-color: {MaterialTheme.surface_variant}; border-radius: 12px; }}"
        )
        root.addWidget(self.container)
        self.setMaximumWidth(480)

    @staticmethod
    def _fmt_args_summary(arguments: dict[str, Any]) -> str:
        import json

        try:
            vals = list(arguments.values())
            if not vals:
                return "(无参数)"
            preview = json.dumps(vals, ensure_ascii=False)
            if len(preview) > 50:
                preview = preview[:47] + "..."
            return preview
        except Exception:
            return str(arguments)[:50]

    @staticmethod
    def _fmt_dict_pretty(d: dict[str, Any]) -> str:
        import json

        try:
            return json.dumps(d, ensure_ascii=False, indent=2)
        except Exception:
            return str(d)

    def _toggle_expand(self) -> None:
        self._is_expanded = not self._is_expanded
        self.detail_widget.setVisible(self._is_expanded)
        self.expand_btn.setText("▾" if self._is_expanded else "▸")
        self.expand_btn.setToolTip("收起详情" if self._is_expanded else "展开详情")

    def _set_status_color(self, color: str, bg_alpha: int = 12) -> None:
        self.strip.setStyleSheet(f"background-color: {color}; border-radius: 2px;")
        bg = MaterialTheme.rgba(color, bg_alpha)
        self.status_label.setStyleSheet(
            f"color: {color}; border: none; background: {bg}; padding: 1px 6px; border-radius: 4px;"
        )

    def set_done(self, result: str, duration_ms: int = 0) -> None:
        has_error = (
            "error" in result.lower() or "exception" in result.lower() or result.startswith("Error")
        )
        if has_error:
            self.icon_label.setText("警告")
            self.status_label.setText(
                "● 完成但有警告" if not duration_ms else f"● 完成但有警告 · {duration_ms}ms"
            )
            self._set_status_color(MaterialTheme.error)
        else:
            self.icon_label.setText("完成")
            self.status_label.setText("● 完成" if not duration_ms else f"● 完成 · {duration_ms}ms")
            self._set_status_color("#4CAF50")

        self.result_text.setPlainText(result)
        self.result_block.show()
        # Auto-expand on error
        if has_error and not self._is_expanded:
            self._toggle_expand()

    def set_error(self, error: str) -> None:
        self.icon_label.setText("失败")
        self.status_label.setText("● 失败")
        self._set_status_color(MaterialTheme.error, 18)
        self.result_text.setPlainText(error)
        self.result_block.show()
        if not self._is_expanded:
            self._toggle_expand()


class _TitleBar(QWidget):
    """Custom MD3 title bar for frameless windows."""

    def __init__(self, window: QWidget, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._win = window
        self._drag_pos: Any = None
        self._setup_ui()
        self.setFixedHeight(40)

    def _setup_ui(self) -> None:
        self.setStyleSheet(
            f"background-color: {MaterialTheme.surface_container}; border-bottom: 1px solid {MaterialTheme.outline_variant};"
        )
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 0, 10, 0)
        layout.setSpacing(8)

        self.title_label = QLabel("AIPet")
        self.title_label.setStyleSheet(
            f"color: {MaterialTheme.on_surface}; font-size: 13px; font-weight: 700; border: none;"
        )
        layout.addWidget(self.title_label)

        # Connection status indicator
        self.status_label = QLabel("● 在线")
        self.status_label.setStyleSheet(
            f"color: {MaterialTheme.on_success_container}; font-size: 11px; border: none; "
            f"font-weight: 600; padding: 3px 9px; background-color: {MaterialTheme.success_container}; "
            "border-radius: 10px;"
        )
        self.status_label.setToolTip("已连接到 Gateway")
        layout.addWidget(self.status_label)

        layout.addStretch()

        btn_style = MaterialTheme.icon_button(size=28)
        close_style = MaterialTheme.icon_button(size=28, danger=True)

        self.min_btn = QPushButton("—")
        self.min_btn.setFixedSize(28, 28)
        self.min_btn.setStyleSheet(btn_style)
        self.min_btn.clicked.connect(self._on_minimize)

        self.max_btn = QPushButton("□")
        self.max_btn.setFixedSize(28, 28)
        self.max_btn.setStyleSheet(btn_style)
        self.max_btn.clicked.connect(self._on_maximize)

        self.close_btn = QPushButton("✕")
        self.close_btn.setFixedSize(28, 28)
        self.close_btn.setStyleSheet(close_style)
        self.close_btn.clicked.connect(self._on_close)

        layout.addWidget(self.min_btn)
        layout.addWidget(self.max_btn)
        layout.addWidget(self.close_btn)

    def set_connected(self, connected: bool) -> None:
        if connected:
            self.status_label.setText("● 在线")
            self.status_label.setStyleSheet(
                f"color: {MaterialTheme.on_success_container}; font-size: 11px; border: none; font-weight: 600; padding: 3px 9px; "
                f"background-color: {MaterialTheme.success_container}; border-radius: 10px;"
            )
            self.status_label.setToolTip("已连接到 Gateway")
        else:
            self.status_label.setText("● 离线")
            self.status_label.setStyleSheet(
                f"color: {MaterialTheme.on_error_container}; font-size: 11px; border: none; font-weight: 600; padding: 3px 9px; "
                f"background-color: {MaterialTheme.error_container}; border-radius: 10px;"
            )
            self.status_label.setToolTip("未连接到 Gateway")

    def _on_minimize(self) -> None:
        if self._win:
            self._win.showMinimized()

    def _on_maximize(self) -> None:
        if self._win:
            if self._win.isMaximized():
                self._win.showNormal()
                self.max_btn.setText("□")
            else:
                self._win.showMaximized()
                self.max_btn.setText("❐")

    def _on_close(self) -> None:
        if self._win:
            self._win.close()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self._win.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if event.buttons() == Qt.MouseButton.LeftButton and self._drag_pos is not None:
            self._win.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._on_maximize()


class ChatWindow(QWidget):
    """Main chat interface with model selector and session sidebar."""

    closed = Signal()
    _logger = logging.getLogger(__name__)

    def __init__(self, client: GatewayClient, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.client = client
        self._message_bubbles: dict[str, MessageBubble] = {}
        self._typing_indicator: QLabel | None = None
        self._tool_cards: dict[str, ToolCard] = {}
        self._providers: list[dict[str, Any]] = []
        self._current_provider_id: str | None = None
        self._current_model: str | None = None
        self._provider_config_warning_shown = False
        self._current_session_id: str = ""
        self._session_items: dict[str, QListWidgetItem] = {}
        self._is_sending = False
        self._is_switching = False
        self._is_near_bottom = True
        self._provider_dialog: ProviderDialog | None = None
        self._shown_disconnect_msg = False
        self._scroll_animation: QPropertyAnimation | None = None
        self._attachments: list[str] = []
        self._tts_enabled: bool = False
        self._setup_ui()
        self._wire_events()
        self._start_connection_checker()
        self.client.on_connect(self._on_gateway_connected)
        # Window fade-in animation
        self._opacity_effect = QGraphicsOpacityEffect(self)
        self._opacity_effect.setOpacity(1.0)
        self.setGraphicsEffect(self._opacity_effect)
        self._show_animation: QPropertyAnimation | None = None
        self._schedule_initial_loads()

    def _schedule_initial_loads(self) -> None:
        """Load remote data when an asyncio loop is actively running."""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return
        fire_and_forget(self._load_providers())
        fire_and_forget(self._load_sessions())

    def _setup_ui(self) -> None:
        self.setWindowTitle("Chat with AIPet")
        self.resize(980, 740)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)
        self.setStyleSheet(f"background-color: {MaterialTheme.surface};")

        root_layout = QHBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(1)
        splitter.setStyleSheet(
            f"QSplitter::handle {{ background: {MaterialTheme.outline_variant}; }}"
        )

        # ---------------- Left sidebar ----------------
        left_panel = QWidget()
        left_panel.setMinimumWidth(240)
        left_panel.setMaximumWidth(360)
        left_panel.setStyleSheet(
            f"background-color: {MaterialTheme.surface_container}; border-right: 1px solid {MaterialTheme.outline_variant};"
        )
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(14, 14, 14, 14)
        left_layout.setSpacing(12)

        self.new_chat_btn = QPushButton("+  新对话")
        self.new_chat_btn.setStyleSheet(MaterialTheme.filled_button())
        self.new_chat_btn.setFixedHeight(38)
        self.new_chat_btn.clicked.connect(lambda: asyncio.ensure_future(self._create_session()))
        left_layout.addWidget(self.new_chat_btn)

        self.session_list = QListWidget()
        self.session_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.session_list.setStyleSheet(
            f"""
            QListWidget {{
                background-color: {MaterialTheme.surface_container};
                color: {MaterialTheme.on_surface};
                border: none;
                outline: none;
                padding: 4px;
            }}
            QListWidget::item {{
                background-color: transparent;
                border-radius: 10px;
                padding: 0px;
                margin: 3px 0px;
            }}
            QListWidget::item:selected {{
                background-color: {MaterialTheme.secondary_container};
            }}
            QListWidget::item:hover:!selected {{
                background-color: {MaterialTheme._alpha(MaterialTheme.on_surface, 4)};
            }}
            """
        )
        self.session_list.itemClicked.connect(self._on_session_item_clicked)
        self.session_list.itemDoubleClicked.connect(self._on_session_item_double_clicked)
        self.session_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.session_list.customContextMenuRequested.connect(self._on_session_context_menu)
        left_layout.addWidget(self.session_list, stretch=1)

        splitter.addWidget(left_panel)

        # ---------------- Right panel ----------------
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)

        # Custom title bar
        self.title_bar = _TitleBar(window=self)
        right_layout.addWidget(self.title_bar)

        # Top app bar with model selector
        top_bar = QWidget()
        top_bar.setStyleSheet(MaterialTheme.top_bar())
        top_bar.setFixedHeight(66)
        top_layout = QHBoxLayout(top_bar)
        top_layout.setContentsMargins(22, 10, 18, 10)
        top_layout.setSpacing(12)

        app_title = QLabel("紫羽·琉璃")
        app_title.setStyleSheet(
            f"color: {MaterialTheme.on_surface}; font-size: 20px; font-weight: 700; border: none;"
        )
        top_layout.addWidget(app_title)
        subtitle = QLabel("陪伴聊天")
        subtitle.setStyleSheet(
            f"color: {MaterialTheme.on_surface_variant}; font-size: 12px; border: none;"
        )
        top_layout.addWidget(subtitle)
        top_layout.addStretch()

        self.model_selector = QComboBox()
        self.model_selector.setMinimumWidth(220)
        self.model_selector.setPlaceholderText("选择模型")
        self.model_selector.setStyleSheet(
            MaterialTheme.combo_box()
            + f"""
            QComboBox {{
                background-color: {MaterialTheme.surface_container_high};
                border-radius: 8px;
                padding: 6px 14px;
                font-size: 14px;
                font-weight: 500;
            }}
            QComboBox::drop-down {{
                width: 20px;
                border: none;
            }}
            """
        )
        self.model_selector.currentIndexChanged.connect(self._on_model_changed)
        top_layout.addWidget(self.model_selector)

        self.refresh_btn = QPushButton("刷")
        self.refresh_btn.setFixedSize(34, 34)
        self.refresh_btn.setStyleSheet(MaterialTheme.icon_button(size=34))
        self.refresh_btn.setToolTip("刷新模型列表")
        self.refresh_btn.clicked.connect(lambda: asyncio.ensure_future(self._load_providers()))

        self.manage_btn = QPushButton("设")
        self.manage_btn.setFixedSize(34, 34)
        self.manage_btn.setStyleSheet(MaterialTheme.icon_button(size=34))
        self.manage_btn.setToolTip("管理模型服务商和 API Key")
        self.manage_btn.clicked.connect(self._on_manage_providers)

        self.clear_btn = QPushButton("清")
        self.clear_btn.setFixedSize(34, 34)
        self.clear_btn.setStyleSheet(MaterialTheme.icon_button(size=34, danger=True))
        self.clear_btn.setToolTip("清空当前会话")
        self.clear_btn.clicked.connect(lambda: asyncio.ensure_future(self._clear_current_session()))

        top_layout.addWidget(self.refresh_btn)
        top_layout.addWidget(self.manage_btn)
        top_layout.addWidget(self.clear_btn)
        right_layout.addWidget(top_bar)

        # Scroll area for messages
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_area.setStyleSheet(
            f"QScrollArea {{ border: none; background: {MaterialTheme.surface}; }}"
        )
        # Track user scroll position
        self.scroll_area.verticalScrollBar().valueChanged.connect(self._on_scroll_changed)

        self.messages_container = QWidget()
        self.messages_layout = QVBoxLayout(self.messages_container)
        self.messages_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.messages_layout.setSpacing(2)
        self.messages_layout.setContentsMargins(16, 16, 16, 16)

        # Empty state shown when no messages
        self._empty_state = self._build_empty_state()
        self.messages_layout.addWidget(self._empty_state)

        self.messages_layout.addStretch()

        self.scroll_area.setWidget(self.messages_container)
        right_layout.addWidget(self.scroll_area, stretch=1)

        # Input area
        input_container = QWidget()
        input_container.setStyleSheet(
            f"background-color: {MaterialTheme.surface_container}; border-top: 1px solid {MaterialTheme.outline_variant};"
        )
        input_outer_layout = QVBoxLayout(input_container)
        input_outer_layout.setContentsMargins(0, 0, 0, 0)
        input_outer_layout.setSpacing(4)

        # Attachments bar (shown when files are dropped)
        self.attachments_bar = QWidget()
        attachments_bar_layout = QHBoxLayout(self.attachments_bar)
        attachments_bar_layout.setContentsMargins(16, 8, 16, 0)
        attachments_bar_layout.setSpacing(6)
        attachments_bar_layout.addStretch()
        self.attachments_bar.hide()
        input_outer_layout.addWidget(self.attachments_bar)

        # Input row
        input_row = QWidget()
        input_layout = QHBoxLayout(input_row)
        input_layout.setContentsMargins(16, 12, 16, 14)
        input_layout.setSpacing(10)

        # Voice input button (placeholder)
        self.voice_btn = QPushButton("◌")
        self.voice_btn.setFixedSize(38, 38)
        self.voice_btn.setStyleSheet(MaterialTheme.icon_button(size=38))
        self.voice_btn.setToolTip("语音输入暂未开放")
        self.voice_btn.setEnabled(False)
        input_layout.addWidget(self.voice_btn)

        self.input_field = QTextEdit()
        self.input_field.setPlaceholderText(
            "输入消息，Shift+Enter 换行，也可以拖入文件"
        )
        self.input_field.setMinimumHeight(54)
        self.input_field.setMaximumHeight(200)
        self.input_field.setStyleSheet(MaterialTheme.outlined_input())
        self.input_field.installEventFilter(self)
        self.input_field.textChanged.connect(self._on_input_text_changed)
        # Set initial height so the field doesn't use QTextEdit's large default preferred size
        self._on_input_text_changed()

        self.send_button = QPushButton("发送")
        self.send_button.setFixedSize(70, 38)
        self.send_button.setStyleSheet(MaterialTheme.filled_button())
        self.send_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.send_button.clicked.connect(self._on_send_clicked)
        # Stop state style (red gradient) — applied dynamically in _set_sending
        self._send_btn_style = MaterialTheme.filled_button()
        self._stop_btn_style = (
            "QPushButton { background: qlineargradient(x1:0, y1:0, x2:1, y2:1, "
            "stop:0 #D44E4E, stop:1 #B93C3C); color: white; border: none; "
            "border-radius: 8px; font-weight: 600; font-size: 13px; padding: 0px; }"
            "QPushButton:hover { background: qlineargradient(x1:0, y1:0, x2:1, y2:1, "
            "stop:0 #ff8585, stop:1 #f06b6b); }"
            "QPushButton:pressed { background: qlineargradient(x1:0, y1:0, x2:1, y2:1, "
            "stop:0 #e05555, stop:1 #d04a4a); }"
            "QPushButton:disabled { opacity: 0.5; }"
        )

        # TTS toggle button
        self.tts_toggle_btn = QPushButton("静")
        self.tts_toggle_btn.setFixedSize(38, 38)
        self.tts_toggle_btn.setStyleSheet(MaterialTheme.icon_button(size=38))
        self.tts_toggle_btn.setToolTip("语音朗读已关闭，点击开启")
        self.tts_toggle_btn.setCheckable(True)
        self.tts_toggle_btn.setChecked(False)
        self.tts_toggle_btn.clicked.connect(self._on_tts_toggle_clicked)
        input_layout.addWidget(self.tts_toggle_btn)

        input_layout.addWidget(self.input_field, stretch=1)
        input_layout.addWidget(self.send_button, alignment=Qt.AlignmentFlag.AlignBottom)
        input_outer_layout.addWidget(input_row)
        right_layout.addWidget(input_container)

        splitter.addWidget(right_panel)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([280, 620])
        root_layout.addWidget(splitter)

    def _build_empty_state(self) -> QWidget:
        """Build the welcome placeholder shown when a session has no messages."""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(12)

        icon = QLabel("AI")
        icon.setStyleSheet(
            f"color: {MaterialTheme.primary}; font-size: 28px; font-weight: 800; "
            f"border: 1px solid {MaterialTheme.outline_variant}; border-radius: 24px; "
            f"background: {MaterialTheme.surface_container}; min-width: 48px; min-height: 48px;"
        )
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)

        title = QLabel("你好，我在这里")
        title.setStyleSheet(
            f"color: {MaterialTheme.on_surface}; font-size: 18px; font-weight: 700; border: none; background: transparent;"
        )
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        subtitle = QLabel("和紫羽·琉璃说点什么，或者拖入文件一起处理。")
        subtitle.setStyleSheet(
            f"color: {MaterialTheme.on_surface_variant}; font-size: 13px; border: none; background: transparent;"
        )
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)

        hint = QLabel("Enter 发送，Shift + Enter 换行")
        hint.setStyleSheet(
            f"color: {MaterialTheme.outline}; font-size: 11px; border: none; background: transparent;"
        )
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)

        layout.addStretch()
        layout.addWidget(icon)
        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addWidget(hint)
        layout.addStretch()
        return widget

    def _wire_events(self) -> None:
        self.client.on("chat.message", self._on_chat_message)
        self.client.on("chat.stream.start", self._on_stream_start)
        self.client.on("chat.stream.chunk", self._on_stream_chunk)
        self.client.on("chat.stream.end", self._on_stream_end)
        self.client.on("chat.thinking", self._on_thinking)
        self.client.on("chat.proactive", self._on_chat_proactive)
        self.client.on("system.error", self._on_system_error)
        self.client.on("tool.start", self._on_tool_start)
        self.client.on("tool.result", self._on_tool_result)
        self.client.on("tool.error", self._on_tool_error)

    def _unwire_events(self) -> None:
        self.client.off("chat.message", self._on_chat_message)
        self.client.off("chat.stream.start", self._on_stream_start)
        self.client.off("chat.stream.chunk", self._on_stream_chunk)
        self.client.off("chat.stream.end", self._on_stream_end)
        self.client.off("chat.thinking", self._on_thinking)
        self.client.off("chat.proactive", self._on_chat_proactive)
        self.client.off("system.error", self._on_system_error)
        self.client.off("tool.start", self._on_tool_start)
        self.client.off("tool.result", self._on_tool_result)
        self.client.off("tool.error", self._on_tool_error)

    def showEvent(self, event: Any) -> None:
        super().showEvent(event)
        # Fade-in animation when showing the window
        self._opacity_effect.setOpacity(0.0)
        if self._show_animation is not None:
            self._show_animation.stop()
        self._show_animation = QPropertyAnimation(self._opacity_effect, b"opacity")
        self._show_animation.setDuration(200)
        self._show_animation.setStartValue(0.0)
        self._show_animation.setEndValue(1.0)
        self._show_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._show_animation.start()

    def closeEvent(self, event: Any) -> None:
        # For desktop pet companion, close means hide to preserve draft & state
        self.hide()
        self.closed.emit()
        event.ignore()

    def force_close(self) -> None:
        """Actually close the window for app quit."""
        self._unwire_events()
        if hasattr(self, "_connection_timer") and self._connection_timer is not None:
            self._connection_timer.stop()
        super().close()

    # ------------------------------------------------------------------
    # Connection status
    # ------------------------------------------------------------------

    def _on_gateway_connected(self) -> None:
        """Called when the Gateway connection is (re)established."""
        fire_and_forget(self._sync_tts_settings())

    async def _sync_tts_settings(self) -> None:
        try:
            settings = await self.client.request("system.get_settings")
            tts_enabled = bool(settings.get("tts_auto_play", False))
            self._tts_enabled = tts_enabled
            self.tts_toggle_btn.blockSignals(True)
            self.tts_toggle_btn.setChecked(tts_enabled)
            self.tts_toggle_btn.blockSignals(False)
            self._update_tts_button_appearance()
        except Exception as exc:
            self._logger.debug("Failed to sync TTS settings: %s", exc)

    def _start_connection_checker(self) -> None:
        self._connection_timer = QTimer(self)
        self._connection_timer.timeout.connect(self._check_connection)
        self._connection_timer.start(3000)
        self._check_connection()

    def _check_connection(self) -> None:
        connected = getattr(self.client, "connected", False)
        self.title_bar.set_connected(connected)
        if not connected:
            if not self._shown_disconnect_msg:
                self.show_system_message("未连接到 Gateway，请确认后台服务已启动。", is_error=True)
                self._shown_disconnect_msg = True
        else:
            self._shown_disconnect_msg = False

    # ------------------------------------------------------------------
    # Scroll behavior
    # ------------------------------------------------------------------

    def _on_scroll_changed(self, value: int) -> None:
        scrollbar = self.scroll_area.verticalScrollBar()
        self._is_near_bottom = scrollbar.value() >= scrollbar.maximum() - 40

    def _scroll_to_bottom(self) -> None:
        if self._is_near_bottom:
            QTimer.singleShot(100, self._do_scroll)

    def _do_scroll(self) -> None:
        scrollbar = self.scroll_area.verticalScrollBar()
        target = scrollbar.maximum()
        if target <= scrollbar.value():
            return
        if self._scroll_animation is not None:
            self._scroll_animation.stop()
        self._scroll_animation = QPropertyAnimation(scrollbar, b"value")
        self._scroll_animation.setDuration(250)
        self._scroll_animation.setStartValue(scrollbar.value())
        self._scroll_animation.setEndValue(target)
        self._scroll_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._scroll_animation.start()

    # ------------------------------------------------------------------
    # Input area
    # ------------------------------------------------------------------

    def _on_input_text_changed(self) -> None:
        doc = self.input_field.document()
        height = int(doc.size().height()) + 16
        new_height = max(56, min(height, 200))
        self.input_field.setFixedHeight(new_height)

    # ------------------------------------------------------------------
    # File drop / attachments
    # ------------------------------------------------------------------

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if path:
                self._add_attachment(path)
        event.acceptProposedAction()

    def _add_attachment(self, path: str) -> None:
        if path not in self._attachments:
            self._attachments.append(path)
            self._update_attachments_bar()

    def _remove_attachment(self, path: str) -> None:
        if path in self._attachments:
            self._attachments.remove(path)
            self._update_attachments_bar()

    def _update_attachments_bar(self) -> None:
        layout = self.attachments_bar.layout()
        if layout is None:
            return
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not self._attachments:
            self.attachments_bar.hide()
            return

        for path in self._attachments:
            name = Path(path).name
            pill = QWidget()
            pill_layout = QHBoxLayout(pill)
            pill_layout.setContentsMargins(8, 2, 4, 2)
            pill_layout.setSpacing(4)

            label = QLabel(f"附件 {name}")
            label.setToolTip(path)
            label.setStyleSheet(
                f"color: {MaterialTheme.on_primary_container}; font-size: 12px; "
                f"border: none; background: transparent;"
            )

            btn = QPushButton("✕")
            btn.setFixedSize(16, 16)
            btn.setStyleSheet(
                f"QPushButton {{ background-color: transparent; "
                f"color: {MaterialTheme.on_primary_container}; border: none; font-size: 11px; }}"
                f"QPushButton:hover {{ color: {MaterialTheme.error}; }}"
            )
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _checked=False, p=path: self._remove_attachment(p))

            pill_layout.addWidget(label)
            pill_layout.addWidget(btn)
            pill.setStyleSheet(
                f"background-color: {MaterialTheme.primary_container}; border-radius: 10px;"
            )
            pill.setMaximumHeight(24)
            layout.addWidget(pill)

        layout.addStretch()
        self.attachments_bar.show()

    def eventFilter(self, obj: Any, event: Any) -> bool:
        if obj is self.input_field and event.type() == event.Type.KeyPress:
            key = event.key()
            modifiers = event.modifiers()

            # Enter to send (no modifier)
            if (
                key in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
                and modifiers == Qt.KeyboardModifier.NoModifier
            ):
                self._on_send_clicked()
                return True

            # Ctrl+L: clear input
            if key == Qt.Key.Key_L and modifiers == Qt.KeyboardModifier.ControlModifier:
                self.input_field.clear()
                return True

            # Up arrow to recall last user message when input is empty
            if (
                key == Qt.Key.Key_Up
                and modifiers == Qt.KeyboardModifier.NoModifier
                and not self.input_field.toPlainText().strip()
            ):
                self._recall_last_message()
                return True

            # Esc: blur input or stop generating (placeholder)
            if key == Qt.Key.Key_Escape:
                self.input_field.clearFocus()
                return True

        # Show/hide session action buttons on hover (deferred to avoid
        # interrupting an active QPainter inside the effect pipeline).
        if isinstance(obj, QWidget) and obj.property("session_item") is True:
            if event.type() == event.Type.Enter:
                for child in obj.findChildren(QWidget):
                    if child.property("action_buttons") is True:
                        QTimer.singleShot(0, child.show)
                        break
            elif event.type() == event.Type.Leave:
                for child in obj.findChildren(QWidget):
                    if child.property("action_buttons") is True:
                        QTimer.singleShot(0, child.hide)
                        break

        return super().eventFilter(obj, event)

    def _recall_last_message(self) -> None:
        """Fill input with the last user message."""
        for i in range(self.messages_layout.count() - 1, -1, -1):
            item = self.messages_layout.itemAt(i)
            if item is None:
                continue
            widget = item.widget()
            if isinstance(widget, MessageBubble) and widget.role == "user":
                self.input_field.setPlainText(widget.get_text())
                # Move cursor to end
                cursor = self.input_field.textCursor()
                cursor.movePosition(cursor.MoveOperation.End)
                self.input_field.setTextCursor(cursor)
                break

    def _on_send_clicked(self) -> None:
        if self._is_sending:
            self._stop_generating()
            return
        text = self.input_field.toPlainText().strip()
        # Allow sending with attachments even if text is empty
        if not text and not self._attachments:
            return
        attachments = self._attachments.copy()
        self.input_field.clear()
        self._attachments.clear()
        self._update_attachments_bar()
        self._set_sending(True)
        self._add_user_bubble(text, attachments)
        asyncio.ensure_future(self._send_message(text, attachments))

    def _stop_generating(self) -> None:
        """Front-end stop: ignore remaining stream chunks and reset UI.

        Note: This does not interrupt the Gateway's LLM request, but it
        gives the user immediate control over the UI.
        """
        self._set_sending(False)
        self._hide_typing_indicator()
        # Finish any streaming bubble
        for bubble in list(self._message_bubbles.values()):
            if getattr(bubble, "_is_streaming", False):
                bubble.finish_streaming()
        self.show_system_message("已停止生成。")

    def _on_tts_toggle_clicked(self) -> None:
        """Toggle TTS on/off and sync to Gateway."""
        enabled = self.tts_toggle_btn.isChecked()
        self._tts_enabled = enabled
        self._update_tts_button_appearance()
        asyncio.ensure_future(
            self.client.request("system.update_settings", {"tts_auto_play": enabled})
        )
        self.show_system_message(f"语音朗读已{'开启' if enabled else '关闭'}")

    def _update_tts_button_appearance(self) -> None:
        """Update the TTS toggle button icon and tooltip."""
        if self._tts_enabled:
            self.tts_toggle_btn.setText("声")
            self.tts_toggle_btn.setToolTip("语音朗读已开启，点击静音")
        else:
            self.tts_toggle_btn.setText("静")
            self.tts_toggle_btn.setToolTip("语音朗读已关闭，点击开启")

    async def _send_message(self, text: str, attachments: list[str]) -> None:
        try:
            await self.client.send(
                {
                    "type": "request",
                    "method": "chat.stream",
                    "payload": {
                        "session_id": self._current_session_id or "main",
                        "content": text,
                        "attachments": attachments,
                    },
                }
            )
        except Exception as exc:
            self.show_system_message(f"发送失败：{exc}", is_error=True)
            self._set_sending(False)

    def _set_sending(self, sending: bool) -> None:
        self._is_sending = sending
        self.send_button.setEnabled(True)  # Always enabled so user can stop
        if sending:
            self.send_button.setText("停止")
            self.send_button.setStyleSheet(self._stop_btn_style)
            self.send_button.setToolTip("停止生成")
        else:
            self.send_button.setText("发送")
            self.send_button.setStyleSheet(self._send_btn_style)
            self.send_button.setToolTip("发送消息")

    # ------------------------------------------------------------------
    # Sessions
    # ------------------------------------------------------------------

    async def _load_sessions(self) -> None:
        try:
            resp = await self.client.request("session.list")
            sessions = resp.get("sessions", [])
            self._populate_session_list(sessions)
            if not self._current_session_id:
                if sessions:
                    await self._switch_session(sessions[0]["id"])
                else:
                    await self._create_session()
        except Exception as exc:
            self.show_system_message(f"加载会话失败：{exc}", is_error=True)

    def _populate_session_list(self, sessions: list[dict[str, Any]]) -> None:
        self.session_list.clear()
        self._session_items.clear()
        for s in sessions:
            sid = s.get("id", "")
            name = s.get("name", "Unnamed")
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, sid)
            self.session_list.addItem(item)
            self._session_items[sid] = item

            # Container widget with hover-aware action buttons
            container = QWidget()
            container.setProperty("session_item", True)
            container.setMouseTracking(True)
            container.installEventFilter(self)
            row = QHBoxLayout(container)
            row.setContentsMargins(12, 10, 8, 10)
            row.setSpacing(6)

            # Label: elide long names so buttons are always visible
            label = QLabel()
            label.setProperty("session_name", name)
            metrics = label.fontMetrics()
            elided = metrics.elidedText(name, Qt.TextElideMode.ElideRight, 180)
            label.setText(elided)
            label.setToolTip(name)
            label.setStyleSheet(
                f"color: {MaterialTheme.on_surface}; font-size: 13px; border: none; background: transparent;"
            )
            label.setWordWrap(False)
            label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            row.addWidget(label, stretch=1)

            # Action buttons container (shown on hover)
            btn_container = QWidget()
            btn_container.setProperty("action_buttons", True)
            btn_layout = QHBoxLayout(btn_container)
            btn_layout.setContentsMargins(0, 0, 0, 0)
            btn_layout.setSpacing(4)

            edit_btn = QPushButton("改")
            edit_btn.setFixedSize(24, 24)
            edit_btn.setStyleSheet(MaterialTheme.icon_button(size=24))
            edit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            edit_btn.setToolTip("重命名会话")
            edit_btn.clicked.connect(
                lambda _checked=False, sid=sid: asyncio.ensure_future(
                    self._rename_session_interactive(sid)
                )
            )
            btn_layout.addWidget(edit_btn)

            del_btn = QPushButton("删")
            del_btn.setFixedSize(24, 24)
            del_btn.setStyleSheet(MaterialTheme.icon_button(size=24, danger=True))
            del_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            del_btn.setToolTip("删除会话")
            del_btn.clicked.connect(
                lambda _checked=False, sid=sid: asyncio.ensure_future(self._delete_session(sid))
            )
            btn_layout.addWidget(del_btn)

            btn_container.hide()
            row.addWidget(btn_container)

            self.session_list.setItemWidget(item, container)
            item.setSizeHint(container.sizeHint())

        self._highlight_current_session()

    def _highlight_current_session(self) -> None:
        for sid, item in self._session_items.items():
            item.setSelected(sid == self._current_session_id)

    def _on_session_item_clicked(self, item: QListWidgetItem) -> None:
        if self._is_switching:
            return
        sid = item.data(Qt.ItemDataRole.UserRole)
        if sid and sid != self._current_session_id:
            asyncio.ensure_future(self._switch_session(sid))

    def _on_session_item_double_clicked(self, item: QListWidgetItem) -> None:
        sid = item.data(Qt.ItemDataRole.UserRole)
        if sid:
            asyncio.ensure_future(self._rename_session_interactive(sid))

    def _on_session_context_menu(self, position: Any) -> None:
        """Show a context menu on the session list for rename / delete."""
        item = self.session_list.itemAt(position)
        if item is None:
            return
        sid = item.data(Qt.ItemDataRole.UserRole)
        if not sid:
            return

        menu = QMenu(self)
        rename_action = menu.addAction("重命名")
        delete_action = menu.addAction("删除")
        action = menu.exec(self.session_list.mapToGlobal(position))
        if action == rename_action:
            asyncio.ensure_future(self._rename_session_interactive(sid))
        elif action == delete_action:
            asyncio.ensure_future(self._delete_session(sid))

    async def _switch_session(self, session_id: str) -> None:
        if self._is_switching:
            return
        self._is_switching = True
        try:
            self._is_near_bottom = True  # Reset scroll flag when switching sessions
            self._current_session_id = session_id
            self._highlight_current_session()
            self._clear_messages()
            try:
                await self.client.request("session.set_current", {"session_id": session_id})
            except Exception as exc:
                self.show_system_message(f"切换会话失败：{exc}", is_error=True)
            try:
                resp = await self.client.request(
                    "chat.history", {"session_id": session_id, "limit": 100}
                )
                messages = resp.get("messages", [])
                # Hide empty state immediately before loading any messages so it
                # doesn't float above partially-loaded history.
                if messages:
                    self._empty_state.hide()
                # Load in small batches so the event loop can process repaints
                # and keep the UI responsive during large history loads.
                batch_size = 15
                last_bubble: MessageBubble | None = None
                for i in range(0, len(messages), batch_size):
                    batch = messages[i : i + batch_size]
                    for msg in batch:
                        role = msg.get("role", "")
                        content = msg.get("content", "")
                        ts_raw = msg.get("created_at", "")
                        msg_id = msg.get("id", "")
                        ts = ""
                        if ts_raw:
                            try:
                                dt = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
                                ts = dt.strftime("%H:%M")
                            except Exception:
                                ts = ""
                        if role == "user":
                            bubble = MessageBubble(
                                "user",
                                content,
                                timestamp=ts,
                                message_id=msg_id,
                                parent=self.messages_container,
                                animate=False,
                            )
                            bubble.delete_requested.connect(self._on_bubble_delete_requested)
                            bubble.edit_requested.connect(self._on_bubble_edit_requested)
                            self.messages_layout.insertWidget(
                                self.messages_layout.count() - 1, bubble
                            )
                            self._message_bubbles[msg_id] = bubble
                            last_bubble = bubble
                        elif role == "assistant":
                            bubble = MessageBubble(
                                "assistant",
                                content,
                                timestamp=ts,
                                message_id=msg_id,
                                parent=self.messages_container,
                                animate=False,
                            )
                            bubble.delete_requested.connect(self._on_bubble_delete_requested)
                            bubble.regenerate_requested.connect(
                                self._on_bubble_regenerate_requested
                            )
                            self.messages_layout.insertWidget(
                                self.messages_layout.count() - 1, bubble
                            )
                            self._message_bubbles[msg_id] = bubble
                            last_bubble = bubble
                    if i + batch_size < len(messages):
                        await asyncio.sleep(0)  # yield to event loop
                if last_bubble is not None:
                    # Defer scrolling so queued paint events finish first.
                    QTimer.singleShot(
                        0, lambda b=last_bubble: self.scroll_area.ensureWidgetVisible(b, 0, 0)
                    )
                    self._scroll_to_bottom()
            except Exception as exc:
                self.show_system_message(f"加载历史消息失败：{exc}", is_error=True)
        finally:
            self._is_switching = False

    def _clear_messages(self) -> None:
        # Remove all items from layout, then re-add empty_state + stretch.
        while self.messages_layout.count():
            item = self.messages_layout.takeAt(0)
            if item is None:
                continue
            widget = item.widget()
            if widget is self._empty_state:
                # Remove the old layout item so it doesn't leak.
                del item
                continue
            if widget is not None:
                widget.deleteLater()
            # Explicitly free the layout item; QSpacerItem is not a QObject
            # and will leak if we rely solely on Python GC.
            del item
        if self._empty_state is not None:
            self.messages_layout.addWidget(self._empty_state)
            self._empty_state.show()
        self.messages_layout.addStretch()
        self._message_bubbles.clear()
        self._tool_cards.clear()
        self._typing_indicator = None

    async def _create_session(self) -> None:
        try:
            resp = await self.client.request("session.create", {"name": "新对话"})
            session = resp.get("session", {})
            sid = session.get("id", "")
            await self._load_sessions()
            if sid:
                await self._switch_session(sid)
        except Exception as exc:
            self.show_system_message(f"创建会话失败：{exc}", is_error=True)

    async def _delete_session(self, session_id: str) -> None:
        confirmed = await self._async_question(
            "删除会话",
            "确定要删除这个会话吗？\n此操作无法撤销。",
        )
        if not confirmed:
            return
        try:
            resp = await self.client.request("session.delete", {"session_id": session_id})
            if resp.get("success"):
                await self._load_sessions()
                if self._current_session_id == session_id:
                    if self._session_items:
                        first_sid = next(iter(self._session_items))
                        await self._switch_session(first_sid)
                    else:
                        await self._create_session()
        except Exception as exc:
            self.show_system_message(f"删除会话失败：{exc}", is_error=True)

    async def _clear_current_session(self) -> None:
        """Clear all messages in the current session."""
        if not self._current_session_id:
            return
        confirmed = await self._async_question(
            "清空会话",
            "确定要清空当前会话的所有消息吗？\n此操作无法撤销。",
        )
        if not confirmed:
            return
        try:
            resp = await self.client.request("chat.clear", {"session_id": self._current_session_id})
            if resp.get("success"):
                self._clear_messages()
        except Exception as exc:
            self.show_system_message(f"清空会话失败：{exc}", is_error=True)

    async def _rename_session_interactive(self, session_id: str) -> None:
        """Rename a session via a simple input dialog."""
        current_name = ""
        for i in range(self.session_list.count()):
            item = self.session_list.item(i)
            if item and item.data(Qt.ItemDataRole.UserRole) == session_id:
                widget = self.session_list.itemWidget(item)
                if widget:
                    label = widget.findChild(QLabel)
                    if label:
                        current_name = label.property("session_name") or label.text()
                break
        name, ok = await self._async_get_text("重命名会话", "新名称：", text=current_name)
        if ok and name.strip():
            try:
                resp = await self.client.request(
                    "session.rename", {"session_id": session_id, "name": name.strip()}
                )
                if resp.get("success"):
                    await self._load_sessions()
            except Exception as exc:
                self.show_system_message(f"重命名会话失败：{exc}", is_error=True)

    async def _async_question(self, title: str, text: str) -> bool:
        """Show a non-blocking QMessageBox and await the result."""
        msg_box = QMessageBox(self)
        msg_box.setWindowTitle(title)
        msg_box.setText(text)
        msg_box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        msg_box.setDefaultButton(QMessageBox.StandardButton.No)
        future = asyncio.get_running_loop().create_future()
        msg_box.finished.connect(
            lambda result: future.set_result(result == QMessageBox.StandardButton.Yes)
        )
        msg_box.open()
        return await future

    async def _async_get_text(self, title: str, label: str, text: str = "") -> tuple[str, bool]:
        """Show a non-blocking QInputDialog and await the result."""
        from PySide6.QtWidgets import QInputDialog

        dialog = QInputDialog(self)
        dialog.setWindowTitle(title)
        dialog.setLabelText(label)
        dialog.setTextValue(text)
        future = asyncio.get_running_loop().create_future()
        dialog.accepted.connect(lambda: future.set_result((dialog.textValue(), True)))
        dialog.rejected.connect(lambda: future.set_result((dialog.textValue(), False)))
        dialog.open()
        return await future

    # ------------------------------------------------------------------
    # Providers
    # ------------------------------------------------------------------

    async def _load_providers(self) -> None:
        try:
            providers_data = await self.client.request("provider.list")
            current_data = await self.client.request("provider.get_current")
            self._providers = providers_data.get("providers", [])
            self._current_provider_id = current_data.get("current_provider_id")
            self._current_model = current_data.get("current_model")
            self._populate_model_selector()
            self._show_provider_config_hint(current_data)
        except Exception as exc:
            self.show_system_message(f"加载模型服务商失败：{exc}", is_error=True)

    def _show_provider_config_hint(self, current_data: dict[str, Any]) -> None:
        if self._provider_config_warning_shown:
            return
        if current_data.get("is_configured", True):
            return
        provider = current_data.get("provider") or {}
        provider_name = (
            provider.get("name") or current_data.get("current_provider_id") or "current provider"
        )
        config_path = current_data.get("config_path") or "config/providers.toml"
        self.show_system_message(
            f"{provider_name} 缺少 API Key，当前会降级为本地 Echo。"
            f"请点击右上角设置按钮配置，或编辑 {config_path}。",
            is_error=True,
        )
        self._provider_config_warning_shown = True

    def _populate_model_selector(self) -> None:
        self.model_selector.blockSignals(True)
        self.model_selector.clear()
        selected_index = -1
        for provider in self._providers:
            pid = provider.get("id", "")
            name = provider.get("name", pid)
            for m in provider.get("models", []):
                display = f"{name} / {m}"
                self.model_selector.addItem(display, {"provider_id": pid, "model": m})
                if pid == self._current_provider_id and m == self._current_model:
                    selected_index = self.model_selector.count() - 1
        if selected_index >= 0:
            self.model_selector.setCurrentIndex(selected_index)
        self.model_selector.blockSignals(False)

    def _on_model_changed(self, index: int) -> None:
        data = self.model_selector.itemData(index)
        if data:
            asyncio.ensure_future(self._set_model(data["provider_id"], data["model"]))

    async def _set_model(self, provider_id: str, model: str) -> None:
        try:
            resp = await self.client.request(
                "provider.set_current",
                {"provider_id": provider_id, "model": model},
            )
            if not resp.get("success"):
                self.show_system_message("切换模型失败：服务商或模型无效。", is_error=True)
                self._populate_model_selector()
                return
            self._current_provider_id = provider_id
            self._current_model = model
        except Exception as exc:
            self.show_system_message(f"设置模型失败：{exc}", is_error=True)
            self._populate_model_selector()

    def _on_manage_providers(self) -> None:
        from aipet.frontend.provider_dialog import ProviderDialog

        if hasattr(self, "_provider_dialog") and self._provider_dialog is not None:
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
        dialog.providers_changed.connect(lambda: asyncio.ensure_future(self._load_providers()))
        asyncio.ensure_future(self._load_and_show_provider_dialog(dialog))

    async def _load_and_show_provider_dialog(self, dialog: ProviderDialog) -> None:
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
        await dialog._load_providers()

    # ------------------------------------------------------------------
    # Bubble management
    # ------------------------------------------------------------------

    def _on_bubble_delete_requested(self, message_id: str) -> None:
        """Remove a single bubble from the UI."""
        reply = QMessageBox.question(
            self,
            "删除消息",
            "确定要删除这条消息吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        for i in range(self.messages_layout.count() - 1, -1, -1):
            item = self.messages_layout.itemAt(i)
            if item is None:
                continue
            widget = item.widget()
            if isinstance(widget, MessageBubble) and widget.message_id == message_id:
                self.messages_layout.removeWidget(widget)
                widget.deleteLater()
                if message_id in self._message_bubbles:
                    del self._message_bubbles[message_id]
                break

    def _on_bubble_regenerate_requested(self, message_id: str) -> None:
        """Regenerate an assistant response by resending the preceding user message."""
        user_text = ""
        for i in range(self.messages_layout.count() - 1, -1, -1):
            item = self.messages_layout.itemAt(i)
            if item is None:
                continue
            widget = item.widget()
            if isinstance(widget, MessageBubble) and widget.message_id == message_id:
                continue
            if isinstance(widget, MessageBubble) and widget.role == "user":
                user_text = widget.get_text()
                break
        if not user_text:
            self.show_system_message(
                "无法重新生成：没有找到上一条用户消息。", is_error=True
            )
            return
        # Remove the assistant bubble
        self._on_bubble_delete_requested(message_id)
        # Resend the user message
        self._set_sending(True)
        self._add_user_bubble(user_text)
        asyncio.ensure_future(self._send_message(user_text))

    def _on_bubble_edit_requested(self, message_id: str) -> None:
        """Edit a user message: fill input field and remove this message and all after it."""
        edit_text = ""
        edit_idx = -1
        for i in range(self.messages_layout.count() - 1, -1, -1):
            item = self.messages_layout.itemAt(i)
            if item is None:
                continue
            widget = item.widget()
            if (
                isinstance(widget, MessageBubble)
                and widget.message_id == message_id
                and widget.role == "user"
            ):
                edit_text = widget.get_text()
                edit_idx = i
                break
        if not edit_text or edit_idx < 0:
            return
        # Fill input field
        self.input_field.setPlainText(edit_text)
        cursor = self.input_field.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self.input_field.setTextCursor(cursor)
        # Remove from this bubble to the end
        for i in range(self.messages_layout.count() - 1, edit_idx - 1, -1):
            item = self.messages_layout.itemAt(i)
            if item is None:
                continue
            widget = item.widget()
            if isinstance(widget, MessageBubble):
                self.messages_layout.removeWidget(widget)
                mid = widget.message_id
                if mid in self._message_bubbles:
                    del self._message_bubbles[mid]
                widget.deleteLater()

    # ------------------------------------------------------------------
    # Chat output handlers
    # ------------------------------------------------------------------

    def _add_user_bubble(self, text: str, attachments: list[str] | None = None) -> None:
        ts = datetime.now().strftime("%H:%M")
        display_text = text
        if attachments:
            file_lines = "\n".join(f"附件 {Path(a).name}" for a in attachments)
            display_text = f"{display_text}\n\n{file_lines}" if display_text else file_lines
        bubble = MessageBubble("user", display_text, timestamp=ts, parent=self.messages_container)
        bubble.delete_requested.connect(self._on_bubble_delete_requested)
        bubble.edit_requested.connect(self._on_bubble_edit_requested)
        self.messages_layout.insertWidget(self.messages_layout.count() - 1, bubble)
        self._empty_state.hide()
        # Defer scrolling so the bubble's opacity animation (QGraphicsOpacityEffect)
        # doesn't collide with the scroll area's paint engine.
        QTimer.singleShot(0, lambda b=bubble: self.scroll_area.ensureWidgetVisible(b, 0, 0))
        self._scroll_to_bottom()

    def _on_chat_message(self, payload: dict[str, Any]) -> None:
        if payload.get("session_id") != self._current_session_id:
            return
        ts = datetime.now().strftime("%H:%M")
        self._add_assistant_bubble(payload.get("content", ""), payload.get("message_id", ""), ts)

    def _on_stream_start(self, payload: dict[str, Any]) -> None:
        if payload.get("session_id") != self._current_session_id:
            return
        msg_id = payload.get("message_id", "")
        self._show_typing_indicator()
        ts = datetime.now().strftime("%H:%M")
        bubble = MessageBubble(
            "assistant", "", timestamp=ts, message_id=msg_id, parent=self.messages_container
        )
        bubble.set_streaming(True)
        bubble.delete_requested.connect(self._on_bubble_delete_requested)
        bubble.regenerate_requested.connect(self._on_bubble_regenerate_requested)
        self._message_bubbles[msg_id] = bubble
        self.messages_layout.insertWidget(self.messages_layout.count() - 1, bubble)
        self._empty_state.hide()
        self._scroll_to_bottom()

    def _on_stream_chunk(self, payload: dict[str, Any]) -> None:
        msg_id = payload.get("message_id", "")
        if msg_id not in self._message_bubbles:
            return
        if payload.get("session_id") != self._current_session_id:
            return
        delta = payload.get("delta", "")
        self._hide_typing_indicator()
        self._message_bubbles[msg_id].append_text(delta)
        self._scroll_to_bottom()

    def _on_stream_end(self, payload: dict[str, Any]) -> None:
        if payload.get("session_id") != self._current_session_id:
            return
        msg_id = payload.get("message_id", "")
        bubble = self._message_bubbles.get(msg_id)
        has_tool_calls = payload.get("has_tool_calls", False)
        if bubble:
            if has_tool_calls:
                # Keep bubble in streaming state while tools execute
                bubble.show_tool_status("正在分析...")
            else:
                bubble.finish_streaming()
        else:
            # Fallback: finish any streaming bubble
            for b in self._message_bubbles.values():
                if getattr(b, "_is_streaming", False):
                    if has_tool_calls:
                        b.show_tool_status("正在分析...")
                    else:
                        b.finish_streaming()
                    break
        self._hide_typing_indicator()
        if not has_tool_calls:
            self._set_sending(False)

    def _on_thinking(self, payload: dict[str, Any]) -> None:
        """Handle chat.thinking events (tool execution in progress)."""
        if payload.get("session_id") != self._current_session_id:
            return
        msg_id = payload.get("message_id", "")
        status = payload.get("status", "")
        tool_names = payload.get("tool_names", [])
        bubble = self._message_bubbles.get(msg_id)
        if bubble and status == "executing_tools":
            names_str = ", ".join(tool_names[:3])
            if len(tool_names) > 3:
                names_str += f" 等{len(tool_names)}个"
            bubble.show_tool_status(f"正在执行：{names_str}...")
            self._scroll_to_bottom()

    def _on_chat_proactive(self, payload: dict[str, Any]) -> None:
        self._logger.debug(
            "Received chat.proactive: session=%s, content=%r",
            payload.get("session_id"),
            payload.get("content", "")[:30],
        )
        sid = payload.get("session_id", "")
        content = payload.get("content", "")
        msg_id = payload.get("message_id", "")
        ts = datetime.now().strftime("%H:%M")

        if sid != self._current_session_id:
            self._logger.debug("Auto-switching to session %s for proactive message", sid)
            asyncio.ensure_future(self._switch_session_and_show_proactive(sid, content, msg_id, ts))
            return
        self._add_assistant_bubble(content, msg_id, ts)

    async def _switch_session_and_show_proactive(
        self, session_id: str, content: str, msg_id: str, timestamp: str
    ) -> None:
        await self._switch_session(session_id)
        self._add_assistant_bubble(content, msg_id, timestamp)

    def _on_system_error(self, payload: dict[str, Any]) -> None:
        self.show_system_message(f"发生错误：{payload.get('message', '未知错误')}", is_error=True)
        self._set_sending(False)

    def _on_tool_start(self, payload: dict[str, Any]) -> None:
        if payload.get("session_id") != self._current_session_id:
            return
        tool_call_id = payload.get("tool_call_id", "")
        if not tool_call_id or tool_call_id in self._tool_cards:
            return
        name = payload.get("name", "tool")
        arguments = payload.get("arguments", {})
        card = ToolCard(tool_call_id, name, arguments, parent=self.messages_container)
        self._tool_cards[tool_call_id] = card
        self.messages_layout.insertWidget(self.messages_layout.count() - 1, card)
        self._empty_state.hide()
        self._scroll_to_bottom()
        # Update the latest assistant bubble to show which tool is running
        for bubble in reversed(list(self._message_bubbles.values())):
            if bubble.role == "assistant" and getattr(bubble, "_is_streaming", False):
                bubble.show_tool_status(f"正在执行：{name}...")
                break

    def _on_tool_result(self, payload: dict[str, Any]) -> None:
        if payload.get("session_id") != self._current_session_id:
            return
        tool_call_id = payload.get("tool_call_id", "")
        card = self._tool_cards.get(tool_call_id)
        if card:
            card.set_done(payload.get("result", ""))
            self._scroll_to_bottom()
        # Update bubble status to show completion of this tool
        for bubble in reversed(list(self._message_bubbles.values())):
            if bubble.role == "assistant" and getattr(bubble, "_is_streaming", False):
                bubble.show_tool_status("等待下一步...")
                break

    def _on_tool_error(self, payload: dict[str, Any]) -> None:
        if payload.get("session_id") != self._current_session_id:
            return
        tool_call_id = payload.get("tool_call_id", "")
        name = payload.get("name", "tool")
        error = payload.get("error", "Unknown error")
        card = self._tool_cards.get(tool_call_id)
        if card:
            card.set_error(error)
            self._scroll_to_bottom()
        # Show a visible system message so the user notices the failure
        self.show_system_message(f"工具 {name} 执行失败: {error}", is_error=True)
        # Also update the assistant bubble
        for bubble in reversed(list(self._message_bubbles.values())):
            if bubble.role == "assistant" and getattr(bubble, "_is_streaming", False):
                bubble.show_tool_status(f"{name} 失败")
                break

    def _add_assistant_bubble(self, text: str, msg_id: str = "", timestamp: str = "") -> None:
        ts = timestamp or datetime.now().strftime("%H:%M")
        if msg_id and msg_id in self._message_bubbles:
            bubble = self._message_bubbles[msg_id]
            bubble.set_text(text)
            bubble.finish_streaming()
            bubble.hide_tool_status()
            self._set_sending(False)
            return
        bubble = MessageBubble(
            "assistant", text, timestamp=ts, message_id=msg_id, parent=self.messages_container
        )
        bubble.delete_requested.connect(self._on_bubble_delete_requested)
        bubble.regenerate_requested.connect(self._on_bubble_regenerate_requested)
        if msg_id:
            self._message_bubbles[msg_id] = bubble
        self.messages_layout.insertWidget(self.messages_layout.count() - 1, bubble)
        self._empty_state.hide()
        # Defer scrolling so the bubble's opacity animation (QGraphicsOpacityEffect)
        # doesn't collide with the scroll area's paint engine.
        QTimer.singleShot(0, lambda b=bubble: self.scroll_area.ensureWidgetVisible(b, 0, 0))
        self._scroll_to_bottom()

    def show_system_message(self, text: str, is_error: bool = False) -> None:
        bubble = MessageBubble("system", text, parent=self.messages_container, is_error=is_error)
        bubble.delete_requested.connect(self._on_bubble_delete_requested)
        self.messages_layout.insertWidget(self.messages_layout.count() - 1, bubble)
        self._empty_state.hide()
        self._scroll_to_bottom()

    def _show_typing_indicator(self) -> None:
        if self._typing_indicator is None:
            self._typing_indicator = QLabel("正在输入...")
            self._typing_indicator.setStyleSheet(
                f"QLabel {{ color: {MaterialTheme.outline}; padding: 6px 12px; font-size: 12px; }}"
            )
            self.messages_layout.insertWidget(
                self.messages_layout.count() - 1, self._typing_indicator
            )
            self._scroll_to_bottom()

    def _hide_typing_indicator(self) -> None:
        if self._typing_indicator is not None:
            self._typing_indicator.deleteLater()
            self._typing_indicator = None
