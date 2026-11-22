from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

from backend.config import FFMPEG

_BLACK_RE = re.compile(r"black_start:\s*([0-9.]+)\s+black_end:\s*([0-9.]+)\s+black_duration:\s*([0-9.]+)")


class QualityGateError(Exception):
    """Raised when rendered output violates quality standards."""


def validate_rendered_output(target: Path, expected_duration: float, expected_aspect: str = "9:16",
                             require_audio: bool = True) -> dict[str, Any]:
    """Validate output media using FFprobe for duration, aspect ratio, audio presence, and size."""
    from backend.pipeline.media import probe, MediaError
    from backend.pipeline.render import OUTPUTS
    if not target.is_file() or target.stat().st_size == 0:
        raise QualityGateError(f"Rendered file is missing or zero bytes: {target.name}")
    meta = probe(target)
    actual_duration = float(meta.get("duration", 0.0))
    duration_error = abs(actual_duration - expected_duration)
    # Output must match expected duration within one frame or 0.22s tolerance
    if duration_error > max(0.22, expected_duration * 0.005):
        raise QualityGateError(
            f"Render duration validation failed: expected {expected_duration:.2f}s, got {actual_duration:.2f}s "
            f"(error {duration_error:.2f}s exceeds tolerance)."
        )
    if expected_aspect in OUTPUTS and OUTPUTS[expected_aspect]:
        exp_w, exp_h = OUTPUTS[expected_aspect]
        act_w = int(meta.get("width") or 0)
        act_h = int(meta.get("height") or 0)
        if act_w != exp_w or act_h != exp_h:
            raise QualityGateError(
                f"Aspect ratio dimensions mismatch for {expected_aspect}: expected {exp_w}x{exp_h}, got {act_w}x{act_h}"
            )
    if require_audio and not meta.get("has_audio"):
        raise QualityGateError("Output video is unexpectedly silent (no audio stream found).")
    return {
        "valid": True,
        "duration": actual_duration,
        "expected_duration": expected_duration,
        "duration_error": round(duration_error, 3),
        "width": meta.get("width"),
        "height": meta.get("height"),
        "bytes": target.stat().st_size,
        "has_audio": meta.get("has_audio", False),
    }


def detect_black_gaps(source: Path, start: float, end: float, min_duration: float = 0.45) -> list[dict[str, float]]:
    """Report fully black visual sections inside a candidate, with times relative to it.

    These are warnings, not automatic cuts: a narration or soundtrack can continue
    through a deliberate black transition, so silently trimming it risks deleting
    spoken words or musical timing.
    """
    duration = max(0.1, float(end) - float(start))
    command = [
        FFMPEG, "-hide_banner", "-ss", f"{max(0.0, float(start)):.3f}", "-i", str(source),
        "-t", f"{duration:.3f}", "-vf", f"blackdetect=d={min_duration:.3f}:pix_th=0.10:pic_th=0.98",
        "-an", "-f", "null", "-",
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=180, check=False)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []
    matches = []
    for line in result.stderr.splitlines():
        match = _BLACK_RE.search(line)
        if not match:
            continue
        black_start, black_end, black_duration = map(float, match.groups())
        matches.append({
            "start": max(0.0, black_start),
            "end": min(duration, black_end),
            "duration": max(0.0, min(black_duration, duration)),
        })
    return matches
