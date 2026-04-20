"""Anthropic Claude API Provider implementation (stub)."""

from __future__ import annotations

from collections.abc import AsyncIterator

from aipet.gateway.providers.ai import Chunk, Message, Tool


class AnthropicProvider:
    """Anthropic Claude API provider."""

    def __init__(self, api_key: str, model_id: str = "claude-3-5-sonnet-20241022") -> None:
        self._api_key = api_key
        self._model_id = model_id

    @property
    def name(self) -> str:
        return "Anthropic"

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def supports_tool_calling(self) -> bool:
        return True

    async def chat(
        self,
        messages: list[Message],
        tools: list[Tool] | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> AsyncIterator[Chunk]:
        raise NotImplementedError("Anthropic provider not yet fully implemented")
        yield Chunk(delta="")

    async def compact(self, messages: list[Message]) -> str:
        raise NotImplementedError("Anthropic provider not yet fully implemented")
