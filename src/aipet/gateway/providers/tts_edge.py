"""Microsoft Edge TTS provider."""

from __future__ import annotations

import hashlib
from pathlib import Path

import edge_tts

from aipet.utils.paths import get_user_data_dir


class EdgeTTSProvider:
    """Microsoft Edge TTS (free, online)."""

    def __init__(self, voice: str = "zh-CN-XiaoxiaoNeural") -> None:
        self._voice = voice

    @property
    def name(self) -> str:
        return "Edge TTS"

    async def synthesize(self, text: str, voice_id: str | None = None) -> str:
        """Synthesize text to an MP3 file and return the path."""
        voice = voice_id or self._voice
        output_dir = get_user_data_dir() / "cache" / "tts"
        output_dir.mkdir(parents=True, exist_ok=True)
        file_hash = hashlib.md5(f"{text}:{voice}".encode()).hexdigest()
        output_path = output_dir / f"{file_hash}.mp3"
        if output_path.exists():
            return str(output_path)
        communicate = edge_tts.Communicate(text, voice)
        await communicate.save(str(output_path))
        self._prune_cache(output_dir)
        return str(output_path)

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
