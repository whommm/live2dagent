"""Session management for isolated AI contexts."""

from __future__ import annotations

from uuid import uuid4

from aipet.gateway.events import EventBus
from aipet.gateway.models import Message, Session
from aipet.gateway.repository import ChatRepository


class SessionManager:
    """Manages creation, retrieval, and lifecycle of sessions with persistence."""

    def __init__(self, bus: EventBus | None = None) -> None:
        self._sessions: dict[str, Session] = {}
        self._bus = bus
        self._repos: dict[str, ChatRepository] = {}

    async def init(self) -> None:
        """Initialize persistence and load existing sessions from disk."""
        # Load all existing sessions
        repo = ChatRepository("main")
        session_ids = await repo.list_all_session_ids()
        for sid in session_ids:
            await self._load_session(sid)
        # Only create default "main" session if no sessions exist at all
        if not self._sessions:
            main = self.create("main", name="Main Session", soul_path="soul.md")
            await self.save_session(main)

    async def _ensure_repo(self, session_id: str) -> ChatRepository:
        """Get or create a repository for the session."""
        if session_id not in self._repos:
            repo = ChatRepository(session_id)
            await repo.init_db()
            self._repos[session_id] = repo
        return self._repos[session_id]

    async def _load_session(self, session_id: str) -> Session | None:
        """Load a session and its messages from SQLite."""
        repo = await self._ensure_repo(session_id)
        session = await repo.load_session()
        if session is None:
            return None
        messages = await repo.load_messages(limit=1000)
        session.messages = messages
        self._sessions[session_id] = session
        return session

    def create(
        self, session_id: str | None = None, *, name: str, soul_path: str = "soul.md"
    ) -> Session:
        """Create a new session."""
        sid = session_id or str(uuid4())
        session = Session(id=sid, name=name, soul_path=soul_path)
        self._sessions[sid] = session
        return session

    def get(self, session_id: str) -> Session | None:
        """Retrieve a session by ID."""
        return self._sessions.get(session_id)

    def list_sessions(self) -> list[Session]:
        """Return all sessions sorted by update time desc."""
        return sorted(self._sessions.values(), key=lambda s: s.updated_at, reverse=True)

    async def delete(self, session_id: str) -> bool:
        """Delete a session from memory and disk."""
        if session_id not in self._sessions:
            return False
        del self._sessions[session_id]
        repo = self._repos.pop(session_id, None)
        if repo is not None:
            return await repo.delete_session()
        return False

    async def rename(self, session_id: str, name: str) -> bool:
        """Rename a session and persist it."""
        session = self._sessions.get(session_id)
        if session is None:
            return False
        session.name = name
        repo = await self._ensure_repo(session_id)
        await repo.save_session(session)
        return True

    async def add_message(
        self, session_id: str, message: Message, *, update_session: bool = True
    ) -> Session | None:
        """Add a message to a session and persist it."""
        session = self._sessions.get(session_id)
        if session is None:
            return None
        session.add_message(message)
        repo = await self._ensure_repo(session_id)
        await repo.save_message(message)
        if update_session:
            await repo.save_session(session)
        return session

    async def clear_messages(self, session_id: str) -> bool:
        """Clear all messages in a session."""
        session = self._sessions.get(session_id)
        if session is None:
            return False
        session.messages.clear()
        repo = await self._ensure_repo(session_id)
        return await repo.clear_messages()

    async def save_session(self, session: Session) -> None:
        """Persist session metadata."""
        repo = await self._ensure_repo(session.id)
        await repo.save_session(session)

    async def compact_session(
        self, session_id: str, summary: str
    ) -> Session | None:
        """Replace early messages with a memory summary.

        The summary is stored in the session's memory_summary field and
        the oldest messages are removed from the in-memory list (but
        remain in the database for history viewing).
        """
        session = self._sessions.get(session_id)
        if session is None:
            return None
        session.memory_summary = summary
        # Keep only the most recent 10 messages in memory for AI context
        if len(session.messages) > 10:
            session.messages = session.messages[-10:]
        await self.save_session(session)
        return session
