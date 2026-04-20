"""Global state store with immutable updates."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from aipet.gateway.events import EventBus


class ProviderStatus(BaseModel):
    """Status of an external provider."""

    name: str = ""
    healthy: bool = False
    last_error: str | None = None


class ClientInfo(BaseModel):
    """Connected client metadata."""

    client_id: str
    client_type: str  # "pyqt", "web", "mobile"
    connected_at: datetime
    version: str = ""


class Notification(BaseModel):
    """System notification."""

    id: str
    level: str  # "info", "warning", "error"
    message: str
    timestamp: datetime


class AppState(BaseModel):
    """The single source of truth for Gateway runtime state."""

    gateway_version: str = "2.0.0a1"
    current_session_id: str | None = None
    current_live2d_model: str | None = None
    clients: dict[str, ClientInfo] = Field(default_factory=dict)
    ai_provider_status: ProviderStatus = Field(
        default_factory=lambda: ProviderStatus(name="gemini")
    )
    tts_status: ProviderStatus = Field(
        default_factory=lambda: ProviderStatus(name="edge-tts")
    )
    asr_status: ProviderStatus = Field(
        default_factory=lambda: ProviderStatus(name="faster-whisper")
    )
    active_skills: list[str] = Field(default_factory=list)
    notifications: list[Notification] = Field(default_factory=list)


class StateStore:
    """Centralized state store with subscription support."""

    def __init__(self, initial_state: AppState | None = None, bus: EventBus | None = None) -> None:
        self._state = initial_state or AppState()
        self._subscribers: list[Callable[[AppState], Awaitable[None]]] = []
        self._bus = bus
        self._lock = asyncio.Lock()

    @property
    def state(self) -> AppState:
        """Read the current state snapshot."""
        return self._state.model_copy(deep=True)

    async def update(self, updater: Callable[[AppState], AppState]) -> None:
        """Apply an immutable state update and notify subscribers."""
        async with self._lock:
            self._state = updater(self._state)
        await self._notify()

    async def patch(self, **kwargs: Any) -> None:
        """Convenience method to patch specific fields."""
        await self.update(lambda s: s.model_copy(update=kwargs))

    def subscribe(self, callback: Callable[[AppState], Awaitable[None]]) -> None:
        """Subscribe to state changes."""
        self._subscribers.append(callback)

    def unsubscribe(self, callback: Callable[[AppState], Awaitable[None]]) -> None:
        """Unsubscribe from state changes."""
        if callback in self._subscribers:
            self._subscribers.remove(callback)

    async def _notify(self) -> None:
        snapshot = self.state
        for subscriber in self._subscribers:
            try:
                await subscriber(snapshot)
            except Exception as exc:
                print(f"StateStore subscriber error: {exc}")
