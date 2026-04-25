"""Microsoft Edge TTS provider."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from pathlib import Path

from aipet.utils.paths import get_user_data_dir

_logger = logging.getLogger("aipet.gateway.providers.tts_edge")


class EdgeTTSProvider:
    """Microsoft Edge TTS (free, online)."""

    def __init__(self, voice: str = "zh-CN-XiaoxiaoNeural") -> None:
        self._voice = voice

    @property
    def name(self) -> str:
        return "Edge TTS"

    async def synthesize(self, text: str, voice_id: str | None = None) -> str:
        """Synthesize text to an MP3 file and return the path.

        Retries up to 2 times on network failures.
        """
        import edge_tts

        voice = voice_id or self._voice
        output_dir = get_user_data_dir() / "cache" / "tts"
        output_dir.mkdir(parents=True, exist_ok=True)
        file_hash = hashlib.md5(f"{text}:{voice}".encode()).hexdigest()
        output_path = output_dir / f"{file_hash}.mp3"
        if output_path.exists():
            return str(output_path)

        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                communicate = edge_tts.Communicate(text, voice)
                await communicate.save(str(output_path))
                self._prune_cache(output_dir)
                return str(output_path)
            except Exception as exc:
                last_exc = exc
                _logger.warning(
                    "Edge TTS synthesis failed (attempt %d/%d)",
                    attempt + 1,
                    3,
                    exc_info=exc if attempt == 2 else False,
                )
                if attempt < 2:
                    await asyncio.sleep(1.5 * (attempt + 1))

        raise RuntimeError(f"Edge TTS failed after 3 attempts: {last_exc}") from last_exc

    def _prune_cache(self, output_dir: Path) -> None:
        """Remove oldest cache files if total size exceeds 500MB."""
        try:
            files = sorted(output_dir.glob("*.mp3"), key=lambda f: f.stat().st_mtime)
            total_size = sum(f.stat().st_size for f in files)
            max_size = 500 * 1024 * 1024  # 500MB
            while total_size > max_size and files:
                oldest = files.pop(0)
                total_size -= oldest.stat().st_size
                oldest.unlink(missing_ok=True)
        except Exception:
            pass
