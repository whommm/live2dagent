"""Phase-1 structured tool decision: direct reply vs. tool call."""

from __future__ import annotations

import json

from pydantic import BaseModel, Field, ValidationError

from aipet.gateway.providers.ai import AIProvider, Message


class SelectedTool(BaseModel):
    """A tool selected by the Phase-1 decider."""

    name: str
    reason: str = ""


class ToolDecision(BaseModel):
    """Structured decision output from Phase 1."""

    action: str = Field(..., pattern="^(direct|tool)$")
    selected_tools: list[SelectedTool] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class ToolDecider:
    """Lightweight Phase-1 router that decides whether tools are needed."""

    def __init__(self, provider: AIProvider, max_tokens: int = 128) -> None:
        self.provider = provider
        self.max_tokens = max_tokens

    def _build_messages(
        self, user_content: str, tool_briefs: list[dict[str, str]]
    ) -> list[Message]:
        briefs_text = (
            "\n".join(f"- {b['name']}: {b['brief']}" for b in tool_briefs)
            if tool_briefs
            else "(no available tools)"
        )

        system_text = (
            "You are a tool-routing decision engine. Decide whether the user's "
            "message needs one of the available tools.\n\n"
            "Available tools (name and short brief only):\n"
            f"{briefs_text}\n\n"
            "Output only one valid JSON object. No markdown, no prose.\n"
            "JSON shape:\n"
            "{\n"
            '  "action": "direct" | "tool",\n'
            '  "selected_tools": [\n'
            '    {"name": "skill_id:tool_name", "reason": "brief reason"}\n'
            "  ],\n"
            '  "confidence": 0.0-1.0\n'
            "}\n\n"
            "Rules:\n"
            '- Casual chat, greetings, and emotional conversation => "direct".\n'
            '- Requests to fetch current information, inspect files, schedule tasks, '
            'calculate, operate the desktop/app, generate images, draw pictures, '
            'paint, or create visual art => "tool".\n'
            "- Only select tool names from the available tool list.\n"
            '- Fill selected_tools only when action is "tool"; select at most 3.\n'
        )

        return [
            Message(role="system", content=system_text),
            Message(role="user", content=user_content),
        ]

    async def decide(
        self, user_content: str, tool_briefs: list[dict[str, str]]
    ) -> ToolDecision:
        """Run Phase-1 and return a structured decision."""
        messages = self._build_messages(user_content, tool_briefs)

        text = ""
        try:
            complete_json = getattr(type(self.provider), "complete_json", None)
            if complete_json is not None:
                text = await self.provider.complete_json(
                    messages,
                    schema=ToolDecision.model_json_schema(),
                    schema_name="tool_decision",
                    temperature=0.0,
                    max_tokens=self.max_tokens,
                )
            else:
                async for chunk in self.provider.chat(
                    messages, tools=None, temperature=0.0, max_tokens=self.max_tokens
                ):
                    text += chunk.delta
        except Exception:
            return ToolDecision(action="direct", confidence=0.0)

        text = text.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            text = "\n".join(lines).strip()

        try:
            data = json.loads(text)
            decision = ToolDecision.model_validate(data)
            return self._filter_unavailable_tools(decision, tool_briefs)
        except (json.JSONDecodeError, ValidationError):
            pass

        lower = text.lower()
        if '"action"' in lower and '"direct"' in lower:
            return ToolDecision(action="direct", confidence=0.5)
        if '"action"' in lower and '"tool"' in lower:
            import re

            names = re.findall(r'"name"\s*:\s*"([^"]+)"', text)
            selected = [SelectedTool(name=n, reason="") for n in names if ":" in n]
            return self._filter_unavailable_tools(
                ToolDecision(
                    action="tool",
                    selected_tools=selected,
                    confidence=0.5,
                ),
                tool_briefs,
            )

        return ToolDecision(action="direct", confidence=0.3)

    @staticmethod
    def _filter_unavailable_tools(
        decision: ToolDecision, tool_briefs: list[dict[str, str]]
    ) -> ToolDecision:
        if decision.action != "tool":
            return decision
        available = {b["name"] for b in tool_briefs}
        selected = [t for t in decision.selected_tools if t.name in available][:3]
        return ToolDecision(
            action="tool",
            selected_tools=selected,
            confidence=decision.confidence,
        )
