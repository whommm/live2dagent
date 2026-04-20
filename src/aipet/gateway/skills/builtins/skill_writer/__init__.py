"""Skill writer tools: create, update, install, and delete skills."""

from __future__ import annotations

from aipet.gateway.skills.writer import (
    SkillWriterError,
    delete_skill_dir,
    install_draft_skill,
    save_draft_skill,
)


async def create_skill(skill_id: str, description: str, skill_md: str, tools_code: str) -> str:
    """Create a new skill draft.

    Parameters
    ----------
    skill_id:
        Unique identifier for the skill (lowercase, alphanumeric + underscores).
    description:
        One-line description of what the skill does.
    skill_md:
        Full content of the SKILL.md file.
    tools_code:
        Full content of the __init__.py file. Must expose a `tools` dict.
    """
    return save_draft_skill(skill_id, skill_md, tools_code)


async def update_skill(skill_id: str, skill_md: str, tools_code: str) -> str:
    """Update an existing skill draft. Use this to fix errors found by the tester."""
    return save_draft_skill(skill_id, skill_md, tools_code)


async def install_skill(skill_id: str) -> str:
    """Move a validated draft skill to production and activate it.

    Only call this after skill_tester:test_skill has returned passed=true.
    """
    try:
        result = install_draft_skill(skill_id)
        # Notify running Gateway to reload skills so the new skill is immediately available
        try:
            from aipet.gateway.scheduler import get_gateway
            gateway = get_gateway()
            if gateway is not None:
                gateway.skills.reload()
                await gateway.store.patch(active_skills=list(gateway.skills._skills.keys()))
        except Exception:
            pass  # Gateway may not be running
        return result
    except SkillWriterError as exc:
        return f"Error: {exc}"


async def delete_skill(skill_id: str) -> str:
    """Delete a production skill permanently."""
    result = delete_skill_dir(skill_id)
    # Notify running Gateway to reload skills
    try:
        from aipet.gateway.scheduler import get_gateway
        gateway = get_gateway()
        if gateway is not None:
            gateway.skills.reload()
            await gateway.store.patch(active_skills=list(gateway.skills._skills.keys()))
    except Exception:
        pass
    return result


tools = {
    "create_skill": create_skill,
    "update_skill": update_skill,
    "install_skill": install_skill,
    "delete_skill": delete_skill,
}
