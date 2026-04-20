"""Ollama local API Provider implementation (stub)."""

from __future__ import annotations

from collections.abc import AsyncIterator

from aipet.gateway.providers.ai import Chunk, Message, Tool


class OllamaProvider:
    """Ollama local model provider."""

    def __init__(self, model_id: str = "llama3", base_url: str = "http://localhost:11434") -> None:
        self._model_id = model_id
        self._base_url = base_url

    @property
    def name(self) -> str:
        return "Ollama"

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
        raise NotImplementedError("Ollama provider not yet fully implemented")
        yield Chunk(delta="")

    async def compact(self, messages: list[Message]) -> str:
        raise NotImplementedError("Ollama provider not yet fully implemented")
