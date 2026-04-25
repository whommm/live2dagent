"""Tests for lip-sync audio analysis."""

from pathlib import Path

import pytest

from aipet.gateway.media.lipsync import analyze_lipsync, dummy_lipsync


def test_analyze_lipsync_missing_file() -> None:
    result = analyze_lipsync("/nonexistent/audio.mp3")
    assert result == []


def test_dummy_lipsync_returns_envelope() -> None:
    result = dummy_lipsync(2.0, points=10)
    assert len(result) == 10
    assert result[0][0] == 0.0
    assert result[-1][0] == pytest.approx(2.0, abs=0.1)
    for _t, v in result:
        assert 0.0 <= v <= 1.0


def test_analyze_lipsync_on_silent_wav(tmp_path: Path) -> None:
    """Create a silent WAV file and verify analysis returns low values."""
    try:
        import numpy as np
        import soundfile as sf
    except ImportError:
        pytest.skip("soundfile/numpy not available")

    path = tmp_path / "silent.wav"
    sf.write(path, np.zeros(16000, dtype="float32"), 16000)

    result = analyze_lipsync(str(path), window_ms=50, step_ms=50)
    assert len(result) > 0
    # Silent audio should have very low values
    assert all(v < 0.1 for _t, v in result)


def test_analyze_lipsync_on_loud_wav(tmp_path: Path) -> None:
    """Create a loud sine wave and verify analysis returns higher values."""
    try:
        import numpy as np
        import soundfile as sf
    except ImportError:
        pytest.skip("soundfile/numpy not available")

    path = tmp_path / "loud.wav"
    t = np.linspace(0, 0.5, int(16000 * 0.5))
    wave = np.sin(2 * np.pi * 440 * t).astype("float32")
    sf.write(path, wave, 16000)

    result = analyze_lipsync(str(path), window_ms=50, step_ms=50)
    assert len(result) > 0
    # Loud sine wave should have some high values
    assert any(v > 0.3 for _t, v in result)
