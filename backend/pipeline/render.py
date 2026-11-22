from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.pipeline.media import MediaError

OUTPUTS = {
    "9:16": (1080, 1920),
    "16:9": (1920, 1080),
    "1:1": (1080, 1080),
    "original": None,
}


def render_clip(source: Path, target: Path, start: float, end: float, aspect_ratio: str = "9:16",
                preserve_audio: bool = True, crf: int = 16, framing_mode: str = "full_frame") -> dict[str, Any]:
    """Compatibility entry point that delegates to the canonical editor renderer.

    Older builds performed a blind centre crop here. Keeping a second renderer
    caused framing differences between bulk export and manual editing. All paths
    now share one render implementation and its duration/quality checks.
    """
    if aspect_ratio not in OUTPUTS:
        raise ValueError(f"Unsupported aspect ratio: {aspect_ratio}")
    from backend.pipeline.editor import render_edited_clip
    return render_edited_clip(
        source, target, start, end,
        {
            "aspect_ratio": aspect_ratio,
            "framing_mode": framing_mode if framing_mode in {"adaptive", "full_frame", "smart_crop", "manual_crop"} else "full_frame",
            "preserve_audio": preserve_audio,
            "normalize_audio": True,
            "crf": max(14, min(30, int(crf if crf != 16 else 18))),
            "preset": "fast",
            "speed": 1.0,
            "volume": 1.0,
            "fade_in": 0.0,
            "fade_out": 0.0,
        },
    )
