"""Scheduler skill — manage recurring task execution."""

from __future__ import annotations

import json
from typing import Any

from aipet.gateway.scheduler import TaskScheduler, get_gateway

_scheduler = TaskScheduler()


async def schedule_task(
    skill_id: str,
    tool_name: str,
    arguments: dict[str, Any],
    interval_seconds: int,
    max_runs: int = 0,
    name: str = "",
) -> str:
    """Create a recurring scheduled task.

    Args:
        skill_id: Skill that owns the tool to invoke.
        tool_name: Name of the tool function.
        arguments: Dict of arguments passed to the tool.
        interval_seconds: Seconds between executions (min 1).
        max_runs: Max number of runs (0 = infinite).
        name: Human-readable task name.
    """
    gateway = get_gateway()
    if gateway is None:
        return "Error: Gateway not available."
    # Handle AI occasionally sending arguments as a JSON string
    if isinstance(arguments, str):
        import json
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError:
            return "Error: arguments must be a valid JSON object or dict."
    task_id = gateway.scheduler.add_task(
        skill_id=skill_id,
        tool_name=tool_name,
        arguments=arguments,
        interval_seconds=interval_seconds,
        max_runs=max_runs,
        name=name,
    )
    return f"Task scheduled: {task_id}"


async def cancel_task(task_id: str) -> str:
    """Stop a scheduled task."""
    gateway = get_gateway()
    if gateway is None:
        return "Error: Gateway not available."
    ok = gateway.scheduler.cancel_task(task_id)
    return "Cancelled." if ok else f"Task {task_id} not found."


async def delete_task(task_id: str = "", name: str = "") -> str:
    """Remove a task entirely by ID or name."""
    gateway = get_gateway()
    if gateway is None:
        return "Error: Gateway not available."
    if task_id:
        ok = gateway.scheduler.delete_task(task_id)
        return "Deleted." if ok else f"Task {task_id} not found."
    if name:
        for tid, task in list(gateway.scheduler._tasks.items()):
            if task.name == name:
                gateway.scheduler.delete_task(tid)
                return f"Deleted task '{name}' ({tid})."
        return f"No task named '{name}' found."
    return "Error: Please provide task_id or name."


async def list_tasks() -> str:
    """Return all tasks as JSON."""
    gateway = get_gateway()
    if gateway is None:
        return "Error: Gateway not available."
    return json.dumps(gateway.scheduler.list_tasks(), ensure_ascii=False, indent=2)


tools = {
    "schedule_task": schedule_task,
    "cancel_task": cancel_task,
    "delete_task": delete_task,
    "list_tasks": list_tasks,
}
