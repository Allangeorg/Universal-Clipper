import json

import pytest
from pathlib import Path

from backend.providers import ai


def test_editorial_plan_validates_style_emphasis_and_preserves_clip_duration(tmp_path, monkeypatch):
    candidate = {"start": 12.0, "end": 57.0, "content_type": "podcast", "transcript": "This is a remarkable idea that changed everything."}
    monkeypatch.setattr(ai, "get_preferences", lambda: {
        "vision_model": "test/vision-model", "llm_model": "test/text-model",
        "send_transcript_to_ai": True,
    })
    monkeypatch.setattr(ai, "chat", lambda *args, **kwargs: json.dumps({
        "title": "A Remarkable Idea", "caption_style": "karaoke", "caption_position": "bottom",
        "font_size": 72, "emphasis_words": ["remarkable", "invented-word"],
        "visual_treatment": "cinematic", "fade_in": 0.2, "fade_out": 0.15,
        "burned_captions_visible": False, "editing_notes": ["Keep the full two-person frame"],
        "quality_risks": [],
        "framing": {"layout": "single_subject", "primary_subject_id": "host", "confidence": 9,
                    "keyframes": [
                        {"t": 2, "subjects": [{"id": "host", "bbox": [420, 180, 620, 700]}]},
                        {"t": 12, "subjects": [{"id": "host", "bbox": [430, 180, 630, 700]}]},
                        {"t": 24, "subjects": [{"id": "host", "bbox": [440, 180, 640, 700]}]},
                        {"t": 36, "subjects": [{"id": "host", "bbox": [450, 180, 650, 700]}]},
                    ]},
    }))
    def fake_frame(source, timestamp, width):
        return b"fake-jpeg"
    plan = ai.create_editorial_plan(candidate, Path("unused.mp4"), "fake-key", fake_frame)
    assert plan["title"] == "A Remarkable Idea"
    assert plan["emphasis_words"] == ["remarkable"]
    assert plan["duration_preserved"] is True
    assert plan["frames_reviewed"] == 15
    assert plan["visual_treatment"] == "cinematic"
    assert plan["fade_in"] == 0.2 and plan["fade_out"] == 0.15
    assert plan["framing_plan"]["layout"] == "single_subject"
    assert len(plan["framing_plan"]["keyframes"]) == 4


def test_editorial_plan_respects_disabled_frame_sharing(tmp_path, monkeypatch):
    candidate = {"start": 0.0, "end": 30.0, "content_type": "podcast", "transcript": "A complete sentence for editing."}
    monkeypatch.setattr(ai, "get_preferences", lambda: {
        "vision_model": "test/vision-model", "llm_model": "test/text-model",
        "send_transcript_to_ai": True, "send_video_frames_to_ai": False, "vision_enabled": True,
    })
    def fake_chat(*args, **kwargs):
        assert all(part.get("type") != "image_url" for part in args[2][0]["content"] if isinstance(part, dict))
        return json.dumps({"title": "A Complete Sentence", "caption_style": "clean",
                           "caption_position": "bottom", "font_size": 64,
                           "emphasis_words": ["complete"], "framing": {"layout": "single_subject"}})
    monkeypatch.setattr(ai, "chat", fake_chat)
    def fail_frame(*args, **kwargs):
        raise AssertionError("frame extraction must not run when sharing is disabled")
    plan = ai.create_editorial_plan(candidate, Path("unused.mp4"), "fake-key", fail_frame)
    assert plan["frames_reviewed"] == 0
    assert plan["framing_plan"]["layout"] == "full_frame"


def test_low_confidence_framing_plan_falls_back_to_full_frame():
    result = ai._validate_framing_plan({
        "layout": "single_subject", "primary_subject_id": "host", "confidence": 3,
        "keyframes": [
            {"t": 0, "subjects": [{"id": "host", "bbox": [300, 100, 600, 800]}]},
            {"t": 1, "subjects": [{"id": "host", "bbox": [310, 100, 610, 800]}]},
            {"t": 2, "subjects": [{"id": "host", "bbox": [320, 100, 620, 800]}]},
        ],
    }, 3)
    assert result["layout"] == "full_frame"
    assert "low" in result["reason"].lower() or "confidence" in result["reason"].lower()


def test_editorial_plan_requires_explicit_vision_model_when_frame_sharing_is_enabled(tmp_path, monkeypatch):
    candidate = {"start": 0.0, "end": 20.0, "content_type": "podcast", "transcript": "A useful complete sentence."}
    monkeypatch.setattr(ai, "get_preferences", lambda: {
        "vision_model": "", "llm_model": "openrouter/auto",
        "send_transcript_to_ai": True, "send_video_frames_to_ai": True, "vision_enabled": True,
    })
    with pytest.raises(ai.ProviderError, match="vision-capable OpenRouter model"):
        ai.create_editorial_plan(candidate, Path("unused.mp4"), "fake-key", lambda *args: b"frame")
