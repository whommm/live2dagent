"""Skill Registry: scan, load, and expose skills."""

from __future__ import annotations

import importlib.util
import inspect
import typing
from collections.abc import Callable
from pathlib import Path
from typing import Any

from aipet.gateway.providers.ai import Tool
from aipet.utils.paths import get_user_data_dir


class SkillInfo:
    """Metadata for a loaded skill."""

    def __init__(
        self,
        skill_id: str,
        description: str,
        brief: str,
        permissions: list[str],
        tools: dict[str, Callable[..., Any]],
    ) -> None:
        self.skill_id = skill_id
        self.description = description
        self.brief = brief
        self.permissions = permissions
        self.tools = tools


class SkillRegistry:
    """Scan and manage skills from disk."""

    SKILLS_DIR_NAME = "skills"
    BUILTINS_DIR = Path(__file__).parent / "builtins"

    def __init__(self) -> None:
        self._skills: dict[str, SkillInfo] = {}

    def reload(self) -> None:
        """Rescan the skills directory and load all skills."""
        self._skills.clear()
        skills_dir = get_user_data_dir() / self.SKILLS_DIR_NAME
        if not skills_dir.exists():
            skills_dir.mkdir(parents=True, exist_ok=True)

        self._ensure_builtin_skills(skills_dir)

        for subdir in skills_dir.iterdir():
            if subdir.is_dir() and not subdir.name.startswith(".") and (subdir / "__init__.py").exists():
                self._load_skill(subdir)

    def _ensure_builtin_skills(self, skills_dir: Path) -> None:
        """Copy built-in skills to user data directory if missing."""
        if not self.BUILTINS_DIR.exists():
            return
        for builtin in self.BUILTINS_DIR.iterdir():
            if builtin.is_dir():
                target = skills_dir / builtin.name
                if not target.exists():
                    target.mkdir(parents=True, exist_ok=True)
                    for src_file in builtin.iterdir():
                        if src_file.is_file():
                            (target / src_file.name).write_text(
                                src_file.read_text(encoding="utf-8"), encoding="utf-8"
                            )

    def _load_skill(self, path: Path) -> None:
        skill_id = path.name
        skill_md = path / "SKILL.md"
        description = ""
        brief = ""
        permissions: list[str] = []
        if skill_md.exists():
            description, brief, permissions = self._parse_skill_md(
                skill_md.read_text(encoding="utf-8")
            )

        init_file = path / "__init__.py"
        spec = importlib.util.spec_from_file_location(
            f"aipet.gateway.skills.user.{skill_id}", init_file
        )
        if spec is None or spec.loader is None:
            return
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        tools = getattr(module, "tools", {})
        self._skills[skill_id] = SkillInfo(skill_id, description, brief, permissions, tools)

    def _parse_skill_md(self, content: str) -> tuple[str, str, list[str]]:
        description = ""
        brief = ""
        permissions: list[str] = []
        in_description = False
        in_brief = False
        in_permissions = False
        for line in content.splitlines():
            stripped = line.strip()
            lower = stripped.lower()
            if lower == "## description":
                in_description = True
                in_brief = False
                in_permissions = False
                continue
            elif lower == "## brief":
                in_brief = True
                in_description = False
                in_permissions = False
                continue
            elif lower == "## permissions":
                in_permissions = True
                in_description = False
                in_brief = False
                continue
            elif stripped.startswith("## "):
                in_description = False
                in_brief = False
                in_permissions = False
                continue

            if in_description:
                description += line + "\n"
            elif in_brief:
                brief += line + "\n"
            elif in_permissions and stripped.startswith("-"):
                permissions.append(stripped.lstrip("-").strip())

        return description.strip(), brief.strip(), permissions

    def list_tools(self) -> list[Tool]:
        """Generate OpenAI-style tool definitions."""
        tools: list[Tool] = []
        for skill in self._skills.values():
            for tool_name, func in skill.tools.items():
                schema = _build_function_schema(
                    skill.skill_id, tool_name, func, skill.description
                )
                tools.append(Tool(function=schema))
        return tools

    def list_tools_brief(self) -> list[dict[str, str]]:
        """Return brief one-line descriptions for each tool."""
        briefs: list[dict[str, str]] = []
        for skill in self._skills.values():
            for tool_name, func in skill.tools.items():
                full_name = f"{skill.skill_id}:{tool_name}"
                # Use the function's docstring first line as the tool brief
                doc = func.__doc__ or ""
                first_line = doc.strip().split("\n")[0].strip()
                briefs.append({
                    "name": full_name,
                    "brief": first_line or skill.brief or f"Tool {tool_name}",
                })
        return briefs

    def get_tool(self, full_name: str) -> tuple[Callable[..., Any], SkillInfo] | None:
        """Lookup a tool by 'skill_id:tool_name'."""
        if ":" not in full_name:
            return None
        skill_id, tool_name = full_name.split(":", 1)
        skill = self._skills.get(skill_id)
        if skill is None or tool_name not in skill.tools:
            return None
        return skill.tools[tool_name], skill


def _build_function_schema(
    skill_id: str, tool_name: str, func: Callable[..., Any], skill_desc: str
) -> dict[str, Any]:
    sig = inspect.signature(func)
    hints = typing.get_type_hints(func)
    properties: dict[str, typing.Any] = {}
    required: list[str] = []
    for param_name, param in sig.parameters.items():
        if param_name == "self":
            continue
        param_type = hints.get(param_name, str)
        json_type = "string"
        if param_type in (int, float):
            json_type = "number"
        elif param_type is bool:
            json_type = "boolean"
        properties[param_name] = {"type": json_type}
        if param.default is inspect.Parameter.empty:
            required.append(param_name)

    doc = inspect.getdoc(func) or ""
    description = f"{skill_desc}\n\nTool `{tool_name}`: {doc}".strip()

    return {
        "name": f"{skill_id}:{tool_name}",
        "description": description,
        "parameters": {
            "type": "object",
            "properties": properties,
            "required": required,
        },
    }
