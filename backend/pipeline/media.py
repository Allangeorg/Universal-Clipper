from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from backend.config import FFMPEG, FFPROBE


class MediaError(RuntimeError):
    pass


def run_command(command: list[str], timeout: int = 3600) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
    except FileNotFoundError as exc:
        raise MediaError(f"Required program was not found: {command[0]}. Install FFmpeg and add it to PATH.") from exc
    except subprocess.TimeoutExpired as exc:
        raise MediaError(f"Command timed out after {timeout} seconds: {command[0]}") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "Unknown media-processing error")[-1800:]
        raise MediaError(detail)
    return result


def probe(path: Path) -> dict[str, Any]:
    result = run_command([
        FFPROBE, "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)
    ], timeout=60)
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise MediaError("FFprobe returned invalid media metadata.") from exc
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        raise MediaError("The selected file does not contain a video stream.")
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    duration = float(data.get("format", {}).get("duration") or video.get("duration") or 0)
    if duration <= 0:
        raise MediaError("Could not determine the video duration.")
    return {
        "duration": duration,
        "width": int(video.get("width") or 0),
        "height": int(video.get("height") or 0),
        "fps": video.get("avg_frame_rate", "0/1"),
        "video_codec": video.get("codec_name"),
        "video_bitrate": int(video.get("bit_rate") or 0),
        "pixel_format": video.get("pix_fmt"),
        "audio_codec": audio.get("codec_name") if audio else None,
        "has_audio": audio is not None,
        "format": data.get("format", {}).get("format_name"),
    }


def extract_audio(source: Path, target: Path) -> bool:
    target.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-i", str(source), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(target)],
        capture_output=True, text=True, timeout=1800, check=False,
    )
    if result.returncode != 0:
        target.unlink(missing_ok=True)
        if "does not contain any stream" in (result.stderr or "").lower():
            return False
        raise MediaError((result.stderr or "Audio extraction failed")[-1200:])
    return target.exists() and target.stat().st_size > 44
