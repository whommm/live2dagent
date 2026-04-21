"""Live2D rendering widget using live2d-py and PySide6 QOpenGLWidget."""

from __future__ import annotations

import contextlib
import math
import random
import time
from pathlib import Path
from typing import Any

from PySide6.QtCore import QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QCursor, QMouseEvent, QWheelEvent
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

    # ------------------------------------------------------------------
    # 紫羽 · 参数映射表（语义 -> 底层参数）
    # ------------------------------------------------------------------

    _POSE_MAP: dict[str, dict[str, float]] = {
        "look_at_user": {
            "ParamAngleX": 0,
            "ParamAngleY": 0,
            "ParamAngleZ": 0,
            "ParamBodyAngleX": 0,
            "ParamBodyAngleY": 0,
            "ParamBodyAngleZ": 0,
            "ParamEyeBallX": 0,
            "ParamEyeBallY": 0,
        },
        "look_away": {
            "ParamAngleX": 10,
            "ParamAngleZ": -5,
            "ParamEyeBallX": 0.6,
            "ParamEyeBallY": 0.1,
        },
        "tilt_left": {
            "ParamAngleZ": -15,
            "ParamEyeBallX": -0.2,
        },
        "tilt_right": {
            "ParamAngleZ": 15,
            "ParamEyeBallX": 0.2,
        },
        "lean_forward": {
            "ParamBodyAngleY": 5,
        },
        "lean_back": {
            "ParamBodyAngleY": -5,
        },
        "look_up": {
            "ParamAngleY": -15,
            "ParamEyeBallY": -0.5,
        },
        "look_down": {
            "ParamAngleY": 10,
            "ParamEyeBallY": 0.5,
        },
        "nod": {
            "ParamAngleY": 8,
        },
        "shake_head": {
            "ParamAngleX": 12,
        },
        "gaze_left": {
            "ParamEyeBallX": -0.7,
        },
        "gaze_right": {
            "ParamEyeBallX": 0.7,
        },
    }

    _EMOTION_MAP: dict[str, dict[str, float]] = {
        "happy": {
            "ParamEyeLSmile": 1.0,
            "ParamEyeRSmile": 1.0,
            "ParamMouthForm": 1.0,
            "ParamBrowLY": -0.3,
            "ParamBrowRY": -0.3,
        },
        "shy": {
            "Param7": 1.0,
            "ParamCheek": 1.0,
            "ParamEyeBallY": -0.3,
            "ParamMouthForm": 0.2,
            "ParamBrowLY": 0.3,
            "ParamBrowRY": 0.3,
        },
        "angry": {
            "Param8": 1.0,
            "ParamBrowLAngle": -1.0,
            "ParamBrowRAngle": -1.0,
            "ParamMouthForm": -0.5,
            "ParamEyeBallX": 0.0,
        },
        "sad": {
            "ParamEyeBallY": -0.2,
            "ParamMouthForm": -0.3,
            "ParamBrowLY": 0.5,
            "ParamBrowRY": 0.5,
            "ParamEyeLOpen": 0.8,
            "ParamEyeROpen": 0.8,
        },
        "cry": {
            "Param6": 1.0,
            "ParamEyeLOpen": 0.5,
            "ParamEyeROpen": 0.5,
            "ParamMouthForm": -0.5,
            "ParamBrowLY": 0.6,
            "ParamBrowRY": 0.6,
            "ParamEyeBallY": -0.1,
        },
        "confused": {
            "Param9": 1.0,
            "ParamMouthOpenY8": 1.0,
            "ParamBrowLAngle": 0.5,
            "ParamBrowRAngle": -0.5,
            "ParamEyeBallX": 0.3,
        },
        "dizzy": {
            "Param10": 1.0,
            "ParamEyeBallX": 0.0,
            "ParamBrowLY": 0.2,
            "ParamBrowRY": 0.2,
            "ParamEyeLOpen": 0.7,
            "ParamEyeROpen": 0.7,
        },
        "excited": {
            "Param11": 1.0,
            "ParamEyeLOpen": 1.2,
            "ParamEyeROpen": 1.2,
            "ParamBrowLY": -0.5,
            "ParamBrowRY": -0.5,
            "ParamMouthForm": 0.8,
        },
        "pout": {
            "ParamMouthOpenY5": 1.0,
            "ParamBrowLY": 0.3,
            "ParamBrowRY": 0.3,
            "ParamMouthForm": 0.0,
        },
        "tease": {
            "ParamMouthOpenY2": 1.0,
            "ParamMouthForm": 0.5,
            "ParamEyeLOpen": 0.8,
            "ParamEyeROpen": 1.0,
        },
        "bite_lip": {
            "ParamMouthOpenY6": 1.0,
            "ParamEyeBallY": 0.2,
            "ParamMouthForm": -0.2,
        },
        "surprised": {
            "ParamEyeLOpen": 1.3,
            "ParamEyeROpen": 1.3,
            "ParamBrowLY": -0.8,
            "ParamBrowRY": -0.8,
            "ParamMouthOpenY3": 0.3,
            "ParamMouthForm": -0.2,
        },
        "calm": {},  # 特殊处理：清空所有情绪参数
    }

    _PROP_MAP: dict[str, dict[str, float]] = {
        "wings_big": {
            "Param106": 1.0,
            "Param103": 0.0,
            "Param104": 0.0,
            "Param105": 0.0,
            "Param83": 0.0,
            "Param84": 0.0,
        },
        "wings_small": {
            "Param106": 0.0,
            "Param103": 1.0,
            "Param104": 1.0,
            "Param105": 1.0,
            "Param83": 0.5,
            "Param84": 0.5,
        },
        "wings_hide": {
            "Param106": 0.0,
            "Param103": 0.0,
            "Param104": 0.0,
            "Param105": 0.0,
            "Param83": 0.0,
            "Param84": 0.0,
            "Param34": 0.0,
            "Param27": 0.0,
        },
        "halo_on": {"Param102": 1.0},
        "halo_off": {"Param102": 0.0},
        "twintails": {"Param101": 1.0},
        "default_hair": {"Param101": 0.0},
        "pray": {"Param88": 1.0},
        "pray_off": {"Param88": 0.0},
        "microphone": {"Param89": 1.0},
        "microphone_off": {"Param89": 0.0},
        "trail_on": {"Param109": 1.0},
        "trail_off": {"Param109": 0.0},
    }

    # 所有可能被 emotion 修改的参数（calm 时清零用）
    _EMOTION_PARAM_KEYS: tuple[str, ...] = (
        "Param6",
        "Param7",
        "Param8",
        "Param9",
        "Param10",
        "Param11",
        "ParamCheek",
        "ParamEyeLSmile",
        "ParamEyeRSmile",
        "ParamMouthForm",
        "ParamMouthOpenY2",
        "ParamMouthOpenY3",
        "ParamMouthOpenY4",
        "ParamMouthOpenY5",
        "ParamMouthOpenY6",
        "ParamMouthOpenY7",
        "ParamMouthOpenY8",
        "ParamBrowLY",
        "ParamBrowRY",
        "ParamBrowLAngle",
        "ParamBrowRAngle",
        "ParamBrowLForm",
        "ParamBrowRForm",
        "ParamEyeLOpen",
        "ParamEyeROpen",
        "ParamEyeBallY",
    )

    # 所有前端可控的参数 ID（用于初始化和快照）
    _ALL_PARAMS: tuple[str, ...] = (
        "ParamAngleX",
        "ParamAngleY",
        "ParamAngleZ",
        "ParamBodyAngleX",
        "ParamBodyAngleY",
        "ParamBodyAngleZ",
        "ParamBodyAngleZ2",
        "ParamBodyAngleZ3",
        "ParamEyeLOpen",
        "ParamEyeROpen",
        "ParamEyeLSmile",
        "ParamEyeRSmile",
        "ParamEyeBallX",
        "ParamEyeBallY",
        "ParamBrowLY",
        "ParamBrowRY",
        "ParamBrowLAngle",
        "ParamBrowRAngle",
        "ParamBrowLForm",
        "ParamBrowRForm",
        "ParamMouthForm",
        "ParamMouthOpenY",
        "ParamMouthOpenY2",
        "ParamMouthOpenY3",
        "ParamMouthOpenY4",
        "ParamMouthOpenY5",
        "ParamMouthOpenY6",
        "ParamMouthOpenY7",
        "ParamMouthOpenY8",
        "ParamCheek",
        "ParamBreath",
        "Param6",
        "Param7",
        "Param8",
        "Param9",
        "Param10",
        "Param11",
        "Param87",
        "Param88",
        "Param89",
        "Param101",
        "Param102",
        "Param103",
        "Param104",
        "Param105",
        "Param106",
        "Param109",
        "Param34",
        "Param27",
        "Param83",
        "Param84",
    )

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

        # ---- 参数动画系统 ----
        self._current_params: dict[str, float] = {pid: 0.0 for pid in self._ALL_PARAMS}
        self._param_targets: dict[str, float] = {}
        self._param_factors: dict[str, float] = {}
        self._idle_offsets: dict[str, float] = {}

        # ---- 眨眼系统 ----
        self._blink_next_time = time.time() + random.uniform(2.0, 4.0)
        self._blink_phase = 0  # 0=开, 1=闭, 2=开
        self._blink_progress = 0.0

        # ---- 自主表演系统（选项 C：虚拟主播级自主动作）----
        self._auto_targets: dict[str, float] = {}
        self._auto_factors: dict[str, float] = {}
        self._auto_timers: dict[str, float] = {}
        self._next_auto_action_time = time.time() + random.uniform(2.0, 5.0)
        # 持续眼神飘动
        self._gaze_target_x = 0.0
        self._gaze_target_y = 0.0
        self._next_gaze_time = time.time()
        # 持续头部微转
        self._head_idle_target_x = 0.0
        self._head_idle_target_y = 0.0
        self._head_idle_target_z = 0.0
        self._next_head_idle_time = time.time()

        # ---- 鼠标视线追踪 ----
        self._mouse_gaze_x = 0.0
        self._mouse_gaze_y = 0.0
        self._mouse_head_x = 0.0   # 头部跟随目标（角度）
        self._mouse_head_y = 0.0
        self._mouse_track_timer = QTimer(self)
        self._mouse_track_timer.timeout.connect(self._update_mouse_gaze)
        self._mouse_track_timer.start(50)

        # ---- 当前高层状态（用于快照回传）----
        self._current_pose = "look_at_user"
        self._current_emotion = "calm"
        self._active_props: set[str] = set()

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
            self._reset_params()
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
            self._reset_params()
            self.timer.start(int(1000 / 60))
        except Exception as exc:
            print(f"Live2D init error: {exc}")

    def _reset_params(self) -> None:
        """Reset all controlled params to defaults and clear targets."""
        self._current_params = {pid: 0.0 for pid in self._ALL_PARAMS}
        # 眼睛默认睁开
        self._current_params["ParamEyeLOpen"] = 1.0
        self._current_params["ParamEyeROpen"] = 1.0
        self._param_targets.clear()
        self._param_factors.clear()
        self._auto_targets.clear()
        self._auto_factors.clear()
        self._auto_timers.clear()
        self._next_auto_action_time = time.time() + random.uniform(1.0, 3.0)
        self._current_pose = "look_at_user"
        self._current_emotion = "calm"
        self._active_props.clear()

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

            # 1. 更新参数动画系统
            self._update_parameters()
            # 2. 写入所有参数到模型
            self._flush_parameters()

            self.model.Draw()
        except Exception as exc:
            print(f"Live2D paint error: {exc}")

    def _update_parameters(self) -> None:
        """每帧执行：Lerp + 自主表演 + idle + 眨眼."""
        now = time.time()

        # --- 1. 清理过期 auto target ---
        for pid in list(self._auto_timers.keys()):
            if now >= self._auto_timers[pid]:
                # 设目标为 0，让参数慢慢回去
                self._auto_targets[pid] = 0.0
                self._auto_factors[pid] = 0.03
                self._auto_timers[pid] = now + 2.0
                if abs(self._current_params.get(pid, 0)) < 0.01:
                    self._auto_targets.pop(pid, None)
                    self._auto_factors.pop(pid, None)
                    self._auto_timers.pop(pid, None)

        # --- 2. Lerp 所有目标值（外部指令优先于自主表演）---
        all_targets: dict[str, tuple[float, float]] = {}
        for pid, target in self._auto_targets.items():
            all_targets[pid] = (target, self._auto_factors.get(pid, 0.03))
        for pid, target in self._param_targets.items():
            all_targets[pid] = (target, self._param_factors.get(pid, 0.08))

        for pid, (target, factor) in all_targets.items():
            current = self._current_params.get(pid, 0.0)
            self._current_params[pid] = current + (target - current) * factor

        # 清理接近目标的外部 target
        for pid in list(self._param_targets.keys()):
            if abs(self._param_targets[pid] - self._current_params.get(pid, 0)) < 0.002:
                self._param_targets.pop(pid, None)
                self._param_factors.pop(pid, None)

        # --- 3. 自主表演触发 ---
        if now >= self._next_auto_action_time:
            self._next_auto_action_time = now + random.uniform(1.0, 3.0)
            self._trigger_auto_action()

        # --- 4. 视线追踪（鼠标跟随）---
        # 优先级：AI 指令 > 鼠标追踪 > 自主飘动
        if "ParamEyeBallX" not in self._param_targets and "ParamEyeBallY" not in self._param_targets:
            # 清除 auto target 中的眼神参数，避免打架
            for pid in ("ParamEyeBallX", "ParamEyeBallY"):
                self._auto_targets.pop(pid, None)
                self._auto_factors.pop(pid, None)
                self._auto_timers.pop(pid, None)
            # 眼珠用较快但平滑的速度跟随鼠标
            current_x = self._current_params.get("ParamEyeBallX", 0.0)
            current_y = self._current_params.get("ParamEyeBallY", 0.0)
            self._current_params["ParamEyeBallX"] = current_x + (self._mouse_gaze_x - current_x) * 0.15
            self._current_params["ParamEyeBallY"] = current_y + (self._mouse_gaze_y - current_y) * 0.15
        else:
            # AI 控制眼神时，回退到自主飘动
            if now >= self._next_gaze_time:
                self._gaze_target_x = random.uniform(-0.6, 0.6)
                self._gaze_target_y = random.uniform(-0.4, 0.4)
                self._next_gaze_time = now + random.uniform(1.5, 4.0)
            for pid, target in [("ParamEyeBallX", self._gaze_target_x), ("ParamEyeBallY", self._gaze_target_y)]:
                if pid not in self._param_targets and pid not in self._auto_targets:
                    current = self._current_params.get(pid, 0.0)
                    self._current_params[pid] = current + (target - current) * 0.06

        # --- 5. 头部跟随鼠标（优先级高于 idle 微转，低于 AI 指令）---
        if "ParamAngleX" not in self._param_targets and "ParamAngleY" not in self._param_targets:
            # 清除 auto target 中的头部参数，避免打架
            for pid in ("ParamAngleX", "ParamAngleY"):
                self._auto_targets.pop(pid, None)
                self._auto_factors.pop(pid, None)
                self._auto_timers.pop(pid, None)
            # 头部跟随，比眼睛慢但幅度大，像真人一样滞后
            cur_x = self._current_params.get("ParamAngleX", 0.0)
            cur_y = self._current_params.get("ParamAngleY", 0.0)
            self._current_params["ParamAngleX"] = cur_x + (self._mouse_head_x - cur_x) * 0.06
            self._current_params["ParamAngleY"] = cur_y + (self._mouse_head_y - cur_y) * 0.06
            # 身体也跟着微微转，增强整体感
            if "ParamBodyAngleX" not in self._param_targets:
                cur_bx = self._current_params.get("ParamBodyAngleX", 0.0)
                self._current_params["ParamBodyAngleX"] = cur_bx + (self._mouse_head_x * 0.3 - cur_bx) * 0.04
            if "ParamBodyAngleY" not in self._param_targets:
                cur_by = self._current_params.get("ParamBodyAngleY", 0.0)
                self._current_params["ParamBodyAngleY"] = cur_by + (self._mouse_head_y * 0.3 - cur_by) * 0.04
        else:
            # AI 控制头部时，回退到 idle 微转
            if now >= self._next_head_idle_time:
                self._head_idle_target_x = random.uniform(-8, 8)
                self._head_idle_target_y = random.uniform(-5, 5)
                self._head_idle_target_z = random.uniform(-10, 10)
                self._next_head_idle_time = now + random.uniform(2.0, 5.0)
            for pid, target in [
                ("ParamAngleX", self._head_idle_target_x),
                ("ParamAngleY", self._head_idle_target_y),
                ("ParamAngleZ", self._head_idle_target_z),
            ]:
                if pid not in self._param_targets and pid not in self._auto_targets:
                    current = self._current_params.get(pid, 0.0)
                    self._current_params[pid] = current + (target - current) * 0.03

        # --- 6. idle 偏移（呼吸 + 翅膀扇动 + 微晃）---
        self._idle_offsets.clear()

        # 呼吸
        breath = math.sin(now * 1.8) * 0.3 + 0.3
        self._idle_offsets["ParamBreath"] = breath

        # 翅膀轻轻扇动
        wing = math.sin(now * 2.5) * 0.15 + 0.15
        self._idle_offsets["Param83"] = wing
        self._idle_offsets["Param84"] = wing

        # 头部微晃（叠加在自主转头上）
        if "ParamAngleX" not in all_targets:
            self._idle_offsets["ParamAngleX"] = math.sin(now * 0.7) * 1.2
        if "ParamAngleY" not in all_targets:
            self._idle_offsets["ParamAngleY"] = math.sin(now * 0.5) * 0.8
        if "ParamAngleZ" not in all_targets:
            self._idle_offsets["ParamAngleZ"] = math.sin(now * 0.3) * 0.6

        # --- 7. 眨眼 ---
        if now >= self._blink_next_time and self._blink_phase == 0:
            self._blink_phase = 1
            self._blink_progress = 0.0

        if self._blink_phase == 1:
            self._blink_progress += 0.18
            if self._blink_progress >= 1.0:
                self._blink_progress = 1.0
                self._blink_phase = 2
        elif self._blink_phase == 2:
            self._blink_progress -= 0.22
            if self._blink_progress <= 0.0:
                self._blink_progress = 0.0
                self._blink_phase = 0
                self._blink_next_time = now + random.uniform(2.5, 6.0)

    def _flush_parameters(self) -> None:
        """将当前参数值（含 idle 偏移、眨眼）写入模型."""
        if not self.model:
            return

        for pid, base_value in self._current_params.items():
            final_value = base_value

            # 叠加 idle 偏移
            if pid in self._idle_offsets:
                final_value += self._idle_offsets[pid]

            # 口型同步最高优先级，覆盖嘴张开度
            if pid == "ParamMouthOpenY" and self._lipsync_active:
                final_value = self._lipsync_value

            # 眨眼覆盖眼睛开闭
            if self._blink_phase != 0 and pid in ("ParamEyeLOpen", "ParamEyeROpen"):
                final_value = base_value * (1.0 - self._blink_progress)

            # 钳制到合理范围
            if pid.startswith("ParamAngle") or pid.startswith("ParamBodyAngle"):
                final_value = max(-30.0, min(30.0, final_value))
            elif pid in ("ParamEyeBallX", "ParamEyeBallY"):
                final_value = max(-1.0, min(1.0, final_value))
            elif pid in ("ParamEyeLOpen", "ParamEyeROpen"):
                final_value = max(0.0, min(2.0, final_value))
            else:
                final_value = max(-1.0, min(2.0, final_value))

            try:
                self.model.SetParameterValue(pid, final_value)
            except Exception:
                # 参数不存在则忽略
                pass

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

        # 被点击时随机给一个可爱反应
        reactions = ["tilt_left", "tilt_right", "excited", "happy", "shy"]
        reaction = random.choice(reactions)
        if reaction in self._POSE_MAP:
            self.set_pose(reaction, duration_ms=800)
        elif reaction in self._EMOTION_MAP:
            self.set_emotion(reaction, duration_ms=800)

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

    # ------------------------------------------------------------------
    # 公开 API：姿态 / 表情 / 道具 控制
    # ------------------------------------------------------------------

    def _update_mouse_gaze(self) -> None:
        """根据鼠标位置计算眼珠和头部目标值."""
        try:
            cursor = QCursor.pos()
            head_pos = self.get_model_head_pos()
            # 手动计算全局坐标，避免 OpenGL widget 的 mapToGlobal 行为异常
            global_head = QPoint(
                self.window().x() + self.x() + head_pos.x(),
                self.window().y() + self.y() + head_pos.y(),
            )

            dx = cursor.x() - global_head.x()
            dy = cursor.y() - global_head.y()

            # 眼珠：归一化到 [-1, 1]，200 像素为满幅度（更灵敏）
            # 注意：屏幕 y 轴向下，模型 y 轴向上，所以 dy 取反
            self._mouse_gaze_x = max(-1.0, min(1.0, dx / 200.0))
            self._mouse_gaze_y = max(-1.0, min(1.0, -dy / 200.0))

            # 头部：左右 ±20°，上下 ±20°（dy 取反，和屏幕方向一致）
            self._mouse_head_x = max(-20.0, min(20.0, dx / 300.0 * 20.0))
            self._mouse_head_y = max(-20.0, min(20.0, -dy / 300.0 * 20.0))

            # 调试：每 2 秒打印一次鼠标追踪状态
            if not hasattr(self, "_last_gaze_print") or time.time() - self._last_gaze_print > 2.0:
                self._last_gaze_print = time.time()
                print(
                    f"[Live2D] Mouse gaze: cursor=({cursor.x()},{cursor.y()}), "
                    f"head_global=({global_head.x()},{global_head.y()}), "
                    f"gaze=({self._mouse_gaze_x:.2f},{self._mouse_gaze_y:.2f}), "
                    f"head=({self._mouse_head_x:.1f},{self._mouse_head_y:.1f})"
                )
        except Exception as exc:
            print(f"[Live2D] Mouse gaze error: {exc}")

    def _clear_auto_for_params(self, param_ids: set[str]) -> None:
        """AI 指令下发时，清除冲突的自主表演目标."""
        for pid in param_ids:
            self._auto_targets.pop(pid, None)
            self._auto_factors.pop(pid, None)
            self._auto_timers.pop(pid, None)

    def _trigger_auto_action(self) -> None:
        """随机触发一个自主小动作."""
        actions = [
            "glance", "wing_flutter", "head_tilt", "micro_smile",
            "sigh", "brow_raise", "look_around",
        ]
        action = random.choice(actions)
        now = time.time()
        print(f"[Live2D] Auto action: {action}")

        if action == "glance":
            target_x = random.choice([-0.7, 0.7])
            self._auto_targets["ParamEyeBallX"] = target_x
            self._auto_factors["ParamEyeBallX"] = 0.08
            self._auto_timers["ParamEyeBallX"] = now + random.uniform(1.0, 2.5)
        elif action == "wing_flutter":
            self._auto_targets["Param83"] = 0.5
            self._auto_targets["Param84"] = 0.5
            self._auto_factors.update({k: 0.12 for k in ["Param83", "Param84"]})
            self._auto_timers.update({k: now + 0.5 for k in ["Param83", "Param84"]})
        elif action == "head_tilt":
            target_z = random.uniform(-15, 15)
            self._auto_targets["ParamAngleZ"] = target_z
            self._auto_factors["ParamAngleZ"] = 0.05
            self._auto_timers["ParamAngleZ"] = now + random.uniform(1.5, 4.0)
        elif action == "micro_smile":
            self._auto_targets["ParamMouthForm"] = 0.6
            self._auto_factors["ParamMouthForm"] = 0.04
            self._auto_timers["ParamMouthForm"] = now + random.uniform(1.5, 3.0)
        elif action == "sigh":
            self._auto_targets["ParamBrowLY"] = 0.5
            self._auto_targets["ParamBrowRY"] = 0.5
            self._auto_targets["ParamMouthOpenY3"] = 0.25
            self._auto_factors.update({k: 0.05 for k in ["ParamBrowLY", "ParamBrowRY", "ParamMouthOpenY3"]})
            self._auto_timers.update({k: now + 1.2 for k in ["ParamBrowLY", "ParamBrowRY", "ParamMouthOpenY3"]})
        elif action == "brow_raise":
            self._auto_targets["ParamBrowLY"] = -0.6
            self._auto_targets["ParamBrowRY"] = -0.6
            self._auto_factors.update({k: 0.08 for k in ["ParamBrowLY", "ParamBrowRY"]})
            self._auto_timers.update({k: now + 0.8 for k in ["ParamBrowLY", "ParamBrowRY"]})
        elif action == "look_around":
            target_x = random.choice([-18, 18])
            self._auto_targets["ParamAngleX"] = target_x
            self._auto_factors["ParamAngleX"] = 0.04
            self._auto_timers["ParamAngleX"] = now + random.uniform(1.0, 2.5)

    def set_pose(self, name: str, duration_ms: float = 500.0) -> None:
        """设置身体姿态（头部角度、眼神方向等）."""
        if name not in self._POSE_MAP:
            print(f"[Live2D] Unknown pose: {name}")
            return

        self._current_pose = name
        values = self._POSE_MAP[name]
        factor = self._calc_lerp_factor(duration_ms)

        # 清除冲突的自主表演目标
        self._clear_auto_for_params(set(values.keys()))

        # nod / shake_head 是临时姿态，结束后自动回到 look_at_user
        if name in ("nod", "shake_head"):
            self._set_targets(values, factor)
            QTimer.singleShot(int(duration_ms), lambda: self.set_pose("look_at_user"))
            return

        self._set_targets(values, factor)

    def set_emotion(self, name: str, duration_ms: float = 500.0) -> None:
        """设置五官情绪表情."""
        if name not in self._EMOTION_MAP:
            print(f"[Live2D] Unknown emotion: {name}")
            return

        self._current_emotion = name
        factor = self._calc_lerp_factor(duration_ms)

        if name == "calm":
            # 清空所有情绪参数
            for pid in self._EMOTION_PARAM_KEYS:
                self._param_targets[pid] = 0.0
                self._param_factors[pid] = factor
            self._clear_auto_for_params(set(self._EMOTION_PARAM_KEYS))
            return

        # 先清除之前的情绪残留（除当前 emotion 要用的参数外）
        new_keys = set(self._EMOTION_MAP[name].keys())
        for pid in self._EMOTION_PARAM_KEYS:
            if pid not in new_keys:
                self._param_targets[pid] = 0.0
                self._param_factors[pid] = factor

        self._clear_auto_for_params(new_keys)
        self._set_targets(self._EMOTION_MAP[name], factor)

    def set_prop(self, name: str, duration_ms: float = 500.0) -> None:
        """设置道具/造型开关（翅膀、光环等）."""
        if name not in self._PROP_MAP:
            print(f"[Live2D] Unknown prop: {name}")
            return

        # 记录活跃 props
        if name.endswith("_off"):
            base = name[:-4]
            self._active_props.discard(base)
        else:
            self._active_props.add(name)

        factor = self._calc_lerp_factor(duration_ms)
        values = self._PROP_MAP[name]
        self._clear_auto_for_params(set(values.keys()))
        self._set_targets(values, factor)

    def _set_targets(self, values: dict[str, float], factor: float) -> None:
        """批量设置参数目标值."""
        for pid, val in values.items():
            self._param_targets[pid] = val
            self._param_factors[pid] = factor

    @staticmethod
    def _calc_lerp_factor(duration_ms: float) -> float:
        """根据过渡时间计算每帧 Lerp factor（假设 60fps）."""
        if duration_ms <= 50:
            return 1.0
        # 约等于 duration_ms 时间内完成 95% 过渡
        return min(1.0, (16.67 / duration_ms) * 3.0)

    # ------------------------------------------------------------------
    # 状态快照：回传给 AI
    # ------------------------------------------------------------------

    def get_state_snapshot(self) -> dict[str, Any]:
        """返回当前高层状态，供 Gateway 注入提示词."""
        # 将当前 pose 翻译成人话
        pose_desc = self._describe_pose()
        emotion_desc = self._describe_emotion()
        props_desc = self._describe_props()

        return {
            "pose": self._current_pose,
            "pose_description": pose_desc,
            "emotion": self._current_emotion,
            "emotion_description": emotion_desc,
            "props": sorted(self._active_props),
            "props_description": props_desc,
        }

    def _describe_pose(self) -> str:
        desc = {
            "look_at_user": "正视着主人",
            "look_away": "目光飘向别处",
            "tilt_left": "头微微向左歪",
            "tilt_right": "头微微向右歪",
            "lean_forward": "身体前倾凑近",
            "lean_back": "身体后仰",
            "look_up": "抬头向上看",
            "look_down": "低头向下看",
            "nod": "轻轻点头",
            "shake_head": "轻轻摇头",
            "gaze_left": "眼神瞟向左边",
            "gaze_right": "眼神瞟向右边",
        }
        return desc.get(self._current_pose, "安静地漂浮着")

    def _describe_emotion(self) -> str:
        desc = {
            "happy": "带着开心的笑容",
            "shy": "害羞地红了脸",
            "angry": "皱着眉头有些生气",
            "sad": "眼神有些落寞",
            "cry": "眼眶含泪快要哭了",
            "confused": "一脸疑惑歪着头",
            "dizzy": "晕乎乎的",
            "excited": "眼睛闪闪发亮很兴奋",
            "pout": "嘟着嘴",
            "tease": "调皮地吐着舌头",
            "bite_lip": "轻轻咬着嘴唇",
            "surprised": "惊讶地睁大了眼睛",
            "calm": "神情平静",
        }
        return desc.get(self._current_emotion, "神情自然")

    def _describe_props(self) -> str:
        if not self._active_props:
            return "没有特殊装饰"
        names = []
        for p in sorted(self._active_props):
            names.append({
                "wings_big": "展开着大翅膀",
                "wings_small": "收着小翅膀",
                "halo_on": "头顶光环发亮",
                "twintails": "扎着双马尾",
                "pray": "双手合十",
                "microphone": "拿着麦克风",
                "trail_on": "身后拖尾飘动",
            }.get(p, p))
        return "、".join(names)

    # ------------------------------------------------------------------
    # 兼容旧 API（expression / motion / lipsync）
    # ------------------------------------------------------------------

    def set_expression(self, name: str) -> None:
        """兼容旧接口：现在映射为 emotion."""
        # 旧 expression 名称 -> 新 emotion 名称的映射
        legacy_map = {
            "xingxing": "excited",
            "lianhong": "shy",
            "QAQ": "cry",
            "shengqi": "angry",
            "wenhao": "confused",
            "yun": "dizzy",
        }
        mapped = legacy_map.get(name)
        if mapped:
            self.set_emotion(mapped)
        elif self.model and name in self.available_expressions and hasattr(self.model, "SetExpression"):
            # fallback：如果模型有对应的 expression 文件，仍允许直接调用
            try:
                self.model.SetExpression(name)
            except Exception as exc:
                print(f"SetExpression error: {exc}")

    def reset_expression(self) -> None:
        """兼容旧接口：恢复平静."""
        self.set_emotion("calm")

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
