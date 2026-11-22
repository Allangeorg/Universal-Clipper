from __future__ import annotations

import subprocess
from pathlib import Path

import cv2
import numpy as np

from backend.config import FFMPEG


def extract_frame_jpeg(source: Path, timestamp: float, max_width: int = 640) -> bytes | None:
    """Extract a small JPEG still for cloud vision review; never uploads the whole video."""
    command = [FFMPEG, "-hide_banner", "-loglevel", "error", "-ss", f"{max(0.0, timestamp):.3f}",
               "-i", str(source), "-frames:v", "1", "-vf", f"scale='min({max_width},iw)':-2",
               "-q:v", "5", "-f", "image2pipe", "-vcodec", "mjpeg", "pipe:1"]
    try:
        result = subprocess.run(command, capture_output=True, timeout=30, check=False)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    return result.stdout if result.returncode == 0 and result.stdout else None
