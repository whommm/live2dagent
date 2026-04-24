"""Ollama local API Provider implementation."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx

from aipet.gateway.providers.ai import Chunk, Message


class OllamaProvider:
    """Ollama local model provider."""

    def __init__(self, model_id: str = "llama3", base_url: str = "http://localhost:11434") -> None:
        self._model_id = model_id
        self._base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=120.0)

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
        tools: list[Any] | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> AsyncIterator[Chunk]:
        url = f"{self._base_url}/api/chat"
        payload: dict[str, Any] = {
            "model": self._model_id,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream": True,
            "options": {},
        }
        if temperature is not None:
            payload["options"]["temperature"] = temperature
        if max_tokens is not None:
            payload["options"]["num_predict"] = max_tokens

        async with self._client.stream("POST", url, json=payload) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                line = line.strip()
                if not line:
                    continue
                try:
                    import json
                    data = json.loads(line)
                    msg = data.get("message", {})
                    content = msg.get("content", "")
                    done = data.get("done", False)
                    if content:
                        yield Chunk(delta=content)
                    if done:
                        yield Chunk(delta="", finish_reason="stop")
                        return
                except Exception:
                    continue

    async def aclose(self) -> None:
        await self._client.aclose()

    async def compact(self, messages: list[Message]) -> str:
        url = f"{self._base_url}/api/chat"
        payload = {
            "model": self._model_id,
            "messages": [
                *[{"role": m.role, "content": m.content} for m in messages],
                {
                    "role": "user",
                    "content": "Summarize the above conversation into one short paragraph.",
                },
            ],
            "stream": False,
            "options": {"num_predict": 256},
        }
        resp = await self._client.post(url, json=payload)
        resp.raise_for_status()
        data = resp.json()
        msg = data.get("message", {})
        return msg.get("content", "")
