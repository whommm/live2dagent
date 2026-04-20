# Skill: Scheduler

Provides recurring/scheduled execution of any installed skill's tool.

## Tools

### schedule_task

Create a new recurring task.

- **skill_id** *(str, required)*: The skill that owns the tool to run.
- **tool_name** *(str, required)*: The specific tool function name.
- **arguments** *(dict, required)*: JSON object of arguments to pass to the tool.
- **interval_seconds** *(int, required)*: Seconds between executions (minimum 1).
- **max_runs** *(int, optional)*: Maximum number of executions (0 = infinite). Default 0.
- **name** *(str, optional)*: Human-readable name for the task.

Returns the task UUID.

### cancel_task

Stop a running scheduled task.

- **task_id** *(str, required)*

Returns success/failure.

### delete_task

Remove a task entirely.

- **task_id** *(str, required)*

### list_tasks

Return all scheduled tasks as a JSON array.

## Usage Example

```
schedule_task(
    skill_id="weather",
    tool_name="get_weather",
    arguments={"city": "Shanghai"},
    interval_seconds=3600,
    name="Hourly weather check"
)
```

## Notes

- Tasks are **in-memory only**; they do not survive a Gateway restart.
- Tasks execute in the Gateway's async event loop.
- Results are broadcast to all connected frontends via `scheduler.task.completed` event.
