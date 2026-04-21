"""PyQt Frontend entry point using qasync."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication
from qasync import QEventLoop

from aipet.frontend.client import GatewayClient
from aipet.frontend.pet_window import PetWindow
from aipet.utils.log import configure_logging


def main() -> int:
    """Run the PyQt Frontend with asyncio integration."""
    configure_logging("INFO", log_to_file=True, log_to_console=True)
    app = QApplication(sys.argv)
    app.setApplicationName("AIPet")
    app.setApplicationVersion("2.0.0a1")
    app.setQuitOnLastWindowClosed(False)

    loop = QEventLoop(app)
    asyncio.set_event_loop(loop)

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
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.critical(
                pet,
                "Connection Failed",
                f"Could not connect to Gateway at {client.uri}:\n{exc}\n\n"
                "Please start the Gateway first (start_gateway.bat) and restart the frontend.",
            )

    with loop:
        loop.run_until_complete(_connect())
        loop.run_forever()

    return 0
