"""Tests for TTS providers."""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from aipet.gateway.providers.tts_edge import EdgeTTSProvider


@pytest.mark.asyncio
async def test_edge_tts_synthesize_caches_file(tmp_path) -> None:
    provider = EdgeTTSProvider(voice="zh-CN-XiaoxiaoNeural")

    with (
        patch("aipet.gateway.providers.tts_edge.get_user_data_dir", return_value=tmp_path),
        patch("edge_tts.Communicate") as MockCommunicate,
    ):
        mock_comm = AsyncMock()

        async def _save(path: str) -> None:
            Path(path).write_bytes(b"fake")

        mock_comm.save.side_effect = _save
        MockCommunicate.return_value = mock_comm

        path1 = await provider.synthesize("hello")
        assert path1.endswith(".mp3")
        mock_comm.save.assert_awaited_once()

        # Second call with same text should use cache
        mock_comm.save.reset_mock()
        path2 = await provider.synthesize("hello")
        assert path1 == path2
        mock_comm.save.assert_not_awaited()
