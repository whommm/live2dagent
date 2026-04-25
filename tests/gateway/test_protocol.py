"""Tests for Gateway protocol handling."""

import json
import shutil

import pytest

from aipet.gateway.config import GatewayConfig
from aipet.gateway.protocol import ProactiveMessageEvent
from aipet.gateway.providers.ai_echo import EchoProvider
from aipet.gateway.server import Gateway


@pytest.fixture
async def gateway(tmp_path):
    g = Gateway(config=GatewayConfig(ai_provider="echo"))
    g.ai_provider = EchoProvider()
    await g.init()
    yield g
    # Cleanup test sessions (use tmp_path to avoid touching real data)
    sessions_dir = tmp_path / "data" / "sessions"
    if sessions_dir.exists():
        shutil.rmtree(sessions_dir)


@pytest.mark.asyncio
async def test_chat_send_and_echo(gateway: Gateway) -> None:
    """Test sending a chat message stores it and broadcasts assistant echo."""
    sent_messages: list[dict] = []

    async def mock_broadcast(data: dict) -> None:
        sent_messages.append(data)

    gateway._broadcast = mock_broadcast  # type: ignore[method-assign]

    await gateway._handle_chat_send("c1", {"session_id": "main", "content": "hello"})

    session = gateway.sessions.get("main")
    assert session is not None
    assert len(session.messages) == 2
    assert session.messages[0].role == "user"
    assert session.messages[0].content == "hello"
    assert session.messages[1].role == "assistant"

    assert len(sent_messages) == 1
    assert sent_messages[0]["method"] == "chat.message"
    assert sent_messages[0]["payload"]["content"] == "Echo: hello"


@pytest.mark.asyncio
async def test_chat_stream_simulates_chunks(gateway: Gateway) -> None:
    """Test streaming chat breaks response into chunks."""
    sent_messages: list[dict] = []

    async def mock_broadcast(data: dict) -> None:
        sent_messages.append(data)

    gateway._broadcast = mock_broadcast  # type: ignore[method-assign]

    await gateway._handle_chat_stream("c1", {"session_id": "main", "content": "hi"})

    session = gateway.sessions.get("main")
    assert session is not None
    assert session.messages[-1].role == "assistant"
    assert session.messages[-1].content == "Echo: hi"

    methods = [m["method"] for m in sent_messages]
    assert methods[0] == "chat.stream.start"
    assert "chat.stream.chunk" in methods
    assert "chat.stream.end" in methods


@pytest.mark.asyncio
async def test_session_create_and_list(gateway: Gateway) -> None:
    """Test session creation and listing."""
    responses: list[dict] = []

    async def mock_send(client_id: str, data: dict) -> None:
        responses.append(data)

    gateway._send = mock_send  # type: ignore[method-assign]

    await gateway._handle_session_create("c1", {"name": "Test Session", "soul_path": "test.md"})
    await gateway._handle_session_list("c1", {})

    assert len(responses) == 2
    assert responses[0]["method"] == "session.create"
    assert responses[0]["payload"]["session"]["name"] == "Test Session"

    sessions = responses[1]["payload"]["sessions"]
    assert len(sessions) >= 2  # default "main" + newly created
    names = {s["name"] for s in sessions}
    assert "Test Session" in names


@pytest.mark.asyncio
async def test_empty_content_rejected(gateway: Gateway) -> None:
    """Test empty chat messages are rejected with error."""
    errors: list[str] = []

    async def mock_send_error(client_id: str, message: str) -> None:
        errors.append(message)

    gateway._send_error = mock_send_error  # type: ignore[method-assign]

    await gateway._handle_chat_send("c1", {"session_id": "main", "content": "   "})
    assert any("empty" in e.lower() for e in errors)


@pytest.mark.asyncio
async def test_chat_send_triggers_tts(gateway: Gateway) -> None:
    """Test chat.send triggers TTS playback for assistant message."""
    tts_calls: list[str] = []

    async def mock_play_tts(text: str) -> None:
        tts_calls.append(text)

    gateway._play_tts = mock_play_tts  # type: ignore[method-assign]

    await gateway._handle_chat_send("c1", {"session_id": "main", "content": "hi"})
    assert len(tts_calls) == 1
    assert tts_calls[0] == "Echo: hi"


@pytest.mark.asyncio
async def test_chat_history_returns_messages(gateway: Gateway) -> None:
    """Test chat.history returns persisted messages."""
    responses: list[dict] = []

    async def mock_send(client_id: str, data: dict) -> None:
        responses.append(data)

    gateway._send = mock_send  # type: ignore[method-assign]

    # Pre-seed a message
    await gateway._handle_chat_send("c1", {"session_id": "main", "content": "history test"})
    responses.clear()

    await gateway._handle_chat_history("c1", {"session_id": "main", "limit": 10})

    assert len(responses) == 1
    assert responses[0]["method"] == "chat.history"
    msgs = responses[0]["payload"]["messages"]
    assert len(msgs) == 2  # user + assistant
    assert msgs[0]["role"] == "user"
    assert msgs[0]["content"] == "history test"
    assert msgs[1]["role"] == "assistant"


@pytest.mark.asyncio
async def test_parse_tool_calls_extracts_json(gateway: Gateway) -> None:
    """Test _parse_tool_calls extracts tool calls from JSON text."""
    text = '{"tool_calls": [{"name": "weather:get_weather", "arguments": {"city": "Beijing"}}]}'
    parsed = gateway._parse_tool_calls(text)
    assert parsed is not None
    assert parsed[0]["name"] == "weather:get_weather"
    assert parsed[0]["arguments"]["city"] == "Beijing"


@pytest.mark.asyncio
async def test_resolve_tool_calls_executes_and_returns_final(gateway: Gateway) -> None:
    """Test _resolve_tool_calls executes tools and fetches final reply."""
    # Seed a fake skill
    from aipet.gateway.skills.registry import SkillInfo

    async def greet(name: str) -> str:
        return f"Hello, {name}!"

    gateway.skills._skills["demo"] = SkillInfo("demo", "Demo", "Demo skill", [], {"greet": greet})

    # Mock provider to return a plain text after tool results
    from aipet.gateway.providers.ai_echo import EchoProvider

    class ToolEchoProvider(EchoProvider):
        async def chat(self, messages, tools=None, temperature=0.7, max_tokens=None):
            # If there is a tool result in messages, echo it back as final text
            for msg in reversed(messages):
                if msg.role == "tool":
                    for word in f"Final: {msg.content}".split():
                        yield type("Chunk", (), {"delta": word + " ", "finish_reason": None})()
                    yield type("Chunk", (), {"delta": "", "finish_reason": "stop"})()
                    return
            yield type("Chunk", (), {"delta": "no tools", "finish_reason": "stop"})()

    gateway.provider_manager = type(
        "obj", (object,), {"create_ai_provider": lambda self: ToolEchoProvider()}
    )()

    initial = '{"tool_calls": [{"name": "demo:greet", "arguments": {"name": "Alice"}}]}'
    final_msg = await gateway._resolve_tool_calls("main", initial)
    assert "Hello, Alice!" in final_msg.content

    session = gateway.sessions.get("main")
    assert session is not None
    roles = [m.role for m in session.messages]
    assert "assistant" in roles
    assert "tool" in roles


def test_proactive_message_event_serialization() -> None:
    """Test ProactiveMessageEvent serializes correctly."""
    event = ProactiveMessageEvent(
        session_id="main",
        message_id="msg_123",
        content="Hello!",
        expression="happy",
        motion="Tap",
    )
    data = event.model_dump(mode="json")
    assert data["session_id"] == "main"
    assert data["content"] == "Hello!"
    assert data["expression"] == "happy"
    assert data["motion"] == "Tap"
    assert data["role"] == "assistant"
    assert data["source"] == "proactive"


@pytest.mark.asyncio
async def test_concurrent_requests_keep_response_ids(gateway: Gateway) -> None:
    """Concurrent requests from one client must not reuse another request id."""
    sent: list[dict] = []

    class FakeClient:
        async def send(self, raw: str) -> None:
            sent.append(json.loads(raw))

    gateway.clients = {"c1": FakeClient()}  # type: ignore[assignment]

    async def delayed_history(client_id: str, payload: dict) -> None:
        await asyncio.sleep(0.05)
        await gateway._send(
            client_id,
            {"type": "response", "method": "chat.history", "payload": {"messages": []}},
        )

    import asyncio

    original_handler = gateway._handle_chat_history
    gateway._handle_chat_history = delayed_history  # type: ignore[method-assign]
    try:
        await asyncio.gather(
            gateway._process_message(
                "c1",
                json.dumps(
                    {"id": "req-1", "type": "request", "method": "chat.history", "payload": {}}
                ),
            ),
            gateway._process_message(
                "c1",
                json.dumps(
                    {"id": "req-2", "type": "request", "method": "session.list", "payload": {}}
                ),
            ),
        )
    finally:
        gateway._handle_chat_history = original_handler  # type: ignore[method-assign]

    ids_by_method = {msg["method"]: msg.get("id") for msg in sent if msg["type"] == "response"}
    assert ids_by_method["chat.history"] == "req-1"
    assert ids_by_method["session.list"] == "req-2"


@pytest.mark.asyncio
async def test_provider_current_reports_configuration_status(gateway: Gateway) -> None:
    responses: list[dict] = []

    async def mock_send(client_id: str, data: dict) -> None:
        responses.append(data)

    gateway._send = mock_send  # type: ignore[method-assign]
    gateway.provider_manager.update_provider("openai", api_key=None)
    gateway.provider_manager.set_current("openai", "gpt-4o")

    await gateway._handle_provider_get_current("c1", {})

    payload = responses[0]["payload"]
    assert payload["requires_api_key"] is True
    assert payload["is_configured"] is False
    assert payload["config_path"].endswith("providers.toml")


@pytest.mark.asyncio
async def test_system_update_settings_changes_runtime_toggles(gateway: Gateway) -> None:
    responses: list[dict] = []
    broadcasts: list[dict] = []

    async def mock_send(client_id: str, data: dict) -> None:
        responses.append(data)

    async def mock_broadcast(data: dict) -> None:
        broadcasts.append(data)

    gateway._send = mock_send  # type: ignore[method-assign]
    gateway._broadcast = mock_broadcast  # type: ignore[method-assign]

    await gateway._handle_system_update_settings(
        "c1", {"tts_auto_play": False, "proactive_enabled": False, "proactive_tts": False}
    )

    assert gateway.config.tts_auto_play is False
    assert gateway.config.proactive_enabled is False
    assert gateway.config.proactive_tts is False
    assert responses[0]["payload"]["success"] is True
    assert responses[0]["payload"]["tts_auto_play"] is False
    assert broadcasts[0]["method"] == "system.settings.updated"


@pytest.mark.asyncio
async def test_proactive_chat_service_broadcasts_event(gateway: Gateway) -> None:
    """Test ProactiveChatService generates and broadcasts a proactive message."""
    sent_messages: list[dict] = []

    async def mock_broadcast(data: dict) -> None:
        sent_messages.append(data)

    gateway._broadcast = mock_broadcast  # type: ignore[method-assign]
    # Simulate a connected client so proactive trigger isn't skipped
    gateway.clients = {"test-client": None}  # type: ignore[assignment]
    # Ensure user activity is old enough to not skip proactive
    import time

    gateway._last_user_activity = time.time() - 60

    await gateway.proactive._trigger()

    assert len(sent_messages) == 1
    assert sent_messages[0]["method"] == "chat.proactive"
    payload = sent_messages[0]["payload"]
    assert payload["session_id"] == "main"
    assert "content" in payload
    assert payload["content"].startswith("Echo:")
    assert payload["role"] == "assistant"
    assert payload["source"] == "proactive"

    session = gateway.sessions.get("main")
    assert session is not None
    assert any(
        m.role == "assistant" and m.source == "proactive" and "Echo:" in m.content
        for m in session.messages
    )
