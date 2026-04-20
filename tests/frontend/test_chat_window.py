"""Tests for ChatWindow and MessageBubble."""

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from aipet.frontend.chat_window import ChatWindow, MessageBubble
from aipet.frontend.client import GatewayClient
from aipet.frontend.live2d_widget import Live2DWidget
from aipet.frontend.pet_window import SpeechBubble


@pytest.fixture(scope="session")
def app():
    """Ensure QApplication exists for all Qt tests."""
    _app = QApplication.instance()
    if _app is None:
        _app = QApplication([])
    yield _app


def test_message_bubble_creation(app: QApplication) -> None:
    bubble = MessageBubble("user", "Hello")
    assert bubble.role == "user"
    assert bubble.get_text() == "Hello"

    bubble2 = MessageBubble("assistant", "Hi there")
    assert bubble2.role == "assistant"
    assert bubble2.get_text() == "Hi there"


def test_message_bubble_append(app: QApplication) -> None:
    bubble = MessageBubble("assistant", "Hello")
    bubble.append_text(" World")
    assert bubble.get_text() == "Hello World"


def test_chat_window_creation(app: QApplication) -> None:
    client = GatewayClient()
    window = ChatWindow(client)
    assert window.client is client
    assert window.input_field is not None


def test_live2d_widget_creation(app: QApplication) -> None:
    widget = Live2DWidget()
    assert widget is not None
    # Without a model file, model_path may be empty but widget still valid
    assert hasattr(widget, "model")


def test_speech_bubble_creation(app: QApplication) -> None:
    bubble = SpeechBubble()
    bubble.set_text("Hello from pet!")
    assert bubble.label.text() == "Hello from pet!"
    assert bubble.width() > 0
    assert bubble.height() > 0


def test_speech_bubble_click_emits_signal(app: QApplication) -> None:
    from unittest.mock import MagicMock

    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QMouseEvent

    bubble = SpeechBubble()
    bubble.set_text("Click me")
    slot = MagicMock()
    bubble.clicked.connect(slot)

    event = QMouseEvent(
        QMouseEvent.Type.MouseButtonPress,
        QPoint(10, 10),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    bubble.mousePressEvent(event)
    assert slot.called


def test_chat_window_does_not_duplicate_assistant_bubble(app: QApplication) -> None:
    client = GatewayClient()
    window = ChatWindow(client)
    window._current_session_id = "main"
    msg_id = "msg-123"

    window._add_assistant_bubble("First text", msg_id, "10:00")
    initial_count = window.messages_layout.count() - 1  # exclude stretch

    window._add_assistant_bubble("Updated text", msg_id, "10:01")
    updated_count = window.messages_layout.count() - 1

    assert updated_count == initial_count
    assert window._message_bubbles[msg_id].get_text() == "Updated text"
