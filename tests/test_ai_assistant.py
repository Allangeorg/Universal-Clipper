import pytest
from backend.providers.ai import interpret_editing_command


def test_interpret_editing_command_cinematic():
    clip = {"start": 10.0, "end": 70.0}
    current = {"brightness": 0.0, "contrast": 1.0, "saturation": 1.0, "caption_style": "bold"}
    result = interpret_editing_command("Make this more cinematic", current, clip, source_duration=120.0)
    assert result["settings"]["contrast"] == 1.08
    assert result["settings"]["saturation"] == 0.92
    assert result["settings"]["caption_style"] == "clean"
    assert any("cinematic" in change.lower() for change in result["applied_changes"])


def test_interpret_editing_command_subtitles_top():
    clip = {"start": 10.0, "end": 70.0}
    current = {"caption_position": "bottom"}
    result = interpret_editing_command("Move subtitles to the top", current, clip, source_duration=120.0)
    assert result["settings"]["caption_position"] == "top"
    assert any("top" in change.lower() for change in result["applied_changes"])


def test_interpret_editing_command_karaoke():
    clip = {"start": 10.0, "end": 70.0}
    current = {"caption_style": "minimal", "captions_enabled": False}
    result = interpret_editing_command("Use karaoke caption style", current, clip, source_duration=120.0)
    assert result["settings"]["caption_style"] == "karaoke"
    assert result["settings"]["captions_enabled"] is True


def test_interpret_editing_command_exact_duration():
    clip = {"start": 10.0, "end": 70.0}
    current = {"start": 10.0, "end": 70.0}
    result = interpret_editing_command("Make this exactly 45 seconds", current, clip, source_duration=120.0)
    assert result["settings"]["end"] - result["settings"]["start"] == pytest.approx(45.0, abs=0.01)


def test_interpret_editing_command_full_frame():
    clip = {"start": 10.0, "end": 70.0}
    current = {"framing_mode": "adaptive"}
    result = interpret_editing_command("Show full frame with blur background", current, clip, source_duration=120.0)
    assert result["settings"]["framing_mode"] == "full_frame"


def test_ai_assist_endpoint(monkeypatch):
    from fastapi.testclient import TestClient
    from backend import main as app_module
    from backend import database

    test_clip = {"id": "c1", "start": 5.0, "end": 65.0, "title": "Test"}
    test_job = {"id": "j1", "duration": 180.0, "clips": [test_clip]}
    monkeypatch.setattr(database, "get_job", lambda jid: test_job if jid == "j1" else None)
    monkeypatch.setattr(app_module.manager, "start", lambda: None)

    with TestClient(app_module.app) as client:
        resp = client.post("/api/jobs/j1/clips/c1/ai-assist", json={
            "command": "Make this cinematic and karaoke",
            "current_settings": {"contrast": 1.0, "caption_style": "clean"}
        })
    assert resp.status_code == 200
    data = resp.json()
    assert data["settings"]["contrast"] == 1.08
    assert data["settings"]["caption_style"] == "karaoke"
