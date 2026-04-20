"""Echo AI Provider for testing."""

from __future__ import annotations

from collections.abc import AsyncIterator

from aipet.gateway.providers.ai import Chunk, Message, Tool


class EchoProvider:
    """A dummy AI provider that echoes back the last user message."""

    def __init__(self, model_id: str = "echo") -> None:
        self._model_id = model_id

    @property
    def name(self) -> str:
        return "Echo"

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def supports_tool_calling(self) -> bool:
        return False

    async def chat(
        self,
        messages: list[Message],
        tools: list[Tool] | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> AsyncIterator[Chunk]:
        last_user = ""
        for msg in reversed(messages):
            if msg.role == "user":
                last_user = msg.content
                break

        reply = f"Echo: {last_user}" if last_user else "Echo: (no message)"
        for word in reply.split():
            yield Chunk(delta=word + " ")
        yield Chunk(delta="", finish_reason="stop")

    async def compact(self, messages: list[Message]) -> str:
        return "Summary of earlier conversation."
