"""Tests for gateway/scheduler.py."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from aipet.gateway.scheduler import ScheduledTask, TaskScheduler

# ---------------------------------------------------------------------------
# ScheduledTask
# ---------------------------------------------------------------------------


def test_scheduled_task_defaults():
    task = ScheduledTask(skill_id="demo", tool_name="ping")
    assert task.id
    assert task.skill_id == "demo"
    assert task.tool_name == "ping"
    assert task.interval_seconds == 60
    assert task.max_runs == 0
    assert task.runs_completed == 0
    assert task.active is True
    assert task.arguments == {}


def test_scheduled_task_to_dict():
    task = ScheduledTask(skill_id="s", tool_name="t", name="n")
    d = task.to_dict()
    assert d["skill_id"] == "s"
    assert d["tool_name"] == "t"
    assert d["name"] == "n"
    assert "created_at" in d
    assert "next_run_at" in d


# ---------------------------------------------------------------------------
# TaskScheduler — basic CRUD
# ---------------------------------------------------------------------------


def test_add_task():
    s = TaskScheduler()
    tid = s.add_task(
        skill_id="weather",
        tool_name="get",
        arguments={"city": "NYC"},
        interval_seconds=300,
        name="w",
    )
    assert tid in s._tasks
    task = s._tasks[tid]
    assert task.skill_id == "weather"
    assert task.interval_seconds == 300


def test_cancel_and_delete_task():
    s = TaskScheduler()
    tid = s.add_task(skill_id="a", tool_name="b", arguments={}, interval_seconds=10)
    assert s.cancel_task(tid) is True
    assert s._tasks[tid].active is False
    assert s.delete_task(tid) is True
    assert tid not in s._tasks


def test_cancel_missing_task():
    s = TaskScheduler()
    assert s.cancel_task("nonexistent") is False
    assert s.delete_task("nonexistent") is False


def test_list_tasks():
    s = TaskScheduler()
    tid = s.add_task(skill_id="a", tool_name="b", arguments={}, interval_seconds=10)
    lst = s.list_tasks()
    assert len(lst) == 1
    assert lst[0]["id"] == tid


def test_get_task():
    s = TaskScheduler()
    tid = s.add_task(skill_id="a", tool_name="b", arguments={}, interval_seconds=10)
    assert s.get_task(tid) is not None
    assert s.get_task("x") is None


# ---------------------------------------------------------------------------
# TaskScheduler — execution loop (async)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_runs_tool_and_broadcasts():
    """A task whose next_run_at is in the past should be executed by the loop."""
    s = TaskScheduler()
    calls: list[tuple] = []

    def cb(task, result):
        calls.append((task.id, result))

    s.set_execute_callback(cb)

    # Mock gateway.tool_router.call by injecting a simple async callable
    executed = []

    async def mock_call(name, args):
        executed.append((name, args))
        return "pong"

    # We can't easily inject gateway here, so test _execute directly with monkeypatch
    from aipet.gateway import scheduler as sched_mod

    old_ref = sched_mod._gateway_ref

    class FakeGateway:
        class FakeRouter:
            @staticmethod
            async def call(name, args):
                return await mock_call(name, args)

        tool_router = FakeRouter()

    sched_mod._gateway_ref = FakeGateway()
    try:
        task = ScheduledTask(
            skill_id="demo",
            tool_name="ping",
            arguments={},
            next_run_at=datetime.now(UTC) - timedelta(seconds=1),
        )
        s._tasks[task.id] = task
        await s._execute(task)
        assert task.runs_completed == 1
        assert task.last_run_at is not None
        assert task.active is True  # infinite runs
        assert calls
        assert calls[0][1] == "pong"
    finally:
        sched_mod._gateway_ref = old_ref


@pytest.mark.asyncio
async def test_execute_max_runs_deactivates():
    s = TaskScheduler()
    from aipet.gateway import scheduler as sched_mod

    old_ref = sched_mod._gateway_ref

    class FakeGateway:
        class FakeRouter:
            @staticmethod
            async def call(name, args):
                return "ok"

        tool_router = FakeRouter()

    sched_mod._gateway_ref = FakeGateway()
    try:
        task = ScheduledTask(
            skill_id="demo",
            tool_name="ping",
            arguments={},
            max_runs=1,
            next_run_at=datetime.now(UTC) - timedelta(seconds=1),
        )
        s._tasks[task.id] = task
        await s._execute(task)
        assert task.runs_completed == 1
        assert task.active is False
    finally:
        sched_mod._gateway_ref = old_ref


@pytest.mark.asyncio
async def test_loop_picks_up_due_task():
    """Start the scheduler, add a due task, and verify it executes quickly."""
    s = TaskScheduler()
    results: list[str] = []

    def cb(task, result):
        results.append(result)
        # Stop after first execution so we don't spin forever
        task.active = False

    s.set_execute_callback(cb)

    from aipet.gateway import scheduler as sched_mod

    old_ref = sched_mod._gateway_ref

    class FakeGateway:
        class FakeRouter:
            @staticmethod
            async def call(name, args):
                return "hello"

        tool_router = FakeRouter()

    sched_mod._gateway_ref = FakeGateway()
    try:
        s.start()
        # Add a task that is already due
        task = ScheduledTask(
            skill_id="demo",
            tool_name="ping",
            arguments={},
            next_run_at=datetime.now(UTC) - timedelta(seconds=1),
        )
        s._tasks[task.id] = task
        # Wait a couple of seconds for the loop to pick it up
        await asyncio.sleep(2)
        assert len(results) >= 1
        assert results[0] == "hello"
    finally:
        sched_mod._gateway_ref = old_ref
        await s.stop()


# ---------------------------------------------------------------------------
# Skill interface
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_skill_schedule_task_no_gateway():
    """When gateway is None, skill tools return an error."""
    from aipet.gateway import scheduler as sched_mod
    from builtin_skills.scheduler import cancel_task, delete_task, list_tasks, schedule_task

    old_ref = sched_mod._gateway_ref
    sched_mod._gateway_ref = None
    try:
        assert "Error" in await schedule_task("s", "t", {}, 10)
        assert "Error" in await list_tasks()
        assert "Error" in await cancel_task("x")
        assert "Error" in await delete_task("x")
    finally:
        sched_mod._gateway_ref = old_ref


@pytest.mark.asyncio
async def test_skill_schedule_task_with_gateway():
    from aipet.gateway import scheduler as sched_mod
    from builtin_skills.scheduler import cancel_task, list_tasks, schedule_task

    old_ref = sched_mod._gateway_ref

    class FakeGateway:
        scheduler = TaskScheduler()

    fake = FakeGateway()
    sched_mod._gateway_ref = fake
    try:
        result = await schedule_task("s", "t", {"a": 1}, 60, name="test")
        assert "Task scheduled:" in result
        tid = result.split(": ")[1]

        lst = await list_tasks()
        assert tid in lst

        cancel = await cancel_task(tid)
        assert "Cancelled" in cancel
    finally:
        sched_mod._gateway_ref = old_ref
