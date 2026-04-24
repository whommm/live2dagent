"""AI Provider abstraction layer."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel


class Chunk(BaseModel):
    """A single chunk from a streaming AI response."""

    delta: str
    finish_reason: str | None = None
    tool_calls: list[dict[str, Any]] | None = None
    reasoning_content: str | None = None


class Message(BaseModel):
    """A message in the conversation."""

    role: str
    content: str
    tool_calls: list[dict[str, Any]] | None = None
    tool_call_id: str | None = None
    name: str | None = None  # For tool role messages
    reasoning_content: str | None = None  # DeepSeek thinking mode


class Tool(BaseModel):
    """Tool definition for function calling."""

    type: str = "function"
    function: dict[str, Any]


class Usage(BaseModel):
    """Token usage information."""

    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None


# Re-export session Message for compatibility


@runtime_checkable
class AIProvider(Protocol):
    """Abstract interface for AI model providers."""

    @property
    def name(self) -> str:
        """Human-readable provider name."""
        ...

    @property
    def model_id(self) -> str:
        """Current model identifier."""
        ...

    @property
    def supports_tool_calling(self) -> bool:
        """Whether this provider supports function calling."""
        ...

    def chat(
        self,
        messages: list[Message],
        tools: list[Tool] | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> AsyncIterator[Chunk]:
        """Stream a chat response."""
        ...

    async def compact(self, messages: list[Message]) -> str:
        """Summarize a list of messages into a short paragraph."""
        ...
