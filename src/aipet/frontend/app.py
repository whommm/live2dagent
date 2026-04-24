"""PyQt Frontend entry point using qasync."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from PySide6.QtCore import Qt
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
    app.setApplicationName("AIPet")
    app.setApplicationVersion("2.0.0a1")
    app.setQuitOnLastWindowClosed(False)

    # Wrap QApplication.notify to catch Qt event-loop exceptions
    _original_notify = app.notify

    def _notify(obj: object, event: object) -> bool:
        try:
            return _original_notify(obj, event)
        except Exception:
            import logging
            logging.getLogger("aipet.frontend.qt").exception("Unhandled Qt exception in notify(%r, %r)", obj, event)
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
            # Notify Gateway of the currently loaded Live2D model
            model_name = Path(pet.live2d_widget.model_path).parent.name
            await client.send({
                "type": "request",
                "method": "live2d.set_model",
                "payload": {"model_name": model_name},
            })
        except Exception as exc:
            import logging
            logger = logging.getLogger("aipet.frontend.app")
            logger.error("Could not connect to Gateway at %s: %s", client.uri, exc)
            # Show non-blocking error message via pet window title
            pet.setWindowTitle(f"AIPet — Connection Failed ({exc})")

    with loop:
        loop.run_until_complete(_connect())
        loop.run_forever()

    return 0


def _qasync_exception_handler(loop: asyncio.AbstractEventLoop, context: dict[str, object]) -> None:
    """Custom exception handler for qasync event loop."""
    import logging
    logger = logging.getLogger("aipet.frontend.asyncio")
    message = context.get("message", "Unknown asyncio error")
    exception = context.get("exception")
    if exception is not None:
        logger.error("qasync exception: %s | exc=%s", message, exception, exc_info=exception)
    else:
        logger.error("qasync error: %s | context=%s", message, context)
    # Call default handler so it still prints to stderr
    loop.default_exception_handler(context)
