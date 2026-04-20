"""Live2D rendering widget using live2d-py and PySide6 QOpenGLWidget."""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Any

from PySide6.QtCore import QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QMouseEvent, QWheelEvent
from PySide6.QtOpenGLWidgets import QOpenGLWidget

from aipet.utils.paths import get_project_root

LIVE2D_AVAILABLE = False
live2d: Any = None

try:
    import live2d.v3 as live2d  # type: ignore

    LIVE2D_AVAILABLE = True
except ImportError:
    live2d = None


class Live2DWidget(QOpenGLWidget):
    """OpenGL widget for rendering a Live2D model."""

    right_clicked = Signal()
    scale_changed = Signal(float)

    def __init__(self, model_path: str | None = None, parent: Any = None) -> None:
        super().__init__(parent)
        self.model: Any = None
        self.model_path = model_path or self._find_default_model()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update)
        self.current_scale = 1.0
        self.is_dragging = False
        self.drag_offset: tuple[float, float] = (0.0, 0.0)
        self.available_expressions: list[str] = []
        self.available_motion_groups: dict[str, int] = {}
        self._lipsync_active = False
        self._lipsync_value = 0.0
        # Click vs drag detection
        self._mouse_press_pos: QPoint | None = None
        self._has_dragged = False
        self._drag_threshold = 5  # pixels

    @staticmethod
    def scan_models() -> list[tuple[str, str]]:
        """Scan project live2dmodels dir for available models.

        Returns list of (display_name, model3_json_path).
        """
        project_models_dir = get_project_root() / "live2dmodels"
        candidates: list[tuple[str, str]] = []
        if project_models_dir.exists():
            for model_json in sorted(project_models_dir.rglob("*.model3.json")):
                display_name = model_json.parent.name
                candidates.append((display_name, str(model_json)))
        return candidates

    def _find_default_model(self) -> str:
        """Look for a default model in the project live2dmodels dir."""
        models = self.scan_models()
        if not models:
            return ""
        # Prefer PurpleBird if available, otherwise first model
        for name, path in models:
            if name.lower() == "purplebird":
                return path
        return models[0][1]

    def load_model(self, model_path: str) -> bool:
        """Hot-load a new Live2D model at runtime."""
        if not LIVE2D_AVAILABLE:
            print("Live2D not available, cannot load model")
            return False
        p = Path(model_path)
        if not p.exists():
            print(f"Model path does not exist: {model_path}")
            return False

        self.timer.stop()
        self.makeCurrent()
        try:
            # Release old model if any
            if self.model is not None:
                try:
                    if hasattr(self.model, "release"):
                        self.model.release()
                except Exception:
                    pass
                self.model = None

            self.model_path = str(p)
            self.model = live2d.LAppModel()
            self.model.LoadModelJson(self.model_path)
            self.model.Resize(self.width(), self.height())
            if hasattr(self.model, "SetScale"):
                self.model.SetScale(self.current_scale)
            self._parse_model_config()
            print(f"[Live2D] Loaded model: {self.model_path}")
            self.doneCurrent()
            self.timer.start(int(1000 / 60))
            self.update()
            return True
        except Exception as exc:
            print(f"[Live2D] Failed to load model {model_path}: {exc}")
            self.doneCurrent()
            self.timer.start(int(1000 / 60))
            return False

    def initializeGL(self) -> None:
        if not LIVE2D_AVAILABLE:
            return
        try:
            live2d.init()
            if hasattr(live2d, "glewInit"):
                live2d.glewInit()
            elif hasattr(live2d, "glInit"):
                live2d.glInit()

            if not self.model_path or not Path(self.model_path).exists():
                print(f"Live2D model not found: {self.model_path}")
                return
            self.model = live2d.LAppModel()
            self.model.LoadModelJson(self.model_path)
            self.model.Resize(self.width(), self.height())
            if hasattr(self.model, "SetScale"):
                self.model.SetScale(self.current_scale)
            self._parse_model_config()
            self.timer.start(int(1000 / 60))
        except Exception as exc:
            print(f"Live2D init error: {exc}")

    def paintGL(self) -> None:
        if not LIVE2D_AVAILABLE or not self.model:
            return
        try:
            if hasattr(live2d, "clearBuffer"):
                live2d.clearBuffer()
            self.model.Update()
            if hasattr(self.model, "SetScale"):
                self.model.SetScale(self.current_scale)
            if hasattr(self.model, "SetOffset"):
                # Place the model near the bottom-right of the viewport
                # Live2D offset is typically in normalized canvas units (~[-1,1])
                self.model.SetOffset(0.4, -0.4)
            if self._lipsync_active:
                self.model.SetParameterValue("ParamMouthOpenY", self._lipsync_value)
            self.model.Draw()
        except Exception as exc:
            print(f"Live2D paint error: {exc}")

    def resizeGL(self, width: int, height: int) -> None:
        if self.model and LIVE2D_AVAILABLE:
            self.model.Resize(width, height)
            if hasattr(self.model, "SetScale"):
                self.model.SetScale(self.current_scale)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._mouse_press_pos = event.pos()
            self._has_dragged = False
            self.is_dragging = True
            self.drag_offset = (event.globalPosition().x() - self.window().x(),
                                event.globalPosition().y() - self.window().y())
            self.grabMouse()
            event.accept()
        elif event.button() == Qt.MouseButton.RightButton:
            self.right_clicked.emit()
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self.is_dragging and event.buttons() == Qt.MouseButton.LeftButton:
            # Detect if user has moved enough to consider it a drag
            if self._mouse_press_pos is not None and not self._has_dragged:
                delta = (event.pos() - self._mouse_press_pos).manhattanLength()
                if delta > self._drag_threshold:
                    self._has_dragged = True
            x = int(event.globalPosition().x() - self.drag_offset[0])
            y = int(event.globalPosition().y() - self.drag_offset[1])
            self.window().move(x, y)
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.is_dragging = False
            self.releaseMouse()
            # If user never dragged beyond threshold, treat as a click/tap
            if not self._has_dragged and self._mouse_press_pos is not None:
                part_id = ""
                if self.model and LIVE2D_AVAILABLE:
                    hits = self.model.HitPart(event.pos().x(), event.pos().y(), True)
                    if hits:
                        part_id = hits[0]
                self._trigger_tap_reaction(part_id)
            self._mouse_press_pos = None
            self._has_dragged = False
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def wheelEvent(self, event: QWheelEvent) -> None:
        """Zoom the model with mouse wheel."""
        delta = event.angleDelta().y() / 120
        self.current_scale = max(0.2, min(3.0, self.current_scale + delta * 0.1))
        self.scale_changed.emit(self.current_scale)
        self.update()

    def get_model_head_pos(self) -> QPoint:
        """Estimate the model's head position in widget local coordinates."""
        if not LIVE2D_AVAILABLE or not self.model:
            return QPoint(self.width() // 2, self.height() // 3)

        w = self.width()
        h = self.height()
        s = self.current_scale
        dx = 0.4
        dy = -0.4

        # Match the projection math used in paintGL (v2/v3 MatrixManager logic).
        # For landscape (w >= h): aspect_scale_x = h/w, aspect_scale_y = 1.0
        # For portrait (h > w): aspect_scale_x = 1.0, aspect_scale_y = w/h
        if w >= h:
            cx = w / 2.0 * (1.0 + (h / w) * s * dx)
            cy = h / 2.0 * (1.0 - s * dy)
        else:
            cx = w / 2.0 * (1.0 + s * dx)
            cy = h / 2.0 * (1.0 - (w / h) * s * dy)

        # Estimate head position: the model origin (cx, cy) is roughly at the chest/waist.
        # The head is about half the model's on-screen height above the origin.
        # The model's visible height is approximated by the smaller viewport dimension * scale.
        head_y = cy - min(w, h) * s * 0.5

        return QPoint(int(cx), int(head_y))

    def hit_test(self, x: int, y: int) -> bool:
        """Check if a point hits the Live2D model."""
        if not LIVE2D_AVAILABLE or not self.model:
            return False
        try:
            hits = self.model.HitPart(x, y, True)
            return bool(hits)
        except (RuntimeError, AttributeError):
            # Model not ready or context lost
            return False

    def _trigger_tap_reaction(self, part_id: str) -> None:
        """React to a tap on a model part with random expression and motion."""
        import random

        # Random motion if available
        if self.available_motion_groups:
            motion_group = random.choice(list(self.available_motion_groups.keys()))
            self.play_motion(motion_group, 0, priority=3)

        # Random expression if available
        if self.available_expressions:
            expr = random.choice(self.available_expressions)
            self.set_expression(expr)
            QTimer.singleShot(2000, self.reset_expression)

    def _parse_model_config(self) -> None:
        """Parse the model JSON to discover expressions and motions."""
        if not self.model_path:
            return
        import json

        try:
            with open(self.model_path, encoding="utf-8") as f:
                config = json.load(f)
            files = config.get("FileReferences", {})
            expr_file = files.get("Expressions")
            if expr_file:
                # expr_file can be a list of expression entries or a single string path
                if isinstance(expr_file, list):
                    self.available_expressions = [
                        e.get("Name", e.get("Id", f"expr_{i}")) for i, e in enumerate(expr_file)
                    ]
                else:
                    expr_path = Path(self.model_path).parent / expr_file
                    if expr_path.exists():
                        with open(expr_path, encoding="utf-8") as f:
                            expr_data = json.load(f)
                        self.available_expressions = [e.get("Name", f"expr_{i}") for i, e in enumerate(expr_data)]
            motions = files.get("Motions", {})
            self.available_motion_groups = {k: len(v) for k, v in motions.items()}
        except Exception as exc:
            print(f"Model parse error: {exc}")

    def set_expression(self, name: str) -> None:
        if self.model and name in self.available_expressions and hasattr(self.model, "SetExpression"):
            try:
                self.model.SetExpression(name)
            except Exception as exc:
                print(f"SetExpression error: {exc}")

    def reset_expression(self) -> None:
        if self.model and hasattr(self.model, "ResetExpression"):
            try:
                self.model.ResetExpression()
            except Exception as exc:
                print(f"ResetExpression error: {exc}")

    def play_motion(self, group: str, index: int, priority: int = 0) -> None:
        if not self.model or group not in self.available_motion_groups:
            return
        try:
            actual_priority = priority
            if hasattr(live2d, "MotionPriority"):
                mp = live2d.MotionPriority
                if priority == 0 and hasattr(mp, "NORMAL"):
                    actual_priority = mp.NORMAL
                elif priority == 1 and hasattr(mp, "IDLE"):
                    actual_priority = mp.IDLE
                elif priority == 2 and hasattr(mp, "FORCE"):
                    actual_priority = mp.FORCE
            if hasattr(self.model, "StartMotion"):
                self.model.StartMotion(group, index, actual_priority)
        except Exception as exc:
            print(f"StartMotion error: {exc}")

    def set_lipsync(self, value: float) -> None:
        """Set mouth openness for lip-sync (0.0 - 1.0)."""
        self._lipsync_active = value > 0.01
        self._lipsync_value = max(0.0, min(1.0, value))

    def stop_lipsync(self) -> None:
        self._lipsync_active = False
        self._lipsync_value = 0.0

    def cleanup(self) -> None:
        self.timer.stop()
        if self.model is not None:
            with contextlib.suppress(Exception):
                if hasattr(self.model, "release"):
                    self.model.release()
            self.model = None
        if LIVE2D_AVAILABLE and hasattr(live2d, "dispose"):
            with contextlib.suppress(Exception):
                live2d.dispose()
