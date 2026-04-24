"""WebSocket client for communicating with the Gateway."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import uuid
from collections.abc import Callable
from typing import Any

import structlog
import websockets
from websockets import ClientConnection

_logger = structlog.get_logger("aipet.frontend.client")


def fire_and_forget(coro: Any) -> None:
    """Schedule a coroutine and log any unhandled exceptions."""
    task = asyncio.ensure_future(coro)

    def _on_done(t: asyncio.Task[Any]) -> None:
        if not t.done():
            return
        exc = t.exception()
        if exc is not None and not isinstance(exc, asyncio.CancelledError):
            logging.getLogger("aipet.frontend.fire_and_forget").exception(
                "Unhandled error in fire-and-forget task %s", t.get_name()
            )

    task.add_done_callback(_on_done)


class GatewayClient:
    """Async WebSocket client that connects to the AIPet Gateway.

    Automatically reconnects with exponential back-off when the connection
    drops.  A heartbeat ping is sent every 15 seconds to detect half-open
    connections.
    """

    def __init__(self, uri: str = "ws://127.0.0.1:18790") -> None:
        self.uri = uri
        self._ws: ClientConnection | None = None
        self._handlers: dict[str, list[Callable[[dict[str, Any]], None]]] = {}
        self._response_futures: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._running = False
        self._should_reconnect = True
        self._read_task: asyncio.Task[None] | None = None
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._reconnect_task: asyncio.Task[None] | None = None
        self._reconnect_delay = 1.0
        self._max_reconnect_delay = 30.0
        self._on_connect_callbacks: list[Callable[[], None]] = []

    async def connect(self) -> None:
        """Establish connection and start background reconnect watcher."""
        self._should_reconnect = True
        await self._try_connect()
        self._reconnect_task = asyncio.create_task(self._reconnect_loop())

    async def disconnect(self) -> None:
        """Close the connection and stop automatic reconnection."""
        self._should_reconnect = False
        self._running = False

        if self._reconnect_task is not None:
            self._reconnect_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reconnect_task
            self._reconnect_task = None

        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._heartbeat_task
            self._heartbeat_task = None

        for fut in list(self._response_futures.values()):
            if not fut.done():
                fut.cancel()
        self._response_futures.clear()

        if self._ws is not None:
            await self._ws.close()
            self._ws = None

        if self._read_task is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await self._read_task
            self._read_task = None

    async def send(self, data: dict[str, Any]) -> None:
        """Send a JSON message to the Gateway."""
        if self._ws is not None:
            try:
                await self._ws.send(json.dumps(data))
            except websockets.exceptions.ConnectionClosed:
                _logger.warning("Send failed: connection closed")

    async def request(
        self,
        method: str,
        payload: dict[str, Any] | None = None,
        timeout: float = 10.0,
    ) -> dict[str, Any]:
        """Send a request and wait for the matching response."""
        req_id = f"req_{uuid.uuid4().hex}_{asyncio.get_event_loop().time()}"
        fut: asyncio.Future[dict[str, Any]] = asyncio.get_event_loop().create_future()
        self._response_futures[req_id] = fut
        await self.send({
            "id": req_id,
            "type": "request",
            "method": method,
            "payload": payload or {},
        })
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        finally:
            self._response_futures.pop(req_id, None)

    def on(self, method: str, handler: Callable[[dict[str, Any]], None]) -> None:
        """Register a handler for a specific Gateway event method."""
        self._handlers.setdefault(method, []).append(handler)

    def off(self, method: str, handler: Callable[[dict[str, Any]], None]) -> None:
        """Unregister a handler."""
        handlers = self._handlers.get(method, [])
        if handler in handlers:
            handlers.remove(handler)

    @property
    def connected(self) -> bool:
        """Return whether the WebSocket connection is active."""
        return self._running and self._ws is not None

    def on_connect(self, callback: Callable[[], None]) -> None:
        """Register a callback invoked after each successful connection."""
        self._on_connect_callbacks.append(callback)

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    async def _try_connect(self) -> None:
        """Single connection attempt."""
        self._ws = await websockets.connect(self.uri)
        self._running = True
        await self.send({
            "type": "request",
            "method": "client.hello",
            "payload": {"client_type": "pyqt", "version": "2.0.0a1"},
        })
        self._read_task = asyncio.create_task(self._read_loop())
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        for cb in self._on_connect_callbacks:
            try:
                cb()
            except Exception:
                _logger.exception("Connect callback error")
        _logger.info("Connected to Gateway", uri=self.uri)

    async def _reconnect_loop(self) -> None:
        """Watch the read loop and reconnect if it exits unexpectedly."""
        while self._should_reconnect:
            if self._read_task is not None:
                try:
                    await self._read_task
                except asyncio.CancelledError:
                    return

            if not self._should_reconnect:
                return

            _logger.debug("Connection lost, reconnecting", delay=self._reconnect_delay)
            await asyncio.sleep(self._reconnect_delay)
            self._reconnect_delay = min(self._reconnect_delay * 2, self._max_reconnect_delay)
            try:
                await self._try_connect()
                self._reconnect_delay = 1.0
            except Exception as exc:
                _logger.warning("Reconnect attempt failed", error=str(exc))

    async def _heartbeat_loop(self) -> None:
        """Send ping every 15 s to detect half-open connections."""
        while self._running and self._ws is not None:
            try:
                await self._ws.ping()
                await asyncio.sleep(15)
            except Exception:
                break

    async def _read_loop(self) -> None:
        """Continuously read messages from the Gateway."""
        if self._ws is None:
            return
        try:
            async for raw in self._ws:
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                method = data.get("method", "")
                msg_type = data.get("type", "")
                msg_id = data.get("id")

                # Fulfill pending request futures
                if msg_type == "response" and msg_id and msg_id in self._response_futures:
                    if not self._response_futures[msg_id].done():
                        self._response_futures[msg_id].set_result(data.get("payload", {}))
                    continue

                for handler in self._handlers.get(method, []):
                    try:
                        handler(data.get("payload", {}))
                    except Exception as exc:
                        _logger.exception("Handler error")
        except websockets.exceptions.ConnectionClosed:
            self._running = False
        except Exception as exc:
            _logger.exception("Read loop error")
            self._running = False
