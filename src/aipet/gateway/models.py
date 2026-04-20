"""Core data models for Gateway (Message, Session)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


class Message(BaseModel):
    """A single chat message."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    role: str  # "user", "assistant", "system", "tool"
    content: str = ""
    tool_calls: list[dict[str, Any]] | None = None
    tool_call_id: str | None = None
    attachments: list[str] = Field(default_factory=list)
    model: str | None = None
    tokens_used: int | None = None
    source: str | None = None  # "proactive", "user", etc.
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class Session(BaseModel):
    """An isolated AI conversation context."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    name: str = "Session"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    soul_path: str = "soul.md"
    memory_summary: str = ""
    model_name: str = "gemini-2.5-flash-preview-05-20"
    messages: list[Message] = Field(default_factory=list)
    context_window_tokens: int = 0

    def add_message(self, message: Message) -> None:
        """Append a message and update metadata."""
        self.messages.append(message)
        self.updated_at = datetime.now(UTC)
        # Simple token estimation: ~0.75 tokens per char for CJK
        self.context_window_tokens += len(message.content) * 3 // 4

    def recent_messages(self, n: int = 10) -> list[Message]:
        """Return the last n messages."""
        return self.messages[-n:]
