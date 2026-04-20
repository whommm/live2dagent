"""WebSocket JSON-RPC style protocol definitions."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class BaseMessage(BaseModel):
    """Base class for all Gateway messages."""

    id: str | None = None
    type: Literal["request", "response", "notification", "event"] = "request"
    method: str
    payload: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Client -> Gateway (Requests)
# ---------------------------------------------------------------------------

class ClientHelloPayload(BaseModel):
    client_type: str = "unknown"
    version: str = ""


class ChatSendPayload(BaseModel):
    session_id: str = "main"
    content: str = ""
    attachments: list[str] = Field(default_factory=list)
    stream: bool = False


class SessionCreatePayload(BaseModel):
    name: str = "New Session"
    soul_path: str = "soul.md"


class SessionListPayload(BaseModel):
    pass


class TTSspeakPayload(BaseModel):
    text: str
    voice_id: str | None = None


class ToolCallPayload(BaseModel):
    skill: str
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)


class SystemShutdownPayload(BaseModel):
    pass


# ---------------------------------------------------------------------------
# Gateway -> Client (Events / Responses)
# ---------------------------------------------------------------------------

class ChatStreamStartEvent(BaseModel):
    session_id: str
    message_id: str


class ChatStreamChunkEvent(BaseModel):
    session_id: str
    message_id: str
    delta: str


class ChatStreamEndEvent(BaseModel):
    session_id: str
    message_id: str
    finish_reason: str = "stop"


class ChatMessageEvent(BaseModel):
    session_id: str
    message_id: str
    role: str
    content: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


class TTSPlaybackStartedEvent(BaseModel):
    session_id: str
    text: str
    audio_url: str | None = None


class TTSPlaybackEndedEvent(BaseModel):
    session_id: str


class Live2DExpressionEvent(BaseModel):
    expression: str


class Live2DMotionEvent(BaseModel):
    motion: str
    priority: int = 3


class CanvasShowEvent(BaseModel):
    canvas_id: str
    canvas_type: str
    data: dict[str, Any] = Field(default_factory=dict)
    title: str = ""
    position: str = "head"  # "head", "right", "left", "custom"
    x: int | None = None
    y: int | None = None
    width: int = 280
    height: int = 0  # 0 = auto
    duration_ms: int = 0  # 0 = persist until manually closed
    click_action: str = "dismiss"  # "none", "dismiss", "open_chat"
    style: dict[str, Any] = Field(default_factory=dict)


class CanvasCloseEvent(BaseModel):
    canvas_id: str


class ProactiveMessageEvent(BaseModel):
    session_id: str
    message_id: str
    role: str = "assistant"
    content: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    expression: str | None = None
    motion: str | None = None
    source: str = "proactive"


class SystemErrorEvent(BaseModel):
    code: str = "UNKNOWN_ERROR"
    message: str = ""
