"""Tests for native function calling across providers and Gateway."""

from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aipet.gateway.providers.ai import Chunk, Message, Tool
from aipet.gateway.providers.ai_openai import OpenAIProvider
from aipet.gateway.server import Gateway


@pytest.mark.asyncio
async def test_gateway_resolve_tool_calls_with_raw_tool_calls() -> None:
    """Verify _resolve_tool_calls handles raw_tool_calls from providers."""
    gateway = Gateway()
    await gateway.init()

    # Inject a mock provider that returns a simple text response
    mock_provider = MagicMock()
    mock_provider.name = "Mock"
    mock_provider.supports_tool_calling = True

    async def mock_chat(
        messages: list[Message], tools: list[Tool] | None = None
    ) -> AsyncIterator[Chunk]:
        yield Chunk(delta="The result is 42", finish_reason="stop")

    mock_provider.chat = mock_chat
    gateway.provider_manager = MagicMock()
    gateway.provider_manager.create_ai_provider.return_value = mock_provider
    gateway.provider_manager.get_provider.return_value = None

    # Register a test skill
    from aipet.gateway.skills.registry import SkillInfo

    gateway.skills._skills["math"] = SkillInfo(
        "math", "Math ops", "Math skill", [], {"add": lambda a, b: a + b}
    )

    # Call with raw_tool_calls
    raw_tool_calls = [{"name": "math:add", "arguments": {"a": 20, "b": 22}}]
    result_msg = await gateway._resolve_tool_calls(
        "main", "some text", raw_tool_calls=raw_tool_calls
    )

    assert "42" in result_msg.content


@pytest.mark.asyncio
async def test_openai_provider_stream_parses_tool_calls() -> None:
    """Verify OpenAIProvider accumulates and yields tool_calls from stream chunks."""
    provider = OpenAIProvider(api_key="test", model_id="gpt-4o")

    # Simulate streamed chunks with tool call deltas
    fake_lines = [
        'data: {"choices":[{"delta":{"content":null,"tool_calls":[{"index":0,"id":"call_abc","type":"function","function":{"name":"weather:get_weather","arguments":""}}]},"finish_reason":null}]}',
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"{\\"city\\": \\"Beijing\\"}"}}]},"finish_reason":null}]}',
        'data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}]}',
        "data: [DONE]",
    ]

    async def _aiter_lines() -> AsyncIterator[str]:
        for line in fake_lines:
            yield line

    mock_response = AsyncMock()
    mock_response.aiter_lines = _aiter_lines
    mock_response.__aenter__ = AsyncMock(return_value=mock_response)
    mock_response.__aexit__ = AsyncMock(return_value=False)
    mock_response.raise_for_status = MagicMock()

    with patch.object(provider._client, "stream", return_value=mock_response):
        chunks = [c async for c in provider.chat([])]

    # Last chunk should contain the tool_calls
    assert chunks[-1].finish_reason == "tool_calls"
    assert chunks[-1].tool_calls is not None
    assert len(chunks[-1].tool_calls) == 1
    assert chunks[-1].tool_calls[0]["name"] == "weather:get_weather"
    assert chunks[-1].tool_calls[0]["arguments"] == {"city": "Beijing"}
    assert chunks[-1].tool_calls[0]["id"] == "call_abc"


def test_time_skill_get_current_time() -> None:
    from aipet.gateway.skills.builtins.time import get_current_time

    result = get_current_time()
    assert len(result) == 19  # YYYY-MM-DD HH:MM:SS
    assert result[4] == "-"
    assert result[13] == ":"


def test_time_skill_get_time_in_city() -> None:
    from aipet.gateway.skills.builtins.time import get_time_in_city

    result = get_time_in_city("Beijing")
    assert "Beijing" in result
    assert "UTC+8" in result

    unknown = get_time_in_city("Mars")
    assert "Unknown city" in unknown


def test_random_skill_tools() -> None:
    from aipet.gateway.skills.builtins.random import flip_coin, random_choice, random_number, roll_dice

    assert flip_coin() in ("heads", "tails")

    num = int(random_number(1, 10))
    assert 1 <= num <= 10

    choice = random_choice("A, B, C")
    assert choice in ("A", "B", "C")

    dice = int(roll_dice(6))
    assert 1 <= dice <= 6

    dice20 = int(roll_dice(20))
    assert 1 <= dice20 <= 20
