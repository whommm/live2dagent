"""Tool Router: execute skill tools with sandboxing."""

from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import json
import multiprocessing
import pickle
import queue
import time
from dataclasses import dataclass
from multiprocessing.queues import Queue
from pathlib import Path
from typing import Any

from aipet.gateway.skills.context import (
    reset_current_skill_session_id,
    set_current_skill_session_id,
)
from aipet.gateway.skills.registry import SkillRegistry
from aipet.gateway.skills.sandbox import (
    SandboxViolationError,
    check_permissions,
    install_sandbox,
    uninstall_sandbox,
)


def _safe_queue_put(result_queue: Queue[Any], payload: dict[str, Any]) -> None:
    """Put a result payload on a multiprocessing queue if it can be pickled."""
    try:
        pickle.dumps(payload)
    except Exception as exc:
        payload = {
            "ok": False,
            "data": None,
            "error": {
                "code": "TOOL_RESULT_NOT_SERIALIZABLE",
                "message": f"Tool result could not be sent to the parent process: {exc}",
            },
        }
    result_queue.put(payload)


def _run_sync_tool_process(
    skill_path: str,
    skill_id: str,
    tool_name: str,
    permissions: list[str],
    arguments: dict[str, Any],
    session_id: str | None,
    result_queue: Queue[Any],
) -> None:
    """Load and execute one sync tool inside an isolated child process."""
    guard: Any = None
    session_token: Any = None
    try:
        module_name = f"aipet.gateway.skills.process.{skill_id}_{time.time_ns()}"
        spec = importlib.util.spec_from_file_location(module_name, skill_path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Cannot load skill module: {skill_path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        tools = getattr(module, "tools", {})
        func = tools.get(tool_name)
        if func is None:
            raise RuntimeError(f"Tool '{skill_id}:{tool_name}' not found in {skill_path}.")
        if asyncio.iscoroutinefunction(func):
            raise RuntimeError(f"Tool '{skill_id}:{tool_name}' is async, not sync.")

        guard = install_sandbox(permissions, skill_id)
        session_token = set_current_skill_session_id(session_id)
        check_permissions(func, permissions)
        output = func(**arguments)
        _safe_queue_put(result_queue, {"ok": True, "data": output, "error": None})
    except SandboxViolationError as exc:
        _safe_queue_put(
            result_queue,
            {
                "ok": False,
                "data": None,
                "error": {"code": "SANDBOX_VIOLATION", "message": str(exc)},
            },
        )
    except Exception as exc:
        _safe_queue_put(
            result_queue,
            {
                "ok": False,
                "data": None,
                "error": {"code": "TOOL_EXECUTION_ERROR", "message": str(exc)},
            },
        )
    finally:
        if session_token is not None:
            reset_current_skill_session_id(session_token)
        if guard is not None:
            uninstall_sandbox(guard)


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
        tool_name = full_name.split(":", 1)[1]

        if asyncio.iscoroutinefunction(func):
            guard = install_sandbox(skill.permissions, skill.skill_id)
            session_token = set_current_skill_session_id(session_id)
            try:
                check_permissions(func, skill.permissions)
                output = await asyncio.wait_for(func(**arguments), timeout=timeout)
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

        return await self._call_sync_tool_process(
            full_name=full_name,
            skill_id=skill.skill_id,
            tool_name=tool_name,
            skill_path=skill.path,
            permissions=skill.permissions,
            arguments=arguments,
            timeout=timeout,
            session_id=session_id,
        )

    async def _call_sync_tool_process(
        self,
        full_name: str,
        skill_id: str,
        tool_name: str,
        skill_path: Path | None,
        permissions: list[str],
        arguments: dict[str, Any],
        timeout: float,
        session_id: str | None,
    ) -> ToolExecutionResult:
        """Execute a sync tool in a child process so timeouts can stop it."""
        if skill_path is None:
            return ToolExecutionResult(
                ok=False,
                error={
                    "code": "TOOL_EXECUTION_ERROR",
                    "message": (
                        f"Tool '{full_name}' cannot run safely because its source path is unknown."
                    ),
                },
            )

        ctx = multiprocessing.get_context("spawn")
        result_queue: Queue[Any] = ctx.Queue(maxsize=1)
        process = ctx.Process(
            target=_run_sync_tool_process,
            args=(
                str(skill_path),
                skill_id,
                tool_name,
                permissions,
                arguments,
                session_id,
                result_queue,
            ),
            daemon=True,
        )
        process.start()
        deadline = time.monotonic() + timeout

        try:
            while process.is_alive():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    process.terminate()
                    await asyncio.to_thread(process.join, 1.0)
                    if process.is_alive():
                        process.kill()
                        await asyncio.to_thread(process.join, 1.0)
                    return ToolExecutionResult(
                        ok=False,
                        error={
                            "code": "TOOL_TIMEOUT",
                            "message": f"Tool '{full_name}' execution timed out after {timeout}s.",
                        },
                    )
                await asyncio.sleep(min(0.05, remaining))

            await asyncio.to_thread(process.join, 0)
            try:
                payload = result_queue.get(timeout=0.1)
            except queue.Empty:
                exitcode = process.exitcode
                return ToolExecutionResult(
                    ok=False,
                    error={
                        "code": "TOOL_EXECUTION_ERROR",
                        "message": (
                            f"Tool '{full_name}' exited without a result "
                            f"(exit code {exitcode})."
                        ),
                    },
                )
            return ToolExecutionResult(
                ok=bool(payload.get("ok")),
                data=payload.get("data"),
                error=payload.get("error"),
            )
        finally:
            if process.is_alive():
                process.terminate()
                with contextlib.suppress(Exception):
                    await asyncio.to_thread(process.join, 1.0)
                if process.is_alive():
                    process.kill()
                    with contextlib.suppress(Exception):
                        await asyncio.to_thread(process.join, 1.0)
            result_queue.close()
            result_queue.join_thread()


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
