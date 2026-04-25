"""Async audio player with queue management."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

import sounddevice as sd
import soundfile as sf

from aipet.gateway.media.lipsync import analyze_lipsync

_logger = logging.getLogger("aipet.gateway.media.audio_player")


class AudioPlayer:
    """Manage audio playback queue."""

    def __init__(
        self,
        on_start: Callable[[str, str, list[tuple[float, float]] | None], Any] | None = None,
        on_end: Callable[[], Any] | None = None,
        on_error: Callable[[str], Any] | None = None,
    ) -> None:
        self._queue: asyncio.Queue[tuple[str, str, list[tuple[float, float]] | None]] = (
            asyncio.Queue(maxsize=20)
        )
        self._running = False
        self._task: asyncio.Task[Any] | None = None
        self._current_playback: sd.CallbackStop | None = None
        self.on_start = on_start
        self.on_end = on_end
        self.on_error = on_error

    async def _put_with_eviction(
        self, item: tuple[str, str, list[tuple[float, float]] | None]
    ) -> None:
        """Add item to queue, evicting oldest if full."""
        if self._queue.full():
            with contextlib.suppress(asyncio.QueueEmpty):
                self._queue.get_nowait()
        await self._queue.put(item)

    def start(self) -> None:
        """Start the playback worker."""
        if not self._running:
            self._running = True
            self._task = asyncio.create_task(self._worker())

    async def stop(self) -> None:
        """Stop the playback worker."""
        self._running = False
        self.skip_current()
        with contextlib.suppress(asyncio.QueueFull):
            self._queue.put_nowait(("", "", None))  # sentinel to wake worker
        if self._task:
            await self._task

    def skip_current(self) -> None:
        """Stop the currently playing audio (if any)."""
        with contextlib.suppress(Exception):
            sd.stop()

    def clear_queue(self) -> None:
        """Remove all pending items from the queue."""
        while not self._queue.empty():
            with contextlib.suppress(asyncio.QueueEmpty):
                self._queue.get_nowait()

    async def enqueue(
        self,
        audio_path: str,
        text: str = "",
        lipsync_data: list[tuple[float, float]] | None = None,
    ) -> None:
        """Add an audio file to the queue.

        If lipsync_data is not provided, the audio file will be analyzed
        automatically to generate a lip-sync envelope.
        """
        if lipsync_data is None:
            lipsync_data = analyze_lipsync(audio_path)
        await self._put_with_eviction((audio_path, text, lipsync_data))

    async def _worker(self) -> None:
        """Process the queue."""
        while self._running:
            path, text, lipsync_data = await self._queue.get()
            if not path or not Path(path).exists():
                continue
            if self.on_start:
                self.on_start(path, text, lipsync_data)
            success = await asyncio.to_thread(self._play_sync, path)
            if self.on_end:
                self.on_end()
            if not success and self.on_error:
                self.on_error(f"Failed to play audio: {path}")

    def _play_sync(self, path: str) -> bool:
        """Blocking audio playback. Returns True on success."""
        try:
            data, samplerate = sf.read(path, dtype="float32")
            sd.play(data, samplerate)
            sd.wait()
            return True
        except Exception:
            _logger.exception("Audio playback error")
            return False
