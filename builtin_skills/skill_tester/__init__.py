"""Skill tester tools: lint and test skill drafts before installation."""

from __future__ import annotations

import json

from aipet.gateway.skills.writer import lint_code, run_skill_tests
from aipet.utils.paths import get_user_data_dir


async def test_skill(skill_id: str) -> str:
    """Run the full test suite on a draft skill.

    Returns a JSON string with keys: passed, stage, errors, tools.
    """
    draft_dir = get_user_data_dir() / "skills" / ".draft" / skill_id
    if not draft_dir.exists():
        return json.dumps(
            {"passed": False, "stage": "lint", "errors": [f"Draft skill '{skill_id}' not found."], "tools": None},
            ensure_ascii=False,
        )

    code = (draft_dir / "__init__.py").read_text(encoding="utf-8")
    skill_md = (draft_dir / "SKILL.md").read_text(encoding="utf-8")
    result = await run_skill_tests(skill_id, code, skill_md)
    return json.dumps(result, ensure_ascii=False, indent=2)


async def lint_skill(skill_id: str) -> str:
    """Run static analysis only (syntax + security). Faster than full test."""
    draft_dir = get_user_data_dir() / "skills" / ".draft" / skill_id
    if not draft_dir.exists():
        return json.dumps(
            {"passed": False, "errors": [f"Draft skill '{skill_id}' not found."]},
            ensure_ascii=False,
        )

    code = (draft_dir / "__init__.py").read_text(encoding="utf-8")
    passed, errors = lint_code(code)
    return json.dumps({"passed": passed, "errors": errors}, ensure_ascii=False, indent=2)


tools = {
    "test_skill": test_skill,
    "lint_skill": lint_skill,
}
