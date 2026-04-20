"""TTS Provider abstraction layer."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class TTSProvider(Protocol):
    """Abstract interface for text-to-speech providers."""

    @property
    def name(self) -> str:
        """Human-readable provider name."""
        ...

    async def synthesize(self, text: str, voice_id: str | None = None) -> str:
        """Synthesize text into an audio file and return the local file path."""
        ...
