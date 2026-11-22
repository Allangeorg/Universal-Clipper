from __future__ import annotations

import wave
from pathlib import Path
from typing import Any

import numpy as np


def analyze_audio(path: Path) -> dict[str, Any]:
    """Create a lightweight one-second audio evidence timeline using bounded memory."""
    rms: list[float] = []
    peak: list[float] = []
    flux: list[float] = []
    previous: np.ndarray | None = None
    with wave.open(str(path), "rb") as wav:
        channels, width, sample_rate = wav.getnchannels(), wav.getsampwidth(), wav.getframerate()
        if width != 2:
            raise ValueError(f"Expected 16-bit PCM audio, received {width * 8}-bit")
        decimated_len = sample_rate // 4
        hanning_win = np.hanning(decimated_len).astype(np.float32)
        while True:
            raw = wav.readframes(sample_rate)
            if not raw:
                break
            chunk = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
            if channels > 1:
                chunk = chunk.reshape(-1, channels).mean(axis=1)
            if chunk.size == 0:
                continue
            rms.append(float(np.sqrt(np.mean(chunk * chunk))))
            peak.append(float(np.max(np.abs(chunk))))
            # Pad the final partial second so every spectrum has the same shape.
            if chunk.size < sample_rate:
                chunk = np.pad(chunk, (0, sample_rate - chunk.size))
            decimated = chunk[::4]
            win = hanning_win if len(decimated) == decimated_len else np.hanning(len(decimated))
            spectrum = np.abs(np.fft.rfft(decimated * win))
            spectrum = spectrum / (np.linalg.norm(spectrum) + 1e-8)
            flux.append(float(np.maximum(spectrum - previous, 0).sum()) if previous is not None else 0.0)
            previous = spectrum

    if not rms:
        return {"seconds": [], "rms": [], "peak": [], "flux": [], "score": []}
    rms_a = np.asarray(rms, dtype=np.float32)
    peak_a = np.asarray(peak, dtype=np.float32)
    flux_a = np.asarray(flux, dtype=np.float32)
    baseline = float(np.median(rms_a) + 1e-6)
    # Local-relative loudness is more useful than a universal dB threshold.
    loud = np.clip((rms_a / baseline - 1.0) / 3.5, 0, 1)
    peak_n = np.clip((peak_a - np.percentile(peak_a, 35)) / (np.percentile(peak_a, 98) + 1e-6), 0, 1)
    flux_n = _normalise(flux_a)
    score = 0.55 * loud + 0.25 * peak_n + 0.20 * flux_n
    return {
        "seconds": list(range(len(rms))), "rms": rms, "peak": peak, "flux": flux,
        "score": np.clip(score, 0, 1).tolist(), "sample_rate": sample_rate,
    }


def _normalise(values: np.ndarray) -> np.ndarray:
    if values.size == 0:
        return values
    lo, hi = np.percentile(values, 10), np.percentile(values, 98)
    if hi - lo < 1e-8:
        return np.zeros_like(values)
    return np.clip((values - lo) / (hi - lo), 0, 1)


def detect_silence_intervals(
    path: Path,
    min_silence_duration: float = 0.6,
    silence_threshold_ratio: float = 0.08,
) -> list[dict[str, float]]:
    """Detect dead air / silent pauses using sliding RMS energy windows.

    Returns a list of intervals: [{'start': s, 'end': e, 'duration': d}].
    """
    if not path.exists():
        return []
    silences: list[dict[str, float]] = []
    try:
        with wave.open(str(path), "rb") as wav:
            channels, width, sample_rate = wav.getnchannels(), wav.getsampwidth(), wav.getframerate()
            if width != 2 or sample_rate <= 0:
                return []
            frame_size = int(sample_rate * 0.05)  # 50ms windows
            all_chunks: list[np.ndarray] = []
            while True:
                raw = wav.readframes(sample_rate * 5)
                if not raw:
                    break
                arr = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
                if channels > 1:
                    arr = arr.reshape(-1, channels).mean(axis=1)
                all_chunks.append(arr)
            if not all_chunks:
                return []
            signal = np.concatenate(all_chunks)
            trimmed_len = (len(signal) // frame_size) * frame_size
            if trimmed_len < frame_size:
                return []
            frames = signal[:trimmed_len].reshape(-1, frame_size)
            rms_per_frame = np.sqrt(np.mean(frames ** 2, axis=1))
            median_energy = float(np.median(rms_per_frame))
            thresh = max(0.003, median_energy * silence_threshold_ratio)
            silent_mask = rms_per_frame < thresh
            min_silent_frames = int(min_silence_duration / 0.05)

            padded = np.concatenate(([0], silent_mask.astype(int), [0]))
            diff = np.diff(padded)
            starts = np.where(diff == 1)[0]
            ends = np.where(diff == -1)[0]

            for s_idx, e_idx in zip(starts, ends):
                if e_idx - s_idx >= min_silent_frames:
                    s_time = round(float(s_idx * 0.05), 3)
                    e_time = round(float(e_idx * 0.05), 3)
                    silences.append({
                        "start": s_time,
                        "end": e_time,
                        "duration": round(e_time - s_time, 3),
                    })
    except Exception as exc:
        silences = []
    return silences

