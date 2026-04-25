"""Provider management dialog for adding, editing, and removing AI providers."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from aipet.frontend.client import GatewayClient
from aipet.frontend.theme import MaterialTheme


class ProviderDialog(QDialog):
    """Dialog to manage AI providers (add / edit / remove)."""

    providers_changed = Signal()
    _logger = logging.getLogger(__name__)

    def __init__(self, client: GatewayClient, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.client = client
        self._providers: list[dict[str, Any]] = []
        self._current_id: str | None = None
        self._pending_tasks: set[asyncio.Task[None]] = set()

        self.setWindowTitle("模型服务商设置")
        self.setMinimumSize(620, 460)
        self._setup_ui()

    def _setup_ui(self) -> None:
        self.setStyleSheet(MaterialTheme.dialog_bg())
        layout = QHBoxLayout(self)
        layout.setContentsMargins(22, 22, 22, 22)
        layout.setSpacing(18)

        # Left: provider list
        left = QVBoxLayout()
        left.setSpacing(12)

        providers_title = QLabel("服务商")
        providers_title.setStyleSheet(
            f"color: {MaterialTheme.on_surface}; font-size: 16px; font-weight: 700; "
            "border: none; background: transparent;"
        )
        left.addWidget(providers_title)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet(f"color: {MaterialTheme.error}; font-size: 12px;")
        self.status_label.setWordWrap(True)
        left.addWidget(self.status_label)

        self.list_widget = QListWidget()
        self.list_widget.setStyleSheet(MaterialTheme.list_widget_card())
        self.list_widget.currentItemChanged.connect(self._on_selection_changed)
        left.addWidget(self.list_widget)

        btn_layout = QHBoxLayout()
        self.refresh_btn = QPushButton("刷")
        self.refresh_btn.setFixedSize(34, 34)
        self.refresh_btn.setToolTip("刷新服务商")
        self.refresh_btn.setStyleSheet(MaterialTheme.icon_button(size=34))
        self.refresh_btn.clicked.connect(lambda: self._run_task(self._load_providers()))

        self.add_btn = QPushButton("+ 添加")
        self.add_btn.setFixedHeight(34)
        self.add_btn.setStyleSheet(MaterialTheme.filled_button())
        self.add_btn.clicked.connect(self._on_add)

        self.delete_btn = QPushButton("删除")
        self.delete_btn.setFixedHeight(34)
        self.delete_btn.setStyleSheet(MaterialTheme.outlined_button(text_color=MaterialTheme.error))
        self.delete_btn.setEnabled(False)
        self.delete_btn.clicked.connect(lambda: self._run_task(self._on_delete()))

        btn_layout.addWidget(self.refresh_btn)
        btn_layout.addWidget(self.add_btn)
        btn_layout.addWidget(self.delete_btn)
        left.addLayout(btn_layout)

        layout.addLayout(left, 1)

        # Right: edit form
        right = QVBoxLayout()
        right.setSpacing(14)

        detail_title = QLabel("配置详情")
        detail_title.setStyleSheet(
            f"color: {MaterialTheme.on_surface}; font-size: 16px; font-weight: 700; "
            "border: none; background: transparent;"
        )
        right.addWidget(detail_title)

        self.form_container = QWidget()
        self.form_container.setObjectName("providerForm")
        self.form_container.setStyleSheet(
            f"QWidget#providerForm {{ background-color: {MaterialTheme.surface_container}; "
            f"border: 1px solid {MaterialTheme.outline_variant}; border-radius: {MaterialTheme.radius_lg}px; }}"
            + MaterialTheme.outlined_input()
        )
        form = QFormLayout(self.form_container)
        form.setContentsMargins(16, 16, 16, 16)
        form.setSpacing(12)

        self.id_edit = QLineEdit()
        self.id_edit.setPlaceholderText("唯一标识，例如 openai-main")
        form.addRow("ID:", self.id_edit)

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("显示名称")
        form.addRow("名称:", self.name_edit)

        self.type_combo = QComboBox()
        self.type_combo.addItems(["openai", "gemini", "anthropic", "ollama", "echo"])
        form.addRow("类型:", self.type_combo)

        self.key_edit = QLineEdit()
        self.key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_edit.setPlaceholderText("可选 API Key")
        form.addRow("API Key:", self.key_edit)

        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("https://api.example.com/v1")
        form.addRow("接口地址:", self.url_edit)

        self.models_edit = QLineEdit()
        self.models_edit.setPlaceholderText("model-1, model-2")
        form.addRow("模型:", self.models_edit)

        self.is_custom_label = QLabel("")
        self.is_custom_label.setStyleSheet(
            f"color: {MaterialTheme.outline_variant}; font-size: 12px;"
        )
        form.addRow(self.is_custom_label)

        right.addWidget(self.form_container)
        right.addStretch()

        self.button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        self.button_box.accepted.connect(lambda: self._run_task(self._on_save()))
        self.button_box.rejected.connect(self.reject)
        right.addWidget(self.button_box)

        save_btn = self.button_box.button(QDialogButtonBox.StandardButton.Save)
        cancel_btn = self.button_box.button(QDialogButtonBox.StandardButton.Cancel)
        if save_btn:
            save_btn.setText("保存")
            save_btn.setStyleSheet(MaterialTheme.filled_button())
        if cancel_btn:
            cancel_btn.setText("取消")
            cancel_btn.setStyleSheet(
                MaterialTheme.text_button(text_color=MaterialTheme.on_surface_variant)
            )

        layout.addLayout(right, 2)

    def _run_task(self, coro: Any) -> asyncio.Task[None]:
        """Start a fire-and-forget task and track it for cleanup."""
        task: asyncio.Task[None] = asyncio.ensure_future(coro)
        self._pending_tasks.add(task)
        task.add_done_callback(self._pending_tasks.discard)
        return task

    def _safe_set_status(self, text: str) -> None:
        """Set status label only if widget is still alive."""
        with contextlib.suppress(RuntimeError):
            self.status_label.setText(text)

    async def _load_providers(self) -> bool:
        """Load providers from Gateway."""
        self._safe_set_status("正在加载...")
        try:
            data = await self.client.request("provider.list")
            self._providers = data.get("providers", [])
            self._populate_list()
            self._safe_set_status("")
            return True
        except TimeoutError:
            self._logger.warning("Failed to load providers: TimeoutError")
            self._safe_set_status(
                "Gateway 连接超时。你仍然可以手动添加服务商。"
            )
            return False
        except Exception as exc:
            self._logger.warning("Failed to load providers: %s", exc)
            self._safe_set_status(
                f"加载服务商失败：{exc}\n你仍然可以手动添加服务商。"
            )
            return False

    def _populate_list(self) -> None:
        try:
            self.list_widget.blockSignals(True)
            self.list_widget.clear()
            for p in self._providers:
                item = QListWidgetItem(f"{p.get('name', 'Unnamed')} ({p.get('id', '')})")
                item.setData(Qt.ItemDataRole.UserRole, p.get("id", ""))
                self.list_widget.addItem(item)
            self.list_widget.blockSignals(False)
            if self.list_widget.count() > 0:
                self.list_widget.setCurrentRow(0)
            else:
                self._clear_form()
        except RuntimeError:
            pass

    def closeEvent(self, event: Any) -> None:
        for task in list(self._pending_tasks):
            if not task.done():
                task.cancel()
        self._pending_tasks.clear()
        event.accept()

    def _on_selection_changed(
        self, current: QListWidgetItem | None, _previous: QListWidgetItem | None
    ) -> None:
        if current is None:
            self._clear_form()
            return
        pid = current.data(Qt.ItemDataRole.UserRole)
        provider = next((p for p in self._providers if p.get("id") == pid), None)
        if provider:
            self._populate_form(provider)

    def _populate_form(self, provider: dict[str, Any]) -> None:
        self._current_id = provider.get("id")
        self.id_edit.setText(provider.get("id", ""))
        self.id_edit.setEnabled(False)
        self.name_edit.setText(provider.get("name", ""))
        idx = self.type_combo.findText(provider.get("type", "openai"))
        if idx >= 0:
            self.type_combo.setCurrentIndex(idx)
        self.key_edit.setText(provider.get("api_key", "") or "")
        self.url_edit.setText(provider.get("base_url", "") or "")
        models = provider.get("models", [])
        self.models_edit.setText(", ".join(models) if isinstance(models, list) else str(models))
        is_custom = provider.get("is_custom", False)
        self.is_custom_label.setText("自定义服务商" if is_custom else "内置服务商")
        self.delete_btn.setEnabled(True)

    def _clear_form(self) -> None:
        self._current_id = None
        self.id_edit.clear()
        self.id_edit.setEnabled(True)
        self.name_edit.clear()
        self.type_combo.setCurrentIndex(0)
        self.key_edit.clear()
        self.url_edit.clear()
        self.models_edit.clear()
        self.is_custom_label.setText("")
        self.delete_btn.setEnabled(False)

    def _on_add(self) -> None:
        self.list_widget.clearSelection()
        self._clear_form()
        self.id_edit.setFocus()

    async def _on_delete(self) -> None:
        pid = self._current_id
        if not pid:
            return
        reply = QMessageBox.question(
            self,
            "删除服务商",
            f"确定要删除服务商“{pid}”吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            resp = await self.client.request("provider.remove", {"provider_id": pid})
            if resp.get("success"):
                await self._load_providers()
                self.providers_changed.emit()
            else:
                QMessageBox.warning(self, "删除失败", "无法删除该服务商。")
        except TimeoutError:
            with contextlib.suppress(RuntimeError):
                QMessageBox.critical(self, "错误", "删除失败：Gateway 连接超时。")
        except Exception as exc:
            with contextlib.suppress(RuntimeError):
                QMessageBox.critical(self, "错误", f"删除失败：{exc}")

    async def _on_save(self) -> None:
        pid = self.id_edit.text().strip()
        name = self.name_edit.text().strip()
        if not pid or not name:
            QMessageBox.warning(self, "信息不完整", "ID 和名称不能为空。")
            return

        ptype = self.type_combo.currentText()
        api_key = self.key_edit.text().strip() or None
        base_url = self.url_edit.text().strip() or None
        models_raw = self.models_edit.text().strip()
        models = [m.strip() for m in models_raw.split(",") if m.strip()]

        if not models:
            QMessageBox.warning(self, "信息不完整", "至少需要填写一个模型。")
            return

        is_new = self._current_id is None
        existing = next((p for p in self._providers if p.get("id") == pid), None)
        is_custom = True if is_new else (existing.get("is_custom", False) if existing else True)

        provider_payload: dict[str, Any] = {
            "id": pid,
            "name": name,
            "type": ptype,
            "models": models,
            "is_custom": is_custom,
            "api_key": api_key,
            "base_url": base_url,
        }

        try:
            if is_new:
                resp = await self.client.request("provider.add", {"provider": provider_payload})
            else:
                # Only send changed fields for update, but for simplicity send all
                resp = await self.client.request(
                    "provider.update",
                    {"provider_id": self._current_id, "updates": provider_payload},
                )
            if resp.get("success"):
                await self._load_providers()
                self.providers_changed.emit()
                # Reselect the saved item
                for i in range(self.list_widget.count()):
                    item = self.list_widget.item(i)
                    if item and item.data(Qt.ItemDataRole.UserRole) == pid:
                        self.list_widget.setCurrentItem(item)
                        break
            else:
                QMessageBox.warning(
                    self, "保存失败", "无法保存服务商，ID 可能已经存在。"
                )
        except TimeoutError:
            with contextlib.suppress(RuntimeError):
                QMessageBox.critical(self, "错误", "保存失败：Gateway 连接超时。")
        except Exception as exc:
            with contextlib.suppress(RuntimeError):
                QMessageBox.critical(self, "错误", f"保存失败：{exc}")
