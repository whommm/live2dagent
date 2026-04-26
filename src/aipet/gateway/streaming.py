"""Streaming event types and ToolCallStreamGuard for safe tool-call handling."""

from __future__ import annotations

import json
import re
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

    def __init__(
        self,
        max_buffer_chars: int = MAX_BUFFER_CHARS,
        hold_initial_text: bool = False,
    ) -> None:
        self.buffer: str = ""
        self.state: Literal[
            "streaming", "candidate", "confirmed_tool", "confirmed_text"
        ] = "streaming"
        self.max_buffer: int = max_buffer_chars
        self.hold_initial_text = hold_initial_text
        self._thinking_emitted = False

    def _ev(self, method: str, **payload: Any) -> StreamEvent:
        return StreamEvent(method=method, payload=payload)

    def _thinking_event(self) -> StreamEvent | None:
        if self._thinking_emitted:
            return None
        self._thinking_emitted = True
        return self._ev(
            "chat.stream.thinking",
            reason="detected_possible_tool_call",
        )

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

            parsed = self._try_parse_tool_json(self.buffer)
            if parsed is not None:
                self.state = "confirmed_tool"
                thinking = self._thinking_event()
                if thinking is not None:
                    events.append(thinking)
                events.append(self._ev("chat.tool_call.detected", tool_calls=parsed))
                self.buffer = ""
                return events

            if self._looks_like_tool_candidate(self.buffer):
                self.state = "candidate"
                thinking = self._thinking_event()
                if thinking is not None:
                    events.append(thinking)
                return events

            if len(self.buffer) > self.max_buffer:
                self.state = "confirmed_text"
                events.append(self._ev("chat.stream.chunk", delta=self.buffer))
                self.buffer = ""
                return events

            if self.hold_initial_text:
                return events

            if chunk and not self._might_be_tool_prefix(stripped):
                self.state = "confirmed_text"
                events.append(self._ev("chat.stream.chunk", delta=self.buffer))
                self.buffer = ""
                return events

            return events

        if self.state == "candidate":
            self.buffer += chunk

            if len(self.buffer) > self.max_buffer and not self._looks_like_tool_candidate(
                self.buffer
            ):
                self.state = "confirmed_text"
                events.append(self._ev("chat.stream.chunk", delta=self.buffer))
                self.buffer = ""
                return events

            parsed = self._try_parse_tool_json(self.buffer)
            if parsed is not None:
                self.state = "confirmed_tool"
                thinking = self._thinking_event()
                if thinking is not None:
                    events.append(thinking)
                events.append(
                    self._ev("chat.tool_call.detected", tool_calls=parsed)
                )
                self.buffer = ""
                return events

            if self._looks_like_tool_candidate(self.buffer):
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
        if self.state in {"streaming", "candidate"} and self.buffer:
            parsed = self._try_parse_tool_json(self.buffer)
            if parsed is not None:
                self.state = "confirmed_tool"
                thinking = self._thinking_event()
                if thinking is not None:
                    events.append(thinking)
                events.append(self._ev("chat.tool_call.detected", tool_calls=parsed))
                self.buffer = ""
                return events
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

    @classmethod
    def _looks_like_tool_candidate(cls, text: str) -> bool:
        stripped = text.lstrip()
        if not stripped:
            return True
        if stripped.startswith(cls.TOOL_ENVELOPES):
            return True
        if '"tool_calls"' in stripped or "'tool_calls'" in stripped:
            return True
        return "DSML" in stripped and "invoke" in stripped

    @staticmethod
    def _try_parse_tool_json(text: str) -> list[dict[str, Any]] | None:
        """Attempt to parse *text* as a tool-calls payload.

        Returns the list of tool calls on success, or ``None``.
        """
        text = text.strip()
        if not text:
            return None
        data = ToolCallStreamGuard._try_load_json(text)
        if isinstance(data, dict) and "tool_calls" in data:
            return data["tool_calls"]
        if isinstance(data, list):
            return data
        embedded = ToolCallStreamGuard._extract_embedded_tool_json(text)
        if embedded is not None:
            return embedded
        dsml = ToolCallStreamGuard._parse_dsml_tool_calls(text)
        if dsml is not None:
            return dsml
        return None

    @staticmethod
    def _try_load_json(text: str) -> Any | None:
        text = text.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            text = "\n".join(lines).strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return None

    @staticmethod
    def _extract_embedded_tool_json(text: str) -> list[dict[str, Any]] | None:
        marker = re.search(r'["\']tool_calls["\']\s*:', text)
        if not marker:
            return None
        start = marker.start()
        while start > 0 and text[start] != "{":
            start -= 1
        if start < 0 or text[start] != "{":
            return None
        end = ToolCallStreamGuard._find_balanced_object_end(text, start)
        if end is None:
            return None
        data = ToolCallStreamGuard._try_load_json(text[start : end + 1])
        if isinstance(data, dict) and isinstance(data.get("tool_calls"), list):
            return data["tool_calls"]
        return None

    @staticmethod
    def _find_balanced_object_end(text: str, start: int) -> int | None:
        depth = 0
        in_string = False
        quote = ""
        escape = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_string:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == quote:
                    in_string = False
                continue
            if ch in {'"', "'"}:
                in_string = True
                quote = ch
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return i
        return None

    @staticmethod
    def _parse_dsml_tool_calls(text: str) -> list[dict[str, Any]] | None:
        if "DSML" not in text or "invoke" not in text:
            return None
        invoke_pat = re.compile(
            r'<[^>]*DSML[^>]*invoke\s+name="([^"]+)"[^>]*>(.*?)</[^>]*invoke>',
            re.DOTALL,
        )
        param_pat = re.compile(
            r'<[^>]*DSML[^>]*parameter\s+name="([^"]+)"(?:\s+\w+="[^"]*")*>(.*?)</[^>]*parameter>',
            re.DOTALL,
        )
        calls: list[dict[str, Any]] = []
        for inv in invoke_pat.finditer(text):
            args: dict[str, Any] = {}
            for pm in param_pat.finditer(inv.group(2)):
                raw = pm.group(2).strip()
                args[pm.group(1)] = ToolCallStreamGuard._coerce_text_value(raw)
            calls.append({"name": inv.group(1), "arguments": args})
        return calls or None

    @staticmethod
    def _coerce_text_value(value: str) -> Any:
        lowered = value.lower()
        if lowered == "true":
            return True
        if lowered == "false":
            return False
        try:
            if "." in value:
                return float(value)
            return int(value)
        except ValueError:
            return value
