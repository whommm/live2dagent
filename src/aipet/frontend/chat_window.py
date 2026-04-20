"""Chat window with bubbles, streaming, model selector and session sidebar."""

from __future__ import annotations

import asyncio
import contextlib
import re
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from markdown_it import MarkdownIt
from PySide6.QtCore import Qt, QTimer, Signal, QPropertyAnimation, QEasingCurve
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QFont, QMouseEvent, QTextDocument

from pygments import highlight
from pygments.lexers import get_lexer_by_name, guess_lexer
from pygments.formatters import HtmlFormatter
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTextBrowser,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


from aipet.frontend.client import GatewayClient
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
    ) -> None:
        super().__init__(parent)
        self.role = role
        self.message_id = message_id
        self._is_error = is_error
        self._is_streaming = False
        self._stream_buffer = ""
        self._setup_ui(content, timestamp)
        self._apply_style()

    def _setup_ui(self, content: str, timestamp: str | None) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(8, 6, 8, 6)
        main_layout.setSpacing(4)

        self.time_label = QLabel(timestamp or "")
        self.time_label.setFont(QFont(MaterialTheme.font_family, 8))
        self.time_label.setStyleSheet(f"color: {MaterialTheme.outline};")
        self.time_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        main_layout.addWidget(self.time_label)

        row = QHBoxLayout()
        row.setSpacing(10)
        row.setContentsMargins(0, 0, 0, 0)

        self.avatar = QLabel()
        self.avatar.setFixedSize(36, 36)
        self.avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.avatar.setStyleSheet(
            f"background-color: {MaterialTheme.surface_variant}; border-radius: 18px; font-size: 16px;"
        )

        # Content column: bubble container + toolbar
        content_col = QVBoxLayout()
        content_col.setSpacing(2)
        content_col.setContentsMargins(0, 0, 0, 0)
        content_col.setAlignment(Qt.AlignmentFlag.AlignTop)

        # Bubble container handles background, radius, padding
        self.bubble_container = QWidget()
        bubble_layout = QVBoxLayout(self.bubble_container)
        bubble_layout.setContentsMargins(12, 10, 12, 10)
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
            color = MaterialTheme.on_secondary_container if self.role == "assistant" else MaterialTheme.on_surface
            browser.setHtml(self._markdown_to_html(self._clean_html_tags(self._strip_live2d_tags(content)), text_color=color))
            self.text_display = browser
            bubble_layout.addWidget(self.text_display)
        else:
            label = QLabel(self._clean_html_tags(self._strip_live2d_tags(content)))
            label.setWordWrap(True)
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            label.setFont(QFont(MaterialTheme.font_family, 11))
            self.text_display = label
            bubble_layout.addWidget(self.text_display)

        self.text_display.setMaximumWidth(460)
        content_col.addWidget(self.bubble_container)

        # Toolbar with copy button (uses opacity effect to avoid layout resize)
        self.toolbar = QHBoxLayout()
        self.toolbar.setSpacing(4)
        self.toolbar.setContentsMargins(0, 0, 0, 0)
        self.toolbar.addStretch()

        self.copy_btn = QPushButton("📋")
        self.copy_btn.setFixedSize(26, 26)
        self.copy_btn.setStyleSheet(
            f"QPushButton {{ background-color: transparent; color: {MaterialTheme.on_surface_variant}; "
            f"border: none; border-radius: 13px; font-size: 12px; padding: 0px; }}"
            f"QPushButton:hover {{ background-color: {MaterialTheme._alpha(MaterialTheme.on_surface, 8)}; }}"
        )
        self.copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.copy_btn.setToolTip("Copy message")
        self.copy_btn.clicked.connect(self._on_copy_clicked)
        self.toolbar.addWidget(self.copy_btn)

        self.delete_btn = QPushButton("🗑️")
        self.delete_btn.setFixedSize(26, 26)
        self.delete_btn.setStyleSheet(
            f"QPushButton {{ background-color: transparent; color: {MaterialTheme.on_surface_variant}; "
            f"border: none; border-radius: 13px; font-size: 12px; padding: 0px; }}"
            f"QPushButton:hover {{ background-color: {MaterialTheme.error}; color: {MaterialTheme.on_error}; }}"
        )
        self.delete_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.delete_btn.setToolTip("Delete message")
        self.delete_btn.clicked.connect(self._on_delete_clicked)
        self.toolbar.addWidget(self.delete_btn)

        # Regenerate for assistant, Edit for user
        if self.role == "assistant":
            self.regenerate_btn = QPushButton("🔄")
            self.regenerate_btn.setFixedSize(26, 26)
            self.regenerate_btn.setStyleSheet(
                f"QPushButton {{ background-color: transparent; color: {MaterialTheme.on_surface_variant}; "
                f"border: none; border-radius: 13px; font-size: 12px; padding: 0px; }}"
                f"QPushButton:hover {{ background-color: {MaterialTheme.primary_container}; color: {MaterialTheme.on_primary_container}; }}"
            )
            self.regenerate_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self.regenerate_btn.setToolTip("Regenerate response")
            self.regenerate_btn.clicked.connect(self._on_regenerate_clicked)
            self.toolbar.addWidget(self.regenerate_btn)
        elif self.role == "user":
            self.edit_btn = QPushButton("✏️")
            self.edit_btn.setFixedSize(26, 26)
            self.edit_btn.setStyleSheet(
                f"QPushButton {{ background-color: transparent; color: {MaterialTheme.on_surface_variant}; "
                f"border: none; border-radius: 13px; font-size: 12px; padding: 0px; }}"
                f"QPushButton:hover {{ background-color: {MaterialTheme.primary_container}; color: {MaterialTheme.on_primary_container}; }}"
            )
            self.edit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self.edit_btn.setToolTip("Edit and resend")
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

        if self.role == "user":
            self.avatar.setText("👤")
            row.addStretch()
            row.addLayout(content_col)
            row.addWidget(self.avatar)
        elif self.role == "system":
            self.avatar.hide()
            row.addStretch()
            row.addLayout(content_col)
            row.addStretch()
        else:
            self.avatar.setText("🐾")
            row.addWidget(self.avatar)
            row.addLayout(content_col)
            row.addStretch()

        main_layout.addLayout(row)

    def _apply_style(self) -> None:
        if self.role == "user":
            # Use plain property syntax (no QWidget selector) to avoid cascading to children
            self.bubble_container.setStyleSheet(
                f"background-color: {MaterialTheme.primary_container}; border-radius: 16px;"
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
                f"background-color: {MaterialTheme.secondary_container}; border-radius: 16px;"
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
        self._update_text_height()

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
            doc.setTextWidth(max_w if max_w > 0 else 460)
            new_height = max(int(doc.size().height()) + 8, 24)
            self.text_display.setFixedHeight(new_height)
            self.text_display.updateGeometry()

    def _on_copy_clicked(self) -> None:
        text = self.get_text()
        QApplication.clipboard().setText(text)
        self.copy_btn.setText("✅")
        QTimer.singleShot(1500, lambda: self.copy_btn.setText("📋"))

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
            r"```", r"\*\*", r"__", r"`[^`]+`", r"^#{1,6} ",
            r"^\s*[-*+] ", r"^\s*\d+\. ", r"\[.*?\]\(.*?\)",
            r"^\s*> ", r"\n\s*---\s*\n",
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
                if lang:
                    lexer = get_lexer_by_name(lang, stripall=True)
                else:
                    lexer = guess_lexer(code)
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
            r'<pre><code>(.*?)</code></pre>',
            lambda m: _replace_block(re.match(r'<pre><code class="language-">(.*?)</code></pre>', '<pre><code class="language-">' + m.group(1) + '</code></pre>')),
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
        """Remove [expression:xxx] and [motion:xxx] tags from displayed text."""
        return re.sub(r"\[\s*(expression|motion)\s*:\s*[^\[\]]+?\s*\]", "", text).strip()

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
                color = MaterialTheme.on_secondary_container if self.role == "assistant" else MaterialTheme.on_surface
                self.text_display.setHtml(self._markdown_to_html(new_text, text_color=color))
                self._update_text_height()
        else:
            self.text_display.setText(self._clean_html_tags(self._strip_live2d_tags(self.text_display.text() + text)))

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
            color = MaterialTheme.on_secondary_container if self.role == "assistant" else MaterialTheme.on_surface
            self.text_display.setHtml(self._markdown_to_html(final_text, text_color=color))
            self._update_text_height()

    def set_text(self, text: str) -> None:
        cleaned = self._clean_html_tags(self._strip_live2d_tags(text))
        if isinstance(self.text_display, QTextBrowser):
            color = MaterialTheme.on_secondary_container if self.role == "assistant" else MaterialTheme.on_surface
            self.text_display.setHtml(self._markdown_to_html(cleaned, text_color=color))
            self._update_text_height()
        else:
            self.text_display.setText(cleaned)

    def get_text(self) -> str:
        if isinstance(self.text_display, QTextBrowser):
            return str(self.text_display.toPlainText()).strip()
        return str(self.text_display.text()).strip()


class ToolCard(QWidget):
    """A visual card showing tool call status (running / done / error)."""

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
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Main card container
        self.container = QWidget()
        container_layout = QVBoxLayout(self.container)
        container_layout.setContentsMargins(12, 10, 12, 10)
        container_layout.setSpacing(6)

        # Header row: icon + name + status
        header = QHBoxLayout()
        header.setSpacing(8)
        header.setContentsMargins(0, 0, 0, 0)

        icon = QLabel("🔧")
        icon.setStyleSheet("font-size: 14px; border: none; background: transparent;")

        self.name_label = QLabel(self.name)
        self.name_label.setFont(QFont(MaterialTheme.font_family, 12, QFont.Weight.Medium))
        self.name_label.setStyleSheet(f"color: {MaterialTheme.on_surface_variant}; border: none; background: transparent;")

        self.status_label = QLabel("● 正在调用...")
        self.status_label.setFont(QFont(MaterialTheme.font_family, 11))
        self.status_label.setStyleSheet(f"color: {MaterialTheme.primary}; border: none; background: transparent;")

        header.addWidget(icon)
        header.addWidget(self.name_label, stretch=1)
        header.addWidget(self.status_label)

        # Expand/collapse button for details
        self.expand_btn = QPushButton("展开")
        self.expand_btn.setFixedSize(40, 22)
        self.expand_btn.setStyleSheet(
            f"QPushButton {{ background-color: transparent; color: {MaterialTheme.outline}; "
            f"border: none; border-radius: 4px; font-size: 11px; padding: 0px; }}"
            f"QPushButton:hover {{ color: {MaterialTheme.on_surface}; }}"
        )
        self.expand_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.expand_btn.clicked.connect(self._toggle_expand)
        header.addWidget(self.expand_btn)

        container_layout.addLayout(header)

        # Detail area (arguments + result), hidden by default
        self.detail_widget = QWidget()
        self.detail_widget.hide()
        detail_layout = QVBoxLayout(self.detail_widget)
        detail_layout.setContentsMargins(0, 0, 0, 0)
        detail_layout.setSpacing(4)

        # Arguments
        args_text = QLabel(f"参数: {self._fmt_dict(self.arguments)}")
        args_text.setFont(QFont(MaterialTheme.font_family, 10))
        args_text.setStyleSheet(f"color: {MaterialTheme.outline}; border: none; background: transparent;")
        args_text.setWordWrap(True)
        detail_layout.addWidget(args_text)

        # Result placeholder
        self.result_label = QLabel("")
        self.result_label.setFont(QFont(MaterialTheme.font_family, 10))
        self.result_label.setStyleSheet(f"color: {MaterialTheme.on_surface_variant}; border: none; background: transparent;")
        self.result_label.setWordWrap(True)
        detail_layout.addWidget(self.result_label)

        container_layout.addWidget(self.detail_widget)

        # Left colored border strip via container stylesheet
        self.container.setStyleSheet(
            f"QWidget {{ background-color: {MaterialTheme.surface_variant}; border-radius: 12px; }}"
        )
        layout.addWidget(self.container)

        # Fixed width to make it look like a sub-item
        self.setMaximumWidth(420)

    def _fmt_dict(self, d: dict[str, Any]) -> str:
        import json
        try:
            return json.dumps(d, ensure_ascii=False)
        except Exception:
            return str(d)

    def _toggle_expand(self) -> None:
        self._is_expanded = not self._is_expanded
        self.detail_widget.setVisible(self._is_expanded)
        self.expand_btn.setText("收起" if self._is_expanded else "展开")

    def set_done(self, result: str) -> None:
        self.status_label.setText("✓ 已完成")
        self.status_label.setStyleSheet(f"color: {MaterialTheme.success}; border: none; background: transparent;")
        self.result_label.setText(f"结果: {result[:200]}{'...' if len(result) > 200 else ''}")
        # Auto-expand on error or if result is interesting
        if "error" in result.lower() or "Error" in result:
            self.status_label.setText("✓ 已完成（有警告）")
            self.status_label.setStyleSheet(f"color: {MaterialTheme.error}; border: none; background: transparent;")

    def set_error(self, error: str) -> None:
        self.status_label.setText("✗ 失败")
        self.status_label.setStyleSheet(f"color: {MaterialTheme.error}; border: none; background: transparent;")
        self.result_label.setText(f"错误: {error}")
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
            f"background-color: {MaterialTheme.surface}; border-bottom: 1px solid {MaterialTheme.outline_variant};"
        )
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 8, 0)
        layout.setSpacing(4)

        self.title_label = QLabel("Chat with AIPet")
        self.title_label.setStyleSheet(
            f"color: {MaterialTheme.on_surface}; font-size: 14px; font-weight: 500; border: none;"
        )
        layout.addWidget(self.title_label)

        # Connection status indicator
        self.status_label = QLabel("● Online")
        self.status_label.setStyleSheet(
            "color: #4CAF50; font-size: 11px; border: none; font-weight: 500; padding: 2px 8px; "
            "background-color: #E8F5E9; border-radius: 10px;"
        )
        self.status_label.setToolTip("Connected to Gateway")
        layout.addWidget(self.status_label)

        layout.addStretch()

        btn_style = (
            f"QPushButton {{ background-color: transparent; color: {MaterialTheme.on_surface_variant}; "
            f"border: none; border-radius: 12px; font-size: 14px; font-weight: bold; padding: 4px 10px; }}"
            f"QPushButton:hover {{ background-color: {MaterialTheme._alpha(MaterialTheme.on_surface, 8)}; }}"
            f"QPushButton:pressed {{ background-color: {MaterialTheme._alpha(MaterialTheme.on_surface, 12)}; }}"
        )
        close_style = (
            f"QPushButton {{ background-color: transparent; color: {MaterialTheme.on_surface_variant}; "
            f"border: none; border-radius: 12px; font-size: 14px; font-weight: bold; padding: 4px 10px; }}"
            f"QPushButton:hover {{ background-color: {MaterialTheme.error}; color: {MaterialTheme.on_error}; }}"
        )

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
            self.status_label.setText("● Online")
            self.status_label.setStyleSheet(
                f"color: {MaterialTheme.on_success_container}; font-size: 11px; border: none; font-weight: 500; padding: 2px 8px; "
                f"background-color: {MaterialTheme.success_container}; border-radius: 10px;"
            )
            self.status_label.setToolTip("Connected to Gateway")
        else:
            self.status_label.setText("● Offline")
            self.status_label.setStyleSheet(
                f"color: {MaterialTheme.on_error_container}; font-size: 11px; border: none; font-weight: 500; padding: 2px 8px; "
                f"background-color: {MaterialTheme.error_container}; border-radius: 10px;"
            )
            self.status_label.setToolTip("Disconnected from Gateway")

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

    def __init__(self, client: GatewayClient, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.client = client
        self._message_bubbles: dict[str, MessageBubble] = {}
        self._typing_indicator: QLabel | None = None
        self._tool_cards: dict[str, ToolCard] = {}
        self._providers: list[dict[str, Any]] = []
        self._current_provider_id: str | None = None
        self._current_model: str | None = None
        self._current_session_id: str = ""
        self._session_items: dict[str, QListWidgetItem] = {}
        self._is_sending = False
        self._is_near_bottom = True
        self._provider_dialog: ProviderDialog | None = None
        self._shown_disconnect_msg = False
        self._scroll_animation: QPropertyAnimation | None = None
        self._attachments: list[str] = []
        self._setup_ui()
        self._wire_events()
        self._start_connection_checker()
        # Window fade-in animation
        self._opacity_effect = QGraphicsOpacityEffect(self)
        self._opacity_effect.setOpacity(1.0)
        self.setGraphicsEffect(self._opacity_effect)
        self._show_animation: QPropertyAnimation | None = None
        asyncio.ensure_future(self._load_providers())
        asyncio.ensure_future(self._load_sessions())

    def _setup_ui(self) -> None:
        self.setWindowTitle("Chat with AIPet")
        self.resize(900, 720)
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
        left_panel.setMinimumWidth(160)
        left_panel.setMaximumWidth(350)
        left_panel.setStyleSheet(
            f"background-color: {MaterialTheme.surface}; border-right: 1px solid {MaterialTheme.outline_variant};"
        )
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(10, 10, 10, 10)
        left_layout.setSpacing(10)

        self.new_chat_btn = QPushButton("+ New Chat")
        self.new_chat_btn.setStyleSheet(MaterialTheme.filled_button())
        self.new_chat_btn.setFixedHeight(40)
        self.new_chat_btn.clicked.connect(lambda: asyncio.ensure_future(self._create_session()))
        left_layout.addWidget(self.new_chat_btn)

        self.session_list = QListWidget()
        self.session_list.setStyleSheet(
            f"""
            QListWidget {{
                background-color: transparent;
                color: {MaterialTheme.on_surface};
                border: none;
                outline: none;
            }}
            QListWidget::item {{
                background-color: transparent;
                border-radius: 8px;
                padding: 0px;
                margin: 2px 0px;
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
        top_bar.setFixedHeight(64)
        top_layout = QHBoxLayout(top_bar)
        top_layout.setContentsMargins(16, 8, 16, 8)
        top_layout.setSpacing(8)

        app_title = QLabel("Chat")
        app_title.setStyleSheet(
            f"color: {MaterialTheme.on_surface}; font-size: 22px; font-weight: 500; border: none;"
        )
        top_layout.addWidget(app_title)
        top_layout.addStretch()

        self.model_selector = QComboBox()
        self.model_selector.setMinimumWidth(200)
        self.model_selector.setStyleSheet(
            MaterialTheme.combo_box()
            + f"""
            QComboBox {{
                background-color: {MaterialTheme.surface_variant};
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
        top_layout.addStretch()

        self.refresh_btn = QPushButton("🔄 Refresh")
        self.refresh_btn.setFixedHeight(36)
        self.refresh_btn.setStyleSheet(MaterialTheme.text_button())
        self.refresh_btn.setToolTip("Refresh model list from gateway")
        self.refresh_btn.clicked.connect(lambda: asyncio.ensure_future(self._load_providers()))

        self.manage_btn = QPushButton("⚙️ Providers")
        self.manage_btn.setFixedHeight(36)
        self.manage_btn.setStyleSheet(MaterialTheme.text_button())
        self.manage_btn.setToolTip("Manage AI providers and API keys")
        self.manage_btn.clicked.connect(self._on_manage_providers)

        self.clear_btn = QPushButton("🗑️ Clear")
        self.clear_btn.setFixedHeight(36)
        self.clear_btn.setStyleSheet(MaterialTheme.text_button())
        self.clear_btn.setToolTip("Clear all messages in current session")
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
        self.messages_layout.setSpacing(6)
        self.messages_layout.setContentsMargins(10, 10, 10, 10)

        # Empty state shown when no messages
        self._empty_state = self._build_empty_state()
        self.messages_layout.addWidget(self._empty_state)

        self.messages_layout.addStretch()

        self.scroll_area.setWidget(self.messages_container)
        right_layout.addWidget(self.scroll_area, stretch=1)

        # Input area
        input_container = QWidget()
        input_container.setStyleSheet(
            f"background-color: {MaterialTheme.surface}; border-top: 1px solid {MaterialTheme.outline_variant};"
        )
        input_outer_layout = QVBoxLayout(input_container)
        input_outer_layout.setContentsMargins(0, 0, 0, 0)
        input_outer_layout.setSpacing(4)

        # Attachments bar (shown when files are dropped)
        self.attachments_bar = QWidget()
        attachments_bar_layout = QHBoxLayout(self.attachments_bar)
        attachments_bar_layout.setContentsMargins(12, 6, 12, 0)
        attachments_bar_layout.setSpacing(6)
        attachments_bar_layout.addStretch()
        self.attachments_bar.hide()
        input_outer_layout.addWidget(self.attachments_bar)

        # Input row
        input_row = QWidget()
        input_layout = QHBoxLayout(input_row)
        input_layout.setContentsMargins(12, 10, 12, 10)
        input_layout.setSpacing(10)

        # Voice input button (placeholder)
        self.voice_btn = QPushButton("🎤")
        self.voice_btn.setFixedSize(40, 40)
        self.voice_btn.setStyleSheet(MaterialTheme.text_button())
        self.voice_btn.setToolTip("Voice input (coming soon)")
        self.voice_btn.setEnabled(False)
        input_layout.addWidget(self.voice_btn)

        self.input_field = QTextEdit()
        self.input_field.setPlaceholderText("Type a message... (Shift+Enter for new line)  Drop files here")
        self.input_field.setMinimumHeight(56)
        self.input_field.setMaximumHeight(200)
        self.input_field.setStyleSheet(MaterialTheme.outlined_input())
        self.input_field.installEventFilter(self)
        self.input_field.textChanged.connect(self._on_input_text_changed)
        # Set initial height so the field doesn't use QTextEdit's large default preferred size
        self._on_input_text_changed()

        self.send_button = QPushButton("Send")
        self.send_button.setFixedSize(64, 40)
        self.send_button.setStyleSheet(MaterialTheme.filled_button())
        self.send_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.send_button.clicked.connect(self._on_send_clicked)

        input_layout.addWidget(self.input_field, stretch=1)
        input_layout.addWidget(self.send_button, alignment=Qt.AlignmentFlag.AlignBottom)
        input_outer_layout.addWidget(input_row)
        right_layout.addWidget(input_container)

        splitter.addWidget(right_panel)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([200, 700])
        root_layout.addWidget(splitter)

    def _build_empty_state(self) -> QWidget:
        """Build the welcome placeholder shown when a session has no messages."""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(12)

        icon = QLabel("🐾")
        icon.setStyleSheet("font-size: 48px; border: none; background: transparent;")
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)

        title = QLabel("你好，主人~")
        title.setStyleSheet(
            f"color: {MaterialTheme.on_surface}; font-size: 18px; font-weight: 500; border: none; background: transparent;"
        )
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        subtitle = QLabel("我是紫羽·琉璃，点击输入框和我聊天吧！")
        subtitle.setStyleSheet(
            f"color: {MaterialTheme.on_surface_variant}; font-size: 13px; border: none; background: transparent;"
        )
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)

        hint = QLabel("Shift + Enter 换行，Enter 发送")
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
                self.show_system_message("Disconnected from Gateway. Please restart the Gateway.", is_error=True)
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
            QTimer.singleShot(50, self._do_scroll)

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

            label = QLabel(f"📎 {name}")
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
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and modifiers == Qt.KeyboardModifier.NoModifier:
                self._on_send_clicked()
                return True

            # Ctrl+L: clear input
            if key == Qt.Key.Key_L and modifiers == Qt.KeyboardModifier.ControlModifier:
                self.input_field.clear()
                return True

            # Up arrow to recall last user message when input is empty
            if key == Qt.Key.Key_Up and modifiers == Qt.KeyboardModifier.NoModifier and not self.input_field.toPlainText().strip():
                self._recall_last_message()
                return True

            # Esc: blur input or stop generating (placeholder)
            if key == Qt.Key.Key_Escape:
                self.input_field.clearFocus()
                return True

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

    async def _send_message(self, text: str, attachments: list[str]) -> None:
        try:
            await self.client.send({
                "type": "request",
                "method": "chat.stream",
                "payload": {
                    "session_id": self._current_session_id or "main",
                    "content": text,
                    "attachments": attachments,
                },
            })
        except Exception as exc:
            self.show_system_message(f"Failed to send: {exc}", is_error=True)
            self._set_sending(False)

    def _set_sending(self, sending: bool) -> None:
        self._is_sending = sending
        self.send_button.setEnabled(not sending)
        self.send_button.setText("..." if sending else "Send")

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
            self.show_system_message(f"Failed to load sessions: {exc}", is_error=True)

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

            # Item widget with label and delete button
            widget = QWidget()
            row = QHBoxLayout(widget)
            row.setContentsMargins(8, 6, 4, 6)
            row.setSpacing(4)

            label = QLabel(name)
            label.setStyleSheet(
                f"color: {MaterialTheme.on_surface}; font-size: 13px; border: none;"
            )
            label.setWordWrap(False)
            row.addWidget(label, stretch=1)

            del_btn = QPushButton("✕")
            del_btn.setFixedSize(22, 22)
            del_btn.setStyleSheet(
                f"QPushButton {{ background-color: transparent; color: {MaterialTheme.on_surface_variant}; "
                f"border: none; border-radius: 11px; font-size: 12px; padding: 0px; }}"
                f"QPushButton:hover {{ background-color: {MaterialTheme.error}; color: {MaterialTheme.on_error}; }}"
            )
            del_btn.clicked.connect(lambda _checked=False, sid=sid: asyncio.ensure_future(self._delete_session(sid)))
            row.addWidget(del_btn)

            self.session_list.setItemWidget(item, widget)
            item.setSizeHint(widget.sizeHint())

        self._highlight_current_session()

    def _highlight_current_session(self) -> None:
        for sid, item in self._session_items.items():
            item.setSelected(sid == self._current_session_id)

    def _on_session_item_clicked(self, item: QListWidgetItem) -> None:
        sid = item.data(Qt.ItemDataRole.UserRole)
        if sid and sid != self._current_session_id:
            asyncio.ensure_future(self._switch_session(sid))

    def _on_session_item_double_clicked(self, item: QListWidgetItem) -> None:
        sid = item.data(Qt.ItemDataRole.UserRole)
        if sid:
            asyncio.ensure_future(self._rename_session_interactive(sid))

    async def _switch_session(self, session_id: str) -> None:
        self._current_session_id = session_id
        self._highlight_current_session()
        self._clear_messages()
        try:
            await self.client.request("session.set_current", {"session_id": session_id})
        except Exception as exc:
            self.show_system_message(f"Failed to set current session: {exc}", is_error=True)
        try:
            resp = await self.client.request("chat.history", {"session_id": session_id, "limit": 100})
            messages = resp.get("messages", [])
            for msg in messages:
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
                    bubble = MessageBubble("user", content, timestamp=ts, message_id=msg_id, parent=self.messages_container)
                    bubble.delete_requested.connect(self._on_bubble_delete_requested)
                    bubble.edit_requested.connect(self._on_bubble_edit_requested)
                    self.messages_layout.insertWidget(self.messages_layout.count() - 1, bubble)
                elif role == "assistant":
                    bubble = MessageBubble("assistant", content, timestamp=ts, message_id=msg_id, parent=self.messages_container)
                    bubble.delete_requested.connect(self._on_bubble_delete_requested)
                    bubble.regenerate_requested.connect(self._on_bubble_regenerate_requested)
                    self.messages_layout.insertWidget(self.messages_layout.count() - 1, bubble)
            if messages:
                self._scroll_to_bottom()
        except Exception as exc:
            self.show_system_message(f"Failed to load history: {exc}", is_error=True)

    def _clear_messages(self) -> None:
        # Remove all items from layout, then re-add empty_state + stretch.
        # Previously takeAt(0) removed empty_state from the layout but left
        # it as a floating visible widget that covered newly inserted bubbles.
        while self.messages_layout.count():
            item = self.messages_layout.takeAt(0)
            widget = item.widget() if item else None
            if widget is self._empty_state:
                continue
            if widget is not None:
                widget.deleteLater()
            # Spacer items are freed automatically when the layout item is removed
        if self._empty_state is not None:
            self.messages_layout.addWidget(self._empty_state)
            self._empty_state.show()
        self.messages_layout.addStretch()
        self._message_bubbles.clear()
        self._tool_cards.clear()
        self._typing_indicator = None

    async def _create_session(self) -> None:
        try:
            resp = await self.client.request("session.create", {"name": "New Session"})
            session = resp.get("session", {})
            sid = session.get("id", "")
            await self._load_sessions()
            if sid:
                await self._switch_session(sid)
        except Exception as exc:
            self.show_system_message(f"Failed to create session: {exc}", is_error=True)

    async def _delete_session(self, session_id: str) -> None:
        reply = QMessageBox.question(
            self,
            "Confirm Delete",
            "Are you sure you want to delete this session?\nThis action cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
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
            self.show_system_message(f"Failed to delete session: {exc}", is_error=True)

    async def _clear_current_session(self) -> None:
        """Clear all messages in the current session."""
        if not self._current_session_id:
            return
        reply = QMessageBox.question(
            self,
            "Confirm Clear",
            "Are you sure you want to clear all messages in this session?\nThis action cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            resp = await self.client.request("chat.clear", {"session_id": self._current_session_id})
            if resp.get("success"):
                self._clear_messages()
        except Exception as exc:
            self.show_system_message(f"Failed to clear session: {exc}", is_error=True)

    async def _rename_session_interactive(self, session_id: str) -> None:
        """Rename a session via a simple input dialog."""
        from PySide6.QtWidgets import QInputDialog
        current_name = ""
        for i in range(self.session_list.count()):
            item = self.session_list.item(i)
            if item and item.data(Qt.ItemDataRole.UserRole) == session_id:
                widget = self.session_list.itemWidget(item)
                if widget:
                    label = widget.findChild(QLabel)
                    if label:
                        current_name = label.text()
                break
        name, ok = QInputDialog.getText(self, "Rename Session", "New name:", text=current_name)
        if ok and name.strip():
            try:
                resp = await self.client.request("session.rename", {"session_id": session_id, "name": name.strip()})
                if resp.get("success"):
                    await self._load_sessions()
            except Exception as exc:
                self.show_system_message(f"Failed to rename session: {exc}", is_error=True)

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
        except Exception as exc:
            self.show_system_message(f"Failed to load providers: {exc}", is_error=True)

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
                self.show_system_message("Failed to switch model: invalid provider or model.")
                self._populate_model_selector()
                return
            self._current_provider_id = provider_id
            self._current_model = model
        except Exception as exc:
            self.show_system_message(f"Failed to set model: {exc}", is_error=True)
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
            "Confirm Delete",
            "Delete this message?",
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
            self.show_system_message("Cannot regenerate: no preceding user message found.", is_error=True)
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
            if isinstance(widget, MessageBubble) and widget.message_id == message_id and widget.role == "user":
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
            file_lines = "\n".join(f"📎 {Path(a).name}" for a in attachments)
            if display_text:
                display_text = f"{display_text}\n\n{file_lines}"
            else:
                display_text = file_lines
        bubble = MessageBubble("user", display_text, timestamp=ts, parent=self.messages_container)
        bubble.delete_requested.connect(self._on_bubble_delete_requested)
        bubble.edit_requested.connect(self._on_bubble_edit_requested)
        self.messages_layout.insertWidget(self.messages_layout.count() - 1, bubble)
        self._empty_state.hide()
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
        bubble = MessageBubble("assistant", "", timestamp=ts, message_id=msg_id, parent=self.messages_container)
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
        if bubble:
            bubble.finish_streaming()
        else:
            # Fallback: finish any streaming bubble
            for b in self._message_bubbles.values():
                if getattr(b, "_is_streaming", False):
                    b.finish_streaming()
                    break
        self._hide_typing_indicator()
        self._set_sending(False)

    def _on_chat_proactive(self, payload: dict[str, Any]) -> None:
        print(f"[ChatWindow] Received chat.proactive: session={payload.get('session_id')}, content={payload.get('content', '')[:30]!r}")
        sid = payload.get("session_id", "")
        content = payload.get("content", "")
        msg_id = payload.get("message_id", "")
        ts = datetime.now().strftime("%H:%M")

        if sid != self._current_session_id:
            print(f"[ChatWindow] Auto-switching to session {sid} for proactive message")
            asyncio.ensure_future(self._switch_session_and_show_proactive(sid, content, msg_id, ts))
            return
        self._add_assistant_bubble(content, msg_id, ts)

    async def _switch_session_and_show_proactive(
        self, session_id: str, content: str, msg_id: str, timestamp: str
    ) -> None:
        await self._switch_session(session_id)
        self._add_assistant_bubble(content, msg_id, timestamp)

    def _on_system_error(self, payload: dict[str, Any]) -> None:
        self.show_system_message(f"Error: {payload.get('message', 'Unknown error')}", is_error=True)
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

    def _on_tool_result(self, payload: dict[str, Any]) -> None:
        if payload.get("session_id") != self._current_session_id:
            return
        tool_call_id = payload.get("tool_call_id", "")
        card = self._tool_cards.get(tool_call_id)
        if card:
            card.set_done(payload.get("result", ""))
            self._scroll_to_bottom()

    def _on_tool_error(self, payload: dict[str, Any]) -> None:
        if payload.get("session_id") != self._current_session_id:
            return
        tool_call_id = payload.get("tool_call_id", "")
        card = self._tool_cards.get(tool_call_id)
        if card:
            card.set_error(payload.get("error", "Unknown error"))
            self._scroll_to_bottom()

    def _add_assistant_bubble(self, text: str, msg_id: str = "", timestamp: str = "") -> None:
        ts = timestamp or datetime.now().strftime("%H:%M")
        if msg_id and msg_id in self._message_bubbles:
            self._message_bubbles[msg_id].set_text(text)
            return
        bubble = MessageBubble("assistant", text, timestamp=ts, message_id=msg_id, parent=self.messages_container)
        bubble.delete_requested.connect(self._on_bubble_delete_requested)
        bubble.regenerate_requested.connect(self._on_bubble_regenerate_requested)
        if msg_id:
            self._message_bubbles[msg_id] = bubble
        self.messages_layout.insertWidget(self.messages_layout.count() - 1, bubble)
        self._empty_state.hide()
        self._scroll_to_bottom()

    def show_system_message(self, text: str, is_error: bool = False) -> None:
        bubble = MessageBubble("system", text, parent=self.messages_container, is_error=is_error)
        bubble.delete_requested.connect(self._on_bubble_delete_requested)
        self.messages_layout.insertWidget(self.messages_layout.count() - 1, bubble)
        self._empty_state.hide()
        self._scroll_to_bottom()

    def _show_typing_indicator(self) -> None:
        if self._typing_indicator is None:
            self._typing_indicator = QLabel("🐾 正在输入...")
            self._typing_indicator.setStyleSheet(
                f"QLabel {{ color: {MaterialTheme.outline}; padding: 6px 12px; font-size: 12px; }}"
            )
            self.messages_layout.insertWidget(self.messages_layout.count() - 1, self._typing_indicator)
            self._scroll_to_bottom()

    def _hide_typing_indicator(self) -> None:
        if self._typing_indicator is not None:
            self._typing_indicator.deleteLater()
            self._typing_indicator = None
