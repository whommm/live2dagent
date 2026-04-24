"""Tests for Skill Engine."""

from unittest.mock import patch

import pytest

from aipet.gateway.skills.registry import SkillRegistry
from aipet.gateway.skills.router import ToolRouter
from aipet.gateway.skills.sandbox import (
    SandboxViolationError,
    check_permissions,
    require_permission,
)


@pytest.mark.asyncio
async def test_tool_router_executes_tool() -> None:
    registry = SkillRegistry()
    registry._skills = {}
    router = ToolRouter(registry)

    async def add(a: int, b: int) -> int:
        return a + b

    from aipet.gateway.skills.registry import SkillInfo

    registry._skills["math"] = SkillInfo("math", "Math ops", "Math skill", [], {"add": add})

    result = await router.call("math:add", {"a": 1, "b": 2})
    assert result == "3"


@pytest.mark.asyncio
async def test_tool_router_returns_error_for_missing_tool() -> None:
    registry = SkillRegistry()
    router = ToolRouter(registry)
    result = await router.call("missing:tool", {})
    assert "not found" in result


def test_sandbox_permission_decorator() -> None:
    @require_permission("network")
    def fetch() -> str:
        return "ok"

    check_permissions(fetch, ["network"])
    with pytest.raises(SandboxViolationError):
        check_permissions(fetch, [])


def test_parse_skill_md() -> None:
    registry = SkillRegistry()
    content = """
# Demo Skill

## Description
A demo skill.

## Permissions
- network
- filesystem

## Tools
- `do_something()`
"""
    desc, brief, perms = registry._parse_skill_md(content)
    assert desc == "A demo skill."
    assert perms == ["network", "filesystem"]


def test_parse_skill_md_frontmatter() -> None:
    registry = SkillRegistry()
    content = """---
description: A frontmatter skill.
brief: Frontmatter brief.
permissions:
  - network
  - filesystem
---

## Description
This should be ignored because frontmatter takes precedence.

## Tools
- `do_something()`
"""
    desc, brief, perms = registry._parse_skill_md(content)
    assert desc == "A frontmatter skill."
    assert brief == "Frontmatter brief."
    assert perms == ["network", "filesystem"]


def test_skill_registry_loads_builtins_and_user_skills(
    tmp_path: pytest.TempPathFactory,
) -> None:
    """Built-in skills are loaded directly from builtins/;
    user skills in data/skills/ override them on name collision."""
    registry = SkillRegistry()
    with patch.object(registry, "BUILTINS_DIR", tmp_path / "builtins"):
        # Set up a built-in skill
        (tmp_path / "builtins").mkdir()
        (tmp_path / "builtins" / "weather").mkdir()
        (tmp_path / "builtins" / "weather" / "SKILL.md").write_text("# W", encoding="utf-8")
        (tmp_path / "builtins" / "weather" / "__init__.py").write_text(
            'tools = {"get": lambda: "builtin"}', encoding="utf-8"
        )

        # Set up a user skill that shadows the built-in one
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        (skills_dir / "weather").mkdir()
        (skills_dir / "weather" / "SKILL.md").write_text("# User W", encoding="utf-8")
        (skills_dir / "weather" / "__init__.py").write_text(
            'tools = {"get": lambda: "user"}', encoding="utf-8"
        )

        with patch("aipet.gateway.skills.registry.get_user_data_dir", return_value=tmp_path):
            registry.reload()

        # User skill should override the built-in one
        assert "weather" in registry._skills
        assert registry._skills["weather"].source == "user"
        tool_fn, _ = registry.get_tool("weather:get")
        assert tool_fn() == "user"

        # Built-in-only skill should still be present
        (tmp_path / "builtins" / "calc").mkdir()
        (tmp_path / "builtins" / "calc" / "__init__.py").write_text(
            'tools = {"add": lambda a,b: a+b}', encoding="utf-8"
        )
        with patch("aipet.gateway.skills.registry.get_user_data_dir", return_value=tmp_path):
            registry.reload()

        assert "calc" in registry._skills
        assert registry._skills["calc"].source == "builtin"
