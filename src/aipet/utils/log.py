"""Structured logging configuration for live2dagent."""

from __future__ import annotations

import asyncio
import logging
import logging.handlers
import sys
import threading
import traceback
from typing import Any

import structlog

from aipet.utils.paths import get_user_data_dir

# Keep a reference to the file handler so exception hooks can use it
_current_file_handler: logging.FileHandler | None = None
_CRASH_REPORT_TEMPLATE = (
    "\n========== UNCAUGHT EXCEPTION ==========\n%s\n========================================"
)


def configure_logging(
    log_level: str = "INFO",
    *,
    log_to_file: bool = True,
    log_to_console: bool = True,
    log_file_name: str | None = None,
) -> None:
    """Configure structured logging for Gateway or Frontend.

    Args:
        log_level: One of DEBUG, INFO, WARNING, ERROR, CRITICAL.
        log_to_file: Whether to write rotating log files to user data dir.
        log_to_console: Whether to emit colored logs to stderr.
        log_file_name: Custom log file name (default: aipet.log).
    """
    global _current_file_handler
    level = getattr(logging, log_level.upper(), logging.INFO)

    # Standard library root logger setup
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()
    _current_file_handler = None

    if log_to_file:
        log_dir = get_user_data_dir() / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        filename = log_file_name or "aipet.log"
        file_handler = logging.handlers.TimedRotatingFileHandler(
            log_dir / filename,
            when="midnight",
            backupCount=7,
            encoding="utf-8",
        )
        file_handler.setFormatter(
            logging.Formatter(
                "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        root.addHandler(file_handler)
        _current_file_handler = file_handler

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


def _get_fallback_logger() -> logging.Logger:
    """Return a logger that writes to stderr when file handler is unavailable."""
    logger = logging.getLogger("aipet.uncaught")
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter("%(asctime)s [UNCAUGHT] %(message)s"))
        logger.addHandler(handler)
    return logger


def _write_crash_report(exc_type: type, exc_value: BaseException, tb: Any) -> None:
    """Write a formatted crash report to the current log file."""
    lines = traceback.format_exception(exc_type, exc_value, tb)
    report = "".join(lines)
    if _current_file_handler is not None:
        # Use the file handler directly to bypass any filtering
        record = logging.LogRecord(
            name="aipet.crash",
            level=logging.ERROR,
            pathname="",
            lineno=0,
            msg=_CRASH_REPORT_TEMPLATE,
            args=(report,),
            exc_info=(exc_type, exc_value, tb),
        )
        _current_file_handler.emit(record)
        _current_file_handler.flush()
    else:
        _get_fallback_logger().error(_CRASH_REPORT_TEMPLATE, report)


def setup_exception_logging(component: str = "aipet") -> None:
    """Install global exception hooks so *all* unhandled errors go to the log file.

    Covers:
    - sync code (sys.excepthook)
    - asyncio tasks (asyncio exception handler)
    - background threads (threading.excepthook)
    """
    # 1. Sync exceptions
    original_excepthook = sys.excepthook

    def _sync_excepthook(exc_type: type, exc_value: BaseException, tb: Any) -> None:
        _write_crash_report(exc_type, exc_value, tb)
        original_excepthook(exc_type, exc_value, tb)

    sys.excepthook = _sync_excepthook

    # 2. Asyncio task exceptions
    def _asyncio_exception_handler(
        loop: asyncio.AbstractEventLoop, context: dict[str, Any]
    ) -> None:
        message = context.get("message", "Asyncio error")
        exception = context.get("exception")
        task = context.get("task")
        if exception is not None:
            _write_crash_report(type(exception), exception, exception.__traceback__)
        else:
            logger = _get_fallback_logger()
            logger.error("Asyncio error: %s | task=%s | context=%s", message, task, context)
        # Also call default handler so it still prints to stderr
        loop.default_exception_handler(context)

    try:
        loop = asyncio.get_running_loop()
        loop.set_exception_handler(_asyncio_exception_handler)
    except RuntimeError:
        pass  # No loop running yet; caller should set it up after loop starts

    # 3. Threading exceptions
    original_threading_excepthook = threading.excepthook

    def _threading_excepthook(args: threading.ExceptHookArgs) -> None:
        if args.exc_type is not None and args.exc_value is not None:
            _write_crash_report(args.exc_type, args.exc_value, args.exc_traceback)
        if original_threading_excepthook is not None:
            original_threading_excepthook(args)

    threading.excepthook = _threading_excepthook

    # 4. asyncio create_task wrapper: auto-log exceptions from fire-and-forget tasks
    original_create_task = asyncio.create_task

    def _logging_create_task(coro: Any, *, name: str | None = None) -> asyncio.Task[Any]:
        task = original_create_task(coro, name=name)

        def _on_task_done(t: asyncio.Task[Any]) -> None:
            if not t.done():
                return
            if t.cancelled():
                return
            try:
                exc = t.exception()
            except asyncio.CancelledError:
                return
            if exc is not None and not isinstance(exc, asyncio.CancelledError):
                logger = _get_fallback_logger()
                logger.error(
                    "Fire-and-forget task '%s' raised %s: %s",
                    name or t.get_name(),
                    type(exc).__name__,
                    exc,
                    exc_info=exc,
                )

        task.add_done_callback(_on_task_done)
        return task

    # Monkey-patch only in this process
    asyncio.create_task = _logging_create_task  # type: ignore[assignment]

    _get_fallback_logger().info("Exception logging installed for %s", component)
