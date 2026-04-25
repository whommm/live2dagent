"""OpenAI API Provider implementation (fully functional, supports custom base_url)."""

from __future__ import annotations

import json as _json
import logging
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx

from aipet.gateway.providers.ai import Chunk, Message, Tool

_logger = logging.getLogger("aipet.gateway.providers.ai_openai")


class OpenAIProvider:
    """OpenAI-compatible API provider (works with DeepSeek, SiliconFlow, etc.)."""

    def __init__(
        self,
        api_key: str,
        model_id: str = "gpt-4o",
        base_url: str = "https://api.openai.com/v1",
        extra_params: dict[str, Any] | None = None,
    ) -> None:
        self._api_key = api_key
        self._model_id = model_id
        self._base_url = base_url.rstrip("/")
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        self._client = httpx.AsyncClient(timeout=120.0)
        self._extra_params = extra_params or {}

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
            msg: dict[str, Any] = {"role": m.role, "content": m.content or ""}
            # Include tool_calls if the message has them, even in text-mode.
            # Some providers (e.g. DeepSeek) require the assistant message to
            # contain tool_calls when a subsequent tool role message is present.
            if m.tool_calls:
                formatted_calls = []
                for tc in m.tool_calls:
                    if "function" in tc:
                        formatted_calls.append(tc)
                    else:
                        args = tc.get("arguments", {})
                        if isinstance(args, dict):
                            args = _json.dumps(args)
                        formatted_calls.append(
                            {
                                "id": tc.get("id", tc.get("name", f"call_{uuid.uuid4().hex}")),
                                "type": "function",
                                "function": {"name": tc.get("name", ""), "arguments": args},
                            }
                        )
                msg["tool_calls"] = formatted_calls
                # When an assistant message carries native tool_calls, the
                # content must be empty/null (OpenAI format requirement).
                # DeepSeek V4 enforces this strictly.
                if m.role == "assistant":
                    msg["content"] = None
            if m.role == "tool" and m.tool_call_id:
                msg["tool_call_id"] = m.tool_call_id
            # Note: OpenAI allows 'name' on tool messages, but DeepSeek
            # rejects it. Omit to stay compatible with all providers.
            # if m.role == "tool" and m.name:
            #     msg["name"] = m.name
            if m.reasoning_content is not None:
                msg["reasoning_content"] = m.reasoning_content
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
        payload.update(self._extra_params)
        # Debug: log tool_call_id pairing
        for idx, om in enumerate(openai_messages):
            if om.get("role") == "assistant" and om.get("tool_calls"):
                _logger.info(
                    "Payload assistant[%d] tool_call_ids: %s",
                    idx,
                    [tc.get("id") for tc in om["tool_calls"]],
                )
            elif om.get("role") == "tool":
                _logger.info("Payload tool[%d] tool_call_id: %s", idx, om.get("tool_call_id"))
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
                    # Raise early, before the stream is consumed, so we can
                    # read the error body reliably on 4xx/5xx.
                    try:
                        response.raise_for_status()
                    except httpx.HTTPStatusError as exc:
                        body = await response.aread()
                        decoded = body.decode("utf-8", errors="replace")
                        _logger.error(
                            "HTTP %d from AI provider: %s",
                            exc.response.status_code,
                            decoded,
                        )
                        raise httpx.HTTPStatusError(
                            f"{exc.response.status_code}: {decoded}",
                            request=exc.request,
                            response=exc.response,
                        ) from exc
                    # Accumulate tool calls across chunks (arguments may be streamed in pieces)
                    tool_call_accumulator: dict[int, dict[str, Any]] = {}
                    reasoning_content = ""
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
                            reasoning = delta.get("reasoning_content") or ""
                            if reasoning:
                                reasoning_content += reasoning
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
                                            "arguments": tc.get("function", {}).get(
                                                "arguments", ""
                                            ),
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
                                    parsed_calls.append(
                                        {
                                            "id": tc.get("id", ""),
                                            "name": tc.get("name", ""),
                                            "arguments": args,
                                        }
                                    )
                                yield Chunk(
                                    delta="",
                                    finish_reason="tool_calls",
                                    tool_calls=parsed_calls,
                                    reasoning_content=reasoning_content,
                                )
                                return

                            if finish and finish != "tool_calls":
                                yield Chunk(
                                    delta="",
                                    finish_reason=finish,
                                    reasoning_content=reasoning_content,
                                )
                                return
                        except Exception:
                            continue
                return
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 400:
                    # Body may have already been read inside the stream block.
                    # The decoded error is embedded in the exception message.
                    exc_msg = str(exc)
                    decoded = exc_msg.split(": ", 1)[-1] if ": " in exc_msg else exc_msg

                    msg_summary = " | ".join(
                        (
                            f"{m.get('role')}({len(m.get('content') or '')}c"
                            f"{'+tc' if m.get('tool_calls') else ''})"
                        )
                        for m in payload.get("messages", [])
                    )
                    _logger.error(
                        "400 error from AI provider (attempt=%d): %s | messages: %s",
                        attempt + 1,
                        decoded,
                        msg_summary,
                    )
                    if attempt < 2:
                        continue
                raise

    async def aclose(self) -> None:
        await self._client.aclose()

    async def compact(self, messages: list[Message]) -> str:
        url = f"{self._base_url}/chat/completions"
        payload = {
            "model": self._model_id,
            "messages": [
                {
                    "role": "system",
                    "content": "Summarize the following conversation into one short paragraph.",
                },
                *[{"role": m.role, "content": m.content or ""} for m in messages],
            ],
            "stream": False,
            "max_tokens": 256,
        }
        resp = await self._client.post(url, headers=self._headers, json=payload)
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"] if data.get("choices") else ""
