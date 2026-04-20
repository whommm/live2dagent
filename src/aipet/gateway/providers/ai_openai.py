"""OpenAI API Provider implementation (fully functional, supports custom base_url)."""

from __future__ import annotations

import json as _json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx

from aipet.gateway.providers.ai import Chunk, Message, Tool


class OpenAIProvider:
    """OpenAI-compatible API provider (works with DeepSeek, SiliconFlow, etc.)."""

    def __init__(
        self, api_key: str, model_id: str = "gpt-4o", base_url: str = "https://api.openai.com/v1"
    ) -> None:
        self._api_key = api_key
        self._model_id = model_id
        self._base_url = base_url.rstrip("/")
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        self._client = httpx.AsyncClient(timeout=120.0)

    @property
    def name(self) -> str:
        return "OpenAI"

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def supports_tool_calling(self) -> bool:
        return True

    def _build_payload(
        self,
        messages: list[Message],
        tools: list[Tool] | None,
        temperature: float | None,
        max_tokens: int | None,
    ) -> dict[str, Any]:
        openai_messages: list[dict[str, Any]] = []
        for m in messages:
            msg: dict[str, Any] = {"role": m.role, "content": m.content}
            # Only include tool_calls when we are actually providing native tools.
            # In pure text-mode (tools=None), strip tool_calls to prevent the model
            # from switching back to native function-calling format.
            if tools and m.tool_calls:
                formatted_calls = []
                for tc in m.tool_calls:
                    if "function" in tc:
                        formatted_calls.append(tc)
                    else:
                        args = tc.get("arguments", {})
                        if isinstance(args, dict):
                            args = _json.dumps(args)
                        formatted_calls.append({
                            "id": tc.get("id", f"call_{uuid.uuid4().hex}"),
                            "type": "function",
                            "function": {"name": tc.get("name", ""), "arguments": args},
                        })
                msg["tool_calls"] = formatted_calls
            if m.role == "tool" and m.tool_call_id:
                msg["tool_call_id"] = m.tool_call_id
            if m.role == "tool" and m.name:
                msg["name"] = m.name
            openai_messages.append(msg)

        payload: dict[str, Any] = {
            "model": self._model_id,
            "messages": openai_messages,
            "stream": True,
        }
        if temperature is not None:
            payload["temperature"] = temperature
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if tools:
            payload["tools"] = [t.model_dump() for t in tools]
        return payload

    async def chat(
        self,
        messages: list[Message],
        tools: list[Tool] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[Chunk]:
        url = f"{self._base_url}/chat/completions"

        for attempt in range(3):
            use_tools = tools if attempt == 0 else None
            use_temp = temperature if attempt < 2 else None
            payload = self._build_payload(messages, use_tools, use_temp, max_tokens)

            try:
                async with self._client.stream(
                    "POST", url, headers=self._headers, json=payload
                ) as response:
                    response.raise_for_status()
                    # Accumulate tool calls across chunks (arguments may be streamed in pieces)
                    tool_call_accumulator: dict[int, dict[str, Any]] = {}
                    async for line in response.aiter_lines():
                        line = line.strip()
                        if not line or line == "data: [DONE]":
                            continue
                        if line.startswith("data: "):
                            line = line[6:]
                        try:
                            data = _json.loads(line)
                            choices = data.get("choices", [])
                            if not choices:
                                continue
                            delta = choices[0].get("delta", {})
                            content = delta.get("content") or ""
                            finish = choices[0].get("finish_reason")

                            # Accumulate tool calls
                            tc_deltas = delta.get("tool_calls")
                            if tc_deltas:
                                for tc in tc_deltas:
                                    idx = tc.get("index", 0)
                                    if idx not in tool_call_accumulator:
                                        tool_call_accumulator[idx] = {
                                            "id": tc.get("id", ""),
                                            "type": tc.get("type", "function"),
                                            "name": tc.get("function", {}).get("name", ""),
                                            "arguments": tc.get("function", {}).get("arguments", ""),
                                        }
                                    else:
                                        # Append to existing
                                        existing = tool_call_accumulator[idx]
                                        if tc.get("id"):
                                            existing["id"] = tc["id"]
                                        if tc.get("type"):
                                            existing["type"] = tc["type"]
                                        func = tc.get("function", {})
                                        if func.get("name"):
                                            existing["name"] = func["name"]
                                        if func.get("arguments"):
                                            existing["arguments"] += func["arguments"]

                            # Yield content chunk
                            if content:
                                yield Chunk(delta=content, finish_reason=finish)

                            # If finished due to tool_calls, yield the accumulated calls
                            if finish == "tool_calls" and tool_call_accumulator:
                                parsed_calls = []
                                for idx in sorted(tool_call_accumulator.keys()):
                                    tc = tool_call_accumulator[idx]
                                    args_str = tc.get("arguments", "")
                                    try:
                                        args = _json.loads(args_str) if args_str else {}
                                    except _json.JSONDecodeError:
                                        args = {}
                                    parsed_calls.append({
                                        "id": tc.get("id", ""),
                                        "name": tc.get("name", ""),
                                        "arguments": args,
                                    })
                                yield Chunk(delta="", finish_reason="tool_calls", tool_calls=parsed_calls)
                                return

                            if finish and finish != "tool_calls":
                                yield Chunk(delta="", finish_reason=finish)
                                return
                        except Exception:
                            continue
                return
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 400 and attempt < 2:
                    try:
                        body = await exc.response.aread()
                        decoded = body.decode('utf-8', errors='replace')
                        print(f"[OpenAIProvider] 400 error (attempt {attempt + 1}): {decoded}")
                        print(f"[OpenAIProvider] Payload was: {payload}")
                    except Exception:
                        pass
                    continue
                raise

    async def compact(self, messages: list[Message]) -> str:
        url = f"{self._base_url}/chat/completions"
        payload = {
            "model": self._model_id,
            "messages": [
                {
                    "role": "system",
                    "content": "Summarize the following conversation into one short paragraph.",
                },
                *[{"role": m.role, "content": m.content} for m in messages],
            ],
            "stream": False,
            "max_tokens": 256,
        }
        resp = await self._client.post(url, headers=self._headers, json=payload)
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"] if data.get("choices") else ""
