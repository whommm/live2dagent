"""Tests for SQLite ChatRepository."""

import shutil

import pytest

from aipet.gateway.repository import ChatRepository
from aipet.gateway.session import Message, Session


@pytest.fixture
async def repo(tmp_path):
    sid = "test-session-123"
    r = ChatRepository(sid)
    await r.init_db()
    yield r
    # Cleanup (use tmp_path to avoid touching real data)
    sessions_dir = tmp_path / "data" / "sessions"
    if sessions_dir.exists():
        shutil.rmtree(sessions_dir)


@pytest.mark.asyncio
async def test_save_and_load_session(repo: ChatRepository) -> None:
    session = Session(id=repo.session_id, name="Test", soul_path="soul.md")
    await repo.save_session(session)
    loaded = await repo.load_session()
    assert loaded is not None
    assert loaded.name == "Test"
    assert loaded.soul_path == "soul.md"


@pytest.mark.asyncio
async def test_save_and_load_messages(repo: ChatRepository) -> None:
    msg1 = Message(role="user", content="hello")
    msg2 = Message(role="assistant", content="hi there")
    await repo.save_message(msg1)
    await repo.save_message(msg2)

    loaded = await repo.load_messages()
    assert len(loaded) == 2
    assert loaded[0].role == "user"
    assert loaded[0].content == "hello"
    assert loaded[1].role == "assistant"
    assert loaded[1].content == "hi there"


@pytest.mark.asyncio
async def test_load_messages_with_limit(repo: ChatRepository) -> None:
    for i in range(5):
        await repo.save_message(Message(role="user", content=f"msg{i}"))
    loaded = await repo.load_messages(limit=3)
    assert len(loaded) == 3
    assert loaded[0].content == "msg0"
    assert loaded[-1].content == "msg2"
