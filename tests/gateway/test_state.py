"""Tests for the StateStore."""

import pytest

from aipet.gateway.state import AppState, StateStore


@pytest.mark.asyncio
async def test_state_update_is_immutable() -> None:
    store = StateStore(initial_state=AppState())
    original = store.state

    await store.update(lambda s: s.model_copy(update={"gateway_version": "9.9.9"}))

    assert original.gateway_version == "2.0.0a1"
    assert store.state.gateway_version == "9.9.9"


@pytest.mark.asyncio
async def test_subscribers_notified_on_update() -> None:
    store = StateStore(initial_state=AppState())
    snapshots: list[AppState] = []

    async def subscriber(state: AppState) -> None:
        snapshots.append(state)

    store.subscribe(subscriber)
    await store.patch(gateway_version="3.0.0")

    assert len(snapshots) == 1
    assert snapshots[0].gateway_version == "3.0.0"


@pytest.mark.asyncio
async def test_subscriber_exception_does_not_break_others() -> None:
    store = StateStore(initial_state=AppState())
    received: list[str] = []

    async def bad_sub(state: AppState) -> None:
        raise RuntimeError("boom")

    async def good_sub(state: AppState) -> None:
        received.append(state.gateway_version)

    store.subscribe(bad_sub)
    store.subscribe(good_sub)
    await store.patch(gateway_version="4.0.0")

    assert received == ["4.0.0"]
