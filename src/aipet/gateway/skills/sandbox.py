"""Lightweight application-level sandbox for skills."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


class SandboxViolationError(Exception):
    """Raised when a skill attempts an unauthorized action."""


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
