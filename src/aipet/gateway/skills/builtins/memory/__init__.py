"""Memory skill: read and update long-term memory."""

from __future__ import annotations

import re
from pathlib import Path

from aipet.utils.paths import get_config_dir


def _memory_path() -> Path:
    return get_config_dir() / "memory.md"


def read_memory() -> str:
    """Read the full memory.md content."""
    path = _memory_path()
    if not path.exists():
        return "(memory.md does not exist yet)"
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return f"Error reading memory: {exc}"


def update_memory(section: str, content: str) -> str:
    """Add or update a section in memory.md.

    If the section exists, its content is replaced.
    If it does not exist, a new section is appended.
    """
    path = _memory_path()

    if not path.exists():
        default = (
            "# Memory — 紫羽的长期记忆\n\n"
            "> 这份文件由紫羽自己维护。每当对话中出现值得记住的信息，\n"
            "> 紫羽会主动更新此文件，确保下次聊天时还记得主人的喜好和故事。\n\n"
        )
        path.write_text(default, encoding="utf-8")

    text = path.read_text(encoding="utf-8", errors="replace")

    # Pattern: match "## SectionName\n" and capture everything until next "## " or EOF
    pattern = rf"(## {re.escape(section)}\n)(.*?)(?=\n## |\Z)"
    match = re.search(pattern, text, re.DOTALL)

    if match:
        # Replace existing section content
        before = text[: match.start(2)]
        after = text[match.end(2) :]
        new_text = before + content.rstrip() + "\n" + after
    else:
        # Append new section
        new_text = text.rstrip() + f"\n\n## {section}\n{content.rstrip()}\n"

    try:
        path.write_text(new_text, encoding="utf-8")
    except Exception as exc:
        return f"Error writing memory: {exc}"

    return f"Memory updated: section '{section}'"


tools = {
    "read_memory": read_memory,
    "update_memory": update_memory,
}
