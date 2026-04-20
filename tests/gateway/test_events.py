"""Tests for the typed EventBus."""

import asyncio

import pytest
from pydantic import BaseModel

from aipet.gateway.events import EventBus


class DemoEvent(BaseModel):
    message: str


class AnotherEvent(BaseModel):
    value: int


@pytest.mark.asyncio
async def test_emit_dispatches_to_subscribers() -> None:
    bus = EventBus()
    received: list[str] = []

    async def handler(event: DemoEvent) -> None:
        received.append(event.message)

    bus.subscribe(DemoEvent, handler)
    await bus.emit(DemoEvent(message="hello"))

    assert received == ["hello"]


@pytest.mark.asyncio
async def test_emit_does_not_dispatch_to_unrelated_subscribers() -> None:
    bus = EventBus()
    received: list[int] = []

    async def handler(event: AnotherEvent) -> None:
        received.append(event.value)

    bus.subscribe(AnotherEvent, handler)
    await bus.emit(DemoEvent(message="ignored"))

    assert received == []


@pytest.mark.asyncio
async def test_multiple_subscribers_run_concurrently() -> None:
    bus = EventBus()
    order: list[str] = []

    async def slow_handler(event: DemoEvent) -> None:
        await asyncio.sleep(0.05)
        order.append("slow")

    async def fast_handler(event: DemoEvent) -> None:
        order.append("fast")

    bus.subscribe(DemoEvent, slow_handler)
    bus.subscribe(DemoEvent, fast_handler)
    await bus.emit(DemoEvent(message="x"))

    # Because they run concurrently, fast should finish first despite slow sleeping
    assert order == ["fast", "slow"]


@pytest.mark.asyncio
async def test_handler_exception_does_not_break_others() -> None:
    bus = EventBus()
    received: list[str] = []

    async def bad_handler(event: DemoEvent) -> None:
        raise RuntimeError("boom")

    async def good_handler(event: DemoEvent) -> None:
        received.append(event.message)

    bus.subscribe(DemoEvent, bad_handler)
    bus.subscribe(DemoEvent, good_handler)
    await bus.emit(DemoEvent(message="survived"))

    assert received == ["survived"]
