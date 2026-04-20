"""Skill authoring engine: safety linting, mock testing, and draft management."""

from __future__ import annotations

import ast
import asyncio
import contextlib
import importlib.util
import inspect
import json
import shutil
import tempfile
from pathlib import Path
from typing import Any

from aipet.utils.paths import get_user_data_dir


# ---------------------------------------------------------------------------
# Security policy
# ---------------------------------------------------------------------------

FORBIDDEN_IMPORTS: set[str] = {
    "os",
    "subprocess",
    "sys",
    "socket",
    "ctypes",
    "threading",
    "multiprocessing",
    "pickle",
    "marshal",
}

FORBIDDEN_NAMES: set[str] = {
    "eval",
    "exec",
    "compile",
    "__import__",
    "breakpoint",
    "exit",
    "quit",
}

FORBIDDEN_ATTRS: set[str] = {
    "system",
    "popen",
    "spawn",
    "kill",
    "remove",
    "rmdir",
    "unlink",
    "chmod",
    "chown",
}


class SkillWriterError(Exception):
    """Raised when skill authoring validation fails."""


# ---------------------------------------------------------------------------
# Linting
# ---------------------------------------------------------------------------


def lint_code(code: str) -> tuple[bool, list[str]]:
    """Run AST static analysis for syntax and security.

    Returns (passed, list_of_errors).
    """
    errors: list[str] = []
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return False, [f"SyntaxError: {exc}"]

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root in FORBIDDEN_IMPORTS:
                    errors.append(f"Forbidden import: {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.module.split(".")[0] in FORBIDDEN_IMPORTS:
                errors.append(f"Forbidden import from: {node.module}")
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in FORBIDDEN_NAMES:
                errors.append(f"Forbidden call: {node.func.id}")
        elif isinstance(node, ast.Attribute):
            if node.attr in FORBIDDEN_ATTRS:
                errors.append(f"Forbidden attribute access: {node.attr}")

    return len(errors) == 0, errors


# ---------------------------------------------------------------------------
# Mock argument generation
# ---------------------------------------------------------------------------


def _mock_value_for_type(param_type: Any) -> Any:
    """Return a sensible mock value for a given Python type hint."""
    import typing

    origin = getattr(param_type, "__origin__", None)

    # Optional[T] / Union[T, None]
    if origin is typing.Union:
        args = getattr(param_type, "__args__", ())
        for arg in args:
            if arg is not type(None):
                return _mock_value_for_type(arg)
        return None

    # list[T]
    if origin is list or origin is typing.List:
        args = getattr(param_type, "__args__", (str,))
        inner = args[0] if args else str
        return [_mock_value_for_type(inner)]

    # dict[K, V]
    if origin is dict or origin is typing.Dict:
        args = getattr(param_type, "__args__", (str, str))
        k = args[0] if len(args) > 0 else str
        v = args[1] if len(args) > 1 else str
        return {_mock_value_for_type(k): _mock_value_for_type(v)}

    # Basic types
    if param_type is str:
        return "test"
    if param_type is int:
        return 42
    if param_type is float:
        return 3.14
    if param_type is bool:
        return True

    return None


def build_mock_args(func: Any) -> tuple[dict[str, Any], list[str]]:
    """Generate mock arguments for a callable based on its signature.

    Returns (args_dict, error_messages).
    """
    import typing

    sig = inspect.signature(func)
    hints = typing.get_type_hints(func)
    args: dict[str, Any] = {}
    errors: list[str] = []

    for name, param in sig.parameters.items():
        if param.default is not inspect.Parameter.empty:
            continue  # let the function use its default
        param_type = hints.get(name, str)
        mock_val = _mock_value_for_type(param_type)
        if mock_val is None:
            errors.append(f"Cannot mock parameter '{name}' of type {param_type}")
        args[name] = mock_val

    return args, errors


# ---------------------------------------------------------------------------
# Full test suite
# ---------------------------------------------------------------------------


async def run_skill_tests(skill_id: str, code: str, skill_md: str) -> dict[str, Any]:
    """Run full lint + import + smoke test on a skill module.

    Returns a dict with keys:
        passed: bool
        stage: str   (lint | import | structure | smoke | complete)
        errors: list[str]
        tools: list[str] | None
    """
    # Stage 1: lint
    passed, errors = lint_code(code)
    if not passed:
        return {"passed": False, "stage": "lint", "errors": errors, "tools": None}

    # Stage 2: import in isolated temp directory
    with tempfile.TemporaryDirectory() as tmpdir:
        skill_dir = Path(tmpdir) / skill_id
        skill_dir.mkdir()
        (skill_dir / "__init__.py").write_text(code, encoding="utf-8")
        (skill_dir / "SKILL.md").write_text(skill_md, encoding="utf-8")

        spec = importlib.util.spec_from_file_location(
            f"aipet.skills.test.{skill_id}", skill_dir / "__init__.py"
        )
        if spec is None or spec.loader is None:
            return {
                "passed": False,
                "stage": "import",
                "errors": ["Failed to create module spec"],
                "tools": None,
            }

        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
        except Exception as exc:
            return {
                "passed": False,
                "stage": "import",
                "errors": [f"Import failed: {exc}"],
                "tools": None,
            }

        # Stage 3: structure check
        tools = getattr(module, "tools", None)
        if not isinstance(tools, dict):
            return {
                "passed": False,
                "stage": "structure",
                "errors": ["Missing or invalid 'tools' dict"],
                "tools": None,
            }
        if not tools:
            return {
                "passed": False,
                "stage": "structure",
                "errors": ["tools dict is empty"],
                "tools": None,
            }

        # Stage 4: smoke test each tool
        for name, func in tools.items():
            if not callable(func):
                return {
                    "passed": False,
                    "stage": "smoke",
                    "errors": [f"Tool '{name}' is not callable"],
                    "tools": list(tools.keys()),
                }

            mock_args, mock_errors = build_mock_args(func)
            if mock_errors:
                return {
                    "passed": False,
                    "stage": "smoke",
                    "errors": mock_errors,
                    "tools": list(tools.keys()),
                }

            try:
                if asyncio.iscoroutinefunction(func):
                    await func(**mock_args)
                else:
                    func(**mock_args)
            except TypeError as exc:
                return {
                    "passed": False,
                    "stage": "smoke",
                    "errors": [f"Tool '{name}' TypeError: {exc}"],
                    "tools": list(tools.keys()),
                }
            except Exception:
                # Runtime / API errors are acceptable for a smoke test.
                pass

    return {
        "passed": True,
        "stage": "complete",
        "errors": [],
        "tools": list(tools.keys()),
    }


# ---------------------------------------------------------------------------
# Draft / install management
# ---------------------------------------------------------------------------


def _draft_dir(skill_id: str) -> Path:
    return get_user_data_dir() / "skills" / ".draft" / skill_id


def _production_dir(skill_id: str) -> Path:
    return get_user_data_dir() / "skills" / skill_id


def save_draft_skill(skill_id: str, skill_md: str, tools_code: str) -> str:
    """Write or overwrite a skill in the draft directory."""
    draft = _draft_dir(skill_id)
    draft.mkdir(parents=True, exist_ok=True)
    (draft / "SKILL.md").write_text(skill_md, encoding="utf-8")
    (draft / "__init__.py").write_text(tools_code, encoding="utf-8")
    return f"Draft skill '{skill_id}' saved. Use skill_tester:test_skill to validate."


def install_draft_skill(skill_id: str) -> str:
    """Move a validated draft skill to production and clean up draft."""
    draft = _draft_dir(skill_id)
    prod = _production_dir(skill_id)
    if not draft.exists():
        raise SkillWriterError(f"Draft skill '{skill_id}' not found.")

    if prod.exists():
        shutil.rmtree(prod)
    shutil.copytree(draft, prod)
    shutil.rmtree(draft)
    return f"Skill '{skill_id}' installed successfully."


def delete_skill_dir(skill_id: str) -> str:
    """Delete a production skill directory."""
    prod = _production_dir(skill_id)
    if not prod.exists():
        return f"Skill '{skill_id}' does not exist."
    shutil.rmtree(prod)
    return f"Skill '{skill_id}' deleted."
