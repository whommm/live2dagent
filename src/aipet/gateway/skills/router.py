"""Tool Router: execute skill tools with sandboxing."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any

from aipet.gateway.skills.registry import SkillRegistry
from aipet.gateway.skills.context import (
    reset_current_skill_session_id,
    set_current_skill_session_id,
)
from aipet.gateway.skills.sandbox import (
    SandboxViolationError,
    check_permissions,
    install_sandbox,
    uninstall_sandbox,
)


class ToolRouter:
    """Route and execute tool calls from AI responses."""

    def __init__(self, registry: SkillRegistry) -> None:
        self.registry = registry

    async def call(
        self,
        full_name: str,
        arguments: dict[str, Any],
        timeout: float = 30.0,
        session_id: str | None = None,
    ) -> ToolExecutionResult:
        """Execute a tool and return a structured result."""
        result = self.registry.get_tool(full_name)
        if result is None:
            return ToolExecutionResult(
                ok=False,
                error={"code": "TOOL_NOT_FOUND", "message": f"Tool '{full_name}' not found."},
            )
        func, skill = result

        guard = install_sandbox(skill.permissions, skill.skill_id)
        session_token = set_current_skill_session_id(session_id)
        try:
            check_permissions(func, skill.permissions)
            if asyncio.iscoroutinefunction(func):
                output = await asyncio.wait_for(func(**arguments), timeout=timeout)
            else:
                # Run sync functions in thread pool to avoid blocking the loop
                output = await asyncio.wait_for(
                    asyncio.get_event_loop().run_in_executor(None, lambda: func(**arguments)),
                    timeout=timeout,
                )
            return ToolExecutionResult(ok=True, data=output)
        except TimeoutError:
            return ToolExecutionResult(
                ok=False,
                error={
                    "code": "TOOL_TIMEOUT",
                    "message": f"Tool '{full_name}' execution timed out after {timeout}s.",
                },
            )
        except SandboxViolationError as exc:
            return ToolExecutionResult(
                ok=False,
                error={"code": "SANDBOX_VIOLATION", "message": str(exc)},
            )
        except Exception as exc:
            return ToolExecutionResult(
                ok=False,
                error={"code": "TOOL_EXECUTION_ERROR", "message": str(exc)},
            )
        finally:
            reset_current_skill_session_id(session_token)
            uninstall_sandbox(guard)


@dataclass
class ToolExecutionResult:
    """Structured result returned by a tool execution."""

    ok: bool
    data: Any = None
    error: dict[str, str] | None = None

    def to_model_text(self) -> str:
        return json.dumps(self.to_payload(), ensure_ascii=False)

    def to_payload(self) -> dict[str, Any]:
        return {"ok": self.ok, "data": self.data, "error": self.error}
