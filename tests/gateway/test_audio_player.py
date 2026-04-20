"""Tests for AudioPlayer."""

import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest

from aipet.gateway.media.audio_player import AudioPlayer


@pytest.mark.asyncio
async def test_audio_player_enqueue_and_callbacks(tmp_path: Path) -> None:
    started: list[tuple[str, str, list[tuple[float, float]] | None]] = []
    ended: list[None] = []

    def on_start(path: str, text: str, lipsync_data: list[tuple[float, float]] | None) -> None:
        started.append((path, text, lipsync_data))

    def on_end() -> None:
        ended.append(None)

    player = AudioPlayer(on_start=on_start, on_end=on_end)
    player.start()

    fake_file = tmp_path / "test.mp3"
    fake_file.write_text("fake")

    with patch.object(player, "_play_sync"):
        await player.enqueue(str(fake_file), "hello")
        await asyncio.sleep(0.1)

    assert len(started) == 1
    assert started[0][0] == str(fake_file)
    assert started[0][1] == "hello"
    assert started[0][2] is not None  # lipsync_data should be present (even if empty list)
    assert len(ended) == 1

    await player.stop()
