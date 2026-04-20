"""Tests for AI providers."""

import pytest

from aipet.gateway.config import GatewayConfig
from aipet.gateway.providers.ai import Message as AIMessage
from aipet.gateway.providers.ai_echo import EchoProvider
from aipet.gateway.providers.ai_openai import OpenAIProvider
from aipet.gateway.providers.factory import create_ai_provider
from aipet.gateway.providers.manager import ProviderEntry, ProviderManager


@pytest.mark.asyncio
async def test_echo_provider_streams_response() -> None:
    provider = EchoProvider()
    messages = [
        type("Message", (), {"role": "user", "content": "hello world"})(),
    ]
    # Actually use pydantic Message from ai.py
    from aipet.gateway.providers.ai import Message

    messages = [Message(role="user", content="hello world")]
    chunks = [c async for c in provider.chat(messages)]
    text = "".join(c.delta for c in chunks if c.delta)
    assert text.strip() == "Echo: hello world"
    assert chunks[-1].finish_reason == "stop"


def test_factory_creates_echo_when_configured() -> None:
    config = GatewayConfig(ai_provider="echo")
    provider = create_ai_provider(config)
    assert provider.name == "Echo"


def test_factory_falls_back_to_echo_without_api_key() -> None:
    config = GatewayConfig(ai_provider="gemini", ai_api_key=None)
    provider = create_ai_provider(config)
    assert provider.name == "Echo"


def test_provider_manager_set_current_rejects_invalid_model() -> None:
    pm = ProviderManager()
    pm.providers = [
        ProviderEntry(id="openai", name="OpenAI", type="openai", models=["gpt-4o"]),
    ]
    pm.current_provider_id = "openai"
    pm.current_model = "gpt-4o"

    assert pm.set_current("openai", model="nonexistent") is False
    assert pm.current_model == "gpt-4o"  # unchanged


def test_provider_manager_create_ai_provider_uses_own_model_for_non_current() -> None:
    pm = ProviderManager()
    pm.providers = [
        ProviderEntry(
            id="gemini", name="Gemini", type="gemini", api_key="key", models=["gemini-flash"]
        ),
        ProviderEntry(id="openai", name="OpenAI", type="openai", api_key="key", models=["gpt-4o"]),
    ]
    pm.current_provider_id = "gemini"
    pm.current_model = "gemini-flash"

    provider = pm.create_ai_provider(provider_id="openai")
    assert provider.model_id == "gpt-4o"


def test_provider_manager_update_can_clear_api_key() -> None:
    pm = ProviderManager()
    pm.providers = [
        ProviderEntry(
            id="openai", name="OpenAI", type="openai", api_key="secret", models=["gpt-4o"]
        ),
    ]
    pm.update_provider("openai", api_key=None)
    entry = pm.get_provider("openai")
    assert entry is not None
    assert entry.api_key is None


def test_openai_build_payload_includes_tool_call_id() -> None:
    provider = OpenAIProvider(api_key="test", model_id="gpt-4o")
    messages = [
        AIMessage(role="user", content="hello"),
        AIMessage(
            role="assistant",
            content="",
            tool_calls=[{"id": "call_1", "function": {"name": "test"}}],
        ),
        AIMessage(role="tool", content="result", tool_call_id="call_1"),
    ]
    payload = provider._build_payload(messages, None, 0.7, None)
    tool_msg = payload["messages"][2]
    assert tool_msg["role"] == "tool"
    assert tool_msg["tool_call_id"] == "call_1"
