"""Anthropic Claude API Provider implementation."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import anthropic

from aipet.gateway.providers.ai import Chunk, Message


class AnthropicProvider:
    """Anthropic Claude API provider."""

    def __init__(
        self,
        api_key: str,
        model_id: str = "claude-3-5-sonnet-20241022",
        base_url: str | None = None,
    ) -> None:
        self._api_key = api_key
        self._model_id = model_id
        kwargs: dict[str, Any] = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        self._client = anthropic.AsyncAnthropic(**kwargs)

    @property
    def name(self) -> str:
        return "Anthropic"

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def supports_tool_calling(self) -> bool:
        return True

    def _to_anthropic_messages(
        self, messages: list[Message]
    ) -> tuple[str | None, list[dict[str, Any]]]:
        """Split system messages and convert the rest to Anthropic format."""
        system_texts: list[str] = []
        anthropic_messages: list[dict[str, Any]] = []
        for m in messages:
            if m.role == "system":
                system_texts.append(m.content)
            elif m.role == "tool":
                anthropic_messages.append(
                    {
                        "role": "user",
                        "content": f"Tool result ({m.name or m.tool_call_id}): {m.content}",
                    }
                )
            else:
                anthropic_messages.append({"role": m.role, "content": m.content})
        system = "\n\n".join(system_texts) if system_texts else None
        return system, anthropic_messages

    async def chat(
        self,
        messages: list[Message],
        tools: list[Any] | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> AsyncIterator[Chunk]:
        system, anthropic_messages = self._to_anthropic_messages(messages)
        kwargs: dict[str, Any] = {
            "model": self._model_id,
            "max_tokens": max_tokens or 4096,
            "messages": anthropic_messages,
            "temperature": temperature,
        }
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = [
                {
                    "name": t.function["name"],
                    "description": t.function["description"],
                    "input_schema": t.function.get("parameters", {"type": "object"}),
                }
                for t in tools
            ]

        try:
            async with self._client.messages.stream(**kwargs) as stream:
                async for text in stream.text_stream:
                    if text:
                        yield Chunk(delta=text)
                # Signal completion
                yield Chunk(delta="", finish_reason="stop")
        except anthropic.APIError as exc:
            yield Chunk(delta="", finish_reason=f"error: {exc}")

    async def compact(self, messages: list[Message]) -> str:
        system, anthropic_messages = self._to_anthropic_messages(messages)
        kwargs: dict[str, Any] = {
            "model": self._model_id,
            "max_tokens": 256,
            "messages": [
                *anthropic_messages,
                {
                    "role": "user",
                    "content": "Please summarize the above conversation into one short paragraph.",
                },
            ],
            "temperature": 0.7,
        }
        if system:
            kwargs["system"] = system

        resp = await self._client.messages.create(**kwargs)
        content = resp.content
        if content and len(content) > 0:
            return content[0].text if hasattr(content[0], "text") else str(content[0])
        return ""
