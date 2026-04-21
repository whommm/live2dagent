"""Tool Router: execute skill tools with sandboxing."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from aipet.gateway.skills.registry import SkillRegistry
from aipet.gateway.skills.sandbox import SandboxViolationError, check_permissions


class ToolRouter:
    """Route and execute tool calls from AI responses."""

    def __init__(self, registry: SkillRegistry) -> None:
        self.registry = registry

    async def call(
        self, full_name: str, arguments: dict[str, Any], timeout: float = 30.0
    ) -> str:
        """Execute a tool and return the result as a string."""
        result = self.registry.get_tool(full_name)
        if result is None:
            return f"Error: Tool '{full_name}' not found."
        func, skill = result

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
            if isinstance(output, (dict, list)):
                return json.dumps(output, ensure_ascii=False)
            return str(output)
        except asyncio.TimeoutError:
            return f"Error: Tool '{full_name}' execution timed out after {timeout}s."
        except SandboxViolationError as exc:
            return str(exc)
        except Exception as exc:
            return f"Error executing tool: {exc}"
