"""Phase 1 structured tool decision: direct reply vs. tool call."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from aipet.gateway.providers.ai import AIProvider, Message


class SelectedTool(BaseModel):
    """A tool selected by the Phase-1 decider."""

    name: str
    reason: str = ""


class ToolDecision(BaseModel):
    """Structured decision output from Phase 1.

    *action*:
        - ``"direct"`` – the user is just chatting; no tool is needed.
        - ``"tool"``   – one or more tools should be invoked.
    """

    action: str = Field(..., pattern="^(direct|tool)$")
    selected_tools: list[SelectedTool] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class ToolDecider:
    """Lightweight Phase-1 router that decides whether tools are needed.

    Uses a *short* non-streaming call with very low ``max_tokens`` so the
    latency / cost impact is minimal.
    """

    def __init__(self, provider: AIProvider, max_tokens: int = 128) -> None:
        self.provider = provider
        self.max_tokens = max_tokens

    def _build_messages(
        self, user_content: str, tool_briefs: list[dict[str, str]]
    ) -> list[Message]:
        if tool_briefs:
            briefs_text = "\n".join(
                f"- {b['name']}: {b['brief']}" for b in tool_briefs
            )
        else:
            briefs_text = "（无可用工具）"

        system_text = (
            "你是工具路由决策器。根据用户输入和可用工具列表，判断用户是否需要调用工具。\n\n"
            "可用工具（仅名称和简介）：\n"
            f"{briefs_text}\n\n"
            "输出要求：只输出一个合法的 JSON 对象，不要任何解释、markdown 代码块或其他文字。\n"
            "JSON 格式：\n"
            "{\n"
            '  "action": "direct" | "tool",\n'
            '  "selected_tools": [\n'
            '    {"name": "skill_id:tool_name", "reason": "简要理由"}\n'
            "  ],\n"
            '  "confidence": 0.0-1.0\n'
            "}\n\n"
            "规则：\n"
            '- 闲聊、打招呼、表达情绪 → action 必须是 "direct"\n'
            '- 查询信息、设置提醒、操作文件等 → action 是 "tool"\n'
            "- confidence 表示确信程度\n"
            '- selected_tools 只在 action="tool" 时填写，最多选 3 个'
        )

        return [
            Message(role="system", content=system_text),
            Message(role="user", content=user_content),
        ]

    async def decide(
        self, user_content: str, tool_briefs: list[dict[str, str]]
    ) -> ToolDecision:
        """Run Phase-1 and return a structured decision.

        Falls back to ``action="direct"`` on any parse or validation error
        so that a failure here never blocks the user from getting a reply.
        """
        messages = self._build_messages(user_content, tool_briefs)

        # Collect the (normally tiny) streaming response into one string.
        text = ""
        try:
            async for chunk in self.provider.chat(
                messages, tools=None, temperature=0.0, max_tokens=self.max_tokens
            ):
                text += chunk.delta
        except Exception:
            # If the decision call itself fails, fall back to direct reply.
            return ToolDecision(action="direct", confidence=0.0)

        text = text.strip()

        # Strip markdown fences if the model wrapped JSON in ```...
        if text.startswith("```"):
            lines = text.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            text = "\n".join(lines).strip()

        # Strict parse + Pydantic validation
        try:
            data = json.loads(text)
            decision = ToolDecision.model_validate(data)
            return decision
        except (json.JSONDecodeError, ValidationError):
            pass

        # Heuristic fallback
        lower = text.lower()
        if '"action"' in lower and '"direct"' in lower:
            return ToolDecision(action="direct", confidence=0.5)
        if '"action"' in lower and '"tool"' in lower:
            # Try to extract tool names with a simple regex
            import re

            names = re.findall(r'"name"\s*:\s*"([^"]+)"', text)
            selected = [SelectedTool(name=n, reason="") for n in names if ":" in n]
            return ToolDecision(
                action="tool",
                selected_tools=selected,
                confidence=0.5,
            )

        # Ultimate fallback: direct reply (safe default)
        return ToolDecision(action="direct", confidence=0.3)
