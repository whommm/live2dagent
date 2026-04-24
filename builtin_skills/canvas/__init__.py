"""Canvas skill: tools for controlling the Live Canvas floating panel."""

from __future__ import annotations

from typing import Any


def show_bubble(text: str, duration_ms: int = 6000) -> dict[str, Any]:
    """Show a simple text bubble above the pet's head."""
    return {"canvas_type": "bubble", "data": {"text": text}, "duration_ms": duration_ms}


def show_card(
    title: str, content: str, icon: str = "", theme: str = "purple", duration_ms: int = 0
) -> dict[str, Any]:
    """Show a structured info card."""
    return {
        "canvas_type": "card",
        "data": {"title": title, "content": content, "icon": icon, "theme": theme},
        "duration_ms": duration_ms,
    }


def show_image(
    src: str, caption: str = "", width: int = 280, duration_ms: int = 0
) -> dict[str, Any]:
    """Show an image panel."""
    return {
        "canvas_type": "image",
        "data": {"src": src, "caption": caption},
        "width": width,
        "duration_ms": duration_ms,
    }


def show_list(
    title: str, items: list[str], checkable: bool = False, duration_ms: int = 0
) -> dict[str, Any]:
    """Show a list panel."""
    return {
        "canvas_type": "list",
        "data": {"title": title, "items": items, "checkable": checkable},
        "duration_ms": duration_ms,
    }


def show_code(
    code: str, language: str = "python", title: str = "Code", duration_ms: int = 0
) -> dict[str, Any]:
    """Show a code snippet panel with syntax highlighting."""
    return {
        "canvas_type": "code",
        "data": {"code": code, "language": language},
        "title": title,
        "duration_ms": duration_ms,
    }


def close(canvas_id: str) -> dict[str, Any]:
    """Close a specific canvas by ID."""
    return {"canvas_id": canvas_id, "action": "close"}


tools = {
    "show_bubble": show_bubble,
    "show_card": show_card,
    "show_image": show_image,
    "show_list": show_list,
    "show_code": show_code,
    "close": close,
}
