"""SQLite persistence for chat history and session metadata."""

from __future__ import annotations

import contextlib
import logging
from typing import Any

import aiosqlite

from aipet.gateway.models import Message, Session
from aipet.utils.paths import get_user_data_dir

_logger = logging.getLogger("aipet.gateway.repository")


class ChatRepository:
    """Async SQLite repository for a single session's chat history."""

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self.db_path = get_user_data_dir() / "sessions" / session_id / "chat.db"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    async def init_db(self) -> None:
        """Create tables if they don't exist."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    soul_path TEXT,
                    memory_summary TEXT,
                    model_name TEXT,
                    context_window_tokens INTEGER DEFAULT 0,
                    created_at TEXT,
                    updated_at TEXT
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT,
                    tool_calls TEXT,
                    tool_call_id TEXT,
                    reasoning_content TEXT,
                    attachments TEXT,
                    model TEXT,
                    tokens_used INTEGER,
                    created_at TEXT,
                    source TEXT,
                    FOREIGN KEY (session_id) REFERENCES sessions(id)
                )
            """)
            # Migrate existing databases that lack the source column
            with contextlib.suppress(aiosqlite.OperationalError):
                await db.execute("ALTER TABLE messages ADD COLUMN source TEXT")
            with contextlib.suppress(aiosqlite.OperationalError):
                await db.execute("ALTER TABLE messages ADD COLUMN reasoning_content TEXT")
            await db.commit()

    async def save_session(self, session: Session) -> None:
        """Upsert session metadata."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO sessions (id, name, soul_path, memory_summary, model_name,
                                      context_window_tokens, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name = excluded.name,
                    soul_path = excluded.soul_path,
                    memory_summary = excluded.memory_summary,
                    model_name = excluded.model_name,
                    context_window_tokens = excluded.context_window_tokens,
                    updated_at = excluded.updated_at
                """,
                (
                    session.id,
                    session.name,
                    session.soul_path,
                    session.memory_summary,
                    session.model_name,
                    session.context_window_tokens,
                    session.created_at.isoformat(),
                    session.updated_at.isoformat(),
                ),
            )
            await db.commit()

    async def load_session(self) -> Session | None:
        """Load session metadata."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM sessions WHERE id = ?", (self.session_id,)
            ) as cursor:
                row = await cursor.fetchone()
                if row is None:
                    return None
                data = dict(row)
                return Session.model_validate(data)

    async def save_message(self, message: Message) -> None:
        """Insert a message."""
        import json

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO messages (
                    id, session_id, role, content, tool_calls, tool_call_id, reasoning_content,
                    attachments, model, tokens_used, created_at, source
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message.id,
                    self.session_id,
                    message.role,
                    message.content,
                    json.dumps(message.tool_calls) if message.tool_calls else None,
                    message.tool_call_id,
                    message.reasoning_content,
                    json.dumps(message.attachments) if message.attachments else None,
                    message.model,
                    message.tokens_used,
                    message.created_at.isoformat(),
                    message.source,
                ),
            )
            await db.commit()

    async def load_messages(self, limit: int | None = None) -> list[Message]:
        """Load messages ordered by creation time."""
        import json

        sql = "SELECT * FROM messages WHERE session_id = ? ORDER BY created_at"
        params: tuple[Any, ...] = (self.session_id,)
        if limit is not None:
            sql += " LIMIT ?"
            params = (self.session_id, limit)

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(sql, params) as cursor:
                rows = await cursor.fetchall()
                messages = []
                for row in rows:
                    data = dict(row)
                    if data.get("tool_calls"):
                        data["tool_calls"] = json.loads(data["tool_calls"])
                    else:
                        data["tool_calls"] = None
                    if data.get("attachments"):
                        data["attachments"] = json.loads(data["attachments"])
                    else:
                        data["attachments"] = []
                    messages.append(Message.model_validate(data))
                return messages

    async def delete_session(self) -> bool:
        """Delete the SQLite database file for this session."""
        try:
            if self.db_path.exists():
                # Explicitly close any open connection before unlink (Windows file lock)
                conn = await aiosqlite.connect(self.db_path)
                await conn.close()
                self.db_path.unlink()
            parent = self.db_path.parent
            if parent.exists() and not any(parent.iterdir()):
                parent.rmdir()
            return True
        except Exception:
            _logger.exception("Failed to delete session DB")
            return False

    async def rename_session(self, name: str) -> bool:
        """Update session name in the database."""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
                    "UPDATE sessions SET name = ? WHERE id = ?",
                    (name, self.session_id),
                )
                await db.commit()
            return True
        except Exception:
            _logger.exception("Failed to rename session")
            return False

    async def clear_messages(self) -> bool:
        """Delete all messages for this session."""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
                    "DELETE FROM messages WHERE session_id = ?",
                    (self.session_id,),
                )
                await db.commit()
            return True
        except Exception:
            _logger.exception("Failed to clear messages")
            return False

    async def list_all_session_ids(self) -> list[str]:
        """List all session IDs that have a database."""
        sessions_dir = get_user_data_dir() / "sessions"
        if not sessions_dir.exists():
            return []
        return [p.name for p in sessions_dir.iterdir() if (p / "chat.db").exists()]
