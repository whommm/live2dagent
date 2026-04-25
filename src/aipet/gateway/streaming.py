"""Streaming event types and ToolCallStreamGuard for safe tool-call handling."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass
class StreamEvent:
    """A single streaming event to be broadcast to clients."""

    method: str
    payload: dict[str, Any] = field(default_factory=dict)


class ToolCallStreamGuard:
    """Prevents suspected tool-call JSON from leaking to the frontend.

    State machine:
        streaming  -> candidate      (on seeing a tool-envelope prefix)
        candidate  -> confirmed_tool (valid tool JSON successfully parsed)
        candidate  -> confirmed_text (clearly not a tool call, flush buffer)
    """

    TOOL_ENVELOPES: tuple[str, ...] = ('{"tool_calls"', '{"action"', '{"tool"')
    MAX_BUFFER_CHARS: int = 2048

    def __init__(self, max_buffer_chars: int = MAX_BUFFER_CHARS) -> None:
        self.buffer: str = ""
        self.state: Literal["streaming", "candidate", "confirmed_tool", "confirmed_text"] = "streaming"
        self.max_buffer: int = max_buffer_chars

    def _ev(self, method: str, **payload: Any) -> StreamEvent:
        return StreamEvent(method=method, payload=payload)

    def feed(self, chunk: str) -> list[StreamEvent]:
        """Process a text chunk and return events to broadcast.

        The caller is responsible for injecting ``session_id`` / ``message_id``
        into the event payload before broadcasting.
        """
        events: list[StreamEvent] = []

        if self.state == "confirmed_text":
            if chunk:
                events.append(self._ev("chat.stream.chunk", delta=chunk))
            return events

        if self.state == "confirmed_tool":
            # Don't emit any text deltas for a confirmed tool call.
            return events

        if self.state == "streaming":
            self.buffer += chunk
            stripped = self.buffer.lstrip()

            if stripped.startswith(self.TOOL_ENVELOPES):
                self.state = "candidate"
                events.append(
                    self._ev(
                        "chat.stream.thinking",
                        reason="detected_possible_tool_call",
                    )
                )
                # The chunk may already contain a complete JSON object;
                # attempt immediate parse before returning.
                parsed = self._try_parse_tool_json(self.buffer)
                if parsed is not None:
                    self.state = "confirmed_tool"
                    events.append(
                        self._ev("chat.tool_call.detected", tool_calls=parsed)
                    )
                    self.buffer = ""
                return events

            if len(self.buffer) > self.max_buffer:
                self.state = "confirmed_text"
                events.append(self._ev("chat.stream.chunk", delta=self.buffer))
                self.buffer = ""
                return events

            if chunk and not self._might_be_tool_prefix(stripped):
                self.state = "confirmed_text"
                events.append(self._ev("chat.stream.chunk", delta=self.buffer))
                self.buffer = ""
                return events

            return events

        if self.state == "candidate":
            self.buffer += chunk

            if len(self.buffer) > self.max_buffer:
                self.state = "confirmed_text"
                events.append(self._ev("chat.stream.chunk", delta=self.buffer))
                self.buffer = ""
                return events

            parsed = self._try_parse_tool_json(self.buffer)
            if parsed is not None:
                self.state = "confirmed_tool"
                events.append(
                    self._ev("chat.tool_call.detected", tool_calls=parsed)
                )
                self.buffer = ""
                return events

            if not self._might_be_tool_prefix(self.buffer.lstrip()):
                self.state = "confirmed_text"
                events.append(self._ev("chat.stream.chunk", delta=self.buffer))
                self.buffer = ""
                return events

            return events

        return events

    def flush(self) -> list[StreamEvent]:
        """Flush any remaining buffer at the end of a stream."""
        events: list[StreamEvent] = []
        if self.state == "candidate" and self.buffer:
            self.state = "confirmed_text"
            events.append(self._ev("chat.stream.chunk", delta=self.buffer))
            self.buffer = ""
        return events

    @staticmethod
    def _might_be_tool_prefix(text: str) -> bool:
        """Return True if *text* could still be the start of a tool envelope."""
        if not text:
            return True
        stripped = text.lstrip()
        if not stripped:
            return True
        return stripped[0] == "{"

    @staticmethod
    def _try_parse_tool_json(text: str) -> list[dict[str, Any]] | None:
        """Attempt to parse *text* as a tool-calls payload.

        Returns the list of tool calls on success, or ``None``.
        """
        text = text.strip()
        if not text:
            return None
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return None
        if isinstance(data, dict) and "tool_calls" in data:
            return data["tool_calls"]
        if isinstance(data, list):
            return data
        return None
