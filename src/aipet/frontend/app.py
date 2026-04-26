"""PyQt Frontend entry point using qasync."""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication
from qasync import QEventLoop

from aipet.frontend.client import GatewayClient
from aipet.frontend.pet_window import PetWindow
from aipet.utils.log import configure_logging, setup_exception_logging


def main() -> int:
    """Run the PyQt Frontend with asyncio integration."""
    configure_logging("INFO", log_to_file=True, log_to_console=True, log_file_name="frontend.log")
    setup_exception_logging("frontend")

    app = QApplication(sys.argv)
    app.setApplicationName("live2dagent")
    app.setApplicationVersion("2.0.0a1")
    app.setQuitOnLastWindowClosed(False)

    # Wrap QApplication.notify to catch Qt event-loop exceptions
    _original_notify = app.notify

    def _notify(obj: object, event: object) -> bool:
        try:
            return _original_notify(obj, event)
        except Exception:
            logging.getLogger("aipet.frontend.qt").exception(
                "Unhandled Qt exception in notify(%r, %r)", obj, event
            )
            raise

    app.notify = _notify  # type: ignore[method-assign]

    loop = QEventLoop(app)
    asyncio.set_event_loop(loop)

    # Install asyncio exception handler for qasync loop
    loop.set_exception_handler(_qasync_exception_handler)

    client = GatewayClient()
    pet = PetWindow(client)
    pet.show()

    async def _connect() -> None:
        try:
            await client.connect()
            if not client.connected:
                pet.setWindowTitle("live2dagent - Connecting")
                pet.show_status_message(
                    "正在连接 Gateway，连接成功后会自动恢复。",
                    is_error=True,
                )
                return
            # Notify Gateway of the currently loaded Live2D model
            model_path = pet.live2d_widget.model_path
            model_name = Path(model_path).parent.name if model_path else ""
            if model_name:
                await client.send(
                    {
                        "type": "request",
                        "method": "live2d.set_model",
                        "payload": {"model_name": model_name},
                    }
                )
            else:
                logger = logging.getLogger("aipet.frontend.app")
                logger.warning("No Live2D model loaded; skipping live2d.set_model")
                pet.show_status_message(
                    "未找到可加载的 Live2D 模型，\n"
                    "请确认 live2dmodels/ 目录包含 .model3.json 或 .vtube.json 文件。",
                    is_error=True,
                )
            pet.refresh_runtime_settings()
        except Exception as exc:
            logger = logging.getLogger("aipet.frontend.app")
            logger.error("Could not connect to Gateway at %s: %s", client.uri, exc)
            # Show non-blocking error message via the pet bubble and window title.
            pet.setWindowTitle(f"live2dagent — Connection Failed ({exc})")
            pet.show_status_message(
                f"无法连接 Gateway：{exc}\n请确认后台服务已启动，或稍后自动重连。",
                is_error=True,
            )

    with loop:
        loop.run_until_complete(_connect())
        loop.run_forever()

    return 0


def _qasync_exception_handler(loop: asyncio.AbstractEventLoop, context: dict[str, object]) -> None:
    """Custom exception handler for qasync event loop."""
    logger = logging.getLogger("aipet.frontend.asyncio")
    message = context.get("message", "Unknown asyncio error")
    exception = context.get("exception")
    if exception is not None:
        logger.error("qasync exception: %s | exc=%s", message, exception, exc_info=exception)
    else:
        logger.error("qasync error: %s | context=%s", message, context)
    # Call default handler so it still prints to stderr
    loop.default_exception_handler(context)
