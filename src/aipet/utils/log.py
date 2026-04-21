"""Structured logging configuration for AIPet."""

from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path

import structlog

from aipet.utils.paths import get_user_data_dir


def configure_logging(
    log_level: str = "INFO",
    *,
    log_to_file: bool = True,
    log_to_console: bool = True,
) -> None:
    """Configure structured logging for Gateway or Frontend.

    Args:
        log_level: One of DEBUG, INFO, WARNING, ERROR, CRITICAL.
        log_to_file: Whether to write rotating log files to user data dir.
        log_to_console: Whether to emit colored logs to stderr.
    """
    level = getattr(logging, log_level.upper(), logging.INFO)

    # Standard library root logger setup
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()

    if log_to_file:
        log_dir = get_user_data_dir() / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.TimedRotatingFileHandler(
            log_dir / "aipet.log",
            when="midnight",
            backupCount=7,
            encoding="utf-8",
        )
        file_handler.setFormatter(logging.Formatter("%(message)s"))
        root.addHandler(file_handler)

    if log_to_console:
        console_handler = logging.StreamHandler(sys.stderr)
        console_handler.setFormatter(logging.Formatter("%(message)s"))
        root.addHandler(console_handler)

    # Structlog processors
    shared_processors: list[structlog.types.Processor] = [
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
    ]

    if sys.stderr is sys.__stderr__ and log_to_console:
        # Development: pretty console
        console_renderer: structlog.types.Processor = structlog.dev.ConsoleRenderer(
            colors=sys.platform != "win32" or sys.stdout.isatty()
        )
    else:
        # Production / file: JSON
        console_renderer = structlog.processors.JSONRenderer()

    structlog.configure(
        processors=shared_processors + [console_renderer],
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # Silence overly chatty third-party loggers
    logging.getLogger("websockets").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
