"""AI and tool-chain settings dialog."""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from aipet.frontend.client import GatewayClient
from aipet.frontend.provider_dialog import ProviderDialog
from aipet.frontend.theme import MaterialTheme


class AIToolSettingsDialog(QDialog):
    """Configure model roles, tool strategy, and runtime diagnostics."""

    settings_changed = Signal()
    providers_changed = Signal()

    def __init__(self, client: GatewayClient, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.client = client
        self._providers: list[dict[str, Any]] = []
        self._settings: dict[str, Any] = {}
        self._pending_tasks: set[asyncio.Task[Any]] = set()
        self._provider_dialog: ProviderDialog | None = None

        self.setWindowTitle("AI 与工具")
        self.setMinimumSize(720, 560)
        self._setup_ui()

    def _setup_ui(self) -> None:
        self.setStyleSheet(
            MaterialTheme.dialog_bg()
            + MaterialTheme.outlined_input()
            + MaterialTheme.combo_box()
            + f"""
            QTabWidget::pane {{
                border: 1px solid {MaterialTheme.outline_variant};
                border-radius: {MaterialTheme.radius_md}px;
                background: {MaterialTheme.surface_container};
            }}
            QTabBar::tab {{
                padding: 8px 14px;
                color: {MaterialTheme.on_surface_variant};
            }}
            QTabBar::tab:selected {{
                color: {MaterialTheme.primary};
                font-weight: 700;
            }}
            QPushButton[checkRole="toggle"] {{
                background-color: {MaterialTheme.surface_container_high};
                color: {MaterialTheme.on_surface};
                border: 1px solid {MaterialTheme.outline_variant};
                border-radius: {MaterialTheme.radius_md}px;
                padding: 8px 12px;
                text-align: left;
                font-weight: 600;
            }}
            QPushButton[checkRole="toggle"]:hover {{
                background-color: {MaterialTheme.surface_variant};
                border: 1px solid {MaterialTheme.outline};
            }}
            QPushButton[checkRole="toggle"]:checked {{
                background-color: {MaterialTheme.secondary_container};
                color: {MaterialTheme.on_secondary_container};
                border: 1px solid {MaterialTheme.secondary};
            }}
            """
        )
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 22, 22, 22)
        root.setSpacing(14)

        header = QHBoxLayout()
        title_col = QVBoxLayout()
        title = QLabel("AI 与工具")
        title.setStyleSheet(
            f"color: {MaterialTheme.on_surface}; font-size: 20px; font-weight: 700;"
        )
        subtitle = QLabel("配置各阶段模型、工具调用策略和调试保护")
        subtitle.setStyleSheet(f"color: {MaterialTheme.on_surface_variant}; font-size: 12px;")
        title_col.addWidget(title)
        title_col.addWidget(subtitle)
        header.addLayout(title_col)
        header.addStretch()

        self.provider_btn = QPushButton("服务商...")
        self.provider_btn.setFixedHeight(34)
        self.provider_btn.setStyleSheet(MaterialTheme.outlined_button())
        self.provider_btn.clicked.connect(self._open_provider_dialog)
        header.addWidget(self.provider_btn)
        root.addLayout(header)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet(f"color: {MaterialTheme.error}; font-size: 12px;")
        self.status_label.setWordWrap(True)
        root.addWidget(self.status_label)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_models_tab(), "模型")
        self.tabs.addTab(self._build_tools_tab(), "工具策略")
        self.tabs.addTab(self._build_runtime_tab(), "运行")
        root.addWidget(self.tabs, 1)

        self.button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        self.button_box.accepted.connect(lambda: self._run_task(self._save()))
        self.button_box.rejected.connect(self.reject)
        save_btn = self.button_box.button(QDialogButtonBox.StandardButton.Save)
        cancel_btn = self.button_box.button(QDialogButtonBox.StandardButton.Cancel)
        if save_btn:
            save_btn.setText("保存")
            save_btn.setStyleSheet(MaterialTheme.filled_button())
        if cancel_btn:
            cancel_btn.setText("取消")
            cancel_btn.setStyleSheet(MaterialTheme.text_button())
        root.addWidget(self.button_box)

    def _build_panel(self) -> QWidget:
        panel = QWidget()
        panel.setStyleSheet(f"background: {MaterialTheme.surface_container}; border: none;")
        return panel

    def _build_models_tab(self) -> QWidget:
        panel = self._build_panel()
        form = QFormLayout(panel)
        form.setContentsMargins(18, 18, 18, 18)
        form.setSpacing(14)

        self.intent_combo = self._make_model_combo()
        self.tool_combo = self._make_model_combo()
        self.summary_combo = self._make_model_combo()
        self.proactive_combo = self._make_model_combo()

        form.addRow("意图判断模型:", self.intent_combo)
        form.addRow("工具规划模型:", self.tool_combo)
        form.addRow("总结压缩模型:", self.summary_combo)
        form.addRow("主动聊天模型:", self.proactive_combo)

        note = QLabel("留空时跟随主聊天模型。意图判断建议使用更快、更便宜、JSON 稳定的小模型。")
        note.setWordWrap(True)
        note.setStyleSheet(f"color: {MaterialTheme.on_surface_variant}; font-size: 12px;")
        form.addRow(note)
        return panel

    def _build_tools_tab(self) -> QWidget:
        panel = self._build_panel()
        form = QFormLayout(panel)
        form.setContentsMargins(18, 18, 18, 18)
        form.setSpacing(14)

        self.strategy_combo = QComboBox()
        self.strategy_combo.addItem("Two-phase 决策", "phase1_decision")
        self.strategy_combo.addItem("Legacy 全量工具提示", "legacy")

        self.context_combo = QComboBox()
        self.context_combo.addItem("直接给选中工具完整 schema", "direct_schema")
        self.context_combo.addItem("先 brief，再让模型拉 schema", "brief_schema")

        self.candidate_spin = QSpinBox()
        self.candidate_spin.setRange(1, 50)
        self.candidate_spin.setSingleStep(1)

        self.threshold_spin = QDoubleSpinBox()
        self.threshold_spin.setRange(0.0, 1.0)
        self.threshold_spin.setSingleStep(0.05)
        self.threshold_spin.setDecimals(2)

        self.loop_spin = QSpinBox()
        self.loop_spin.setRange(1, 10)
        self.loop_spin.setSingleStep(1)

        self.guard_check = self._make_toggle_button("阻止工具 JSON / DSML 闪到前端")
        self.debug_check = self._make_toggle_button("调试模式：保留更详细的工具事件")

        form.addRow("工具调用策略:", self.strategy_combo)
        form.addRow("工具上下文:", self.context_combo)
        form.addRow("候选工具数量:", self.candidate_spin)
        form.addRow("直聊置信阈值:", self.threshold_spin)
        form.addRow("最大工具循环:", self.loop_spin)
        form.addRow(self.guard_check)
        form.addRow(self.debug_check)
        return panel

    def _build_runtime_tab(self) -> QWidget:
        panel = self._build_panel()
        form = QFormLayout(panel)
        form.setContentsMargins(18, 18, 18, 18)
        form.setSpacing(14)

        self.tts_check = self._make_toggle_button("自动语音朗读")
        self.proactive_check = self._make_toggle_button("允许主动聊天")
        self.proactive_tts_check = self._make_toggle_button("主动聊天也朗读")

        form.addRow(self.tts_check)
        form.addRow(self.proactive_check)
        form.addRow(self.proactive_tts_check)
        return panel

    def _make_model_combo(self) -> QComboBox:
        combo = QComboBox()
        combo.setMinimumWidth(360)
        return combo

    def _make_toggle_button(self, label: str) -> QPushButton:
        button = QPushButton()
        button.setCheckable(True)
        button.setProperty("checkRole", "toggle")
        button.setMinimumHeight(38)

        def refresh(checked: bool) -> None:
            button.setText(f"√  {label}" if checked else f"   {label}")
            button.style().unpolish(button)
            button.style().polish(button)

        button.toggled.connect(refresh)
        refresh(False)
        return button

    def _run_task(self, coro: Any) -> asyncio.Task[Any]:
        task = asyncio.ensure_future(coro)
        self._pending_tasks.add(task)
        task.add_done_callback(self._pending_tasks.discard)
        return task

    async def load(self) -> None:
        self.status_label.setText("正在加载...")
        providers_data = await self.client.request("provider.list")
        settings = await self.client.request("system.get_settings")
        self._providers = providers_data.get("providers", [])
        self._settings = settings
        self._populate_model_combos()
        self._populate_values()
        self.status_label.setText("")

    def _populate_model_combos(self) -> None:
        combos = [self.intent_combo, self.tool_combo, self.summary_combo, self.proactive_combo]
        for combo in combos:
            combo.blockSignals(True)
            combo.clear()
            combo.addItem("跟随主聊天模型", {"provider_id": "", "model": ""})
            for provider in self._providers:
                pid = provider.get("id", "")
                pname = provider.get("name") or pid
                for model in provider.get("models", []):
                    combo.addItem(f"{pname} / {model}", {"provider_id": pid, "model": model})
            combo.blockSignals(False)

    def _set_model_combo(
        self, combo: QComboBox, provider_id: str | None, model: str | None
    ) -> None:
        for i in range(combo.count()):
            data = combo.itemData(i) or {}
            if data.get("provider_id") == (provider_id or "") and data.get("model") == (
                model or ""
            ):
                combo.setCurrentIndex(i)
                return
        combo.setCurrentIndex(0)

    def _populate_values(self) -> None:
        s = self._settings
        self._set_model_combo(self.intent_combo, s.get("intent_provider_id"), s.get("intent_model"))
        self._set_model_combo(self.tool_combo, s.get("tool_provider_id"), s.get("tool_model"))
        self._set_model_combo(
            self.summary_combo, s.get("summary_provider_id"), s.get("summary_model")
        )
        self._set_model_combo(
            self.proactive_combo, s.get("proactive_provider_id"), s.get("proactive_model")
        )
        self._set_combo_data(self.strategy_combo, s.get("tool_calling_strategy", "phase1_decision"))
        self._set_combo_data(self.context_combo, s.get("tool_context_mode", "direct_schema"))
        self.candidate_spin.setValue(int(s.get("phase1_max_candidate_tools", 8)))
        self.threshold_spin.setValue(float(s.get("phase1_direct_confidence_threshold", 0.55)))
        self.loop_spin.setValue(int(s.get("max_tool_loops", 5)))
        self.guard_check.setChecked(bool(s.get("enable_streaming_guard", True)))
        self.debug_check.setChecked(bool(s.get("tool_debug_events", False)))
        self.tts_check.setChecked(bool(s.get("tts_auto_play", False)))
        self.proactive_check.setChecked(bool(s.get("proactive_enabled", True)))
        self.proactive_tts_check.setChecked(bool(s.get("proactive_tts", True)))

    @staticmethod
    def _set_combo_data(combo: QComboBox, value: Any) -> None:
        for i in range(combo.count()):
            if combo.itemData(i) == value:
                combo.setCurrentIndex(i)
                return

    @staticmethod
    def _model_payload(combo: QComboBox) -> dict[str, str]:
        data = combo.currentData() or {}
        return {
            "provider_id": data.get("provider_id", ""),
            "model": data.get("model", ""),
        }

    async def _save(self) -> None:
        intent = self._model_payload(self.intent_combo)
        tool = self._model_payload(self.tool_combo)
        summary = self._model_payload(self.summary_combo)
        proactive = self._model_payload(self.proactive_combo)
        payload = {
            "intent_provider_id": intent["provider_id"],
            "intent_model": intent["model"],
            "tool_provider_id": tool["provider_id"],
            "tool_model": tool["model"],
            "summary_provider_id": summary["provider_id"],
            "summary_model": summary["model"],
            "proactive_provider_id": proactive["provider_id"],
            "proactive_model": proactive["model"],
            "tool_calling_strategy": self.strategy_combo.currentData(),
            "tool_context_mode": self.context_combo.currentData(),
            "phase1_max_candidate_tools": self.candidate_spin.value(),
            "phase1_direct_confidence_threshold": self.threshold_spin.value(),
            "max_tool_loops": self.loop_spin.value(),
            "enable_streaming_guard": self.guard_check.isChecked(),
            "tool_debug_events": self.debug_check.isChecked(),
            "tts_auto_play": self.tts_check.isChecked(),
            "proactive_enabled": self.proactive_check.isChecked(),
            "proactive_tts": self.proactive_tts_check.isChecked(),
        }
        try:
            resp = await self.client.request("system.update_settings", payload)
            if not resp.get("success"):
                QMessageBox.warning(self, "保存失败", "Gateway 没有接受这次设置。")
                return
            self.settings_changed.emit()
            self.accept()
        except Exception as exc:
            with contextlib.suppress(RuntimeError):
                QMessageBox.critical(self, "保存失败", str(exc))

    def _open_provider_dialog(self) -> None:
        if self._provider_dialog is not None:
            with contextlib.suppress(RuntimeError):
                self._provider_dialog.show()
                self._provider_dialog.raise_()
                self._provider_dialog.activateWindow()
                return
            self._provider_dialog = None

        dialog = ProviderDialog(self.client, self)
        self._provider_dialog = dialog
        dialog.finished.connect(lambda: setattr(self, "_provider_dialog", None))
        dialog.providers_changed.connect(self._on_providers_changed)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
        self._run_task(dialog._load_providers())

    def _on_providers_changed(self) -> None:
        self.providers_changed.emit()
        self._run_task(self.load())

    def closeEvent(self, event: Any) -> None:
        for task in list(self._pending_tasks):
            if not task.done():
                task.cancel()
        self._pending_tasks.clear()
        event.accept()
