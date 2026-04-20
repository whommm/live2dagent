"""Tests for the skill authoring engine (writer + tester)."""

from __future__ import annotations

import pytest

from typing import List, Optional

from aipet.gateway.skills.writer import (
    SkillWriterError,
    build_mock_args,
    delete_skill_dir,
    install_draft_skill,
    lint_code,
    run_skill_tests,
    save_draft_skill,
)


# ---------------------------------------------------------------------------
# Lint
# ---------------------------------------------------------------------------


def test_lint_code_passes_safe_code() -> None:
    code = '''
import httpx

async def get_weather(city: str) -> str:
    """Get weather."""
    return "sunny"

tools = {"get_weather": get_weather}
'''
    passed, errors = lint_code(code)
    assert passed is True
    assert not errors


def test_lint_code_fails_forbidden_import() -> None:
    code = "import os\n"
    passed, errors = lint_code(code)
    assert passed is False
    assert any("os" in e for e in errors)


def test_lint_code_fails_forbidden_from_import() -> None:
    code = "from subprocess import run\n"
    passed, errors = lint_code(code)
    assert passed is False
    assert any("subprocess" in e for e in errors)


def test_lint_code_fails_eval() -> None:
    code = "eval('1+1')\n"
    passed, errors = lint_code(code)
    assert passed is False
    assert any("eval" in e for e in errors)


def test_lint_code_fails_exec() -> None:
    code = "exec('print(1)')\n"
    passed, errors = lint_code(code)
    assert passed is False
    assert any("exec" in e for e in errors)


def test_lint_code_fails_syntax_error() -> None:
    code = "def foo(\n"
    passed, errors = lint_code(code)
    assert passed is False
    assert any("SyntaxError" in e for e in errors)


def test_lint_code_fails_os_system_attr() -> None:
    code = "os.system('ls')\n"
    passed, errors = lint_code(code)
    assert passed is False
    assert any("system" in e for e in errors)


# ---------------------------------------------------------------------------
# Mock args
# ---------------------------------------------------------------------------


def test_build_mock_args_basic_types() -> None:
    def demo(a: str, b: int, c: float, d: bool) -> str:
        return ""

    args, errors = build_mock_args(demo)
    assert not errors
    assert args == {"a": "test", "b": 42, "c": 3.14, "d": True}


def test_build_mock_args_skips_defaults() -> None:
    def demo(a: str, b: int = 10) -> str:
        return ""

    args, errors = build_mock_args(demo)
    assert not errors
    assert "a" in args
    assert "b" not in args  # has default, skipped


def test_build_mock_args_optional() -> None:
    def demo(a: Optional[str]) -> str:
        return ""

    args, errors = build_mock_args(demo)
    assert not errors
    assert args == {"a": "test"}


def test_build_mock_args_list() -> None:
    def demo(items: List[str]) -> str:
        return ""

    args, errors = build_mock_args(demo)
    assert not errors
    assert args == {"items": ["test"]}


# ---------------------------------------------------------------------------
# Full test suite
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_test_skill_module_passes_valid_skill() -> None:
    code = '''
async def get_weather(city: str) -> str:
    """Get weather."""
    return "sunny"

tools = {"get_weather": get_weather}
'''
    result = await run_skill_tests("test_weather", code, "# Test")
    assert result["passed"] is True
    assert result["stage"] == "complete"
    assert "get_weather" in (result.get("tools") or [])


@pytest.mark.asyncio
async def test_test_skill_module_fails_syntax_error() -> None:
    code = "def foo(\n"
    result = await run_skill_tests("test_bad", code, "# Bad")
    assert result["passed"] is False
    assert result["stage"] == "lint"


@pytest.mark.asyncio
async def test_test_skill_module_fails_missing_tools() -> None:
    code = "x = 1\n"
    result = await run_skill_tests("test_no_tools", code, "# No tools")
    assert result["passed"] is False
    assert result["stage"] == "structure"


@pytest.mark.asyncio
async def test_test_skill_module_fails_empty_tools() -> None:
    code = "tools = {}\n"
    result = await run_skill_tests("test_empty", code, "# Empty")
    assert result["passed"] is False
    assert result["stage"] == "structure"


@pytest.mark.asyncio
async def test_test_skill_module_fails_forbidden_import() -> None:
    code = "import os\ntools = {'a': lambda: 1}\n"
    result = await run_skill_tests("test_forbidden", code, "# Forbidden")
    assert result["passed"] is False
    assert result["stage"] == "lint"


@pytest.mark.asyncio
async def test_test_skill_module_smoke_test_type_error() -> None:
    code = '''
def broken(a: str, b: int) -> str:
    return a + b  # "test" + 42 raises TypeError

tools = {"broken": broken}
'''
    result = await run_skill_tests("test_broken", code, "# Broken")
    # mock args pass str and int, so a+b raises TypeError at runtime.
    assert result["passed"] is False
    assert result["stage"] == "smoke"
    assert any("TypeError" in e for e in result.get("errors", []))


# ---------------------------------------------------------------------------
# Draft lifecycle
# ---------------------------------------------------------------------------


def test_save_install_delete_draft_skill(tmp_path: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("aipet.gateway.skills.writer.get_user_data_dir", lambda: tmp_path)

    # Create draft
    msg = save_draft_skill("demo", "# Demo", "tools = {'a': lambda: 1}\n")
    assert "saved" in msg
    draft = tmp_path / "skills" / ".draft" / "demo"
    assert draft.exists()
    assert (draft / "SKILL.md").read_text() == "# Demo"

    # Update draft
    msg = save_draft_skill("demo", "# Demo v2", "tools = {'b': lambda: 2}\n")
    assert (draft / "SKILL.md").read_text() == "# Demo v2"

    # Install
    msg = install_draft_skill("demo")
    assert "installed" in msg
    prod = tmp_path / "skills" / "demo"
    assert prod.exists()
    assert not draft.exists()  # draft cleaned up

    # Delete
    msg = delete_skill_dir("demo")
    assert "deleted" in msg
    assert not prod.exists()


def test_install_missing_draft_raises() -> None:
    with pytest.raises(SkillWriterError):
        install_draft_skill("nonexistent_skill_12345")
