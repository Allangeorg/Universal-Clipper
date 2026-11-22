from __future__ import annotations

import concurrent.futures
import json
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

import httpx

from backend.config import FFMPEG

TRANSCRIPTION_PROVIDERS = {
    "openrouter": {"base_url": "https://openrouter.ai/api/v1", "default_model": "openai/whisper-large-v3-turbo"},
    "groq": {"base_url": "https://api.groq.com/openai/v1", "default_model": "whisper-large-v3-turbo"},
    "openai": {"base_url": "https://api.openai.com/v1", "default_model": "gpt-transcribe"},
}
# OpenRouter's transcription route documents a 60-second upstream timeout and
# a 25 MB multipart limit; four-minute 48 kbps MP3 parts stay comfortably below it.
MAX_CHUNK_SECONDS = 4 * 60


class TranscriptionError(RuntimeError):
    pass


def _make_mp3(source: Path, target: Path, start: float, duration: float) -> None:
    command = [
        FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{start:.3f}",
        "-t", f"{duration:.3f}", "-i", str(source), "-vn", "-ac", "1", "-ar", "16000",
        "-b:a", "48k", "-f", "mp3", str(target),
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=240, check=False)
    if result.returncode != 0 or not target.exists() or target.stat().st_size == 0:
        raise TranscriptionError((result.stderr or "Could not prepare audio for transcription.")[-1200:])
    if target.stat().st_size > 24 * 1024 * 1024:
        raise TranscriptionError("Prepared transcription chunk exceeds the 24 MB safety limit. Reduce the chunk duration.")


def transcribe_audio_api(
    audio_path: Path,
    output_path: Path,
    provider: str,
    api_key: str,
    model: str,
    progress: Callable[[float, str], None] | None = None,
    base_url: str | None = None,
) -> dict[str, Any]:
    if provider not in TRANSCRIPTION_PROVIDERS:
        raise TranscriptionError("Select OpenRouter, Groq or OpenAI for API transcription, or disable transcription.")
    if not api_key:
        raise TranscriptionError(f"No {provider.title()} API key is saved. Add it in Settings to enable speech recognition.")
    endpoint = (base_url or TRANSCRIPTION_PROVIDERS[provider]["base_url"]).rstrip("/") + "/audio/transcriptions"
    duration = _audio_duration(audio_path)
    if duration <= 0:
        raise TranscriptionError("Audio duration could not be determined for transcription.")
    segments: list[dict[str, Any]] = []
    words: list[dict[str, Any]] = []
    texts: list[str] = []
    languages: list[str] = []
    chunk_count = max(1, int((duration + MAX_CHUNK_SECONDS - 1) // MAX_CHUNK_SECONDS))
    with tempfile.TemporaryDirectory(prefix="universal-clipper-transcribe-") as temp_dir:
        temp_root = Path(temp_dir)
        def process_chunk(chunk_index: int) -> tuple[int, dict[str, Any], float, float]:
            start = chunk_index * MAX_CHUNK_SECONDS
            chunk_duration = min(MAX_CHUNK_SECONDS, duration - start)
            chunk_path = temp_root / f"chunk_{chunk_index:04d}.mp3"
            _make_mp3(audio_path, chunk_path, start, chunk_duration)
            payload = _transcribe_chunk(endpoint, api_key, model, chunk_path, provider)
            return chunk_index, payload, start, chunk_duration

        max_workers = min(4, chunk_count)
        chunk_results: list[tuple[int, dict[str, Any], float, float]] = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(process_chunk, idx) for idx in range(chunk_count)]
            completed = 0
            for fut in concurrent.futures.as_completed(futures):
                res = fut.result()
                chunk_results.append(res)
                completed += 1
                if progress:
                    progress(completed / chunk_count, f"Transcribing audio via {provider.title()} · part {completed}/{chunk_count}")

        chunk_results.sort(key=lambda item: item[0])
        for chunk_index, payload, start, chunk_duration in chunk_results:
            language = payload.get("language")
            if language and language not in languages:
                languages.append(str(language))
            chunk_segments = payload.get("segments") or []
            if chunk_segments:
                for item in chunk_segments:
                    try:
                        local_start = float(item.get("start", 0))
                        local_end = float(item.get("end", local_start))
                    except (TypeError, ValueError):
                        continue
                    absolute_start, absolute_end = start + local_start, start + local_end
                    if absolute_start >= duration or absolute_end <= start:
                        continue
                    text = str(item.get("text", "")).strip()
                    if text:
                        segments.append({"start": round(max(0, absolute_start), 3), "end": round(min(duration, absolute_end), 3), "text": text, "words": []})
            elif payload.get("text"):
                segments.append({"start": round(start, 3), "end": round(start + chunk_duration, 3), "text": str(payload["text"]).strip(), "words": []})
            for word in (payload.get("words") or []):
                try:
                    word_start = start + float(word.get("start", 0))
                    word_end = start + float(word.get("end", word_start))
                except (TypeError, ValueError):
                    continue
                word_item = {"start": round(max(0, word_start), 3), "end": round(min(duration, word_end), 3), "text": str(word.get("word", word.get("text", ""))).strip()}
                if word_item["text"] and word_item["end"] > word_item["start"]:
                    words.append(word_item)
            if payload.get("text"):
                texts.append(str(payload["text"]).strip())
    segments.sort(key=lambda item: item["start"])
    transcript_text = " ".join(texts).strip() or " ".join(item["text"] for item in segments)
    if not transcript_text and not segments:
        raise TranscriptionError("The transcription API returned no speech. You can still continue with audio and visual event detection.")
    words.sort(key=lambda item: item["start"])
    result = {"provider": provider, "model": model, "language": languages[0] if languages else None,
              "segments": segments, "words": words, "text": transcript_text,
              "word_timestamps_available": bool(words)}
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = output_path.with_suffix(output_path.suffix + ".tmp")
    temp_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_path.replace(output_path)
    return result


def _transcribe_chunk(endpoint: str, api_key: str, model: str, path: Path, provider: str) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {api_key}"}
    if provider == "openrouter":
        headers["HTTP-Referer"] = "http://127.0.0.1:8765"
        headers["X-Title"] = "Universal Clipper"
    response_format = "json" if provider == "openai" and model.startswith("gpt-") else "verbose_json"
    data: dict[str, Any] = {"model": model, "response_format": response_format}
    if response_format == "verbose_json":
        data["timestamp_granularities[]"] = ["segment", "word"]
    last_error = "Unknown transcription error"
    for attempt in range(3):
        try:
            with path.open("rb") as audio_file:
                response = httpx.post(endpoint, headers=headers, data=data,
                                      files={"file": (path.name, audio_file, "audio/mpeg")}, timeout=85 if provider == "openrouter" else 300)
            if response.status_code in {429, 500, 502, 503, 504} and attempt < 2:
                last_error = _response_error(response)
                time.sleep(1.5 * (2 ** attempt))
                continue
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise TranscriptionError("Transcription provider returned an unexpected response format.")
            return payload
        except httpx.HTTPStatusError as exc:
            raise TranscriptionError(f"Transcription API failed ({exc.response.status_code}): {_response_error(exc.response)}") from exc
        except (httpx.HTTPError, ValueError) as exc:
            last_error = str(exc)
            if attempt == 2:
                break
            time.sleep(1.5 * (2 ** attempt))
    raise TranscriptionError(f"Transcription API request failed after retries: {last_error}")


def _audio_duration(path: Path) -> float:
    import wave
    try:
        with wave.open(str(path), "rb") as wav:
            return wav.getnframes() / float(wav.getframerate())
    except (wave.Error, OSError):
        # The audio extractor currently creates PCM WAV; FFmpeg can probe other
        # audio containers as a defensive fallback.
        result = subprocess.run([FFMPEG, "-hide_banner", "-i", str(path)], capture_output=True, text=True, timeout=30, check=False)
        import re
        match = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", result.stderr or "")
        if not match:
            return 0.0
        hours, minutes, seconds = match.groups()
        return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def _response_error(response: httpx.Response) -> str:
    try:
        error = response.json().get("error", {})
        if isinstance(error, dict):
            return str(error.get("message") or error)[:900]
        return str(error)[:900]
    except ValueError:
        return (response.text or "Provider error")[:900]
