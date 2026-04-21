"""WebSocket client for communicating with the Gateway."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import Callable
from typing import Any

import websockets
from websockets import ClientConnection

_logger = logging.getLogger("aipet.frontend.client")


class GatewayClient:
    """Async WebSocket client that connects to the AIPet Gateway."""

    def __init__(self, uri: str = "ws://127.0.0.1:18790") -> None:
        self.uri = uri
        self._ws: ClientConnection | None = None
        self._handlers: dict[str, list[Callable[[dict[str, Any]], None]]] = {}
        self._response_futures: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._running = False
        self._read_task: asyncio.Task[None] | None = None

    async def connect(self) -> None:
        """Establish connection and start the read loop."""
        self._ws = await websockets.connect(self.uri)
        self._running = True
        # Send handshake
        await self.send({
            "type": "request",
            "method": "client.hello",
            "payload": {"client_type": "pyqt", "version": "2.0.0a1"},
        })
        # Start background read loop
        self._read_task = asyncio.create_task(self._read_loop())
        self._read_task.add_done_callback(self._on_read_loop_done)

    async def disconnect(self) -> None:
        """Close the connection."""
        self._running = False
        # Cancel any pending request futures so they don't hang forever
        for fut in list(self._response_futures.values()):
            if not fut.done():
                fut.cancel()
        self._response_futures.clear()
        if self._ws is not None:
            await self._ws.close()
            self._ws = None

    async def send(self, data: dict[str, Any]) -> None:
        """Send a JSON message to the Gateway."""
        if self._ws is not None:
            await self._ws.send(json.dumps(data))

    async def request(self, method: str, payload: dict[str, Any] | None = None, timeout: float = 10.0) -> dict[str, Any]:
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

    def _on_read_loop_done(self, task: asyncio.Task[None]) -> None:
        """Handle read loop termination (crash or normal close)."""
        try:
            task.result()
        except Exception as exc:
            _logger.exception("Read loop terminated unexpectedly")
        self._running = False

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
