"""Async audio player with queue management."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable
from pathlib import Path
from typing import Any

import sounddevice as sd
import soundfile as sf

from aipet.gateway.media.lipsync import analyze_lipsync


class AudioPlayer:
    """Manage audio playback queue."""

    def __init__(
        self,
        on_start: Callable[[str, str, list[tuple[float, float]] | None], Any] | None = None,
        on_end: Callable[[], Any] | None = None,
    ) -> None:
        self._queue: asyncio.Queue[tuple[str, str, list[tuple[float, float]] | None]] = asyncio.Queue(maxsize=20)
        self._running = False
        self._task: asyncio.Task[Any] | None = None
        self.on_start = on_start
        self.on_end = on_end

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
        with contextlib.suppress(asyncio.QueueFull):
            self._queue.put_nowait(("", "", None))  # sentinel to wake worker
        if self._task:
            await self._task

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
            await asyncio.to_thread(self._play_sync, path)
            if self.on_end:
                self.on_end()

    def _play_sync(self, path: str) -> None:
        """Blocking audio playback."""
        try:
            data, samplerate = sf.read(path, dtype="float32")
            sd.play(data, samplerate)
            sd.wait()
        except Exception as exc:
            _logger.exception("Audio playback error")
