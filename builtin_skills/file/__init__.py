"""File skill: read, write, and edit files."""

from __future__ import annotations

from pathlib import Path

from aipet.utils.paths import get_project_root


def _resolve_path(path: str) -> Path:
    """Resolve a path relative to the project root or as absolute."""
    p = Path(path)
    if p.is_absolute():
        return p
    # Relative paths are resolved from the project root
    return get_project_root() / p


def read_file(path: str, offset: int = 0, limit: int = 200) -> str:
    """Read a text file. offset and limit are line numbers (0-based)."""
    target = _resolve_path(path)
    if not target.exists():
        return f"Error: File not found: {path}"
    if not target.is_file():
        return f"Error: Not a file: {path}"

    try:
        text = target.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return f"Error reading file: {exc}"

    lines = text.splitlines()
    if offset > 0:
        lines = lines[offset:]
    if limit > 0:
        lines = lines[:limit]

    header = f"--- {path} (lines {offset}-{offset + len(lines)}) ---\n"
    return header + "\n".join(lines)


def write_file(path: str, content: str) -> str:
    """Write content to a file. Creates parent directories if needed."""
    target = _resolve_path(path)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return f"File written: {path} ({len(content)} chars)"
    except Exception as exc:
        return f"Error writing file: {exc}"


def edit_file(path: str, old_string: str, new_string: str) -> str:
    """Replace old_string with new_string in a file."""
    target = _resolve_path(path)
    if not target.exists():
        return f"Error: File not found: {path}"

    try:
        text = target.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return f"Error reading file: {exc}"

    if old_string not in text:
        return f"Error: old_string not found in {path}"

    new_text = text.replace(old_string, new_string, 1)
    try:
        target.write_text(new_text, encoding="utf-8")
    except Exception as exc:
        return f"Error writing file: {exc}"

    return f"File edited: {path}"


def list_dir(path: str = ".") -> str:
    """List files and directories."""
    target = _resolve_path(path)
    if not target.exists():
        return f"Error: Path not found: {path}"
    if not target.is_dir():
        return f"Error: Not a directory: {path}"

    lines = [f"Directory: {path}"]
    try:
        for item in sorted(target.iterdir()):
            prefix = "[D]" if item.is_dir() else "[F]"
            size = ""
            if item.is_file():
                size = f"  ({item.stat().st_size} bytes)"
            lines.append(f"  {prefix} {item.name}{size}")
    except Exception as exc:
        return f"Error listing directory: {exc}"

    return "\n".join(lines)


tools = {
    "read_file": read_file,
    "write_file": write_file,
    "edit_file": edit_file,
    "list_dir": list_dir,
}
