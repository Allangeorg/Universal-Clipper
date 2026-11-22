from __future__ import annotations

import json
import threading
from typing import Any

from backend.config import SETTINGS_PATH

_LOCK = threading.RLock()
DEFAULTS: dict[str, Any] = {
    "llm_provider": "openrouter",
    "llm_model": "openrouter/auto",
    "vision_model": "auto",
    "vision_enabled": True,
    "transcription_provider": "openrouter",
    "transcription_model": "openai/whisper-large-v3-turbo",
    "transcription_enabled": True,
    "max_ai_candidates": 12,
    "send_transcript_to_ai": True,
    "send_video_frames_to_ai": True,
}


def get_effective_vision_model(api_key: str | None = None) -> str:
    prefs = get_preferences()
    model = str(prefs.get("vision_model") or "").strip()
    if model and model != "auto":
        return model
    try:
        from backend.providers.openrouter import resolve_best_model
        from backend.providers.secrets import get_secret
        key = api_key or get_secret("openrouter") or get_secret("openai") or ""
        return resolve_best_model(key, kind="vision")
    except Exception:
        return ""


def get_preferences() -> dict[str, Any]:
    with _LOCK:
        try:
            raw = json.loads(SETTINGS_PATH.read_text(encoding="utf-8")) if SETTINGS_PATH.exists() else {}
        except (OSError, json.JSONDecodeError):
            raw = {}
        result = dict(DEFAULTS)
        if isinstance(raw, dict):
            result.update({key: value for key, value in raw.items() if key in DEFAULTS})
        return result


def save_preferences(updates: dict[str, Any]) -> dict[str, Any]:
    allowed = set(DEFAULTS)
    clean = {key: value for key, value in updates.items() if key in allowed}
    current = get_preferences()
    current.update(clean)
    # Validate and normalize preferences before they touch disk.
    if current["llm_provider"] != "openrouter":
        raise ValueError("The current build supports OpenRouter for AI ranking and vision. More providers will be added through the same adapter interface.")
    if current["transcription_provider"] not in {"openrouter", "groq", "openai", "disabled"}:
        raise ValueError("Transcription provider must be OpenRouter, Groq, OpenAI, or Disabled.")
    for key in ("llm_model", "vision_model", "transcription_model"):
        if not isinstance(current[key], str) or len(current[key]) > 200:
            raise ValueError(f"Invalid {key} value.")
    current["vision_enabled"] = bool(current["vision_enabled"])
    current["transcription_enabled"] = bool(current["transcription_enabled"])
    current["send_transcript_to_ai"] = bool(current["send_transcript_to_ai"])
    current["send_video_frames_to_ai"] = bool(current["send_video_frames_to_ai"])
    try:
        current["max_ai_candidates"] = max(1, min(24, int(current["max_ai_candidates"])))
    except (TypeError, ValueError):
        current["max_ai_candidates"] = DEFAULTS["max_ai_candidates"]
    with _LOCK:
        SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = SETTINGS_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(current, indent=2), encoding="utf-8")
        tmp.replace(SETTINGS_PATH)
    return current
