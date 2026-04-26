"""Background task scheduler for recurring skill executions.

Tasks are checked every second. When a task's next_run_at is reached,
the specified skill:tool is invoked and the result is broadcast.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from aipet.gateway.skills.router import ToolExecutionResult
from aipet.utils.paths import get_user_data_dir

_logger = logging.getLogger("aipet.gateway.scheduler")


class ScheduledTask(BaseModel):
    """A single recurring scheduled task."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    skill_id: str
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    interval_seconds: int = 60
    max_runs: int = 0  # 0 = infinite
    runs_completed: int = 0
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    last_run_at: datetime | None = None
    next_run_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    active: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "skill_id": self.skill_id,
            "tool_name": self.tool_name,
            "arguments": self.arguments,
            "interval_seconds": self.interval_seconds,
            "max_runs": self.max_runs,
            "runs_completed": self.runs_completed,
            "created_at": self.created_at.isoformat(),
            "last_run_at": self.last_run_at.isoformat() if self.last_run_at else None,
            "next_run_at": self.next_run_at.isoformat(),
            "active": self.active,
        }


# Global reference set by Gateway during init so skills can access the scheduler.
_gateway_ref: Any = None


def set_gateway(gateway: Any) -> None:
    """Called once by Gateway to publish itself to the scheduler module."""
    global _gateway_ref
    _gateway_ref = gateway


def get_gateway() -> Any:
    return _gateway_ref


class TaskScheduler:
    """Manages and executes recurring tasks."""

    def __init__(self) -> None:
        self._tasks: dict[str, ScheduledTask] = {}
        self._running = False
        self._task: asyncio.Task[Any] | None = None
        self._on_execute: Callable[[ScheduledTask, str], Any] | None = None
        self._tasks_file: Path = get_user_data_dir() / "tasks.json"

    def set_execute_callback(self, callback: Callable[[ScheduledTask, str], Any]) -> None:
        """Set a callback invoked after each task execution.

        callback(task, result_or_error_text)
        """
        self._on_execute = callback

    def start(self) -> None:
        if not self._running:
            self._load_tasks()
            self._running = True
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    async def _loop(self) -> None:
        """Main loop: wake every second and run due tasks."""
        while self._running:
            now = datetime.now(UTC)
            for task in list(self._tasks.values()):
                if not task.active:
                    continue
                if task.next_run_at <= now:
                    await self._execute(task)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stop_event(), timeout=1.0)

    async def _stop_event(self) -> None:
        """Placeholder that never resolves; used for interruptible sleep."""
        await asyncio.Future()

    async def _execute(self, task: ScheduledTask) -> None:
        """Run a single scheduled task and reschedule or deactivate."""
        gateway = get_gateway()
        result_text = ""
        try:
            if gateway is None:
                result_text = "Error: Gateway not available"
            else:
                full_name = f"{task.skill_id}:{task.tool_name}"
                result = await gateway.tool_router.call(
                    full_name, task.arguments, session_id="main"
                )
                result = await gateway._postprocess_tool_result(full_name, result)
                if isinstance(result, ToolExecutionResult):
                    result_text = result.to_model_text()[:500]
                else:
                    result_text = str(result)[:500]
        except Exception as exc:
            result_text = f"Error: {exc}"

        task.runs_completed += 1
        task.last_run_at = datetime.now(UTC)

        if self._on_execute:
            with contextlib.suppress(Exception):
                self._on_execute(task, result_text)

        # Reschedule or deactivate
        if task.max_runs > 0 and task.runs_completed >= task.max_runs:
            task.active = False
        else:
            task.next_run_at = task.last_run_at + timedelta(seconds=task.interval_seconds)
        self._save_tasks()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load_tasks(self) -> None:
        """Load tasks from disk on startup."""
        if not self._tasks_file.exists():
            return
        try:
            with self._tasks_file.open("r", encoding="utf-8") as f:
                data = json.load(f)
            now = datetime.now(UTC)
            for item in data:
                # Convert ISO strings back to datetime
                for key in ("created_at", "last_run_at", "next_run_at"):
                    val = item.get(key)
                    if val is not None:
                        item[key] = datetime.fromisoformat(val)
                task = ScheduledTask.model_validate(item)
                # If next_run_at is in the past, fast-forward to the next slot
                if task.next_run_at < now:
                    if task.last_run_at:
                        task.next_run_at = task.last_run_at + timedelta(
                            seconds=task.interval_seconds
                        )
                        while task.next_run_at < now:
                            task.next_run_at += timedelta(seconds=task.interval_seconds)
                    else:
                        task.next_run_at = now + timedelta(seconds=task.interval_seconds)
                self._tasks[task.id] = task
            _logger.info("Loaded %d tasks from %s", len(self._tasks), self._tasks_file)
        except Exception:
            _logger.exception("Failed to load tasks")

    def _save_tasks(self) -> None:
        """Save tasks to disk."""
        try:
            self._tasks_file.parent.mkdir(parents=True, exist_ok=True)
            with self._tasks_file.open("w", encoding="utf-8") as f:
                json.dump(
                    [t.to_dict() for t in self._tasks.values()], f, ensure_ascii=False, indent=2
                )
        except Exception:
            _logger.exception("Failed to save tasks")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add_task(
        self,
        skill_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        interval_seconds: int,
        max_runs: int = 0,
        name: str = "",
    ) -> str:
        task = ScheduledTask(
            skill_id=skill_id,
            tool_name=tool_name,
            arguments=arguments,
            interval_seconds=max(1, interval_seconds),
            max_runs=max(0, max_runs),
            name=name,
            next_run_at=datetime.now(UTC) + timedelta(seconds=max(1, interval_seconds)),
        )
        self._tasks[task.id] = task
        self._save_tasks()
        return task.id

    def cancel_task(self, task_id: str) -> bool:
        task = self._tasks.get(task_id)
        if task is None:
            return False
        task.active = False
        self._save_tasks()
        return True

    def delete_task(self, task_id: str = "", name: str = "") -> bool:
        if task_id and task_id in self._tasks:
            del self._tasks[task_id]
            self._save_tasks()
            return True
        if name:
            for tid, task in list(self._tasks.items()):
                if task.name == name:
                    del self._tasks[tid]
                    self._save_tasks()
                    return True
        return False

    def get_task(self, task_id: str) -> ScheduledTask | None:
        return self._tasks.get(task_id)

    def list_tasks(self) -> list[dict[str, Any]]:
        return [t.to_dict() for t in self._tasks.values()]
