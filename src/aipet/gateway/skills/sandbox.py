"""Lightweight application-level sandbox for skills."""

from __future__ import annotations

import builtins
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

from aipet.utils.paths import get_user_data_dir


class SandboxViolationError(Exception):
    """Raised when a skill attempts an unauthorized action."""


# Store original functions for restoration
_original_urlopen = urllib.request.urlopen
_original_open = builtins.open


def require_permission(permission: str) -> Callable[..., Any]:
    """Decorator to mark a tool function as requiring a specific permission."""

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        func._required_permission = permission  # type: ignore[attr-defined]
        return func

    return decorator


def check_permissions(func: Callable[..., Any], skill_permissions: list[str]) -> None:
    """Verify that a function's required permissions are granted."""
    required = getattr(func, "_required_permission", None)
    if required and required not in skill_permissions:
        raise SandboxViolationError(f"Missing permission '{required}' for this tool.")


class _SandboxGuard:
    """Runtime monkey-patch guard for a single tool execution."""

    def __init__(self, permissions: list[str], skill_id: str) -> None:
        self.permissions = permissions
        self.skill_id = skill_id
        self.workspace = get_user_data_dir() / "skills" / skill_id / "workspace"
        self.workspace.mkdir(parents=True, exist_ok=True)

    def install(self) -> None:
        if "network" not in self.permissions:
            urllib.request.urlopen = self._blocked_urlopen  # type: ignore[assignment]
        if "filesystem" not in self.permissions and "filesystem:write" not in self.permissions:
            builtins.open = self._sandboxed_open  # type: ignore[assignment]

    def uninstall(self) -> None:
        urllib.request.urlopen = _original_urlopen  # type: ignore[assignment]
        builtins.open = _original_open  # type: ignore[assignment]

    def _blocked_urlopen(self, *args: Any, **kwargs: Any) -> Any:
        raise SandboxViolationError(
            "Network access denied for this skill. Declare 'network' permission in SKILL.md."
        )

    def _sandboxed_open(self, *args: Any, **kwargs: Any) -> Any:
        # Allow read-only access to workspace only
        if args:
            path = Path(args[0]).resolve()
            try:
                path.relative_to(self.workspace.resolve())
            except ValueError:
                raise SandboxViolationError(
                    f"Filesystem access denied. Skill '{self.skill_id}' can only access "
                    f"its workspace: {self.workspace}"
                ) from None
        return _original_open(*args, **kwargs)


def install_sandbox(permissions: list[str], skill_id: str) -> _SandboxGuard:
    """Install sandbox patches and return a guard that must be uninstalled."""
    guard = _SandboxGuard(permissions, skill_id)
    guard.install()
    return guard


def uninstall_sandbox(guard: _SandboxGuard) -> None:
    """Restore original functions."""
    guard.uninstall()
