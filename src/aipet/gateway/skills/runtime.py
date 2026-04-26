"""Shared runtime helpers for skill authors.

These helpers give user-facing skills a consistent way to surface readable
errors instead of leaking low-level tracebacks or vague failure strings.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class SkillUserError(Exception):
    """An expected, user-facing skill error.

    ``message`` should be short and actionable because it may be shown to the
    model and then relayed to the user.
    """

    code: str
    message: str

    def __str__(self) -> str:
        return self.message


def read_json_file(path: Path, label: str = "JSON file") -> Any:
    """Read and parse a JSON file with a consistent friendly error."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SkillUserError(
            code="FILE_NOT_FOUND",
            message=f"{label} not found: {path}",
        ) from exc
    except json.JSONDecodeError as exc:
        raise SkillUserError(
            code="INVALID_JSON",
            message=(
                f"{label} is invalid JSON at line {exc.lineno}, column {exc.colno}: {path}"
            ),
        ) from exc
    except Exception as exc:
        raise SkillUserError(
            code="FILE_READ_ERROR",
            message=f"Failed to read {label}: {path} ({exc})",
        ) from exc


def read_toml_file(path: Path, label: str = "TOML file") -> dict[str, Any]:
    """Read and parse a TOML file with a consistent friendly error."""
    try:
        import toml

        data = toml.load(path)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError as exc:
        raise SkillUserError(
            code="FILE_NOT_FOUND",
            message=f"{label} not found: {path}",
        ) from exc
    except Exception as exc:
        raise SkillUserError(
            code="INVALID_TOML",
            message=f"{label} is invalid or unreadable: {path} ({exc})",
        ) from exc
