"""Typed EventBus for asynchronous, decoupled communication."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)

Handler = Callable[[T], Awaitable[None]]


class EventBus:
    """A type-safe, async event bus.

    Usage:
        bus = EventBus()
        bus.subscribe(ChatMessageReceived, on_message)
        await bus.emit(ChatMessageReceived(...))
    """

    def __init__(self) -> None:
        self._handlers: dict[type[BaseModel], list[Handler[Any]]] = defaultdict(list)
        self._lock = asyncio.Lock()

    def subscribe(self, event_type: type[T], handler: Handler[T]) -> None:
        """Register an async handler for a specific event type."""
        self._handlers[event_type].append(handler)

    def unsubscribe(self, event_type: type[T], handler: Handler[T]) -> None:
        """Remove a previously registered handler."""
        handlers = self._handlers.get(event_type, [])
        if handler in handlers:
            handlers.remove(handler)

    async def emit(self, event: BaseModel) -> None:
        """Emit an event to all subscribed handlers concurrently."""
        async with self._lock:
            handlers = list(self._handlers.get(type(event), []))
        if not handlers:
            return
        # Run all handlers concurrently; exceptions in one do not break others
        results = await asyncio.gather(
            *[h(event) for h in handlers],
            return_exceptions=True,
        )
        for result in results:
            if isinstance(result, Exception):
                # In production this should go through structured logging
                print(f"EventBus handler error: {result}")

    def clear(self) -> None:
        """Remove all subscriptions."""
        self._handlers.clear()
