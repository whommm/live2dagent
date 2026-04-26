"""Execution context helpers for skills invoked through the gateway."""

from __future__ import annotations

import contextvars

_current_skill_session_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "aipet_current_skill_session_id", default=None
)


def set_current_skill_session_id(session_id: str | None) -> contextvars.Token[str | None]:
    return _current_skill_session_id.set(session_id)


def reset_current_skill_session_id(token: contextvars.Token[str | None]) -> None:
    _current_skill_session_id.reset(token)


def get_current_skill_session_id() -> str | None:
    return _current_skill_session_id.get()
