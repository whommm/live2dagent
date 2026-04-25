"""Audio volume envelope analysis for lip-sync."""

from __future__ import annotations

from pathlib import Path


def analyze_lipsync(
    audio_path: str,
    window_ms: float = 50.0,
    step_ms: float = 50.0,
    smoothing: int = 3,
    max_points: int = 200,
) -> list[tuple[float, float]]:
    """Analyze an audio file and return a lip-sync envelope.

    Returns a list of (timestamp_seconds, mouth_openness_0_to_1) tuples.
    The envelope is derived from the RMS energy of short audio windows.

    Parameters
    ----------
    audio_path:
        Path to the audio file (any format supported by soundfile).
    window_ms:
        Analysis window length in milliseconds.
    step_ms:
        Step size between consecutive windows in milliseconds.
    smoothing:
        Number of adjacent windows to average for smoothing.
    max_points:
        Maximum number of keyframes to return. If the analysis produces more,
        it is downsampled evenly.
    """
    try:
        import numpy as np
        import soundfile as sf
    except ImportError:
        return []

    path = Path(audio_path)
    if not path.exists():
        return []

    try:
        data, samplerate = sf.read(str(path), dtype="float32")
    except Exception:
        return []

    # Convert to mono if stereo
    if data.ndim > 1:
        data = data.mean(axis=1)

    if data.size == 0:
        return []

    window_samples = max(1, int(window_ms / 1000.0 * samplerate))
    step_samples = max(1, int(step_ms / 1000.0 * samplerate))

    # Compute RMS energy per window
    energies: list[float] = []
    timestamps: list[float] = []
    for start in range(0, len(data) - window_samples + 1, step_samples):
        window = data[start : start + window_samples]
        rms = float(np.sqrt(np.mean(window * window)))
        energies.append(rms)
        timestamps.append(start / samplerate)

    if not energies:
        return []

    # Smooth with moving average
    if smoothing > 1:
        smoothed: list[float] = []
        half = smoothing // 2
        for i in range(len(energies)):
            start = max(0, i - half)
            end = min(len(energies), i + half + 1)
            smoothed.append(sum(energies[start:end]) / (end - start))
        energies = smoothed

    # Normalize to 0-1 with a soft ceiling
    max_energy = max(energies) if max(energies) else 1.0
    # Use a percentile-based ceiling to avoid outliers dominating
    ceiling = float(np.percentile(energies, 95)) if len(energies) > 10 else max_energy
    ceiling = max(ceiling, 0.001)

    normalized = [min(1.0, e / ceiling) for e in energies]

    # Apply a gentle curve to make quiet parts quieter and loud parts louder
    shaped = [v**0.7 for v in normalized]

    # Downsample if too many points
    if len(timestamps) > max_points:
        step = len(timestamps) // max_points
        result = []
        for i in range(0, len(timestamps), step):
            chunk = shaped[i : i + step]
            result.append((timestamps[i], max(chunk)))
        return result

    return list(zip(timestamps, shaped, strict=False))


def dummy_lipsync(duration_sec: float, points: int = 20) -> list[tuple[float, float]]:
    """Generate a dummy lip-sync envelope when audio analysis is unavailable."""
    import math

    result: list[tuple[float, float]] = []
    for i in range(points):
        t = duration_sec * i / (points - 1) if points > 1 else 0.0
        # Simulated speech pattern: periodic bursts
        value = 0.3 + 0.5 * abs(math.sin(t * 8.0)) * (0.5 + 0.5 * math.sin(t * 3.0))
        result.append((t, min(1.0, value)))
    return result
