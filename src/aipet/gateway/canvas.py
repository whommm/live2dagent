"""Canvas Manager: handle AI-driven floating visual panels."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from aipet.gateway.events import EventBus


@dataclass
class CanvasElement:
    """A single canvas element state."""

    canvas_id: str
    canvas_type: str
    data: dict[str, Any] = field(default_factory=dict)
    title: str = ""
    position: str = "head"
    x: int | None = None
    y: int | None = None
    width: int = 280
    height: int = 0
    duration_ms: int = 0
    click_action: str = "dismiss"
    style: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    closed: bool = False


class CanvasManager:
    """Manage the lifecycle of Live Canvas elements."""

    def __init__(self, bus: EventBus | None = None) -> None:
        self._canvases: dict[str, CanvasElement] = {}
        self._bus = bus

    def create(
        self,
        canvas_type: str,
        data: dict[str, Any],
        title: str = "",
        position: str = "head",
        x: int | None = None,
        y: int | None = None,
        width: int = 280,
        height: int = 0,
        duration_ms: int = 0,
        click_action: str = "dismiss",
        style: dict[str, Any] | None = None,
        canvas_id: str | None = None,
    ) -> CanvasElement:
        """Create a new canvas element."""
        element = CanvasElement(
            canvas_id=canvas_id or f"canvas_{uuid.uuid4().hex[:8]}",
            canvas_type=canvas_type,
            data=data,
            title=title,
            position=position,
            x=x,
            y=y,
            width=width,
            height=height,
            duration_ms=duration_ms,
            click_action=click_action,
            style=style or {},
        )
        self._canvases[element.canvas_id] = element
        return element

    def get(self, canvas_id: str) -> CanvasElement | None:
        """Retrieve a canvas by ID."""
        return self._canvases.get(canvas_id)

    def list_canvases(self) -> list[CanvasElement]:
        """Return all non-closed canvases."""
        return [c for c in self._canvases.values() if not c.closed]

    def close(self, canvas_id: str) -> bool:
        """Mark a canvas as closed."""
        element = self._canvases.get(canvas_id)
        if element is None:
            return False
        element.closed = True
        return True

    def update_data(self, canvas_id: str, data: dict[str, Any]) -> bool:
        """Update the data of an existing canvas."""
        element = self._canvases.get(canvas_id)
        if element is None or element.closed:
            return False
        element.data.update(data)
        return True

    def close_all(self) -> None:
        """Close all canvases."""
        for c in self._canvases.values():
            c.closed = True

    def to_dict(self, element: CanvasElement) -> dict[str, Any]:
        """Serialize a canvas element to a plain dict."""
        return {
            "canvas_id": element.canvas_id,
            "canvas_type": element.canvas_type,
            "data": element.data,
            "title": element.title,
            "position": element.position,
            "x": element.x,
            "y": element.y,
            "width": element.width,
            "height": element.height,
            "duration_ms": element.duration_ms,
            "click_action": element.click_action,
            "style": element.style,
            "created_at": element.created_at.isoformat(),
        }
